"""Research analysis, topic clustering and trend detection (spec sections 31 and 35).

Analysis runs in Celery workers (sync DB, one short event loop per AI step).
Trends are computed from counts, not asked of a model.
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.ai.prompts.research import SYSTEM, BatchAnalysis, analysis_prompt
from app.ai.service import AIContext, AIService, ModelTier, get_ai_service
from app.core.database import sync_session
from app.models import ResearchItem, ResearchItemTopic, Topic, User
from app.services import brand_service

ANALYSIS_BATCH = 10
# How many existing topic labels to offer the model for reuse.
REUSE_TOPICS = 40
MAX_ITEMS_PER_RUN = 50
ANALYSIS_TEXT_CHARS = 2500
# Measured with gemini-embedding-2 on 2026-09-24: rewordings of one topic scored 0.87-0.95,
# different topics 0.48-0.72.
TOPIC_MATCH_SIMILARITY = 0.86

ai_service_factory = get_ai_service


@dataclass
class AnalysisCounts:
    items_analyzed: int = 0
    topics_created: int = 0


def analyze_pending_items(brand_id: uuid.UUID, context: AIContext) -> AnalysisCounts:
    """Analyzes the brand's newest unanalyzed research items, in batches."""
    counts = AnalysisCounts()
    while counts.items_analyzed < MAX_ITEMS_PER_RUN:
        with sync_session() as db:
            batch = db.execute(
                select(ResearchItem.id, ResearchItem.title, ResearchItem.clean_text)
                .where(ResearchItem.brand_id == brand_id, ResearchItem.analyzed_at.is_(None))
                .order_by(ResearchItem.fetched_at.desc())
                .limit(ANALYSIS_BATCH)
            ).all()
            existing = _topic_names_by_size(db, brand_id)
        if not batch:
            break

        results = asyncio.run(_analyze_batch(ai_service_factory(), batch, existing, context))
        with sync_session() as db:
            counts.topics_created += store_analysis(db, brand_id, results)
        counts.items_analyzed += len(batch)
    return counts


@dataclass
class ItemResult:
    item_id: uuid.UUID
    summary: str | None
    topics: list[str]
    keywords: list[str]
    entities: list[str]
    embedding: list[float]
    topic_vectors: dict[str, list[float]]


def _topic_names_by_size(db: Session, brand_id: uuid.UUID) -> list[str]:
    return list(
        db.scalars(
            select(Topic.name)
            .join(ResearchItemTopic, ResearchItemTopic.topic_id == Topic.id)
            .where(Topic.brand_id == brand_id)
            .group_by(Topic.id)
            .order_by(func.count().desc(), Topic.name)
            .limit(REUSE_TOPICS)
        )
    )


async def _analyze_batch(ai: AIService, batch, existing_topics: list[str], context: AIContext) -> list[ItemResult]:
    refs = {f"R{i + 1}": row for i, row in enumerate(batch)}
    analysis = await ai.generate_structured(
        task="research_analysis",
        prompt=analysis_prompt(
            [(ref, row.title, row.clean_text[:ANALYSIS_TEXT_CHARS]) for ref, row in refs.items()], existing_topics
        ),
        schema=BatchAnalysis,
        system=SYSTEM,
        context=context,
        tier=ModelTier.FAST,
        max_output_tokens=8192,
        thinking_level="minimal",
    )
    by_ref = {a.ref: a for a in analysis.items if a.ref in refs}

    # One embedding call for every item and every distinct topic label.
    labels = sorted({label.lower() for a in by_ref.values() for label in a.topics})
    item_texts = [
        f"{row.title}\n{by_ref[ref].summary if ref in by_ref else row.clean_text[:500]}" for ref, row in refs.items()
    ]
    vectors = await ai.embed(task="research_embedding", texts=item_texts + labels, context=context)
    label_vectors = dict(zip(labels, vectors[len(item_texts) :], strict=True))

    results = []
    for (ref, row), vector in zip(refs.items(), vectors, strict=False):
        found = by_ref.get(ref)
        # An item the model skipped is still marked analyzed, so it isn't retried forever.
        results.append(
            ItemResult(
                item_id=row.id,
                summary=found.summary if found and found.summary else None,
                topics=found.topics if found else [],
                keywords=found.keywords if found else [],
                entities=found.entities if found else [],
                embedding=vector,
                topic_vectors={label: label_vectors[label.lower()] for label in (found.topics if found else [])},
            )
        )
    return results


def store_analysis(db: Session, brand_id: uuid.UUID, results: list[ItemResult]) -> int:
    """Saves analysis and links items to topics. Returns how many topics were created."""
    now = datetime.now(UTC)
    created = 0
    for result in results:
        item = db.get(ResearchItem, result.item_id)
        if item is None:
            continue
        if result.summary:
            item.summary = result.summary
        item.topics, item.keywords, item.entities = result.topics, result.keywords, result.entities
        item.embedding = result.embedding
        item.analyzed_at = now

        linked: set[uuid.UUID] = set()
        for label, vector in result.topic_vectors.items():
            topic, is_new = match_or_create_topic(db, brand_id, label, vector, now)
            created += is_new
            topic.last_seen_at = now
            if topic.id not in linked:
                db.add(ResearchItemTopic(research_item_id=item.id, topic_id=topic.id))
                linked.add(topic.id)
        db.flush()
    return created


def match_or_create_topic(
    db: Session, brand_id: uuid.UUID, label: str, vector: list[float], now: datetime
) -> tuple[Topic, bool]:
    """Same name, or close enough in meaning, joins an existing topic; otherwise a new one starts."""
    exact = db.scalar(select(Topic).where(Topic.brand_id == brand_id, func.lower(Topic.name) == label.lower()))
    if exact is not None:
        return exact, False

    distance = Topic.embedding.cosine_distance(vector)
    nearest = db.execute(
        select(Topic, distance.label("distance")).where(Topic.brand_id == brand_id).order_by(distance).limit(1)
    ).first()
    if nearest is not None and 1 - nearest.distance >= TOPIC_MATCH_SIMILARITY:
        return nearest.Topic, False

    topic = Topic(brand_id=brand_id, name=label[:120], embedding=vector, first_seen_at=now, last_seen_at=now)
    db.add(topic)
    db.flush()
    return topic, True


# --- Trends -------------------------------------------------------------------


@dataclass
class TopicTrend:
    id: uuid.UUID
    name: str
    item_count: int
    source_count: int
    items_7d: int
    items_prev_7d: int
    first_seen_at: datetime
    latest_at: datetime
    trend: str  # new | rising | recurring | steady
    score: float


def trends_query(brand_id: uuid.UUID, now: datetime) -> Select:
    moment = func.coalesce(ResearchItem.published_at, ResearchItem.fetched_at)
    week_ago, two_weeks_ago = now - timedelta(days=7), now - timedelta(days=14)
    return (
        select(
            Topic.id,
            Topic.name,
            Topic.first_seen_at,
            func.count(ResearchItem.id).label("item_count"),
            func.count(distinct(ResearchItem.source_id)).label("source_count"),
            func.count(ResearchItem.id).filter(moment >= week_ago).label("items_7d"),
            func.count(ResearchItem.id).filter(moment >= two_weeks_ago, moment < week_ago).label("items_prev_7d"),
            func.max(moment).label("latest_at"),
        )
        .join(ResearchItemTopic, ResearchItemTopic.topic_id == Topic.id)
        .join(ResearchItem, ResearchItem.id == ResearchItemTopic.research_item_id)
        .where(Topic.brand_id == brand_id)
        .group_by(Topic.id)
    )


def classify(row, now: datetime) -> TopicTrend:
    if row.first_seen_at >= now - timedelta(days=7):
        trend = "new"
    elif row.items_7d >= 2 and row.items_7d > row.items_prev_7d:
        trend = "rising"
    elif row.source_count >= 2 and row.item_count >= 3:
        trend = "recurring"
    else:
        trend = "steady"
    score = (
        row.items_7d * 2
        + row.source_count * 1.5
        + max(0, row.items_7d - row.items_prev_7d)
        + (3 if trend == "new" else 0)
    )
    return TopicTrend(
        id=row.id,
        name=row.name,
        item_count=row.item_count,
        source_count=row.source_count,
        items_7d=row.items_7d,
        items_prev_7d=row.items_prev_7d,
        first_seen_at=row.first_seen_at,
        latest_at=row.latest_at,
        trend=trend,
        score=score,
    )


def trending_topics(db: Session, brand_id: uuid.UUID, *, within_days: int = 30, limit: int = 8) -> list[TopicTrend]:
    now = datetime.now(UTC)
    trends = [classify(row, now) for row in db.execute(trends_query(brand_id, now))]
    recent = [t for t in trends if t.latest_at >= now - timedelta(days=within_days)]
    return sorted(recent, key=lambda t: (t.score, t.latest_at), reverse=True)[:limit]


async def list_topics(db: AsyncSession, user: User, brand_id: uuid.UUID, limit: int) -> list[TopicTrend]:
    await brand_service.get_brand(db, user, brand_id)
    now = datetime.now(UTC)
    rows = (await db.execute(trends_query(brand_id, now))).all()
    trends = [classify(row, now) for row in rows]
    return sorted(trends, key=lambda t: (t.score, t.latest_at), reverse=True)[:limit]

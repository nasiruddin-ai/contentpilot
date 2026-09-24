"""Content opportunities: generation (worker side) and management (API side).

Research → topic clustering → trend detection → opportunity generation →
validation → database (spec section 35).
"""

import asyncio
import logging
import math
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.ai.prompts.brand import brand_context
from app.ai.prompts.strategy import SYSTEM, OpportunityBatch, OpportunityDraft, PromptSource, PromptTopic, opportunity_prompt
from app.ai.service import AIContext, AIError, AIService, ModelTier
from app.core.database import sync_session
from app.core.errors import AppError
from app.models import (
    Brand,
    ContentOpportunity,
    OpportunityRun,
    OpportunityRunStatus,
    OpportunityStatus,
    ResearchItem,
    ResearchItemTopic,
    User,
)
from app.services import brand_service, topic_service

logger = logging.getLogger(__name__)

SOURCES_PER_TOPIC = 3
TOPICS_PER_RUN = 8
DUPLICATE_WINDOW = timedelta(days=90)
STALE_RUN_AFTER = timedelta(minutes=30)
# Measured with gemini-embedding-2 on 2026-09-24: rewordings of one idea scored 0.94-0.95,
# different ideas on related subjects about 0.74.
DUPLICATE_SIMILARITY = 0.90
# Similarity at or below this counts as fully novel.
NOVEL_SIMILARITY = 0.50
FRESH_DAYS = 30
PRIORITY_WEIGHTS = {"relevance": 0.35, "brand_fit": 0.25, "freshness": 0.20, "novelty": 0.20}

ACTIVE = (OpportunityRunStatus.QUEUED, OpportunityRunStatus.RUNNING)


# --- Scoring and validation (pure) ---------------------------------------------


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def freshness_score(newest: datetime | None, now: datetime) -> int:
    if newest is None:
        return 0
    age_days = max(0.0, (now - newest).total_seconds() / 86400)
    return max(0, round(100 * (1 - age_days / FRESH_DAYS)))


def novelty_score(max_similarity: float) -> int:
    span = DUPLICATE_SIMILARITY - NOVEL_SIMILARITY
    return max(0, min(100, round(100 * (DUPLICATE_SIMILARITY - max_similarity) / span)))


def priority_score(relevance: int, brand_fit: int, freshness: int, novelty: int) -> int:
    w = PRIORITY_WEIGHTS
    return round(
        w["relevance"] * relevance + w["brand_fit"] * brand_fit + w["freshness"] * freshness + w["novelty"] * novelty
    )


def contains_banned_word(text: str, banned: list[str]) -> str | None:
    for word in banned:
        if word and re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text, re.IGNORECASE):
            return word
    return None


@dataclass
class SourceRef:
    id: uuid.UUID
    title: str
    summary: str
    moment: datetime


@dataclass
class TopicRef:
    id: uuid.UUID
    name: str
    trend: str
    items_7d: int
    source_count: int
    sources: dict[str, SourceRef]  # ref -> source


@dataclass
class Accepted:
    draft: OpportunityDraft
    topic: TopicRef
    sources: list[SourceRef]
    embedding: list[float]
    freshness: int
    novelty: int
    priority: int


def screen_drafts(
    drafts: list[OpportunityDraft],
    topics: dict[str, TopicRef],
    draft_vectors: list[list[float]],
    existing_vectors: list[list[float]],
    banned_words: list[str],
    now: datetime,
) -> tuple[list[Accepted], int]:
    """Keeps only grounded, on-brand, non-duplicate ideas. Returns (accepted, rejected_count)."""
    all_sources = {ref: src for t in topics.values() for ref, src in t.sources.items()}
    accepted: list[Accepted] = []
    rejected = 0
    for draft, vector in zip(drafts, draft_vectors, strict=True):
        topic = topics.get(draft.topic_ref)
        # Only sources the model was actually shown; anything else is invented.
        sources = [all_sources[r] for r in dict.fromkeys(draft.source_refs) if r in all_sources]
        text = " ".join([draft.angle, draft.why_now, draft.audience])
        if topic is None or not sources or not draft.angle.strip() or contains_banned_word(text, banned_words):
            rejected += 1
            continue

        compared = existing_vectors + [a.embedding for a in accepted]
        max_similarity = max((cosine(vector, other) for other in compared), default=0.0)
        if max_similarity >= DUPLICATE_SIMILARITY:
            rejected += 1
            continue

        freshness = freshness_score(max(s.moment for s in sources), now)
        novelty = novelty_score(max_similarity)
        accepted.append(
            Accepted(
                draft=draft,
                topic=topic,
                sources=sources,
                embedding=vector,
                freshness=freshness,
                novelty=novelty,
                priority=priority_score(draft.relevance_score, draft.brand_fit_score, freshness, novelty),
            )
        )
    return accepted, rejected


# --- Worker side ---------------------------------------------------------------


def execute_run(run_id: uuid.UUID) -> dict:
    with sync_session() as db:
        run = db.get(OpportunityRun, run_id)
        if run is None or run.status not in ACTIVE:
            return {"status": "skipped"}
        run.status = OpportunityRunStatus.RUNNING
        run.started_at = datetime.now(UTC)
        brand = db.get(Brand, run.brand_id)
        brand_id, count = brand.id, run.requested_count
        context = AIContext(user_id=brand.user_id, brand_id=brand.id)

    try:
        analysis = topic_service.analyze_pending_items(brand_id, context)
        created, rejected = _generate(brand_id, count, context, run_id)
    except (AIError, AppError) as exc:
        return _finish(run_id, OpportunityRunStatus.FAILED, error=exc.message)
    except Exception:
        logger.exception("opportunity_run_crashed", extra={"run_id": str(run_id)})
        _finish(run_id, OpportunityRunStatus.FAILED, error="Unexpected error while generating opportunities.")
        raise

    return _finish(
        run_id,
        OpportunityRunStatus.SUCCEEDED,
        items_analyzed=analysis.items_analyzed,
        topics_created=analysis.topics_created,
        opportunities_created=created,
        opportunities_rejected=rejected,
    )


def _finish(run_id: uuid.UUID, status: OpportunityRunStatus, *, error: str | None = None, **counts) -> dict:
    with sync_session() as db:
        run = db.get(OpportunityRun, run_id)
        run.status = status
        run.error = error[:500] if error else None
        run.finished_at = datetime.now(UTC)
        for name, value in counts.items():
            setattr(run, name, value)
    result = {"status": status.value, "error": error, **counts}
    logger.info("opportunity_run_finished", extra={"run_id": str(run_id), **result})
    return result


def _generate(brand_id: uuid.UUID, count: int, context: AIContext, run_id: uuid.UUID) -> tuple[int, int]:
    now = datetime.now(UTC)
    with sync_session() as db:
        brand = db.get(Brand, brand_id)
        brand_text, banned = brand_context(brand), list(brand.banned_words)
        topics = _prompt_topics(db, brand_id)
        existing = [
            list(v)
            for v in db.scalars(
                select(ContentOpportunity.embedding).where(
                    ContentOpportunity.brand_id == brand_id, ContentOpportunity.created_at >= now - DUPLICATE_WINDOW
                )
            )
        ]
    if not topics:
        raise AppError("NO_RESEARCH", "No recent analyzed research to work from. Add sources and run research first.")

    accepted, rejected = asyncio.run(
        _draft_and_screen(topic_service.ai_service_factory(), brand_text, topics, count, existing, banned, context, now)
    )
    with sync_session() as db:
        for item in accepted:
            d = item.draft
            db.add(
                ContentOpportunity(
                    brand_id=brand_id,
                    run_id=run_id,
                    topic_id=item.topic.id,
                    topic=item.topic.name[:120],
                    angle=d.angle.strip()[:300],
                    why_now=d.why_now.strip(),
                    audience=d.audience.strip()[:300],
                    recommended_format=d.recommended_format,
                    recommended_platforms=[p.value for p in d.recommended_platforms],
                    content_pillar=d.content_pillar,
                    source_ids=[s.id for s in item.sources],
                    relevance_score=d.relevance_score,
                    brand_fit_score=d.brand_fit_score,
                    freshness_score=item.freshness,
                    novelty_score=item.novelty,
                    priority_score=item.priority,
                    embedding=item.embedding,
                )
            )
    return len(accepted), rejected


def _prompt_topics(db, brand_id: uuid.UUID) -> dict[str, TopicRef]:
    topics: dict[str, TopicRef] = {}
    source_number = 0
    for index, trend in enumerate(topic_service.trending_topics(db, brand_id, limit=TOPICS_PER_RUN), start=1):
        moment = func.coalesce(ResearchItem.published_at, ResearchItem.fetched_at)
        rows = db.execute(
            select(ResearchItem.id, ResearchItem.title, ResearchItem.summary, moment.label("moment"))
            .join(ResearchItemTopic, ResearchItemTopic.research_item_id == ResearchItem.id)
            .where(ResearchItemTopic.topic_id == trend.id)
            .order_by(moment.desc())
            .limit(SOURCES_PER_TOPIC)
        ).all()
        sources = {}
        for row in rows:
            source_number += 1
            sources[f"S{source_number}"] = SourceRef(row.id, row.title, row.summary, row.moment)
        topics[f"T{index}"] = TopicRef(trend.id, trend.name, trend.trend, trend.items_7d, trend.source_count, sources)
    return topics


async def _draft_and_screen(
    ai: AIService,
    brand_text: str,
    topics: dict[str, TopicRef],
    count: int,
    existing: list[list[float]],
    banned: list[str],
    context: AIContext,
    now: datetime,
) -> tuple[list[Accepted], int]:
    prompt_topics = [
        PromptTopic(
            ref=ref,
            name=t.name,
            trend=t.trend,
            items_7d=t.items_7d,
            source_count=t.source_count,
            sources=[
                PromptSource(ref=sref, title=s.title, summary=s.summary, published=s.moment.date().isoformat())
                for sref, s in t.sources.items()
            ],
        )
        for ref, t in topics.items()
    ]
    batch = await ai.generate_structured(
        task="opportunity_generation",
        prompt=opportunity_prompt(brand_text, prompt_topics, count),
        schema=OpportunityBatch,
        system=SYSTEM,
        context=context,
        tier=ModelTier.QUALITY,
        max_output_tokens=16384,
        thinking_level="low",
    )
    drafts = batch.opportunities[: count * 2]
    if not drafts:
        return [], 0
    vectors = await ai.embed(
        task="opportunity_embedding",
        texts=[f"{topics[d.topic_ref].name if d.topic_ref in topics else ''}: {d.angle}" for d in drafts],
        context=context,
    )
    accepted, rejected = screen_drafts(drafts, topics, vectors, existing, banned, now)
    # Extras beyond the requested count are simply not kept; they aren't counted as rejected.
    return accepted[:count], rejected


def enqueue_run(run_id: uuid.UUID) -> None:
    from app.workers.opportunity_tasks import generate_opportunities  # tasks import this module

    generate_opportunities.delay(str(run_id))


# --- API side ------------------------------------------------------------------


async def start_generation(db: AsyncSession, user: User, brand_id: uuid.UUID, count: int) -> OpportunityRun:
    """Returns the brand's in-flight run if there is one, so repeated clicks don't stack AI jobs."""
    await brand_service.get_brand(db, user, brand_id)
    existing = await db.scalar(
        select(OpportunityRun).where(
            OpportunityRun.brand_id == brand_id,
            OpportunityRun.status.in_(ACTIVE),
            OpportunityRun.created_at > datetime.now(UTC) - STALE_RUN_AFTER,
        )
    )
    if existing is not None:
        return existing
    run = OpportunityRun(brand_id=brand_id, requested_count=count)
    db.add(run)
    await db.commit()
    await db.refresh(run)
    await run_in_threadpool(enqueue_run, run.id)
    return run


async def get_run(db: AsyncSession, user: User, run_id: uuid.UUID) -> OpportunityRun:
    run = await db.scalar(
        select(OpportunityRun).join(Brand).where(OpportunityRun.id == run_id, Brand.user_id == user.id)
    )
    if run is None:
        raise AppError("RUN_NOT_FOUND", "Opportunity run not found.", 404)
    return run


async def list_runs(db: AsyncSession, user: User, brand_id: uuid.UUID, limit: int) -> list[OpportunityRun]:
    await brand_service.get_brand(db, user, brand_id)
    result = await db.scalars(
        select(OpportunityRun)
        .where(OpportunityRun.brand_id == brand_id)
        .order_by(OpportunityRun.created_at.desc())
        .limit(limit)
    )
    return list(result)


async def list_opportunities(
    db: AsyncSession,
    user: User,
    brand_id: uuid.UUID,
    *,
    status: OpportunityStatus | None,
    limit: int,
    offset: int,
) -> list[ContentOpportunity]:
    await brand_service.get_brand(db, user, brand_id)
    stmt = select(ContentOpportunity).where(ContentOpportunity.brand_id == brand_id)
    if status is not None:
        stmt = stmt.where(ContentOpportunity.status == status)
    else:
        stmt = stmt.where(ContentOpportunity.status != OpportunityStatus.DISMISSED)
    stmt = stmt.order_by(
        ContentOpportunity.priority_score.desc(), ContentOpportunity.created_at.desc(), ContentOpportunity.id
    )
    return list(await db.scalars(stmt.limit(limit).offset(offset)))


async def get_opportunity(
    db: AsyncSession, user: User, opportunity_id: uuid.UUID
) -> tuple[ContentOpportunity, list[ResearchItem]]:
    opportunity = await db.scalar(
        select(ContentOpportunity)
        .join(Brand)
        .where(ContentOpportunity.id == opportunity_id, Brand.user_id == user.id)
    )
    if opportunity is None:
        raise AppError("OPPORTUNITY_NOT_FOUND", "Opportunity not found.", 404)
    sources = list(
        await db.scalars(
            select(ResearchItem).where(
                ResearchItem.id.in_(opportunity.source_ids), ResearchItem.brand_id == opportunity.brand_id
            )
        )
    )
    return opportunity, sources


async def set_status(
    db: AsyncSession, user: User, opportunity_id: uuid.UUID, status: OpportunityStatus
) -> tuple[ContentOpportunity, list[ResearchItem]]:
    opportunity, sources = await get_opportunity(db, user, opportunity_id)
    opportunity.status = status
    await db.commit()
    await db.refresh(opportunity)
    return opportunity, sources

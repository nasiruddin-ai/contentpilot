"""Analytics sync and the learning loop (spec sections 57, 60-61).

Worker side: pull each published post's numbers from its platform and store a snapshot.
API side: overview, per-post, per-platform and per-topic reports from the latest snapshots.
Learning loop: summarise what performed, and hand that to opportunity generation as
plain-language guidance plus small, bounded score adjustments.

Engagement is weighted, not just likes (section 61): comments, shares and clicks count
for more, and clicks count for most when the brand's goal is leads, traffic or sales.
"""

import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core import crypto
from app.core.database import sync_session
from app.core.errors import AppError
from app.integrations import facebook, x
from app.models import Brand, ContentOpportunity, Platform, Post, PostStatus, User
from app.models.analytics import AnalyticsSync, PostMetric, SyncStatus
from app.models.social_account import SocialAccount
from app.services import brand_service, social_service

logger = logging.getLogger(__name__)

# Posts older than this stop being re-synced; their numbers have settled.
SYNC_WINDOW = timedelta(days=90)
DEFAULT_REPORT_DAYS = 30
MAX_POSTS_PER_SYNC = 200
# A group (pillar, format, topic) needs this many scored posts before it influences anything.
MIN_GROUP_SIZE = 3
MIN_POSTS_FOR_LEARNING = 5
MAX_LEARNED_BOOST = 10

BASE_WEIGHTS = {"likes": 1, "comments": 3, "shares": 4, "clicks": 2}
CONVERSION_GOAL_WORDS = ("lead", "traffic", "sale", "conversion", "enquir", "inquir", "client", "customer", "booking", "signup", "sign-up")

LINKEDIN_NOTE = "LinkedIn only shares member post analytics with approved partners (r_member_social)."


# --- Scoring (pure) ------------------------------------------------------------------


def weights_for(goals: list[str]) -> dict[str, int]:
    """Clicks count most when the brand wants leads, traffic or sales, not applause."""
    weights = dict(BASE_WEIGHTS)
    text = " ".join(goals).lower()
    if any(word in text for word in CONVERSION_GOAL_WORDS):
        weights["clicks"] = 5
    return weights


def engagement_score(metrics: dict, weights: dict[str, int]) -> int:
    return sum(weights[key] * int(metrics.get(key) or 0) for key in weights)


# --- Worker side ---------------------------------------------------------------------


def enqueue_sync(sync_id: uuid.UUID) -> None:
    from app.workers.analytics_tasks import sync_brand  # tasks import this module

    sync_brand.delay(str(sync_id))


def queue_all_brands() -> dict:
    """Beat task body: one sync per brand that has a connected account."""
    with sync_session() as db:
        brand_ids = list(db.scalars(select(SocialAccount.brand_id).distinct()))
        syncs = []
        for brand_id in brand_ids:
            sync = AnalyticsSync(brand_id=brand_id)
            db.add(sync)
            syncs.append(sync)
        db.flush()
        sync_ids = [s.id for s in syncs]
    for sync_id in sync_ids:
        enqueue_sync(sync_id)
    return {"queued": len(sync_ids)}


def execute_sync(sync_id: uuid.UUID) -> dict:
    with sync_session() as db:
        sync = db.get(AnalyticsSync, sync_id)
        if sync is None or sync.status not in (SyncStatus.QUEUED, SyncStatus.RUNNING):
            return {"status": "skipped"}
        sync.status = SyncStatus.RUNNING
        sync.started_at = datetime.now(UTC)
        brand = db.get(Brand, sync.brand_id)
        weights = weights_for(list(brand.goals))
        accounts = {
            a.platform: a
            for a in db.scalars(select(SocialAccount).where(SocialAccount.brand_id == brand.id))
            if social_service.is_usable(a)
        }
        posts = db.scalars(
            select(Post)
            .where(
                Post.brand_id == brand.id,
                Post.status == PostStatus.PUBLISHED,
                Post.external_post_id.is_not(None),
                Post.published_at >= datetime.now(UTC) - SYNC_WINDOW,
            )
            .order_by(Post.published_at.desc())
            .limit(MAX_POSTS_PER_SYNC)
        ).all()
        jobs = [(p.id, p.platform, p.external_post_id) for p in posts]
        tokens = {}
        for platform, account in accounts.items():
            try:
                tokens[platform] = crypto.decrypt(account.access_token_encrypted)
            except crypto.DecryptionError:
                pass
        brand_id = brand.id

    synced, failed, skipped = 0, 0, {}
    by_platform: dict[str, list] = defaultdict(list)
    for post_id, platform, external_id in jobs:
        by_platform[platform].append((post_id, external_id))

    for platform, items in by_platform.items():
        if platform == Platform.LINKEDIN:
            skipped[platform] = LINKEDIN_NOTE
            continue
        if platform not in tokens:
            skipped[platform] = "No usable connection; reconnect the account."
            continue
        try:
            results = asyncio.run(_fetch_platform(platform, tokens[platform], items))
        except (facebook.FacebookError, x.XError) as exc:
            skipped[platform] = f"{exc.code}: {exc.message}"[:300]
            continue
        with sync_session() as db:
            for post_id, metrics in results.items():
                if metrics is None:
                    failed += 1
                    continue
                db.add(_snapshot(brand_id, post_id, platform, metrics, weights))
                synced += 1

    with sync_session() as db:
        sync = db.get(AnalyticsSync, sync_id)
        sync.status = SyncStatus.SUCCEEDED
        sync.posts_synced, sync.posts_failed, sync.skipped = synced, failed, skipped
        sync.finished_at = datetime.now(UTC)
    logger.info("analytics_synced", extra={"sync_id": str(sync_id), "synced": synced, "failed": failed, "skipped": list(skipped)})
    return {"status": "succeeded", "synced": synced, "failed": failed, "skipped": skipped}


async def _fetch_platform(platform: str, token: str, items: list[tuple[uuid.UUID, str]]) -> dict[uuid.UUID, dict | None]:
    results: dict[uuid.UUID, dict | None] = {}
    if platform == Platform.FACEBOOK:
        client = facebook.FacebookClient(token)
        for post_id, external_id in items:
            try:
                m = await client.post_metrics(external_id)
            except facebook.FacebookError as exc:
                if exc.code in ("RECONNECT_REQUIRED", "RATE_LIMITED", "PROVIDER_ERROR"):
                    raise  # affects every post: skip the platform this time
                # Typically a deleted post: Facebook reports it as a permissions problem.
                logger.info("post_metrics_unavailable", extra={"post_id": str(post_id), "error": exc.code})
                results[post_id] = None
                continue
            results[post_id] = {
                "likes": m.likes,
                "comments": m.comments,
                "shares": m.shares,
                "clicks": m.clicks,
                "impressions": m.impressions,
                "missing_permissions": sorted(set(m.missing_permissions)),
                "raw": {"reactions_by_type": m.reactions_by_type},
            }
    elif platform == Platform.X:
        client = x.XClient(token)
        by_external = {external_id: post_id for post_id, external_id in items}
        for start in range(0, len(items), 100):
            batch = [external_id for _, external_id in items[start : start + 100]]
            found = await client.post_metrics(batch)
            for external_id in batch:
                data = found.get(external_id)
                results[by_external[external_id]] = {**data, "missing_permissions": []} if data else None
    return results


def _snapshot(brand_id: uuid.UUID, post_id: uuid.UUID, platform: str, m: dict, weights: dict[str, int]) -> PostMetric:
    score = engagement_score(m, weights)
    impressions = m.get("impressions")
    return PostMetric(
        post_id=post_id,
        brand_id=brand_id,
        platform=platform,
        impressions=impressions,
        reach=m.get("reach"),
        likes=m.get("likes"),
        comments=m.get("comments"),
        shares=m.get("shares"),
        clicks=m.get("clicks"),
        engagement_score=score,
        engagement_rate=(score / impressions) if impressions else None,
        missing_permissions=m.get("missing_permissions") or [],
        raw=m.get("raw") or {},
    )


# --- Reporting -----------------------------------------------------------------------


@dataclass
class ScoredPost:
    post_id: uuid.UUID
    platform: str
    content_type: str
    hook: str
    published_at: datetime
    external_post_id: str | None
    topic: str | None
    content_pillar: str | None
    likes: int | None
    comments: int | None
    shares: int | None
    clicks: int | None
    impressions: int | None
    engagement_score: int
    engagement_rate: float | None
    synced_at: datetime
    missing_permissions: list = field(default_factory=list)


def _latest_metrics_stmt(brand_id: uuid.UUID, since: datetime | None):
    """Each post's newest snapshot, joined with its topic and pillar."""
    latest = (
        select(PostMetric)
        .where(PostMetric.brand_id == brand_id)
        .distinct(PostMetric.post_id)
        .order_by(PostMetric.post_id, PostMetric.synced_at.desc())
        .subquery()
    )
    stmt = (
        select(
            Post.id,
            Post.platform,
            Post.content_type,
            Post.hook,
            Post.published_at,
            Post.external_post_id,
            ContentOpportunity.topic,
            ContentOpportunity.content_pillar,
            latest.c.likes,
            latest.c.comments,
            latest.c.shares,
            latest.c.clicks,
            latest.c.impressions,
            latest.c.engagement_score,
            latest.c.engagement_rate,
            latest.c.synced_at,
            latest.c.missing_permissions,
        )
        .join(latest, latest.c.post_id == Post.id)
        .outerjoin(ContentOpportunity, ContentOpportunity.id == Post.opportunity_id)
        .where(Post.brand_id == brand_id, Post.status == PostStatus.PUBLISHED)
    )
    if since is not None:
        stmt = stmt.where(Post.published_at >= since)
    return stmt.order_by(latest.c.engagement_score.desc(), Post.published_at.desc())


def _rows_to_scored(rows) -> list[ScoredPost]:
    return [
        ScoredPost(
            post_id=r[0], platform=r[1], content_type=r[2], hook=r[3], published_at=r[4], external_post_id=r[5],
            topic=r[6], content_pillar=r[7], likes=r[8], comments=r[9], shares=r[10], clicks=r[11],
            impressions=r[12], engagement_score=r[13], engagement_rate=r[14], synced_at=r[15],
            missing_permissions=list(r[16] or []),
        )
        for r in rows
    ]


def scored_posts_sync(db: Session, brand_id: uuid.UUID, since: datetime | None) -> list[ScoredPost]:
    return _rows_to_scored(db.execute(_latest_metrics_stmt(brand_id, since)).all())


async def scored_posts(db: AsyncSession, brand_id: uuid.UUID, since: datetime | None) -> list[ScoredPost]:
    return _rows_to_scored((await db.execute(_latest_metrics_stmt(brand_id, since))).all())


def _sum(posts: list[ScoredPost], attr: str) -> int | None:
    values = [getattr(p, attr) for p in posts if getattr(p, attr) is not None]
    return sum(values) if values else None


def totals(posts: list[ScoredPost]) -> dict:
    count = len(posts)
    score = sum(p.engagement_score for p in posts)
    return {
        "posts": count,
        "likes": _sum(posts, "likes"),
        "comments": _sum(posts, "comments"),
        "shares": _sum(posts, "shares"),
        "clicks": _sum(posts, "clicks"),
        "impressions": _sum(posts, "impressions"),
        "engagement_score": score,
        "avg_engagement_score": round(score / count, 1) if count else 0.0,
    }


def group_report(posts: list[ScoredPost], key: str) -> list[dict]:
    """Per pillar/format/topic/platform: totals, and how the group compares with the brand average."""
    groups: dict[str, list[ScoredPost]] = defaultdict(list)
    for post in posts:
        value = getattr(post, key)
        if value:
            groups[str(value)].append(post)
    overall = totals(posts)["avg_engagement_score"] if posts else 0.0
    report = []
    for name, members in groups.items():
        t = totals(members)
        avg = t["avg_engagement_score"]
        if len(members) < MIN_GROUP_SIZE or overall <= 0:
            verdict = "not_enough_data"
        elif avg >= overall * 1.25:
            verdict = "above_average"
        elif avg <= overall * 0.75:
            verdict = "below_average"
        else:
            verdict = "average"
        report.append({key: name, **t, "vs_brand_average": verdict})
    return sorted(report, key=lambda r: (r["avg_engagement_score"], r["posts"]), reverse=True)


# --- Learning loop --------------------------------------------------------------------


@dataclass
class Learning:
    posts_scored: int
    summary_text: str  # for the strategy prompt; empty when there isn't enough data
    boosts: dict[tuple[str, str], int]  # ("content_pillar" | "content_type", value) -> priority delta


def learn(posts: list[ScoredPost]) -> Learning:
    scored = [p for p in posts if p.engagement_score > 0 or p.likes is not None or p.comments is not None]
    if len(scored) < MIN_POSTS_FOR_LEARNING:
        return Learning(len(scored), "", {})
    overall = totals(scored)["avg_engagement_score"]
    boosts: dict[tuple[str, str], int] = {}
    lines = [f"WHAT HAS WORKED (last 90 days, {len(scored)} published posts with metrics; brand average engagement {overall:.0f})"]
    for key, label in (("content_pillar", "pillars"), ("content_type", "formats"), ("topic", "topics"), ("platform", "platforms")):
        rows = [r for r in group_report(scored, key) if r["vs_brand_average"] != "not_enough_data"]
        if not rows:
            continue
        best = [r for r in rows if r["vs_brand_average"] == "above_average"]
        worst = [r for r in rows if r["vs_brand_average"] == "below_average"]
        if best:
            lines.append(f"- Strong {label}: " + ", ".join(f"{r[key]} (avg {r['avg_engagement_score']:.0f}, {r['posts']} posts)" for r in best[:4]))
        if worst:
            lines.append(f"- Weak {label}: " + ", ".join(f"{r[key]} (avg {r['avg_engagement_score']:.0f}, {r['posts']} posts)" for r in worst[:4]))
        if key in ("content_pillar", "content_type") and overall > 0:
            for r in rows:
                delta = round((r["avg_engagement_score"] - overall) / overall * MAX_LEARNED_BOOST)
                boosts[(key, r[key])] = max(-MAX_LEARNED_BOOST, min(MAX_LEARNED_BOOST, delta))
    lines.append("Favour what performed for this brand's goal, but keep the content pillar mix balanced.")
    return Learning(len(scored), "\n".join(lines) if len(lines) > 2 else "", boosts)


def learning_for_brand(db: Session, brand_id: uuid.UUID) -> Learning:
    return learn(scored_posts_sync(db, brand_id, datetime.now(UTC) - SYNC_WINDOW))


# --- API side ---------------------------------------------------------------------------


async def start_sync(db: AsyncSession, user: User, brand_id: uuid.UUID) -> AnalyticsSync:
    await brand_service.get_brand(db, user, brand_id)
    connected = await db.scalar(select(func.count()).select_from(SocialAccount).where(SocialAccount.brand_id == brand_id))
    if not connected:
        raise AppError("ACCOUNT_NOT_CONNECTED", "Connect a social account before syncing analytics.", 409)
    running = await db.scalar(
        select(AnalyticsSync).where(
            AnalyticsSync.brand_id == brand_id,
            AnalyticsSync.status.in_((SyncStatus.QUEUED, SyncStatus.RUNNING)),
            AnalyticsSync.created_at > datetime.now(UTC) - timedelta(minutes=30),
        )
    )
    if running is not None:
        return running
    sync = AnalyticsSync(brand_id=brand_id)
    db.add(sync)
    await db.commit()
    await db.refresh(sync)
    await run_in_threadpool(enqueue_sync, sync.id)
    return sync


async def get_sync(db: AsyncSession, user: User, sync_id: uuid.UUID) -> AnalyticsSync:
    sync = await db.scalar(select(AnalyticsSync).join(Brand).where(AnalyticsSync.id == sync_id, Brand.user_id == user.id))
    if sync is None:
        raise AppError("SYNC_NOT_FOUND", "Analytics sync not found.", 404)
    return sync


async def _posts_for(db: AsyncSession, user: User, brand_id: uuid.UUID, days: int) -> list[ScoredPost]:
    await brand_service.get_brand(db, user, brand_id)
    return await scored_posts(db, brand_id, datetime.now(UTC) - timedelta(days=days))


async def overview(db: AsyncSession, user: User, brand_id: uuid.UUID, days: int) -> dict:
    posts = await _posts_for(db, user, brand_id, days)
    published_total = await db.scalar(
        select(func.count()).select_from(Post).where(
            Post.brand_id == brand_id,
            Post.status == PostStatus.PUBLISHED,
            Post.published_at >= datetime.now(UTC) - timedelta(days=days),
        )
    )
    missing = sorted({perm for p in posts for perm in p.missing_permissions})
    best = posts[0] if posts else None
    return {
        "days": days,
        "posts_published": published_total,
        "posts_with_metrics": len(posts),
        **{k: v for k, v in totals(posts).items() if k != "posts"},
        "best_post": _post_dict(best) if best else None,
        "last_synced_at": max((p.synced_at for p in posts), default=None),
        "missing_permissions": missing,
        "notes": _notes(posts, missing),
    }


def _notes(posts: list[ScoredPost], missing: list[str]) -> list[str]:
    notes = []
    if any(p.platform == Platform.FACEBOOK for p in posts):
        notes.append("Facebook no longer provides impressions or reach for Page posts, so engagement rate isn't available there.")
    if "pages_read_user_content" in missing:
        notes.append("Add the pages_read_user_content permission to your Facebook app to get comment and reaction counts.")
    if "read_insights" in missing:
        notes.append("Add the read_insights permission to your Facebook app to get clicks and reaction breakdowns.")
    if any(p.platform == Platform.LINKEDIN for p in posts):
        notes.append(LINKEDIN_NOTE)
    return notes


def _post_dict(p: ScoredPost) -> dict:
    from app.services.publishing_service import published_url

    return {
        "post_id": p.post_id,
        "platform": p.platform,
        "content_type": p.content_type,
        "hook": p.hook,
        "published_at": p.published_at,
        "published_url": published_url(p.platform, p.external_post_id) if p.external_post_id else None,
        "topic": p.topic,
        "content_pillar": p.content_pillar,
        "likes": p.likes,
        "comments": p.comments,
        "shares": p.shares,
        "clicks": p.clicks,
        "impressions": p.impressions,
        "engagement_score": p.engagement_score,
        "engagement_rate": p.engagement_rate,
        "synced_at": p.synced_at,
        "missing_permissions": p.missing_permissions,
    }


async def posts_report(db: AsyncSession, user: User, brand_id: uuid.UUID, days: int, limit: int) -> list[dict]:
    return [_post_dict(p) for p in (await _posts_for(db, user, brand_id, days))[:limit]]


async def platforms_report(db: AsyncSession, user: User, brand_id: uuid.UUID, days: int) -> list[dict]:
    return group_report(await _posts_for(db, user, brand_id, days), "platform")


async def topics_report(db: AsyncSession, user: User, brand_id: uuid.UUID, days: int) -> dict:
    posts = await _posts_for(db, user, brand_id, days)
    learning = learn(posts)
    return {
        "topics": group_report(posts, "topic"),
        "content_pillars": group_report(posts, "content_pillar"),
        "formats": group_report(posts, "content_type"),
        "learning_active": bool(learning.summary_text),
        "learning_summary": learning.summary_text or None,
    }

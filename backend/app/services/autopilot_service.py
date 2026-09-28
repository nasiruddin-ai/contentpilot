"""Autopilot (spec sections 5.2 and 83, milestone 10).

A cycle for one brand:
  refresh stale research → generate opportunities if needed → write posts for the best
  unused ideas → quality + risk checks → auto-approve and schedule into the next free
  slots, or hold for review → notify.

The user stays in control through the mode (off / copilot / autopilot), the posting
schedule, the platforms, a per-run cap, and the approval rules. Anything the rules or
the AI risk check flag waits for a person. When the risk check itself fails, the post
waits for review too.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.ai.prompts import risk as risk_prompts
from app.ai.service import AIContext, AIError, ModelTier
from app.core.database import sync_session
from app.core.errors import AppError
from app.models import (
    DEFAULT_RULES,
    AutopilotMode,
    AutopilotRun,
    AutopilotRunStatus,
    AutopilotSettings,
    Brand,
    ContentGeneration,
    ContentOpportunity,
    OpportunityRun,
    OpportunityStatus,
    Platform,
    Post,
    PostStatus,
    ResearchRun,
    RunStatus,
    RunTrigger,
    Source,
    User,
    Visual,
    VisualType,
)
from app.models.social_account import SocialAccount
from app.models.source import FETCHABLE_SOURCE_TYPES
from app.services import (
    brand_service,
    content_service,
    notification_service,
    opportunity_service,
    research_service,
    social_service,
    topic_service,
    visual_service,
)
from app.services.content_quality import render

logger = logging.getLogger(__name__)

RESEARCH_STALE_AFTER = timedelta(hours=24)
# Ideas are generated when fewer than this many unused ones are available.
MIN_UNUSED_OPPORTUNITIES = 3
# Daily cadence: the hourly tick runs a brand when its last run is older than this.
RUN_INTERVAL = timedelta(hours=23)
STALE_RUN_AFTER = timedelta(minutes=45)
# Two posts within this window count as the same slot.
SLOT_TOLERANCE = timedelta(hours=2)
CAROUSEL_FORMATS = {"carousel"}


# --- Slot planning (pure) --------------------------------------------------------------


def parse_time(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def open_slots(
    settings: AutopilotSettings, taken: list[datetime], now: datetime, *, limit: int
) -> list[datetime]:
    """Upcoming posting slots (UTC) in the horizon that no scheduled post occupies yet."""
    if not settings.days_of_week:
        return []
    try:
        zone = ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    days = {int(d) for d in settings.days_of_week}
    at = parse_time(settings.post_time)
    local_now = now.astimezone(zone)
    slots = []
    for offset in range(settings.horizon_days + 1):
        day = (local_now + timedelta(days=offset)).date()
        if day.weekday() not in days:
            continue
        slot = datetime.combine(day, at, tzinfo=zone).astimezone(UTC)
        if slot <= now + timedelta(minutes=30):
            continue
        if any(abs(slot - t) <= SLOT_TOLERANCE for t in taken):
            continue
        slots.append(slot)
        if len(slots) >= limit:
            break
    return slots


# --- Approval decision (pure) ------------------------------------------------------------


@dataclass
class Decision:
    action: str  # schedule | review | reject
    reasons: list[str] = field(default_factory=list)


def decide(
    mode: AutopilotMode,
    rules: dict,
    *,
    content_pillar: str | None,
    quality_issues: list[dict],
    risk: dict | None,
) -> Decision:
    """Applies the brand's approval rules. `risk` is None when the AI check failed."""
    if any(i.get("severity") == "error" for i in quality_issues):
        return Decision("reject", [i.get("detail", "quality error") for i in quality_issues if i.get("severity") == "error"])
    if mode != AutopilotMode.AUTOPILOT:
        return Decision("review", ["Copilot mode: every post is reviewed by a person."])

    reasons: list[str] = []
    review_if = {**DEFAULT_RULES["review_if"], **(rules.get("review_if") or {})}
    pillar = content_pillar or ""
    if pillar in (rules.get("always_review_pillars") or []):
        reasons.append(f"{pillar} posts always need review.")
    if pillar not in (rules.get("auto_approve_pillars") or []):
        reasons.append(f"'{pillar or 'unknown'}' isn't in the auto-approve pillars.")
    if review_if.get("quality_warnings") and any(i.get("severity") == "warning" for i in quality_issues):
        reasons.append("Quality warnings: " + "; ".join(i.get("detail", "") for i in quality_issues if i.get("severity") == "warning"))
    if risk is None:
        reasons.append("The risk check couldn't run.")
    else:
        labels = {
            "news_or_current_events": "news or current events",
            "sensitive_topic": "sensitive topic",
            "product_claims": "product claims",
            "high_risk_factual_claims": "high-risk factual claims",
        }
        for key, label in labels.items():
            if review_if.get(key) and risk.get(key):
                reasons.append(f"Flagged: {label}.")
        if reasons and risk.get("reasons"):
            reasons.extend(str(r) for r in risk["reasons"][:4])
    return Decision("review", reasons) if reasons else Decision("schedule", ["Brand-safe evergreen content; auto-approved by rules."])


# --- Cycle (worker) --------------------------------------------------------------------------


def enqueue_run(run_id: uuid.UUID) -> None:
    from app.workers.autopilot_tasks import run_autopilot  # tasks import this module

    run_autopilot.delay(str(run_id))


def tick() -> dict:
    """Beat task body: queue a cycle for each enabled brand that is due."""
    now = datetime.now(UTC)
    with sync_session() as db:
        due = db.scalars(
            select(AutopilotSettings).where(
                AutopilotSettings.mode != AutopilotMode.OFF,
                (AutopilotSettings.last_run_at.is_(None)) | (AutopilotSettings.last_run_at <= now - RUN_INTERVAL),
            )
        ).all()
        run_ids = []
        for settings in due:
            if db.scalar(_active_run(settings.brand_id)):
                continue
            settings.last_run_at = now
            run = AutopilotRun(brand_id=settings.brand_id, trigger="scheduled")
            db.add(run)
            db.flush()
            run_ids.append(run.id)
    for run_id in run_ids:
        enqueue_run(run_id)
    return {"queued": len(run_ids)}


def _active_run(brand_id: uuid.UUID):
    return select(AutopilotRun).where(
        AutopilotRun.brand_id == brand_id,
        AutopilotRun.status.in_((AutopilotRunStatus.QUEUED, AutopilotRunStatus.RUNNING)),
        AutopilotRun.created_at > datetime.now(UTC) - STALE_RUN_AFTER,
    )


def execute_cycle(run_id: uuid.UUID) -> dict:
    with sync_session() as db:
        run = db.get(AutopilotRun, run_id)
        if run is None or run.status not in (AutopilotRunStatus.QUEUED, AutopilotRunStatus.RUNNING):
            return {"status": "skipped"}
        run.status = AutopilotRunStatus.RUNNING
        run.started_at = datetime.now(UTC)
        settings = db.scalar(select(AutopilotSettings).where(AutopilotSettings.brand_id == run.brand_id))
        if settings is None or settings.mode == AutopilotMode.OFF:
            return _finish(db, run, AutopilotRunStatus.FAILED, error="Autopilot is off for this brand.")
        brand = db.get(Brand, run.brand_id)
        context = AIContext(user_id=brand.user_id, brand_id=brand.id)
        brand_id, mode, rules = brand.id, settings.mode, dict(settings.approval_rules or DEFAULT_RULES)
        platforms = _usable_platforms(db, brand_id, settings.platforms)
        if not platforms:
            return _finish(db, run, AutopilotRunStatus.FAILED, error="No connected platform matches the Autopilot settings.")
        taken = list(db.scalars(select(Post.scheduled_at).where(Post.brand_id == brand_id, Post.status == PostStatus.SCHEDULED, Post.scheduled_at.is_not(None))))
        slots = open_slots(settings, taken, datetime.now(UTC), limit=settings.max_posts_per_run)
        run.slots_open = len(slots)
        run.research_queued = _refresh_research(db, brand_id)
        auto_visual = settings.auto_visual

    if not slots:
        with sync_session() as db:
            return _finish(db, db.get(AutopilotRun, run_id), AutopilotRunStatus.SUCCEEDED)

    try:
        created_ideas = _ensure_opportunities(brand_id, needed=len(slots), run_id=run_id)
        decisions = _create_and_route_posts(brand_id, platforms, slots, mode, rules, auto_visual, context, run_id)
    except (AIError, AppError) as exc:
        with sync_session() as db:
            return _finish(db, db.get(AutopilotRun, run_id), AutopilotRunStatus.FAILED, error=exc.message)
    except Exception:
        logger.exception("autopilot_cycle_crashed", extra={"run_id": str(run_id)})
        with sync_session() as db:
            _finish(db, db.get(AutopilotRun, run_id), AutopilotRunStatus.FAILED, error="Unexpected error during the Autopilot cycle.")
        raise

    with sync_session() as db:
        run = db.get(AutopilotRun, run_id)
        run.opportunities_created = created_ideas
        run.decisions = decisions
        run.posts_created = len(decisions)
        run.posts_scheduled = sum(1 for d in decisions if d["decision"] == "schedule")
        run.posts_for_review = sum(1 for d in decisions if d["decision"] == "review")
        run.posts_rejected = sum(1 for d in decisions if d["decision"] == "reject")
        if run.posts_for_review:
            notification_service.notify(
                db, brand_id=brand_id, type="approval_required", title="Autopilot needs your review",
                message=f"{run.posts_for_review} new post(s) are waiting for approval.",
            )
        if run.posts_scheduled:
            notification_service.notify(
                db, brand_id=brand_id, type="autopilot_scheduled", title="Autopilot scheduled posts",
                message=f"{run.posts_scheduled} post(s) were approved by your rules and put on the calendar.",
            )
        return _finish(db, run, AutopilotRunStatus.SUCCEEDED)


def _finish(db, run: AutopilotRun, status: AutopilotRunStatus, *, error: str | None = None) -> dict:
    run.status = status
    run.error = error[:500] if error else None
    run.finished_at = datetime.now(UTC)
    result = {
        "status": status.value, "error": error, "slots_open": run.slots_open, "opportunities_created": run.opportunities_created,
        "posts_created": run.posts_created, "scheduled": run.posts_scheduled, "review": run.posts_for_review, "rejected": run.posts_rejected,
    }
    logger.info("autopilot_cycle_finished", extra={"run_id": str(run.id), **result})
    return result


def _usable_platforms(db, brand_id: uuid.UUID, wanted: list[str]) -> list[str]:
    accounts = db.scalars(select(SocialAccount).where(SocialAccount.brand_id == brand_id)).all()
    connected = {a.platform for a in accounts if social_service.is_usable(a) and a.platform in social_service.SUPPORTED_PLATFORMS}
    return [p for p in wanted if p in connected]


def _refresh_research(db, brand_id: uuid.UUID) -> int:
    """Queues fetches for sources that haven't been read in a day. Results land for the next cycle."""
    stale_before = datetime.now(UTC) - RESEARCH_STALE_AFTER
    sources = db.scalars(
        select(Source).where(
            Source.brand_id == brand_id, Source.active.is_(True), Source.source_type.in_(FETCHABLE_SOURCE_TYPES),
            (Source.last_fetched_at.is_(None)) | (Source.last_fetched_at < stale_before),
        )
    ).all()
    run_ids = []
    for source in sources:
        if db.scalar(select(ResearchRun).where(ResearchRun.source_id == source.id, ResearchRun.status.in_((RunStatus.QUEUED, RunStatus.RUNNING)))):
            continue
        run = ResearchRun(brand_id=brand_id, source_id=source.id, trigger=RunTrigger.SCHEDULED)
        db.add(run)
        db.flush()
        run_ids.append(run.id)
    db.commit()
    for run_id in run_ids:
        research_service.enqueue_run(run_id)
    return len(run_ids)


def _ensure_opportunities(brand_id: uuid.UUID, *, needed: int, run_id: uuid.UUID) -> int:
    with sync_session() as db:
        unused_ids = db.scalars(
            select(ContentOpportunity.id).where(
                ContentOpportunity.brand_id == brand_id,
                ContentOpportunity.status.in_((OpportunityStatus.NEW, OpportunityStatus.SAVED)),
            )
        ).all()
        if len(unused_ids) >= max(needed, MIN_UNUSED_OPPORTUNITIES):
            return 0
        opp_run = OpportunityRun(brand_id=brand_id, requested_count=max(needed, MIN_UNUSED_OPPORTUNITIES))
        db.add(opp_run)
        db.flush()
        opp_run_id = opp_run.id
    result = opportunity_service.execute_run(opp_run_id)
    if result.get("status") != "succeeded":
        if not unused_ids:
            raise AppError("AUTOPILOT_NO_IDEAS", f"Couldn't generate content ideas: {result.get('error') or 'unknown error'}")
        return 0
    return int(result.get("opportunities_created") or 0)


def _create_and_route_posts(
    brand_id: uuid.UUID, platforms: list[str], slots: list[datetime], mode: AutopilotMode, rules: dict,
    auto_visual: bool, context: AIContext, run_id: uuid.UUID,
) -> list[dict]:
    with sync_session() as db:
        ideas = db.scalars(
            select(ContentOpportunity)
            .where(ContentOpportunity.brand_id == brand_id, ContentOpportunity.status.in_((OpportunityStatus.NEW, OpportunityStatus.SAVED)))
            .order_by(ContentOpportunity.status.desc(), ContentOpportunity.priority_score.desc(), ContentOpportunity.created_at.desc())
            .limit(len(slots))
        ).all()
        idea_ids = [i.id for i in ideas]
        brand = db.get(Brand, brand_id)
        banned = list(brand.banned_words)

    decisions: list[dict] = []
    for idea_id, slot in zip(idea_ids, slots, strict=False):
        with sync_session() as db:
            generation = ContentGeneration(brand_id=brand_id, opportunity_id=idea_id, platforms=platforms)
            db.add(generation)
            db.flush()
            generation_id = generation.id
        result = content_service.execute_generation(generation_id)
        if result.get("status") != "succeeded":
            decisions.append({"post_id": None, "platform": ",".join(platforms), "decision": "reject", "reasons": [f"Writing failed: {result.get('error')}"]})
            continue
        with sync_session() as db:
            posts = db.scalars(select(Post).where(Post.generation_id == generation_id)).all()
            post_ids = [(p.id, p.platform, p.content_type) for p in posts]
        for post_id, platform, content_type in post_ids:
            decisions.append(_route_post(post_id, platform, content_type, slot, mode, rules, auto_visual, banned, context))
    return decisions


def _route_post(post_id, platform, content_type, slot, mode, rules, auto_visual, banned, context) -> dict:
    with sync_session() as db:
        post = db.get(Post, post_id)
        pillar = None
        if post.opportunity_id:
            idea = db.get(ContentOpportunity, post.opportunity_id)
            pillar = idea.content_pillar.value if idea and idea.content_pillar else None
        text = render(post.hook, post.body, post.cta, list(post.hashtags), Platform(post.platform))
        quality_issues = list(post.quality_issues or [])

    risk = None
    if mode == AutopilotMode.AUTOPILOT and not any(i.get("severity") == "error" for i in quality_issues):
        risk = _risk_check(platform, text, context)

    decision = decide(mode, rules, content_pillar=pillar, quality_issues=quality_issues, risk=risk)
    with sync_session() as db:
        post = db.get(Post, post_id)
        if decision.action == "reject":
            post.status = PostStatus.ARCHIVED
            post.review_note = ("Autopilot: " + "; ".join(decision.reasons))[:500]
        elif decision.action == "review":
            post.status = PostStatus.REVIEW
            post.review_note = ("Autopilot held this for review: " + "; ".join(decision.reasons))[:500]
        else:
            post.status = PostStatus.SCHEDULED
            post.approved_at = datetime.now(UTC)
            post.scheduled_at = slot
            post.review_note = ("Autopilot: " + "; ".join(decision.reasons))[:500]
        db.commit()

    if decision.action == "schedule" and auto_visual and content_type in CAROUSEL_FORMATS:
        _auto_visual(post_id)
    logger.info("autopilot_post_routed", extra={"post_id": str(post_id), "decision": decision.action})
    return {"post_id": str(post_id), "platform": platform, "decision": decision.action, "reasons": decision.reasons[:6]}


def _risk_check(platform: str, text: str, context: AIContext) -> dict | None:
    try:
        assessment = asyncio.run(
            topic_service.ai_service_factory().generate_structured(
                task="autopilot_risk_check", prompt=risk_prompts.risk_prompt(platform, text), schema=risk_prompts.RiskAssessment,
                system=risk_prompts.SYSTEM, context=context, tier=ModelTier.FAST, max_output_tokens=1024, thinking_level="minimal",
            )
        )
        return assessment.model_dump()
    except AIError as exc:
        logger.warning("autopilot_risk_check_failed", extra={"error": exc.code})
        return None


def _auto_visual(post_id: uuid.UUID) -> None:
    """Best effort: a carousel for scheduled carousel posts. Failure never blocks the post."""
    try:
        with sync_session() as db:
            post = db.get(Post, post_id)
            visual = Visual(brand_id=post.brand_id, post_id=post.id, visual_type=VisualType.CAROUSEL, aspect_ratio=visual_service.default_ratio(post.platform, VisualType.CAROUSEL))
            db.add(visual)
            db.flush()
            visual_id = visual.id
        visual_service.execute_visual(visual_id)
    except Exception:
        logger.exception("autopilot_visual_failed", extra={"post_id": str(post_id)})


# --- API side ----------------------------------------------------------------------------


async def get_settings(db: AsyncSession, user: User, brand_id: uuid.UUID) -> AutopilotSettings:
    await brand_service.get_brand(db, user, brand_id)
    settings = await db.scalar(select(AutopilotSettings).where(AutopilotSettings.brand_id == brand_id))
    if settings is None:
        settings = AutopilotSettings(brand_id=brand_id, approval_rules=dict(DEFAULT_RULES))
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


async def update_settings(db: AsyncSession, user: User, brand_id: uuid.UUID, changes: dict) -> AutopilotSettings:
    settings = await get_settings(db, user, brand_id)
    if "timezone" in changes:
        try:
            ZoneInfo(changes["timezone"])
        except ZoneInfoNotFoundError:
            raise AppError("INVALID_TIMEZONE", "Use an IANA timezone such as Europe/Amsterdam or Asia/Dhaka.", 422) from None
    if "platforms" in changes:
        unsupported = [p for p in changes["platforms"] if p not in social_service.SUPPORTED_PLATFORMS]
        if unsupported:
            raise AppError("PLATFORM_NOT_SUPPORTED", f"Autopilot can't publish to: {', '.join(unsupported)}.", 422)
    if "days_of_week" in changes:
        changes["days_of_week"] = [str(d) for d in sorted(set(changes["days_of_week"]))]
    if changes.get("mode") in (AutopilotMode.AUTOPILOT, AutopilotMode.COPILOT):
        platforms = changes.get("platforms", settings.platforms)
        days = changes.get("days_of_week", settings.days_of_week)
        if not platforms or not days:
            raise AppError("AUTOPILOT_INCOMPLETE", "Choose at least one platform and one posting day before turning Autopilot on.", 422)
        accounts = await db.scalars(select(SocialAccount).where(SocialAccount.brand_id == brand_id))
        connected = {a.platform for a in accounts if social_service.is_usable(a)}
        missing = [p for p in platforms if p not in connected]
        if missing:
            raise AppError("ACCOUNT_NOT_CONNECTED", f"Connect these platforms first: {', '.join(missing)}.", 409)
    for key, value in changes.items():
        setattr(settings, key, value)
    await db.commit()
    await db.refresh(settings)
    return settings


async def start_run(db: AsyncSession, user: User, brand_id: uuid.UUID) -> AutopilotRun:
    settings = await get_settings(db, user, brand_id)
    if settings.mode == AutopilotMode.OFF:
        raise AppError("AUTOPILOT_OFF", "Turn Autopilot on (copilot or autopilot mode) first.", 409)
    existing = await db.scalar(_active_run(brand_id))
    if existing is not None:
        return existing
    run = AutopilotRun(brand_id=brand_id, trigger="manual")
    settings.last_run_at = datetime.now(UTC)
    db.add(run)
    await db.commit()
    await db.refresh(run)
    await run_in_threadpool(enqueue_run, run.id)
    return run


async def list_runs(db: AsyncSession, user: User, brand_id: uuid.UUID, limit: int) -> list[AutopilotRun]:
    await brand_service.get_brand(db, user, brand_id)
    return list(await db.scalars(select(AutopilotRun).where(AutopilotRun.brand_id == brand_id).order_by(AutopilotRun.created_at.desc()).limit(limit)))


async def get_run(db: AsyncSession, user: User, run_id: uuid.UUID) -> AutopilotRun:
    run = await db.scalar(select(AutopilotRun).join(Brand).where(AutopilotRun.id == run_id, Brand.user_id == user.id))
    if run is None:
        raise AppError("RUN_NOT_FOUND", "Autopilot run not found.", 404)
    return run


async def preview_slots(db: AsyncSession, user: User, brand_id: uuid.UUID) -> list[datetime]:
    settings = await get_settings(db, user, brand_id)
    taken = list(await db.scalars(select(Post.scheduled_at).where(Post.brand_id == brand_id, Post.status == PostStatus.SCHEDULED, Post.scheduled_at.is_not(None))))
    return open_slots(settings, taken, datetime.now(UTC), limit=settings.max_posts_per_run)

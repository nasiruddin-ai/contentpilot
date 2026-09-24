"""Content generation (spec section 37):

Opportunity → research context → brand context → hook → outline → draft →
platform adaptation → quality check → final post.

Worker side runs the pipeline; API side starts it and reads posts.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.ai.prompts import editor, writer
from app.ai.prompts.brand import brand_context
from app.ai.prompts.writer import Brief, ContentPlan, MasterDraft, PlatformPost
from app.ai.service import AIContext, AIError, AIService, ModelTier
from app.core.database import sync_session
from app.core.errors import AppError
from app.models import (
    Brand,
    ContentFormat,
    ContentOpportunity,
    OpportunityStatus,
    Platform,
    ResearchItem,
    User,
)
from app.models.post import ContentGeneration, GenerationStatus, Post, PostStatus
from app.services import brand_service, opportunity_service, topic_service
from app.services.content_quality import PLATFORM_RULES, Issue, check_post, normalize_hashtags

logger = logging.getLogger(__name__)

SOURCE_TEXT_CHARS = 2000
STALE_AFTER = timedelta(minutes=30)
ACTIVE = (GenerationStatus.QUEUED, GenerationStatus.RUNNING)


# --- Pipeline (AI only, no database) ------------------------------------------


@dataclass
class FinalPost:
    platform: Platform
    hook: str
    body: str
    cta: str
    hashtags: list[str]
    issues: list[Issue] = field(default_factory=list)


@dataclass
class PipelineResult:
    plan: ContentPlan
    draft: MasterDraft
    posts: list[FinalPost]


def _platform_notes(platforms: list[Platform]) -> dict[str, str]:
    notes = {}
    for platform in platforms:
        rules = PLATFORM_RULES[platform]
        limit = f"Max {rules.max_chars} characters in total, at most {rules.max_hashtags} hashtags."
        if rules.hook_max:
            limit += f" Title (hook) max {rules.hook_max} characters."
        notes[platform.value] = f"{rules.notes} {limit}"
    return notes


def _check(post: FinalPost, content_type: ContentFormat, banned: list[str], source_text: str) -> list[Issue]:
    return check_post(
        platform=post.platform,
        content_type=content_type,
        hook=post.hook,
        body=post.body,
        cta=post.cta,
        hashtags=post.hashtags,
        banned_words=banned,
        source_text=source_text,
    )


def _errors(issues: list[Issue]) -> int:
    return sum(1 for i in issues if i.severity == "error")


async def write_content(
    ai: AIService,
    brief: Brief,
    platforms: list[Platform],
    content_type: ContentFormat,
    banned: list[str],
    source_text: str,
    context: AIContext,
) -> PipelineResult:
    common = {"system": writer.SYSTEM, "context": context, "tier": ModelTier.QUALITY, "thinking_level": "low"}
    plan = await ai.generate_structured(
        task="content_plan", prompt=writer.plan_prompt(brief), schema=ContentPlan, max_output_tokens=8192, **common
    )
    draft = await ai.generate_structured(
        task="content_draft",
        prompt=writer.draft_prompt(brief, plan),
        schema=MasterDraft,
        max_output_tokens=8192,
        **common,
    )
    notes = _platform_notes(platforms)
    adapted = await ai.generate_structured(
        task="content_adapt",
        prompt=writer.adapt_prompt(brief, draft, notes),
        schema=writer.Adaptations,
        max_output_tokens=16384,
        **common,
    )

    by_name = {p.platform.strip().lower(): p for p in adapted.posts}
    posts: list[FinalPost] = []
    for platform in platforms:
        source = by_name.get(platform.value)
        missing = source is None
        if missing:
            source = PlatformPost(platform=platform.value, hook=draft.hook, body=draft.body, cta=draft.cta)
        post = FinalPost(platform, source.hook, source.body, source.cta, normalize_hashtags(source.hashtags))
        post.issues = _check(post, content_type, banned, source_text)
        if missing:
            post.issues.append(Issue("warning", "not_adapted", "The master draft is used unadapted for this platform."))
        posts.append(post)

    await _review(ai, brief, posts, content_type, banned, source_text, notes, context)
    return PipelineResult(plan=plan, draft=draft, posts=posts)


async def _review(
    ai: AIService,
    brief: Brief,
    posts: list[FinalPost],
    content_type: ContentFormat,
    banned: list[str],
    source_text: str,
    notes: dict[str, str],
    context: AIContext,
) -> None:
    """Editor + brand check. A failed review leaves the posts as they were, with their issues."""
    try:
        review = await ai.generate_structured(
            task="content_review",
            prompt=editor.review_prompt(
                brief.brand_context,
                [PlatformPost(platform=p.platform.value, hook=p.hook, body=p.body, cta=p.cta, hashtags=p.hashtags) for p in posts],
                {p.platform.value: [i.detail for i in p.issues] for p in posts},
                notes,
                brief.sources,
            ),
            schema=editor.Review,
            system=editor.SYSTEM,
            context=context,
            tier=ModelTier.FAST,
            max_output_tokens=16384,
            thinking_level="minimal",
        )
    except AIError as exc:
        logger.warning("content_review_failed", extra={"error": exc.code})
        for post in posts:
            post.issues.append(Issue("warning", "not_reviewed", "The AI editor was unavailable; review manually."))
        return

    revisions = {r.platform.strip().lower(): r for r in review.posts if r.changed}
    for post in posts:
        revision = revisions.get(post.platform.value)
        if revision is None or not revision.body.strip():
            continue
        candidate = FinalPost(
            post.platform, revision.hook, revision.body, revision.cta, normalize_hashtags(revision.hashtags)
        )
        candidate.issues = _check(candidate, content_type, banned, source_text)
        # Only accept an edit that doesn't make hard problems worse.
        if _errors(candidate.issues) <= _errors(post.issues):
            post.hook, post.body, post.cta, post.hashtags = candidate.hook, candidate.body, candidate.cta, candidate.hashtags
            post.issues = candidate.issues


# --- Worker side ---------------------------------------------------------------


def execute_generation(generation_id: uuid.UUID) -> dict:
    with sync_session() as db:
        generation = db.get(ContentGeneration, generation_id)
        if generation is None or generation.status not in ACTIVE:
            return {"status": "skipped"}
        opportunity = db.get(ContentOpportunity, generation.opportunity_id) if generation.opportunity_id else None
        if opportunity is None:
            # Recorded in this session; a separate one would be overwritten when this one commits.
            generation.status = GenerationStatus.FAILED
            generation.error = "The opportunity no longer exists."
            generation.finished_at = datetime.now(UTC)
            return {"status": "failed", "error": generation.error}
        generation.status = GenerationStatus.RUNNING
        generation.started_at = datetime.now(UTC)
        brand = db.get(Brand, generation.brand_id)
        items = db.scalars(select(ResearchItem).where(ResearchItem.id.in_(opportunity.source_ids))).all()
        sources = [
            (f"S{i}", item.title, f"{item.summary}\n{item.clean_text[:SOURCE_TEXT_CHARS]}")
            for i, item in enumerate(items, start=1)
        ]
        brief = Brief(
            brand_context=brand_context(brand),
            topic=opportunity.topic,
            angle=opportunity.angle,
            why_now=opportunity.why_now,
            audience=opportunity.audience,
            content_format=opportunity.recommended_format.value,
            content_pillar=opportunity.content_pillar.value if opportunity.content_pillar else None,
            sources=sources,
        )
        platforms = [Platform(p) for p in generation.platforms]
        content_type = opportunity.recommended_format
        banned = list(brand.banned_words)
        source_ids = [item.id for item in items]
        opportunity_id = opportunity.id
        context = AIContext(user_id=brand.user_id, brand_id=brand.id)
        # Numbers are checked against the full source text, not just the excerpt sent to the model.
        source_text = "\n".join(f"{item.title}\n{item.summary}\n{item.clean_text}" for item in items)

    try:
        result = asyncio.run(
            write_content(topic_service.ai_service_factory(), brief, platforms, content_type, banned, source_text, context)
        )
    except (AIError, AppError) as exc:
        return _finish(generation_id, GenerationStatus.FAILED, error=exc.message)
    except Exception:
        logger.exception("content_generation_crashed", extra={"generation_id": str(generation_id)})
        _finish(generation_id, GenerationStatus.FAILED, error="Unexpected error while writing content.")
        raise

    with sync_session() as db:
        generation = db.get(ContentGeneration, generation_id)
        generation.hook_options = result.plan.hook_options
        generation.outline = [
            {"section": line} for line in result.plan.outline
        ] + [{"point": p.point, "source_refs": p.source_refs} for p in result.plan.key_points]
        generation.master_draft = f"{result.draft.hook}\n\n{result.draft.body}\n\n{result.draft.cta}".strip()
        for post in result.posts:
            db.add(
                Post(
                    brand_id=generation.brand_id,
                    opportunity_id=opportunity_id,
                    generation_id=generation_id,
                    platform=post.platform.value,
                    content_type=content_type,
                    hook=post.hook.strip(),
                    body=post.body.strip(),
                    cta=post.cta.strip() or None,
                    hashtags=post.hashtags,
                    source_ids=source_ids,
                    quality_issues=[i.as_dict() for i in post.issues],
                    status=PostStatus.DRAFT,
                )
            )
        opportunity = db.get(ContentOpportunity, opportunity_id)
        if opportunity is not None and opportunity.status in (OpportunityStatus.NEW, OpportunityStatus.SAVED):
            opportunity.status = OpportunityStatus.USED
    return _finish(generation_id, GenerationStatus.SUCCEEDED, posts=len(result.posts))


def _finish(generation_id: uuid.UUID, status: GenerationStatus, *, error: str | None = None, **extra) -> dict:
    with sync_session() as db:
        generation = db.get(ContentGeneration, generation_id)
        generation.status = status
        generation.error = error[:500] if error else None
        generation.finished_at = datetime.now(UTC)
    result = {"status": status.value, "error": error, **extra}
    logger.info("content_generation_finished", extra={"generation_id": str(generation_id), **result})
    return result


def enqueue_generation(generation_id: uuid.UUID) -> None:
    from app.workers.content_tasks import generate_content  # tasks import this module

    generate_content.delay(str(generation_id))


# --- API side ------------------------------------------------------------------


async def start_generation(
    db: AsyncSession, user: User, opportunity_id: uuid.UUID, platforms: list[Platform] | None
) -> ContentGeneration:
    opportunity, _ = await opportunity_service.get_opportunity(db, user, opportunity_id)
    chosen = list(dict.fromkeys(platforms or [Platform(p) for p in opportunity.recommended_platforms]))
    if not chosen:
        chosen = [Platform.LINKEDIN]

    existing = await db.scalar(
        select(ContentGeneration).where(
            ContentGeneration.opportunity_id == opportunity.id,
            ContentGeneration.status.in_(ACTIVE),
            ContentGeneration.created_at > datetime.now(UTC) - STALE_AFTER,
        )
    )
    if existing is not None:
        return existing

    generation = ContentGeneration(
        brand_id=opportunity.brand_id, opportunity_id=opportunity.id, platforms=[p.value for p in chosen]
    )
    db.add(generation)
    await db.commit()
    await db.refresh(generation)
    await run_in_threadpool(enqueue_generation, generation.id)
    return generation


async def get_generation(db: AsyncSession, user: User, generation_id: uuid.UUID) -> tuple[ContentGeneration, list[uuid.UUID]]:
    generation = await db.scalar(
        select(ContentGeneration)
        .join(Brand)
        .where(ContentGeneration.id == generation_id, Brand.user_id == user.id)
    )
    if generation is None:
        raise AppError("GENERATION_NOT_FOUND", "Content generation not found.", 404)
    post_ids = list(await db.scalars(select(Post.id).where(Post.generation_id == generation.id)))
    return generation, post_ids


async def list_posts(
    db: AsyncSession,
    user: User,
    brand_id: uuid.UUID,
    *,
    status: PostStatus | None,
    platform: Platform | None,
    limit: int,
    offset: int,
) -> list[Post]:
    await brand_service.get_brand(db, user, brand_id)
    stmt = select(Post).where(Post.brand_id == brand_id)
    stmt = stmt.where(Post.status == status) if status else stmt.where(Post.status != PostStatus.ARCHIVED)
    if platform:
        stmt = stmt.where(Post.platform == platform.value)
    stmt = stmt.order_by(Post.created_at.desc(), Post.platform, Post.id).limit(limit).offset(offset)
    return list(await db.scalars(stmt))


async def get_post(db: AsyncSession, user: User, post_id: uuid.UUID) -> tuple[Post, list[ResearchItem]]:
    post = await db.scalar(select(Post).join(Brand).where(Post.id == post_id, Brand.user_id == user.id))
    if post is None:
        raise AppError("POST_NOT_FOUND", "Post not found.", 404)
    sources = list(
        await db.scalars(
            select(ResearchItem).where(ResearchItem.id.in_(post.source_ids), ResearchItem.brand_id == post.brand_id)
        )
    )
    return post, sources


async def delete_post(db: AsyncSession, user: User, post_id: uuid.UUID) -> None:
    post, _ = await get_post(db, user, post_id)
    if post.status in (PostStatus.PUBLISHING, PostStatus.PUBLISHED):
        raise AppError("POST_PUBLISHED", "Published posts can't be deleted; archive them instead.", 409)
    await db.delete(post)
    await db.commit()

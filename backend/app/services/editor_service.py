"""Content editor (spec section 63): edit, AI revise, approve, reject, version history.

Rules:
- Human edits keep the post's status, unless they introduce an error-level issue; then the
  post drops back to draft and off the calendar, since it can't be published like that.
- AI revisions always return the post to draft, so a person approves AI-written text.
- Every change first saves the previous content as a version, so it can be restored.
"""

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.ai.prompts import writer
from app.ai.prompts.brand import brand_context
from app.ai.prompts.writer import PlatformPost
from app.ai.service import AIContext, AIError, ModelTier
from app.core.database import sync_session
from app.core.errors import AppError
from app.models import (
    EDITABLE_STATUSES,
    Brand,
    GenerationStatus,
    Platform,
    Post,
    PostRevision,
    PostStatus,
    PostVersion,
    ResearchItem,
    RevisionAction,
    User,
)
from app.services import content_service, topic_service
from app.services.content_quality import PLATFORM_RULES, check_post, normalize_hashtags

logger = logging.getLogger(__name__)

ACTIVE = (GenerationStatus.QUEUED, GenerationStatus.RUNNING)
STALE_AFTER = timedelta(minutes=30)
SOURCE_TEXT_CHARS = 2000


def content_hash(post: Post) -> str:
    payload = json.dumps([post.hook, post.body, post.cta, list(post.hashtags)], ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _snapshot(post: Post, reason: str) -> PostVersion:
    return PostVersion(
        post_id=post.id, hook=post.hook, body=post.body, cta=post.cta, hashtags=list(post.hashtags), reason=reason
    )


def _has_errors(issues: list[dict]) -> bool:
    return any(issue["severity"] == "error" for issue in issues)


def _issues_for(post: Post, banned: list[str], source_text: str) -> list[dict]:
    return [
        issue.as_dict()
        for issue in check_post(
            platform=Platform(post.platform),
            content_type=post.content_type,
            hook=post.hook,
            body=post.body,
            cta=post.cta,
            hashtags=list(post.hashtags),
            banned_words=banned,
            source_text=source_text,
        )
    ]


def _demote(post: Post) -> None:
    post.status = PostStatus.DRAFT
    post.scheduled_at = None
    post.approved_at = None


def _require_editable(post: Post) -> None:
    if post.status not in EDITABLE_STATUSES:
        raise AppError("POST_NOT_EDITABLE", f"A post that is {post.status} can't be changed.", 409)


async def _check_context(db: AsyncSession, post: Post) -> tuple[list[str], str]:
    brand = await db.get(Brand, post.brand_id)
    sources = await db.scalars(
        select(ResearchItem).where(ResearchItem.id.in_(post.source_ids), ResearchItem.brand_id == post.brand_id)
    )
    source_text = "\n".join(f"{s.title}\n{s.summary}\n{s.clean_text}" for s in sources)
    return list(brand.banned_words), source_text


# --- Human actions ----------------------------------------------------------------


async def edit_post(db: AsyncSession, user: User, post_id: uuid.UUID, changes: dict) -> Post:
    post, _ = await content_service.get_post(db, user, post_id)
    _require_editable(post)
    if "hashtags" in changes:
        changes["hashtags"] = normalize_hashtags(changes["hashtags"])
    if not any(getattr(post, field) != value for field, value in changes.items()):
        return post

    db.add(_snapshot(post, "edit"))
    for field, value in changes.items():
        setattr(post, field, value)
    banned, source_text = await _check_context(db, post)
    post.quality_issues = _issues_for(post, banned, source_text)
    if _has_errors(post.quality_issues) and post.status in (PostStatus.APPROVED, PostStatus.SCHEDULED):
        _demote(post)
    await db.commit()
    await db.refresh(post)
    return post


async def approve_post(db: AsyncSession, user: User, post_id: uuid.UUID) -> Post:
    post, _ = await content_service.get_post(db, user, post_id)
    if post.status in (PostStatus.APPROVED, PostStatus.SCHEDULED):
        return post
    if post.status not in (PostStatus.DRAFT, PostStatus.REVIEW):
        raise AppError("POST_NOT_EDITABLE", f"A post that is {post.status} can't be approved.", 409)
    errors = [i["detail"] for i in post.quality_issues if i["severity"] == "error"]
    if errors:
        raise AppError("POST_HAS_ERRORS", "Fix these before approving: " + " ".join(errors), 409)
    post.status = PostStatus.APPROVED
    post.approved_at = datetime.now(UTC)
    post.review_note = None
    await db.commit()
    await db.refresh(post)
    return post


async def reject_post(db: AsyncSession, user: User, post_id: uuid.UUID, reason: str | None) -> Post:
    post, _ = await content_service.get_post(db, user, post_id)
    _require_editable(post)
    post.status = PostStatus.ARCHIVED
    post.scheduled_at = None
    post.approved_at = None
    post.review_note = reason
    await db.commit()
    await db.refresh(post)
    return post


async def list_versions(db: AsyncSession, user: User, post_id: uuid.UUID) -> list[PostVersion]:
    post, _ = await content_service.get_post(db, user, post_id)
    result = await db.scalars(
        select(PostVersion).where(PostVersion.post_id == post.id).order_by(PostVersion.created_at.desc())
    )
    return list(result)


async def restore_version(db: AsyncSession, user: User, post_id: uuid.UUID, version_id: uuid.UUID) -> Post:
    post, _ = await content_service.get_post(db, user, post_id)
    _require_editable(post)
    version = await db.scalar(select(PostVersion).where(PostVersion.id == version_id, PostVersion.post_id == post.id))
    if version is None:
        raise AppError("VERSION_NOT_FOUND", "Version not found.", 404)
    return await edit_post(
        db,
        user,
        post_id,
        {"hook": version.hook, "body": version.body, "cta": version.cta, "hashtags": list(version.hashtags)},
    )


# --- AI revisions --------------------------------------------------------------------


async def start_revision(
    db: AsyncSession, user: User, post_id: uuid.UUID, action: RevisionAction, instruction: str | None
) -> PostRevision:
    post, _ = await content_service.get_post(db, user, post_id)
    _require_editable(post)
    if action in (RevisionAction.CHANGE_TONE, RevisionAction.CUSTOM) and not (instruction or "").strip():
        raise AppError("INSTRUCTION_REQUIRED", f"'{action}' needs an instruction.", 422)

    existing = await db.scalar(
        select(PostRevision).where(
            PostRevision.post_id == post.id,
            PostRevision.status.in_(ACTIVE),
            PostRevision.created_at > datetime.now(UTC) - STALE_AFTER,
        )
    )
    if existing is not None:
        raise AppError("REVISION_IN_PROGRESS", "This post is already being revised.", 409)

    revision = PostRevision(
        post_id=post.id,
        brand_id=post.brand_id,
        action=action,
        instruction=(instruction or "").strip() or None,
        base_hash=content_hash(post),
    )
    db.add(revision)
    await db.commit()
    await db.refresh(revision)
    await run_in_threadpool(enqueue_revision, revision.id)
    return revision


async def get_revision(db: AsyncSession, user: User, revision_id: uuid.UUID) -> PostRevision:
    revision = await db.scalar(
        select(PostRevision).join(Brand).where(PostRevision.id == revision_id, Brand.user_id == user.id)
    )
    if revision is None:
        raise AppError("REVISION_NOT_FOUND", "Revision not found.", 404)
    return revision


def enqueue_revision(revision_id: uuid.UUID) -> None:
    from app.workers.content_tasks import revise_post  # tasks import this module

    revise_post.delay(str(revision_id))


def execute_revision(revision_id: uuid.UUID) -> dict:
    with sync_session() as db:
        revision = db.get(PostRevision, revision_id)
        if revision is None or revision.status not in ACTIVE:
            return {"status": "skipped"}
        revision.status = GenerationStatus.RUNNING
        revision.started_at = datetime.now(UTC)
        post = db.get(Post, revision.post_id)
        brand = db.get(Brand, post.brand_id)
        items = db.scalars(select(ResearchItem).where(ResearchItem.id.in_(post.source_ids))).all()
        sources = [
            (f"S{i}", item.title, f"{item.summary}\n{item.clean_text[:SOURCE_TEXT_CHARS]}")
            for i, item in enumerate(items, start=1)
        ]
        source_text = "\n".join(f"{i.title}\n{i.summary}\n{i.clean_text}" for i in items)
        platform = Platform(post.platform)
        rules = PLATFORM_RULES[platform]
        notes = f"{rules.notes} Max {rules.max_chars} characters, at most {rules.max_hashtags} hashtags."
        prompt = writer.revise_prompt(
            brand_context(brand),
            platform.value,
            notes,
            PlatformPost(platform=platform.value, hook=post.hook, body=post.body, cta=post.cta or "", hashtags=list(post.hashtags)),
            sources,
            revision.action.value,
            revision.instruction,
        )
        action, banned = revision.action.value, list(brand.banned_words)
        context = AIContext(user_id=brand.user_id, brand_id=brand.id)

    try:
        revised = asyncio.run(
            topic_service.ai_service_factory().generate_structured(
                task=f"post_revision:{action}",
                prompt=prompt,
                schema=PlatformPost,
                system=writer.SYSTEM,
                context=context,
                tier=ModelTier.QUALITY,
                max_output_tokens=8192,
                thinking_level="low",
            )
        )
    except (AIError, AppError) as exc:
        return _finish(revision_id, GenerationStatus.FAILED, exc.message)
    except Exception:
        logger.exception("post_revision_crashed", extra={"revision_id": str(revision_id)})
        _finish(revision_id, GenerationStatus.FAILED, "Unexpected error while revising the post.")
        raise

    if not revised.body.strip():
        return _finish(revision_id, GenerationStatus.FAILED, "The AI returned an empty post.")

    with sync_session() as db:
        revision = db.get(PostRevision, revision_id)
        post = db.get(Post, revision.post_id)
        if post.status not in EDITABLE_STATUSES or content_hash(post) != revision.base_hash:
            # The user changed the post while we were working; don't overwrite their edit.
            revision.status = GenerationStatus.FAILED
            revision.error = "The post changed while it was being revised. Nothing was overwritten; try again."
            revision.finished_at = datetime.now(UTC)
            return {"status": "failed", "error": revision.error}

        db.add(_snapshot(post, f"revision:{action}"))
        post.hook = revised.hook.strip()
        post.body = revised.body.strip()
        post.cta = revised.cta.strip() or None
        post.hashtags = normalize_hashtags(revised.hashtags)
        post.quality_issues = _issues_for(post, banned, source_text)
        _demote(post)
        revision.status = GenerationStatus.SUCCEEDED
        revision.finished_at = datetime.now(UTC)
    logger.info("post_revised", extra={"revision_id": str(revision_id), "action": action})
    return {"status": "succeeded"}


def _finish(revision_id: uuid.UUID, status: GenerationStatus, error: str | None = None) -> dict:
    with sync_session() as db:
        revision = db.get(PostRevision, revision_id)
        revision.status = status
        revision.error = error[:500] if error else None
        revision.finished_at = datetime.now(UTC)
    return {"status": status.value, "error": error}

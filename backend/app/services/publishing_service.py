"""Publishing posts to connected accounts (spec sections 45, 56, 58-59): LinkedIn and X.

A post moves approved/scheduled → publishing → published | failed. Celery beat
queues scheduled posts when they are due; each publish runs under a per-post lock.

Never posting twice matters more than never failing. Neither platform accepts an
idempotency key, so failures are sorted by whether the post could have gone out:

- Nothing reached the platform (couldn't connect, 429, 502/503/504) or the failure
  was before the create call (media upload): retry with backoff (1, 4, 16 minutes).
- The create call timed out, dropped, or returned 500: the post may be live. It is
  marked failed as "uncertain" and the user is asked to check before retrying.
- Everything else (login expired, no credits, permission denied): fail with the reason.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from redis.exceptions import LockError, RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core import crypto
from app.core.database import sync_session
from app.core.errors import AppError
from app.core.redis import get_sync_redis
from app.integrations import facebook, linkedin, x
from app.models import Platform, Post, PostStatus, User, Visual, VisualStatus
from app.models.social_account import SocialAccount, SocialAccountStatus
from app.services import content_service, notification_service, social_service
from app.services.content_quality import render
from app.storage import get_storage

logger = logging.getLogger(__name__)

# FAILED is included so a failed post can be retried by hand.
PUBLISHABLE = (PostStatus.APPROVED, PostStatus.SCHEDULED, PostStatus.FAILED)
# Renew X tokens this long before they expire, so a publish never races the expiry.
REFRESH_MARGIN = timedelta(minutes=5)
# Seconds to wait before each automatic retry.
RETRY_DELAYS = (60, 240, 960)
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1
# Longer than the longest retry delay; a post "publishing" for this long was interrupted.
STUCK_AFTER = timedelta(minutes=30)
DUE_BATCH = 100
LOCK_SECONDS = 10 * 60
PlatformError = (linkedin.LinkedInError, x.XError, facebook.FacebookError)


def published_url(platform: str, external_id: str) -> str:
    if platform == Platform.LINKEDIN:
        return linkedin.post_url(external_id)
    if platform == Platform.FACEBOOK:
        return facebook.post_url(external_id)
    return x.post_url(external_id)


class _CreateFailed(Exception):
    """A failure of the create-post call itself, where the post may already exist."""

    def __init__(self, error) -> None:
        super().__init__(error.message)
        self.error = error


def _outcome(exc, *, at_create: bool) -> str:
    """'retry', 'uncertain' or 'fail'."""
    if at_create:
        maybe_created = exc.code in {"TIMEOUT", "NETWORK_ERROR", "PUBLISH_UNCONFIRMED"} or (
            exc.code == "PROVIDER_ERROR" and exc.status not in (502, 503, 504)
        )
        if maybe_created:
            return "uncertain"
    return "retry" if exc.retryable else "fail"


# --- API side ---------------------------------------------------------------------


async def start_publish(db: AsyncSession, user: User, post_id: uuid.UUID) -> Post:
    post, _ = await content_service.get_post(db, user, post_id)
    if post.status not in PUBLISHABLE:
        raise AppError(
            "POST_NOT_PUBLISHABLE", f"Only approved, scheduled or failed posts can be published (this one is {post.status}).", 409
        )
    if post.external_post_id:
        raise AppError("ALREADY_PUBLISHED", "This post has already been published.", 409)
    if post.platform not in social_service.SUPPORTED_PLATFORMS:
        raise AppError("PLATFORM_NOT_SUPPORTED", f"Publishing to {post.platform} isn't available yet.", 422)
    if any(issue["severity"] == "error" for issue in post.quality_issues):
        raise AppError("POST_HAS_ERRORS", "Fix the post's errors before publishing.", 409)

    account = await db.scalar(
        select(SocialAccount).where(SocialAccount.brand_id == post.brand_id, SocialAccount.platform == post.platform)
    )
    label = social_service.platform_label(post.platform)
    if account is None:
        raise AppError("ACCOUNT_NOT_CONNECTED", f"Connect {label} for this brand first.", 409)
    if not social_service.is_usable(account):
        raise AppError("RECONNECT_REQUIRED", f"The {label} connection has expired. Reconnect it.", 409)

    post.status = PostStatus.PUBLISHING
    post.publish_error = None
    post.publish_attempts = 0
    await db.commit()
    await db.refresh(post)
    await run_in_threadpool(enqueue_publish, post.id)
    return post


def enqueue_publish(post_id: uuid.UUID) -> None:
    from app.workers.publishing_tasks import publish_post  # tasks import this module

    publish_post.delay(str(post_id))


def enqueue_retry(post_id: uuid.UUID, delay_seconds: int) -> None:
    from app.workers.publishing_tasks import publish_post

    publish_post.apply_async(args=[str(post_id)], countdown=delay_seconds)


# --- Scheduler ------------------------------------------------------------------------


def queue_due_posts() -> dict:
    """Beat task body: queue scheduled posts whose time has come; flag interrupted ones."""
    now = datetime.now(UTC)
    with sync_session() as db:
        due = db.scalars(
            select(Post)
            .where(Post.status == PostStatus.SCHEDULED, Post.scheduled_at <= now)
            .order_by(Post.scheduled_at)
            .limit(DUE_BATCH)
            .with_for_update(skip_locked=True)
        ).all()
        for post in due:
            post.status = PostStatus.PUBLISHING
            post.publish_attempts = 0
            post.publish_error = None
        due_ids = [post.id for post in due]

        stuck = db.scalars(
            select(Post)
            .where(Post.status == PostStatus.PUBLISHING, Post.updated_at < now - STUCK_AFTER)
            .with_for_update(skip_locked=True)
        ).all()
        for post in stuck:
            _mark_uncertain(db, post, "Publishing was interrupted.")
    # Enqueued after the commit, so workers see the new status.
    for post_id in due_ids:
        enqueue_publish(post_id)
    if due_ids or stuck:
        logger.info("publish_due_queued", extra={"queued": len(due_ids), "stuck": len(stuck)})
    return {"queued": len(due_ids), "stuck": len(stuck)}


# --- Worker side ----------------------------------------------------------------------


def execute_publish(post_id: uuid.UUID) -> dict:
    lock = get_sync_redis().lock(f"lock:publish:{post_id}", timeout=LOCK_SECONDS)
    try:
        if not lock.acquire(blocking=False):
            return {"status": "skipped", "reason": "already running"}
    except RedisError:
        lock = None  # Redis down: the status check below still stops most duplicates.
    try:
        return _execute(post_id)
    finally:
        if lock is not None:
            try:
                lock.release()
            except (LockError, RedisError):
                pass


def _execute(post_id: uuid.UUID) -> dict:
    with sync_session() as db:
        post = db.get(Post, post_id)
        if post is None or post.status != PostStatus.PUBLISHING:
            return {"status": "skipped"}
        if post.external_post_id:
            # Already live (e.g. a redelivered job): never post twice.
            post.status = PostStatus.PUBLISHED
            return {"status": "published", "external_post_id": post.external_post_id}
        platform = post.platform
        label = social_service.platform_label(platform)
        post.publish_attempts += 1
        # Row lock: concurrent publishes for one account take turns, so an X refresh token
        # (single-use) is never spent twice.
        account = db.scalar(
            select(SocialAccount)
            .where(SocialAccount.brand_id == post.brand_id, SocialAccount.platform == platform)
            .with_for_update()
        )
        if account is None or not social_service.is_usable(account):
            return _fail(db, post, account, "RECONNECT_REQUIRED", f"The {label} connection is missing or expired. Reconnect it.")
        try:
            token = _fresh_token(account)
        except crypto.DecryptionError:
            return _fail(db, post, account, "RECONNECT_REQUIRED", f"The saved {label} login can't be read. Reconnect it.")
        except x.XError as exc:
            if _outcome(exc, at_create=False) == "retry":
                return _retry_or_fail(db, post, exc)
            return _fail(db, post, account, exc.code, exc.message)

        if platform == Platform.LINKEDIN:
            job = {
                "author": f"urn:li:person:{account.external_account_id}",
                "text": linkedin.commentary(post.hook, post.body, post.cta, list(post.hashtags)),
                "media": _media(db.get(Visual, post.visual_id) if post.visual_id else None, post.hook),
            }
        elif platform == Platform.FACEBOOK:
            job = {
                "page_id": account.external_account_id,
                "text": render(post.hook, post.body, post.cta, list(post.hashtags), Platform.FACEBOOK),
                "images": _slide_keys(db.get(Visual, post.visual_id) if post.visual_id else None),
            }
        else:
            # X posts are text for now; attaching images needs X's media upload (media.write).
            job = {"text": render(post.hook, post.body, post.cta, list(post.hashtags), Platform.X)}

    try:
        if platform == Platform.LINKEDIN:
            external_id = asyncio.run(_publish_linkedin(token, job["author"], job["text"], job["media"]))
        elif platform == Platform.FACEBOOK:
            external_id = asyncio.run(_publish_facebook(token, job["page_id"], job["text"], job["images"]))
        else:
            external_id = asyncio.run(_publish_x(token, job["text"]))
    except _CreateFailed as wrapped:
        return _handle_error(post_id, wrapped.error, at_create=True)
    except PlatformError as exc:
        return _handle_error(post_id, exc, at_create=False)
    except Exception:
        # Never leave a post stuck in "publishing". Where it failed is unknown, so be careful.
        logger.exception("post_publish_crashed", extra={"post_id": str(post_id)})
        with sync_session() as db:
            _mark_uncertain(db, db.get(Post, post_id), "An unexpected error happened while publishing.")
        raise

    with sync_session() as db:
        post = db.get(Post, post_id)
        post.status = PostStatus.PUBLISHED
        post.published_at = datetime.now(UTC)
        post.external_post_id = external_id
        post.publish_error = None
        url = published_url(platform, external_id)
        notification_service.notify(
            db,
            brand_id=post.brand_id,
            post_id=post.id,
            type="post_published",
            title=f"Published to {label}",
            message=post.hook[:200],
            link=url,
        )
    logger.info("post_published", extra={"post_id": str(post_id), "external_post_id": external_id})
    return {"status": "published", "external_post_id": external_id}


def _handle_error(post_id: uuid.UUID, exc, *, at_create: bool) -> dict:
    with sync_session() as db:
        post = db.get(Post, post_id)
        account = db.scalar(
            select(SocialAccount).where(SocialAccount.brand_id == post.brand_id, SocialAccount.platform == post.platform)
        )
        outcome = _outcome(exc, at_create=at_create)
        if outcome == "uncertain":
            return _mark_uncertain(db, post, exc.message)
        if outcome == "retry":
            return _retry_or_fail(db, post, exc)
        return _fail(db, post, account, exc.code, exc.message)


def _retry_or_fail(db, post: Post, exc) -> dict:
    if post.publish_attempts >= MAX_ATTEMPTS:
        return _fail(db, post, None, exc.code, f"{exc.message} Gave up after {post.publish_attempts} attempts.")
    delay = RETRY_DELAYS[post.publish_attempts - 1]
    post.publish_error = f"{exc.code}: {exc.message} Retrying in {delay // 60} min."[:500]
    post_id = post.id
    db.commit()
    enqueue_retry(post_id, delay)
    logger.info("post_publish_retry", extra={"post_id": str(post_id), "error": exc.code, "delay": delay})
    return {"status": "retrying", "error": exc.code, "delay": delay, "attempt": post.publish_attempts}


def _mark_uncertain(db, post: Post, reason: str) -> dict:
    label = social_service.platform_label(post.platform)
    message = (
        f"{reason} The post may or may not be live on {label}. Check your profile before retrying, "
        "so it isn't posted twice."
    )
    post.status = PostStatus.FAILED
    post.publish_error = f"PUBLISH_UNCERTAIN: {message}"[:500]
    notification_service.notify(
        db, brand_id=post.brand_id, post_id=post.id, type="publish_uncertain", title=f"Check {label}", message=message
    )
    logger.warning("post_publish_uncertain", extra={"post_id": str(post.id)})
    return {"status": "failed", "error": "PUBLISH_UNCERTAIN", "message": message}


def _fail(db, post: Post, account: SocialAccount | None, code: str, message: str) -> dict:
    label = social_service.platform_label(post.platform)
    post.status = PostStatus.FAILED
    post.publish_error = f"{code}: {message}"[:500]
    newly_disconnected = (
        code == "RECONNECT_REQUIRED" and account is not None and account.status == SocialAccountStatus.ACTIVE
    )
    if code == "RECONNECT_REQUIRED" and account is not None:
        account.status = SocialAccountStatus.RECONNECT_REQUIRED
    if newly_disconnected:
        notification_service.notify(
            db,
            brand_id=post.brand_id,
            type="reconnect_required",
            title=f"Reconnect {label}",
            message=f"{label} no longer accepts the saved login. Reconnect it to keep publishing.",
        )
    notification_service.notify(
        db,
        brand_id=post.brand_id,
        post_id=post.id,
        type="publish_failed",
        title=f"Couldn't publish to {label}",
        message=message,
    )
    logger.info("post_publish_failed", extra={"post_id": str(post.id), "error": code})
    return {"status": "failed", "error": code, "message": message}


def _fresh_token(account: SocialAccount) -> str:
    """The access token, renewed first if it is an X token near expiry. Call under the row lock."""
    near_expiry = account.token_expires_at is not None and account.token_expires_at - REFRESH_MARGIN <= datetime.now(UTC)
    if account.platform == Platform.X and near_expiry and account.refresh_token_encrypted:
        grant = asyncio.run(x.XClient().refresh(crypto.decrypt(account.refresh_token_encrypted)))
        social_service.store_tokens(account, grant.access_token, grant.refresh_token, grant.expires_in)
        logger.info("social_token_refreshed", extra={"platform": "x", "brand_id": str(account.brand_id)})
        return grant.access_token
    return crypto.decrypt(account.access_token_encrypted)


def _media(visual: Visual | None, hook: str) -> dict | None:
    """The file to attach: a carousel PDF as a document, otherwise the first image."""
    if visual is None or visual.status != VisualStatus.SUCCEEDED:
        return None
    pdf = next((a for a in visual.assets if a["kind"] == "pdf"), None)
    if pdf:
        return {"kind": "document", "key": pdf["key"], "title": hook[:100]}
    slide = next((a for a in visual.assets if a["kind"] == "slide"), None)
    if slide:
        return {"kind": "image", "key": slide["key"], "altText": (visual.alt_text or "")[:300]}
    return None


async def _publish_linkedin(token: str, author: str, text: str, media: dict | None) -> str:
    client = linkedin.LinkedInClient(token)
    attached = None
    if media:
        # Uploads happen before the post exists, so their failures are safe to retry.
        data = get_storage().read(media["key"])
        if media["kind"] == "document":
            attached = {"id": await client.upload_document(author, data), "title": media["title"]}
        else:
            attached = {"id": await client.upload_image(author, data), "altText": media.get("altText")}
    try:
        return await client.create_post(author, text, attached)
    except linkedin.LinkedInError as exc:
        raise _CreateFailed(exc) from None


def _slide_keys(visual: Visual | None) -> list[str]:
    """Facebook takes images, not PDFs: a carousel becomes a multi-photo post."""
    if visual is None or visual.status != VisualStatus.SUCCEEDED:
        return []
    return [a["key"] for a in visual.assets if a["kind"] == "slide"][: facebook.MAX_PHOTOS]


async def _publish_facebook(token: str, page_id: str, text: str, image_keys: list[str]) -> str:
    client = facebook.FacebookClient(token)
    storage = get_storage()
    # Unpublished photo uploads create no post, so their failures are safe to retry.
    photo_ids = [await client.upload_photo(page_id, storage.read(key)) for key in image_keys]
    try:
        return await client.create_post(page_id, text, photo_ids)
    except facebook.FacebookError as exc:
        raise _CreateFailed(exc) from None


async def _publish_x(token: str, text: str) -> str:
    try:
        return await x.XClient(token).create_post(text)
    except x.XError as exc:
        raise _CreateFailed(exc) from None

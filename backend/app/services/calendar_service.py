"""Content calendar (spec sections 55, 58 and 62).

A post's `scheduled_at` is its calendar slot; there is no second copy to drift out of
sync. Scheduling plans a post; publishing it at that time arrives with the social
publishing adapters (spec prompts 12-13).
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import Post, PostStatus, User, Visual
from app.services import brand_service, content_service

MIN_LEAD_TIME = timedelta(minutes=1)
MAX_AHEAD = timedelta(days=365)
MAX_RANGE = timedelta(days=92)


def _check_time(when: datetime) -> datetime:
    if when.tzinfo is None:
        raise AppError("TIMEZONE_REQUIRED", "Include a timezone, e.g. 2026-10-01T09:00:00+02:00.", 422)
    now = datetime.now(UTC)
    if when < now + MIN_LEAD_TIME:
        raise AppError("SCHEDULE_IN_PAST", "Pick a time in the future.", 422)
    if when > now + MAX_AHEAD:
        raise AppError("SCHEDULE_TOO_FAR", "Posts can be scheduled up to a year ahead.", 422)
    return when.astimezone(UTC)


async def schedule(db: AsyncSession, user: User, post_id: uuid.UUID, when: datetime) -> Post:
    """Puts an approved post on the calendar, or moves a scheduled one."""
    post, _ = await content_service.get_post(db, user, post_id)
    if post.status not in (PostStatus.APPROVED, PostStatus.SCHEDULED):
        raise AppError("POST_NOT_APPROVED", "Only approved posts can be scheduled.", 409)
    post.scheduled_at = _check_time(when)
    post.status = PostStatus.SCHEDULED
    await db.commit()
    await db.refresh(post)
    return post


async def unschedule(db: AsyncSession, user: User, post_id: uuid.UUID) -> Post:
    post, _ = await content_service.get_post(db, user, post_id)
    if post.status != PostStatus.SCHEDULED:
        raise AppError("POST_NOT_SCHEDULED", "This post isn't scheduled.", 409)
    post.status = PostStatus.APPROVED
    post.scheduled_at = None
    await db.commit()
    await db.refresh(post)
    return post


async def calendar(
    db: AsyncSession, user: User, brand_id: uuid.UUID, start: datetime, end: datetime
) -> list[tuple[Post, str | None]]:
    """Scheduled and published posts in [start, end), with their visual's thumbnail."""
    await brand_service.get_brand(db, user, brand_id)
    if start.tzinfo is None or end.tzinfo is None:
        raise AppError("TIMEZONE_REQUIRED", "Include a timezone in start and end.", 422)
    if end <= start or end - start > MAX_RANGE:
        raise AppError("INVALID_RANGE", "The range must be positive and at most 92 days.", 422)

    moment = Post.scheduled_at
    rows = await db.execute(
        select(Post, Visual.thumbnail_url)
        .outerjoin(Visual, Visual.id == Post.visual_id)
        .where(
            Post.brand_id == brand_id,
            or_(
                (Post.scheduled_at >= start) & (Post.scheduled_at < end),
                (Post.published_at >= start) & (Post.published_at < end),
            ),
            Post.status != PostStatus.ARCHIVED,
        )
        .order_by(moment.asc().nulls_last(), Post.published_at.asc(), Post.id)
    )
    return [(post, thumbnail) for post, thumbnail in rows.all()]

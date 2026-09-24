"""In-app notifications (spec section 71)."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import Brand, User
from app.models.notification import Notification


def notify(
    db: Session,
    *,
    brand_id: uuid.UUID,
    type: str,
    title: str,
    message: str,
    post_id: uuid.UUID | None = None,
    link: str | None = None,
) -> None:
    """Adds a notification for the brand's owner, inside the caller's transaction."""
    user_id = db.scalar(select(Brand.user_id).where(Brand.id == brand_id))
    if user_id is None:
        return
    db.add(
        Notification(
            user_id=user_id,
            brand_id=brand_id,
            post_id=post_id,
            type=type,
            title=title[:200],
            message=message[:1000],
            link=link,
        )
    )


async def list_notifications(db: AsyncSession, user: User, *, unread_only: bool, limit: int) -> list[Notification]:
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    result = await db.scalars(stmt.order_by(Notification.created_at.desc(), Notification.id).limit(limit))
    return list(result)


async def unread_count(db: AsyncSession, user: User) -> int:
    return await db.scalar(
        select(func.count()).select_from(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
    )


async def mark_read(db: AsyncSession, user: User, notification_id: uuid.UUID) -> Notification:
    notification = await db.scalar(
        select(Notification).where(Notification.id == notification_id, Notification.user_id == user.id)
    )
    if notification is None:
        raise AppError("NOTIFICATION_NOT_FOUND", "Notification not found.", 404)
    if notification.read_at is None:
        notification.read_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(notification)
    return notification


async def mark_all_read(db: AsyncSession, user: User) -> int:
    result = await db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    await db.commit()
    return result.rowcount

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.schemas.notification import MarkedRead, NotificationRead, UnreadCount
from app.services import notification_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(
    user: CurrentUser,
    db: DbSession,
    unread_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> list[NotificationRead]:
    """Newest first."""
    items = await notification_service.list_notifications(db, user, unread_only=unread_only, limit=limit)
    return [NotificationRead.model_validate(n) for n in items]


@router.get("/unread-count")
async def unread_count(user: CurrentUser, db: DbSession) -> UnreadCount:
    return UnreadCount(unread=await notification_service.unread_count(db, user))


@router.post("/read-all")
async def mark_all_read(user: CurrentUser, db: DbSession) -> MarkedRead:
    return MarkedRead(updated=await notification_service.mark_all_read(db, user))


@router.post("/{notification_id}/read")
async def mark_read(notification_id: uuid.UUID, user: CurrentUser, db: DbSession) -> NotificationRead:
    return NotificationRead.model_validate(await notification_service.mark_read(db, user, notification_id))

import uuid
from datetime import datetime

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.schemas.post import CalendarItem, PostRead, RescheduleRequest, ScheduleRequest
from app.services import calendar_service

router = APIRouter(prefix="/calendar", tags=["calendar"])


def _item(post, thumbnail=None) -> CalendarItem:
    return CalendarItem(**PostRead.model_validate(post).model_dump(exclude={"full_text", "published_url"}), thumbnail_url=thumbnail)


@router.get("")
async def get_calendar(
    brand_id: uuid.UUID, start: datetime, end: datetime, user: CurrentUser, db: DbSession
) -> list[CalendarItem]:
    """Scheduled and published posts between start and end (ISO 8601 with timezone, up to 92 days)."""
    return [_item(p, t) for p, t in await calendar_service.calendar(db, user, brand_id, start, end)]


@router.post("/items", status_code=201)
async def schedule(body: ScheduleRequest, user: CurrentUser, db: DbSession) -> CalendarItem:
    """Schedules an approved post."""
    return _item(await calendar_service.schedule(db, user, body.post_id, body.scheduled_at))


@router.patch("/items/{post_id}")
async def reschedule(post_id: uuid.UUID, body: RescheduleRequest, user: CurrentUser, db: DbSession) -> CalendarItem:
    return _item(await calendar_service.schedule(db, user, post_id, body.scheduled_at))


@router.delete("/items/{post_id}")
async def unschedule(post_id: uuid.UUID, user: CurrentUser, db: DbSession) -> CalendarItem:
    """Takes the post off the calendar; it stays approved."""
    return _item(await calendar_service.unschedule(db, user, post_id))

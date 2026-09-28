import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DbSession, rate_limit
from app.models.engage import InboxStatus
from app.schemas.engage import EngageSettingsRead, EngageSettingsUpdate, InboxItemRead, PollRequest, SendReplyRequest
from app.services import engage_service

router = APIRouter(prefix="/engage", tags=["engage"])


@router.get("/settings")
async def get_settings(brand_id: uuid.UUID, user: CurrentUser, db: DbSession) -> EngageSettingsRead:
    return EngageSettingsRead.model_validate(await engage_service.get_settings(db, user, brand_id))


@router.patch("/settings")
async def update_settings(
    brand_id: uuid.UUID, body: EngageSettingsUpdate, user: CurrentUser, db: DbSession
) -> EngageSettingsRead:
    changes = body.model_dump(exclude_unset=True)
    return EngageSettingsRead.model_validate(await engage_service.update_settings(db, user, brand_id, changes))


@router.get("/inbox")
async def list_inbox(
    brand_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    status_filter: Annotated[InboxStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[InboxItemRead]:
    items = await engage_service.list_inbox(db, user, brand_id, status=status_filter, limit=limit, offset=offset)
    return [InboxItemRead.model_validate(i) for i in items]


@router.post("/inbox/{item_id}/send", dependencies=[rate_limit("engage_send", limit=60, window_seconds=3600)])
async def send(item_id: uuid.UUID, body: SendReplyRequest, user: CurrentUser, db: DbSession) -> InboxItemRead:
    """Approves the draft (or an edited version) and replies on the platform right away."""
    return InboxItemRead.model_validate(await engage_service.send_reply(db, user, item_id, body.reply))


@router.post("/inbox/{item_id}/dismiss")
async def dismiss(item_id: uuid.UUID, user: CurrentUser, db: DbSession) -> InboxItemRead:
    return InboxItemRead.model_validate(await engage_service.dismiss(db, user, item_id))


@router.post(
    "/poll",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[rate_limit("engage_poll", limit=12, window_seconds=3600)],
)
async def poll_now(body: PollRequest, user: CurrentUser, db: DbSession) -> dict:
    """Checks the Page for new comments and messages in the background."""
    await engage_service.poll_now(db, user, body.brand_id)
    return {"queued": True}

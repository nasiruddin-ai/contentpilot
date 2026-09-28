import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DbSession, rate_limit
from app.schemas.autopilot import AutopilotRunRead, AutopilotSettingsRead, AutopilotSettingsUpdate, RunRequest, SlotsPreview
from app.services import autopilot_service

router = APIRouter(prefix="/autopilot", tags=["autopilot"])


@router.get("/settings")
async def get_settings(brand_id: uuid.UUID, user: CurrentUser, db: DbSession) -> AutopilotSettingsRead:
    return AutopilotSettingsRead.model_validate(await autopilot_service.get_settings(db, user, brand_id))


@router.patch("/settings")
async def update_settings(brand_id: uuid.UUID, body: AutopilotSettingsUpdate, user: CurrentUser, db: DbSession) -> AutopilotSettingsRead:
    """Mode off / copilot / autopilot, platforms, posting days and time, caps, and approval rules."""
    changes = body.model_dump(exclude_unset=True)
    if "platforms" in changes:
        changes["platforms"] = [p.value for p in body.platforms]
    if "mode" in changes:
        changes["mode"] = body.mode
    if "approval_rules" in changes:
        # Store the complete rule set, so unspecified checks keep their defaults explicitly.
        changes["approval_rules"] = body.approval_rules.model_dump()
    settings = await autopilot_service.update_settings(db, user, brand_id, changes)
    return AutopilotSettingsRead.model_validate(settings)


@router.get("/slots")
async def slots(brand_id: uuid.UUID, user: CurrentUser, db: DbSession) -> SlotsPreview:
    """The posting slots the next cycle would fill."""
    return SlotsPreview(slots=await autopilot_service.preview_slots(db, user, brand_id))


@router.post("/run", status_code=status.HTTP_202_ACCEPTED, dependencies=[rate_limit("autopilot_run", limit=6, window_seconds=3600)])
async def run_now(body: RunRequest, user: CurrentUser, db: DbSession) -> AutopilotRunRead:
    """Runs one cycle now (research → ideas → posts → checks → schedule or review)."""
    return AutopilotRunRead.model_validate(await autopilot_service.start_run(db, user, body.brand_id))


@router.get("/runs")
async def list_runs(brand_id: uuid.UUID, user: CurrentUser, db: DbSession, limit: Annotated[int, Query(ge=1, le=100)] = 20) -> list[AutopilotRunRead]:
    return [AutopilotRunRead.model_validate(r) for r in await autopilot_service.list_runs(db, user, brand_id, limit)]


@router.get("/runs/{run_id}")
async def get_run(run_id: uuid.UUID, user: CurrentUser, db: DbSession) -> AutopilotRunRead:
    return AutopilotRunRead.model_validate(await autopilot_service.get_run(db, user, run_id))

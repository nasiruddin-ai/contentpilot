import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DbSession, rate_limit
from app.schemas.research import (
    ResearchItemRead,
    ResearchItemSummary,
    ResearchRunList,
    ResearchRunRead,
    ResearchRunRequest,
)
from app.services import research_service

router = APIRouter(prefix="/research", tags=["research"])


@router.post(
    "/run",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[rate_limit("research_run", limit=20, window_seconds=600)],
)
async def run_research(body: ResearchRunRequest, user: CurrentUser, db: DbSession) -> ResearchRunList:
    """Queues a fetch of every active source of the brand."""
    runs = await research_service.start_brand_research(db, user, body.brand_id)
    return ResearchRunList(runs=[ResearchRunRead.model_validate(r) for r in runs])


@router.get("/runs")
async def list_runs(
    brand_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ResearchRunList:
    runs = await research_service.list_runs(db, user, brand_id, limit)
    return ResearchRunList(runs=[ResearchRunRead.model_validate(r) for r in runs])


@router.get("/runs/{run_id}")
async def get_run(run_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ResearchRunRead:
    return ResearchRunRead.model_validate(await research_service.get_run(db, user, run_id))


@router.get("")
async def list_research(
    brand_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    source_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ResearchItemSummary]:
    """Newest first. `q` searches titles and summaries."""
    items = await research_service.list_items(
        db, user, brand_id, source_id=source_id, query=q, limit=limit, offset=offset
    )
    return [ResearchItemSummary.model_validate(i) for i in items]


@router.get("/{item_id}")
async def get_research_item(item_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ResearchItemRead:
    return ResearchItemRead.model_validate(await research_service.get_item(db, user, item_id))

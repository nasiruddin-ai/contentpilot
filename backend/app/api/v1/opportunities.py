import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DbSession, rate_limit
from app.models import OpportunityStatus
from app.schemas.opportunity import (
    OpportunityDetail,
    OpportunityGenerateRequest,
    OpportunityRead,
    OpportunityRunList,
    OpportunityRunRead,
    OpportunityUpdate,
    TopicRead,
)
from app.schemas.research import ResearchItemSummary
from app.services import opportunity_service, topic_service

router = APIRouter(tags=["opportunities"])


def _detail(opportunity, sources) -> OpportunityDetail:
    return OpportunityDetail(
        **OpportunityRead.model_validate(opportunity).model_dump(),
        sources=[ResearchItemSummary.model_validate(s) for s in sources],
    )


@router.post(
    "/opportunities/generate",
    status_code=status.HTTP_202_ACCEPTED,
    # Each run makes several AI calls.
    dependencies=[rate_limit("opportunity_generate", limit=10, window_seconds=3600)],
)
async def generate(body: OpportunityGenerateRequest, user: CurrentUser, db: DbSession) -> OpportunityRunRead:
    """Analyzes new research, updates topics, and queues opportunity generation."""
    run = await opportunity_service.start_generation(db, user, body.brand_id, body.count)
    return OpportunityRunRead.model_validate(run)


@router.get("/opportunities/runs")
async def list_runs(
    brand_id: uuid.UUID, user: CurrentUser, db: DbSession, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> OpportunityRunList:
    runs = await opportunity_service.list_runs(db, user, brand_id, limit)
    return OpportunityRunList(runs=[OpportunityRunRead.model_validate(r) for r in runs])


@router.get("/opportunities/runs/{run_id}")
async def get_run(run_id: uuid.UUID, user: CurrentUser, db: DbSession) -> OpportunityRunRead:
    return OpportunityRunRead.model_validate(await opportunity_service.get_run(db, user, run_id))


@router.get("/opportunities")
async def list_opportunities(
    brand_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    status_filter: Annotated[OpportunityStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[OpportunityRead]:
    """Highest priority first. Dismissed ideas are hidden unless `status=dismissed`."""
    items = await opportunity_service.list_opportunities(
        db, user, brand_id, status=status_filter, limit=limit, offset=offset
    )
    return [OpportunityRead.model_validate(o) for o in items]


@router.get("/opportunities/{opportunity_id}")
async def get_opportunity(opportunity_id: uuid.UUID, user: CurrentUser, db: DbSession) -> OpportunityDetail:
    return _detail(*await opportunity_service.get_opportunity(db, user, opportunity_id))


@router.patch("/opportunities/{opportunity_id}")
async def update_opportunity(
    opportunity_id: uuid.UUID, body: OpportunityUpdate, user: CurrentUser, db: DbSession
) -> OpportunityDetail:
    return _detail(*await opportunity_service.set_status(db, user, opportunity_id, body.status))


@router.get("/topics", tags=["research"])
async def list_topics(
    brand_id: uuid.UUID, user: CurrentUser, db: DbSession, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[TopicRead]:
    """The brand's research topics with trend signals, strongest first."""
    return [TopicRead.model_validate(t) for t in await topic_service.list_topics(db, user, brand_id, limit)]

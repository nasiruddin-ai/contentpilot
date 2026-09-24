import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import CurrentUser, DbSession, rate_limit
from app.research.fetcher import SafeFetcher
from app.schemas.research import ResearchRunRead
from app.schemas.source import SourceCreate, SourceRead, SourceTestResult, SourceUpdate
from app.services import research_service, source_service

router = APIRouter(prefix="/sources", tags=["sources"])


def get_fetcher() -> SafeFetcher:
    return SafeFetcher()


@router.get("")
async def list_sources(brand_id: uuid.UUID, user: CurrentUser, db: DbSession) -> list[SourceRead]:
    sources = await source_service.list_sources(db, user, brand_id)
    return [SourceRead.model_validate(s) for s in sources]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_source(body: SourceCreate, user: CurrentUser, db: DbSession) -> SourceRead:
    return SourceRead.model_validate(await source_service.create_source(db, user, body))


@router.get("/{source_id}")
async def get_source(source_id: uuid.UUID, user: CurrentUser, db: DbSession) -> SourceRead:
    return SourceRead.model_validate(await source_service.get_source(db, user, source_id))


@router.patch("/{source_id}")
async def update_source(source_id: uuid.UUID, body: SourceUpdate, user: CurrentUser, db: DbSession) -> SourceRead:
    return SourceRead.model_validate(await source_service.update_source(db, user, source_id, body))


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(source_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    await source_service.delete_source(db, user, source_id)


# Each test makes outbound requests, so it is rate limited to stop the API being used as a proxy.
@router.post("/{source_id}/test", dependencies=[rate_limit("source_test", limit=30, window_seconds=600)])
async def test_source(
    source_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    fetcher: Annotated[SafeFetcher, Depends(get_fetcher)],
) -> SourceTestResult:
    return await source_service.test_source(db, user, source_id, fetcher)


@router.post(
    "/{source_id}/fetch",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[rate_limit("source_fetch", limit=30, window_seconds=600)],
)
async def fetch_source(source_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ResearchRunRead:
    """Queues a research run for this source. Returns the in-flight run if one exists."""
    return ResearchRunRead.model_validate(await research_service.start_source_fetch(db, user, source_id))

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DbSession, rate_limit
from app.schemas.analytics import GroupRow, Overview, PostAnalytics, SyncRead, SyncRequest, TopicsReport
from app.services import analytics_service

router = APIRouter(prefix="/analytics", tags=["analytics"])

Days = Annotated[int, Query(ge=1, le=365)]


@router.post("/sync", status_code=status.HTTP_202_ACCEPTED, dependencies=[rate_limit("analytics_sync", limit=12, window_seconds=3600)])
async def sync_now(body: SyncRequest, user: CurrentUser, db: DbSession) -> SyncRead:
    """Pulls fresh numbers for the brand's published posts. Also runs automatically every 6 hours."""
    return SyncRead.model_validate(await analytics_service.start_sync(db, user, body.brand_id))


@router.get("/syncs/{sync_id}")
async def get_sync(sync_id: uuid.UUID, user: CurrentUser, db: DbSession) -> SyncRead:
    return SyncRead.model_validate(await analytics_service.get_sync(db, user, sync_id))


@router.get("/overview")
async def overview(brand_id: uuid.UUID, user: CurrentUser, db: DbSession, days: Days = 30) -> Overview:
    return Overview(**await analytics_service.overview(db, user, brand_id, days))


@router.get("/posts")
async def posts(
    brand_id: uuid.UUID, user: CurrentUser, db: DbSession, days: Days = 30, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[PostAnalytics]:
    """Published posts with their latest numbers, best first."""
    return [PostAnalytics(**p) for p in await analytics_service.posts_report(db, user, brand_id, days, limit)]


@router.get("/platforms")
async def platforms(brand_id: uuid.UUID, user: CurrentUser, db: DbSession, days: Days = 30) -> list[GroupRow]:
    return [GroupRow(**r) for r in await analytics_service.platforms_report(db, user, brand_id, days)]


@router.get("/topics")
async def topics(brand_id: uuid.UUID, user: CurrentUser, db: DbSession, days: Days = 90) -> TopicsReport:
    """What performs by topic, content pillar and format, plus the learning summary used for new ideas."""
    return TopicsReport(**await analytics_service.topics_report(db, user, brand_id, days))

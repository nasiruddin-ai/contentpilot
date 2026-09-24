import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession, rate_limit
from app.schemas.post import PostRead
from app.services import publishing_service

router = APIRouter(prefix="/publishing", tags=["publishing"])


@router.post(
    "/{post_id}/publish",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[rate_limit("publish", limit=30, window_seconds=3600)],
)
async def publish_now(post_id: uuid.UUID, user: CurrentUser, db: DbSession) -> PostRead:
    """Publishes an approved post to its platform now. Poll GET /posts/{id} for the result."""
    return PostRead.model_validate(await publishing_service.start_publish(db, user, post_id))

import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentUser, DbSession, rate_limit
from app.schemas.visual import VisualGenerateRequest, VisualRead
from app.services import visual_service

router = APIRouter(prefix="/visuals", tags=["visuals"])


@router.post(
    "/generate",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[rate_limit("visual_generate", limit=30, window_seconds=3600)],
)
async def generate(body: VisualGenerateRequest, user: CurrentUser, db: DbSession) -> VisualRead:
    """Creates a brand-styled graphic for a post in the background."""
    visual = await visual_service.start_generation(db, user, body.post_id, body.visual_type, body.aspect_ratio)
    return VisualRead.model_validate(visual)


@router.get("/{visual_id}")
async def get_visual(visual_id: uuid.UUID, user: CurrentUser, db: DbSession) -> VisualRead:
    return VisualRead.model_validate(await visual_service.get_visual(db, user, visual_id))


@router.post(
    "/{visual_id}/regenerate",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[rate_limit("visual_generate", limit=30, window_seconds=3600)],
)
async def regenerate(visual_id: uuid.UUID, user: CurrentUser, db: DbSession) -> VisualRead:
    return VisualRead.model_validate(await visual_service.regenerate(db, user, visual_id))


@router.get("/{visual_id}/download")
async def download(visual_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Response:
    """ZIP of the images (and the carousel PDF, ready to upload to LinkedIn as a document)."""
    data = await visual_service.download(db, user, visual_id)
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="visual-{visual_id}.zip"'},
    )

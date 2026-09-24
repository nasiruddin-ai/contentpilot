import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DbSession, rate_limit
from app.models import Platform
from app.models.post import PostStatus
from app.schemas.post import (
    GenerationRead,
    PostDetail,
    PostGenerateRequest,
    PostRead,
    PostUpdate,
    RejectRequest,
    RevisionRead,
    RevisionRequest,
    VersionRead,
)
from app.schemas.research import ResearchItemSummary
from app.services import content_service, editor_service

router = APIRouter(prefix="/posts", tags=["content"])


@router.post(
    "/generate",
    status_code=status.HTTP_202_ACCEPTED,
    # Each generation makes four AI calls.
    dependencies=[rate_limit("post_generate", limit=20, window_seconds=3600)],
)
async def generate(body: PostGenerateRequest, user: CurrentUser, db: DbSession) -> GenerationRead:
    """Writes platform-specific posts for an opportunity in the background."""
    generation = await content_service.start_generation(db, user, body.opportunity_id, body.platforms)
    return GenerationRead.model_validate(generation)


@router.get("/generations/{generation_id}")
async def get_generation(generation_id: uuid.UUID, user: CurrentUser, db: DbSession) -> GenerationRead:
    generation, post_ids = await content_service.get_generation(db, user, generation_id)
    return GenerationRead.model_validate(generation).model_copy(update={"post_ids": post_ids})


@router.get("")
async def list_posts(
    brand_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    status_filter: Annotated[PostStatus | None, Query(alias="status")] = None,
    platform: Platform | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PostRead]:
    """Newest first. Archived posts are hidden unless `status=archived`."""
    posts = await content_service.list_posts(
        db, user, brand_id, status=status_filter, platform=platform, limit=limit, offset=offset
    )
    return [PostRead.model_validate(p) for p in posts]


async def _detail(db, user, post_id: uuid.UUID) -> PostDetail:
    post, sources = await content_service.get_post(db, user, post_id)
    return PostDetail(
        **PostRead.model_validate(post).model_dump(exclude={"full_text"}),
        sources=[ResearchItemSummary.model_validate(s) for s in sources],
    )


@router.get("/revisions/{revision_id}")
async def get_revision(revision_id: uuid.UUID, user: CurrentUser, db: DbSession) -> RevisionRead:
    return RevisionRead.model_validate(await editor_service.get_revision(db, user, revision_id))


@router.get("/{post_id}")
async def get_post(post_id: uuid.UUID, user: CurrentUser, db: DbSession) -> PostDetail:
    return await _detail(db, user, post_id)


@router.patch("/{post_id}")
async def edit_post(post_id: uuid.UUID, body: PostUpdate, user: CurrentUser, db: DbSession) -> PostDetail:
    """Edits text. Quality checks re-run; an approved post goes back to draft only if the edit adds an error."""
    await editor_service.edit_post(db, user, post_id, body.model_dump(exclude_unset=True))
    return await _detail(db, user, post_id)


@router.post("/{post_id}/approve")
async def approve(post_id: uuid.UUID, user: CurrentUser, db: DbSession) -> PostDetail:
    await editor_service.approve_post(db, user, post_id)
    return await _detail(db, user, post_id)


@router.post("/{post_id}/reject")
async def reject(
    post_id: uuid.UUID, user: CurrentUser, db: DbSession, body: RejectRequest | None = None
) -> PostDetail:
    await editor_service.reject_post(db, user, post_id, body.reason if body else None)
    return await _detail(db, user, post_id)


@router.post(
    "/{post_id}/regenerate",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[rate_limit("post_revise", limit=60, window_seconds=3600)],
)
async def regenerate(post_id: uuid.UUID, body: RevisionRequest, user: CurrentUser, db: DbSession) -> RevisionRead:
    """AI revision: rewrite, shorten, expand, change_tone, new_hook, new_cta or custom. The result is a draft."""
    revision = await editor_service.start_revision(db, user, post_id, body.action, body.instruction)
    return RevisionRead.model_validate(revision)


@router.get("/{post_id}/versions")
async def list_versions(post_id: uuid.UUID, user: CurrentUser, db: DbSession) -> list[VersionRead]:
    """Earlier content of the post, newest first."""
    return [VersionRead.model_validate(v) for v in await editor_service.list_versions(db, user, post_id)]


@router.post("/{post_id}/versions/{version_id}/restore")
async def restore_version(
    post_id: uuid.UUID, version_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> PostDetail:
    await editor_service.restore_version(db, user, post_id, version_id)
    return await _detail(db, user, post_id)


@router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_post(post_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    await content_service.delete_post(db, user, post_id)

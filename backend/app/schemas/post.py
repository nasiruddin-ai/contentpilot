import uuid
from datetime import datetime

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, computed_field, model_validator

from app.models import ContentFormat, Platform
from app.models.post import GenerationStatus, PostStatus, RevisionAction
from app.schemas.research import ResearchItemSummary
from app.services.content_quality import render


class PostGenerateRequest(BaseModel):
    opportunity_id: uuid.UUID
    # Defaults to the opportunity's recommended platforms.
    platforms: list[Platform] | None = Field(default=None, min_length=1, max_length=len(Platform))


class GenerationRead(BaseModel):
    """`id` is the job ID to poll with GET /posts/generations/{id}."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    brand_id: uuid.UUID
    opportunity_id: uuid.UUID | None
    platforms: list[str]
    status: GenerationStatus
    hook_options: list[str]
    outline: list
    master_draft: str | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    post_ids: list[uuid.UUID] = []


class QualityIssue(BaseModel):
    severity: str
    type: str
    detail: str


class PostRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    brand_id: uuid.UUID
    opportunity_id: uuid.UUID | None
    generation_id: uuid.UUID | None
    platform: Platform
    content_type: ContentFormat
    hook: str
    body: str
    cta: str | None
    hashtags: list[str]
    quality_issues: list[QualityIssue]
    visual_id: uuid.UUID | None
    status: PostStatus
    scheduled_at: datetime | None
    published_at: datetime | None
    approved_at: datetime | None
    review_note: str | None
    external_post_id: str | None
    publish_error: str | None
    publish_attempts: int
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def published_url(self) -> str | None:
        """Link to the live post, once published."""
        if not self.external_post_id:
            return None
        from app.services.publishing_service import published_url

        return published_url(self.platform, self.external_post_id)

    @computed_field
    @property
    def full_text(self) -> str:
        """The post as it would be published (title-style platforms keep the hook separate)."""
        return render(self.hook, self.body, self.cta, self.hashtags, self.platform)


class PostDetail(PostRead):
    sources: list[ResearchItemSummary]


Hook = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Body = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40000)]
Cta = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]
Tag = Annotated[str, StringConstraints(max_length=100)]


class PostUpdate(BaseModel):
    """PATCH: only fields present are changed. Send cta as null to remove the call to action."""

    hook: Hook | None = None
    body: Body | None = None
    cta: Cta | None = None
    hashtags: list[Tag] | None = Field(default=None, max_length=30)

    @model_validator(mode="after")
    def _check(self) -> "PostUpdate":
        if not self.model_fields_set:
            raise ValueError("Send at least one field to change.")
        cleared = sorted(f for f in {"hook", "body", "hashtags"} & self.model_fields_set if getattr(self, f) is None)
        if cleared:
            raise ValueError(f"These fields can't be null: {', '.join(cleared)}")
        return self


class RejectRequest(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


class RevisionRequest(BaseModel):
    action: RevisionAction
    # Required for change_tone (the target tone) and custom (what to change).
    instruction: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


class RevisionRead(BaseModel):
    """Poll GET /posts/revisions/{id}; on success the post holds the revised text, as a draft."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    post_id: uuid.UUID
    action: RevisionAction
    instruction: str | None
    status: GenerationStatus
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class VersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hook: str
    body: str
    cta: str | None
    hashtags: list[str]
    reason: str
    created_at: datetime


class ScheduleRequest(BaseModel):
    post_id: uuid.UUID
    scheduled_at: datetime


class RescheduleRequest(BaseModel):
    scheduled_at: datetime


class CalendarItem(PostRead):
    thumbnail_url: str | None = None

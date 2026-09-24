import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from app.models.opportunity import ContentFormat


class PostStatus(StrEnum):
    IDEA = "idea"
    DRAFT = "draft"
    REVIEW = "review"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    ARCHIVED = "archived"


class GenerationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ContentGeneration(UUIDPrimaryKeyMixin, Base):
    """One run of the writing pipeline for one opportunity (spec section 37).
    Keeps the intermediate stages so a post can be traced back to its plan."""

    __tablename__ = "content_generations"

    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("content_opportunities.id", ondelete="SET NULL"), index=True
    )
    platforms: Mapped[list[str]] = mapped_column(ARRAY(String(20)))
    status: Mapped[GenerationStatus] = mapped_column(
        str_enum(GenerationStatus, "generation_status"), default=GenerationStatus.QUEUED, server_default="queued"
    )
    hook_options: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    outline: Mapped[list] = mapped_column(JSONB, default=list)
    master_draft: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Post(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One platform-specific piece of content (spec section 19)."""

    __tablename__ = "posts"

    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("content_opportunities.id", ondelete="SET NULL"), index=True
    )
    generation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("content_generations.id", ondelete="SET NULL"), index=True
    )
    platform: Mapped[str] = mapped_column(String(20))
    content_type: Mapped[ContentFormat] = mapped_column(str_enum(ContentFormat, "post_content_type"))
    hook: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    cta: Mapped[str | None] = mapped_column(Text)
    # Stored without the leading "#".
    hashtags: Mapped[list[str]] = mapped_column(ARRAY(String(100)), default=list)
    # Research items the post is grounded in.
    source_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    # Problems the quality check found and couldn't fix: [{"severity", "type", "detail"}].
    quality_issues: Mapped[list] = mapped_column(JSONB, default=list)
    # The post's current visual. Posts and visuals point at each other, so this key is added after both tables exist.
    visual_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("visuals.id", ondelete="SET NULL", use_alter=True, name="fk_posts_visual_id_visuals")
    )
    status: Mapped[PostStatus] = mapped_column(
        str_enum(PostStatus, "post_status"), default=PostStatus.DRAFT, server_default="draft", index=True
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    external_post_id: Mapped[str | None] = mapped_column(String(200))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Why it was rejected, when it was.
    review_note: Mapped[str | None] = mapped_column(String(500))
    # Why the last publish attempt failed.
    publish_error: Mapped[str | None] = mapped_column(String(500))
    # Attempts in the current publish run (reset when a new run starts).
    publish_attempts: Mapped[int] = mapped_column(default=0, server_default=text("0"))


# Posts that can still be edited, revised, approved or rejected.
EDITABLE_STATUSES = (PostStatus.DRAFT, PostStatus.REVIEW, PostStatus.APPROVED, PostStatus.SCHEDULED)


class PostVersion(UUIDPrimaryKeyMixin, Base):
    """The content of a post before a change, so any edit or AI revision can be undone."""

    __tablename__ = "post_versions"

    post_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    hook: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    cta: Mapped[str | None] = mapped_column(Text)
    hashtags: Mapped[list[str]] = mapped_column(ARRAY(String(100)), default=list)
    # What replaced it: "edit", "revision:<action>" or "restore".
    reason: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RevisionAction(StrEnum):
    REWRITE = "rewrite"
    SHORTEN = "shorten"
    EXPAND = "expand"
    CHANGE_TONE = "change_tone"
    NEW_HOOK = "new_hook"
    NEW_CTA = "new_cta"
    CUSTOM = "custom"


class PostRevision(UUIDPrimaryKeyMixin, Base):
    """An AI revision job for one post (editor actions in spec section 63)."""

    __tablename__ = "post_revisions"

    post_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    action: Mapped[RevisionAction] = mapped_column(str_enum(RevisionAction, "revision_action"))
    # Target tone for change_tone, or the user's own instruction for custom.
    instruction: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[GenerationStatus] = mapped_column(
        str_enum(GenerationStatus, "revision_status"), default=GenerationStatus.QUEUED, server_default="queued"
    )
    # Fingerprint of the post's content when the job was queued. If the user edits the post
    # meanwhile, the AI result is discarded rather than overwriting their edit.
    base_hash: Mapped[str] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

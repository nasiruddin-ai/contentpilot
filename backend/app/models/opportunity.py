import uuid
from datetime import datetime
from enum import StrEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import EMBEDDING_DIMENSIONS, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from app.models.content_pillar import ContentPillar


class ContentFormat(StrEnum):
    TEXT_POST = "text_post"
    CAROUSEL = "carousel"
    THREAD = "thread"
    IMAGE_POST = "image_post"
    SHORT_VIDEO = "short_video"
    ARTICLE = "article"


class Platform(StrEnum):
    LINKEDIN = "linkedin"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    X = "x"
    REDDIT = "reddit"
    YOUTUBE = "youtube"


class OpportunityStatus(StrEnum):
    NEW = "new"
    SAVED = "saved"
    DISMISSED = "dismissed"
    USED = "used"


SCORE_COLUMNS = ("relevance_score", "freshness_score", "brand_fit_score", "novelty_score", "priority_score")


class ContentOpportunity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A suggested piece of content, grounded in specific research items (spec section 18)."""

    __tablename__ = "content_opportunities"
    __table_args__ = tuple(
        CheckConstraint(f"{column} BETWEEN 0 AND 100", name=f"{column}_range") for column in SCORE_COLUMNS
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("opportunity_runs.id", ondelete="SET NULL"))
    topic_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("topics.id", ondelete="SET NULL"), index=True)
    # Snapshot of the topic name, kept if the topic is later merged or removed.
    topic: Mapped[str] = mapped_column(String(120))
    angle: Mapped[str] = mapped_column(String(300))
    why_now: Mapped[str] = mapped_column(Text)
    audience: Mapped[str] = mapped_column(String(300))
    recommended_format: Mapped[ContentFormat] = mapped_column(str_enum(ContentFormat, "content_format"))
    recommended_platforms: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list)
    content_pillar: Mapped[ContentPillar | None] = mapped_column(str_enum(ContentPillar, "opportunity_pillar"))
    # The research items this idea is based on. Only IDs the model was actually shown are kept.
    source_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)

    # Internal prioritization signals, 0-100 (spec section 18). Relevance and brand fit are
    # the model's judgement; freshness and novelty are computed, not generated.
    relevance_score: Mapped[int]
    freshness_score: Mapped[int]
    brand_fit_score: Mapped[int]
    novelty_score: Mapped[int]
    priority_score: Mapped[int] = mapped_column(index=True)

    status: Mapped[OpportunityStatus] = mapped_column(
        str_enum(OpportunityStatus, "opportunity_status"), default=OpportunityStatus.NEW, server_default="new"
    )
    # For semantic duplicate detection against later ideas.
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))


class OpportunityRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class OpportunityRun(UUIDPrimaryKeyMixin, Base):
    """One analyze-and-generate job, so it can be tracked like research runs."""

    __tablename__ = "opportunity_runs"

    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    status: Mapped[OpportunityRunStatus] = mapped_column(
        str_enum(OpportunityRunStatus, "opportunity_run_status"),
        default=OpportunityRunStatus.QUEUED,
        server_default="queued",
    )
    requested_count: Mapped[int]
    items_analyzed: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    topics_created: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    opportunities_created: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    # Suggestions dropped by validation: duplicates, banned words, or no valid sources.
    opportunities_rejected: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

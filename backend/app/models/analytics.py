import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import UUIDPrimaryKeyMixin, str_enum


class PostMetric(UUIDPrimaryKeyMixin, Base):
    """A snapshot of a published post's numbers (spec section 60). One row per sync, so
    growth over time is kept; queries use the latest row per post.

    Only metrics the platform actually provides are filled; the rest stay NULL.
    """

    __tablename__ = "post_metrics"
    __table_args__ = (Index("ix_post_metrics_post_id_synced_at", "post_id", "synced_at"),)

    post_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"))
    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(20))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    impressions: Mapped[int | None]
    reach: Mapped[int | None]
    likes: Mapped[int | None]
    comments: Mapped[int | None]
    shares: Mapped[int | None]
    clicks: Mapped[int | None]
    # Weighted engagement (see analytics_service.engagement_score), always computed.
    engagement_score: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    # engagement / impressions, only where impressions exist.
    engagement_rate: Mapped[float | None]
    # Permissions the platform said were missing, so the UI can explain gaps.
    missing_permissions: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    raw: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))


class SyncStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AnalyticsSync(UUIDPrimaryKeyMixin, Base):
    """One metrics sync for one brand, so background syncs are observable."""

    __tablename__ = "analytics_syncs"

    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    status: Mapped[SyncStatus] = mapped_column(
        str_enum(SyncStatus, "analytics_sync_status"), default=SyncStatus.QUEUED, server_default="queued"
    )
    posts_synced: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    posts_failed: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    # Platforms that were skipped and why, e.g. {"linkedin": "not available for self-serve apps"}.
    skipped: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

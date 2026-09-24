import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin, str_enum


class ResearchContentType(StrEnum):
    ARTICLE = "article"  # full text fetched from the article page
    FEED_SUMMARY = "feed_summary"  # only the feed's summary was available
    PAGE = "page"  # snapshot of a monitored page


class ResearchItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One piece of fetched source material. Untrusted: plain text only, never instructions."""

    __tablename__ = "research_items"
    __table_args__ = (
        UniqueConstraint("brand_id", "canonical_url"),
        Index("ix_research_items_brand_id_content_hash", "brand_id", "content_hash"),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    # Research outlives its source: deleting a source keeps what was already collected.
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    canonical_url: Mapped[str] = mapped_column(String(2048))
    author: Mapped[str | None] = mapped_column(String(200))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    summary: Mapped[str] = mapped_column(Text)
    clean_text: Mapped[str] = mapped_column(Text)
    content_type: Mapped[ResearchContentType] = mapped_column(str_enum(ResearchContentType, "research_content_type"))
    # Exact-duplicate detection.
    content_hash: Mapped[str] = mapped_column(String(64))
    # Near-duplicate detection: 64-bit SimHash stored as a signed BIGINT.
    simhash: Mapped[int] = mapped_column(BigInteger)
    source_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))


class RunTrigger(StrEnum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


ACTIVE_RUN_STATUSES = (RunStatus.QUEUED, RunStatus.RUNNING)


class ResearchRun(UUIDPrimaryKeyMixin, Base):
    """One fetch of one source. Makes every background research job observable."""

    __tablename__ = "research_runs"

    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    trigger: Mapped[RunTrigger] = mapped_column(str_enum(RunTrigger, "run_trigger"))
    status: Mapped[RunStatus] = mapped_column(
        str_enum(RunStatus, "run_status"), default=RunStatus.QUEUED, server_default="queued"
    )
    items_found: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    items_new: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    items_updated: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    items_duplicate: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

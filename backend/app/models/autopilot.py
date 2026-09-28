import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, String, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin, str_enum


class AutopilotMode(StrEnum):
    OFF = "off"
    # Everything Autopilot creates waits for a person (spec section 5.1).
    COPILOT = "copilot"
    # Brand-safe, evergreen posts are approved and scheduled automatically;
    # anything the rules flag waits for review (spec section 5.2).
    AUTOPILOT = "autopilot"


DEFAULT_RULES = {
    # Pillars that may be approved without a person, when nothing else flags the post.
    "auto_approve_pillars": ["educational", "how_to", "faq"],
    # Pillars that always wait for review.
    "always_review_pillars": ["promotion", "opinion", "industry_insight", "comparison", "case_study"],
    # AI risk checks that send a post to review when true.
    "review_if": {
        "news_or_current_events": True,
        "sensitive_topic": True,
        "product_claims": True,
        "high_risk_factual_claims": True,
        # Deterministic quality warnings (e.g. a number not found in the sources).
        "quality_warnings": True,
    },
}


class AutopilotSettings(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "autopilot_settings"

    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), unique=True)
    mode: Mapped[AutopilotMode] = mapped_column(str_enum(AutopilotMode, "autopilot_mode"), default=AutopilotMode.OFF, server_default="off")
    platforms: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list)
    # Posting slots: e.g. days [0, 2, 4] (Mon, Wed, Fri) at "09:00" in the brand's timezone.
    days_of_week: Mapped[list[int]] = mapped_column(ARRAY(String(2)), default=list)
    post_time: Mapped[str] = mapped_column(String(5), default="09:00", server_default="09:00")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", server_default="UTC")
    # Fill slots this far ahead.
    horizon_days: Mapped[int] = mapped_column(default=7, server_default=text("7"))
    # Never create more posts than this in one cycle, whatever the calendar says.
    max_posts_per_run: Mapped[int] = mapped_column(default=3, server_default=text("3"))
    auto_visual: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    approval_rules: Mapped[dict] = mapped_column(JSONB, default=lambda: dict(DEFAULT_RULES))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AutopilotRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AutopilotRun(UUIDPrimaryKeyMixin, Base):
    """One Autopilot cycle for one brand, with what it did and why."""

    __tablename__ = "autopilot_runs"

    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    trigger: Mapped[str] = mapped_column(String(20))  # manual | scheduled
    status: Mapped[AutopilotRunStatus] = mapped_column(
        str_enum(AutopilotRunStatus, "autopilot_run_status"), default=AutopilotRunStatus.QUEUED, server_default="queued"
    )
    slots_open: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    research_queued: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    opportunities_created: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    posts_created: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    posts_scheduled: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    posts_for_review: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    posts_rejected: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    # Per post: {"post_id", "platform", "decision", "reasons": [...]}
    decisions: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

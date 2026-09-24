import uuid
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, text, true
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin, str_enum


class SourceType(StrEnum):
    WEBSITE = "website"
    BLOG = "blog"
    RSS = "rss"
    YOUTUBE = "youtube"
    REDDIT = "reddit"
    NEWS = "news"
    SEARCH = "search"
    USER_URL = "user_url"
    API = "api"


# The rest need official API integrations, which come in later milestones.
FETCHABLE_SOURCE_TYPES = {SourceType.WEBSITE, SourceType.BLOG, SourceType.RSS, SourceType.NEWS, SourceType.USER_URL}


class FetchFrequency(StrEnum):
    HOURLY = "hourly"
    EVERY_6_HOURS = "every_6_hours"
    DAILY = "daily"
    WEEKLY = "weekly"


FETCH_INTERVALS = {
    FetchFrequency.HOURLY: timedelta(hours=1),
    FetchFrequency.EVERY_6_HOURS: timedelta(hours=6),
    FetchFrequency.DAILY: timedelta(days=1),
    FetchFrequency.WEEKLY: timedelta(weeks=1),
}


class SourceStatus(StrEnum):
    PENDING = "pending"  # never checked
    OK = "ok"
    ERROR = "error"


class Source(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("brand_id", "url"),)

    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    # Normalized by app.utils.urls.parse_public_url, so duplicates are caught.
    url: Mapped[str] = mapped_column(String(2048))
    source_type: Mapped[SourceType] = mapped_column(str_enum(SourceType, "source_type"))
    active: Mapped[bool] = mapped_column(default=True, server_default=true())
    fetch_frequency: Mapped[FetchFrequency] = mapped_column(
        str_enum(FetchFrequency, "fetch_frequency"), default=FetchFrequency.DAILY, server_default="daily"
    )
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the scheduler should next fetch it. NULL means as soon as possible.
    next_fetch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[SourceStatus] = mapped_column(
        str_enum(SourceStatus, "source_status"), default=SourceStatus.PENDING, server_default="pending"
    )
    error_count: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(String(500))

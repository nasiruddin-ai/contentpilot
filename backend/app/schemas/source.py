import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from app.models import FetchFrequency, SourceStatus, SourceType

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
RawUrl = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]


class SourceCreate(BaseModel):
    brand_id: uuid.UUID
    name: Name
    url: RawUrl
    source_type: SourceType
    fetch_frequency: FetchFrequency = FetchFrequency.DAILY
    active: bool = True


class SourceUpdate(BaseModel):
    """PATCH: only fields present in the request are changed."""

    name: Name | None = None
    url: RawUrl | None = None
    source_type: SourceType | None = None
    fetch_frequency: FetchFrequency | None = None
    active: bool | None = None

    @model_validator(mode="after")
    def _no_nulls(self) -> "SourceUpdate":
        cleared = sorted(f for f in self.model_fields_set if getattr(self, f) is None)
        if cleared:
            raise ValueError(f"These fields can't be null: {', '.join(cleared)}")
        return self


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    brand_id: uuid.UUID
    name: str
    url: str
    source_type: SourceType
    active: bool
    fetch_frequency: FetchFrequency
    status: SourceStatus
    error_count: int
    last_error: str | None
    last_fetched_at: datetime | None
    next_fetch_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SourceTestError(BaseModel):
    code: str
    message: str


class FeedItemPreview(BaseModel):
    title: str
    url: str | None
    published: datetime | None


class SourceTestResult(BaseModel):
    """Preview of what the source returns right now. Nothing is stored."""

    ok: bool
    error: SourceTestError | None = None
    final_url: str | None = None
    content_type: str | None = None
    kind: Literal["feed", "page"] | None = None
    title: str | None = None
    items: list[FeedItemPreview] = []
    excerpt: str | None = None
    # Feeds the page advertises. Following a feed is preferred over scraping the page.
    feed_urls: list[str] = []

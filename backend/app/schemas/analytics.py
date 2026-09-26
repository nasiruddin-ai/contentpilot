import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SyncRequest(BaseModel):
    brand_id: uuid.UUID


class SyncRead(BaseModel):
    """Poll GET /analytics/syncs/{id}."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    brand_id: uuid.UUID
    status: str
    posts_synced: int
    posts_failed: int
    skipped: dict
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class PostAnalytics(BaseModel):
    post_id: uuid.UUID
    platform: str
    content_type: str
    hook: str
    published_at: datetime
    published_url: str | None
    topic: str | None
    content_pillar: str | None
    likes: int | None
    comments: int | None
    shares: int | None
    clicks: int | None
    impressions: int | None
    engagement_score: int
    engagement_rate: float | None
    synced_at: datetime
    missing_permissions: list[str]


class Overview(BaseModel):
    days: int
    posts_published: int
    posts_with_metrics: int
    likes: int | None
    comments: int | None
    shares: int | None
    clicks: int | None
    impressions: int | None
    engagement_score: int
    avg_engagement_score: float
    best_post: PostAnalytics | None
    last_synced_at: datetime | None
    missing_permissions: list[str]
    notes: list[str]


class GroupRow(BaseModel):
    """Totals for one platform, topic, pillar or format. `vs_brand_average` is
    above_average, average, below_average or not_enough_data."""

    model_config = ConfigDict(extra="allow")

    posts: int
    likes: int | None
    comments: int | None
    shares: int | None
    clicks: int | None
    impressions: int | None
    engagement_score: int
    avg_engagement_score: float
    vs_brand_average: str


class TopicsReport(BaseModel):
    topics: list[GroupRow]
    content_pillars: list[GroupRow]
    formats: list[GroupRow]
    learning_active: bool
    learning_summary: str | None

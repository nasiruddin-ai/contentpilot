import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import ContentFormat, ContentPillar, OpportunityRunStatus, OpportunityStatus
from app.schemas.research import ResearchItemSummary


class OpportunityGenerateRequest(BaseModel):
    brand_id: uuid.UUID
    count: int = Field(default=5, ge=1, le=10)


class OpportunityRunRead(BaseModel):
    """`id` is the job ID to poll with GET /opportunities/runs/{id}."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    brand_id: uuid.UUID
    status: OpportunityRunStatus
    requested_count: int
    items_analyzed: int
    topics_created: int
    opportunities_created: int
    opportunities_rejected: int
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class OpportunityRunList(BaseModel):
    runs: list[OpportunityRunRead]


class OpportunityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    brand_id: uuid.UUID
    topic_id: uuid.UUID | None
    topic: str
    angle: str
    why_now: str
    audience: str
    recommended_format: ContentFormat
    recommended_platforms: list[str]
    content_pillar: ContentPillar | None
    source_ids: list[uuid.UUID]
    relevance_score: int
    freshness_score: int
    brand_fit_score: int
    novelty_score: int
    priority_score: int
    status: OpportunityStatus
    created_at: datetime
    updated_at: datetime


class OpportunityDetail(OpportunityRead):
    sources: list[ResearchItemSummary]


class OpportunityUpdate(BaseModel):
    status: OpportunityStatus


class TopicRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    trend: str
    item_count: int
    source_count: int
    items_7d: int
    items_prev_7d: int
    first_seen_at: datetime
    latest_at: datetime

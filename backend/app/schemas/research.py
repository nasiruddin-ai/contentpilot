import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import ResearchContentType, RunStatus, RunTrigger


class ResearchRunRequest(BaseModel):
    brand_id: uuid.UUID


class ResearchRunRead(BaseModel):
    """`id` is the job ID to poll with GET /research/runs/{id}."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    brand_id: uuid.UUID
    source_id: uuid.UUID
    trigger: RunTrigger
    status: RunStatus
    items_found: int
    items_new: int
    items_updated: int
    items_duplicate: int
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ResearchRunList(BaseModel):
    runs: list[ResearchRunRead]


class ResearchItemSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    brand_id: uuid.UUID
    source_id: uuid.UUID | None
    title: str
    canonical_url: str
    author: str | None
    published_at: datetime | None
    fetched_at: datetime
    summary: str
    content_type: ResearchContentType
    topics: list[str]
    analyzed_at: datetime | None


class ResearchItemRead(ResearchItemSummary):
    clean_text: str
    keywords: list[str]
    entities: list[str]
    source_metadata: dict

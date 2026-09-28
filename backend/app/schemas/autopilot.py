import re
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import AutopilotMode, ContentPillar, Platform

PILLARS = {p.value for p in ContentPillar}


class ReviewIf(BaseModel):
    news_or_current_events: bool = True
    sensitive_topic: bool = True
    product_claims: bool = True
    high_risk_factual_claims: bool = True
    quality_warnings: bool = True


class ApprovalRules(BaseModel):
    auto_approve_pillars: list[str] = ["educational", "how_to", "faq"]
    always_review_pillars: list[str] = ["promotion", "opinion", "industry_insight", "comparison", "case_study"]
    review_if: ReviewIf = ReviewIf()

    @field_validator("auto_approve_pillars", "always_review_pillars")
    @classmethod
    def _known_pillars(cls, values: list[str]) -> list[str]:
        unknown = [v for v in values if v not in PILLARS]
        if unknown:
            raise ValueError(f"Unknown content pillars: {', '.join(unknown)}")
        return list(dict.fromkeys(values))


class AutopilotSettingsUpdate(BaseModel):
    """PATCH: only fields present are changed."""

    mode: AutopilotMode | None = None
    platforms: list[Platform] | None = None
    # 0 = Monday ... 6 = Sunday
    days_of_week: list[Annotated[int, Field(ge=0, le=6)]] | None = None
    post_time: str | None = None
    timezone: Annotated[str, Field(max_length=64)] | None = None
    horizon_days: Annotated[int, Field(ge=1, le=30)] | None = None
    max_posts_per_run: Annotated[int, Field(ge=1, le=10)] | None = None
    auto_visual: bool | None = None
    approval_rules: ApprovalRules | None = None

    @field_validator("post_time")
    @classmethod
    def _hhmm(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("post_time must be HH:MM, e.g. 09:00")
        return value


class AutopilotSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    brand_id: uuid.UUID
    mode: AutopilotMode
    platforms: list[str]
    days_of_week: list[int]
    post_time: str
    timezone: str
    horizon_days: int
    max_posts_per_run: int
    auto_visual: bool
    approval_rules: dict
    last_run_at: datetime | None
    updated_at: datetime

    @field_validator("days_of_week", mode="before")
    @classmethod
    def _ints(cls, values) -> list[int]:
        return [int(v) for v in values or []]


class AutopilotRunRead(BaseModel):
    """Poll GET /autopilot/runs/{id}. `decisions` explains what happened to each post."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    brand_id: uuid.UUID
    trigger: str
    status: str
    slots_open: int
    research_queued: int
    opportunities_created: int
    posts_created: int
    posts_scheduled: int
    posts_for_review: int
    posts_rejected: int
    decisions: list
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RunRequest(BaseModel):
    brand_id: uuid.UUID


class SlotsPreview(BaseModel):
    slots: list[datetime]

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.engage import EngageMode, InboxKind, InboxStatus


class EngageSettingsUpdate(BaseModel):
    """PATCH: only fields present are changed."""

    mode: EngageMode | None = None
    reply_to_comments: bool | None = None
    reply_to_messages: bool | None = None
    business_facts: Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)] | None = None
    max_replies_per_day: Annotated[int, Field(ge=1, le=200)] | None = None


class EngageSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    brand_id: uuid.UUID
    mode: EngageMode
    reply_to_comments: bool
    reply_to_messages: bool
    business_facts: str
    max_replies_per_day: int
    activated_at: datetime | None
    last_polled_at: datetime | None
    last_error: str | None
    updated_at: datetime


class InboxItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    brand_id: uuid.UUID
    platform: str
    kind: InboxKind
    external_id: str
    thread_external_id: str | None
    post_id: uuid.UUID | None
    author_name: str
    text: str
    received_at: datetime
    status: InboxStatus
    draft_reply: str
    assessment: dict
    sent_reply: str | None
    sent_automatically: bool
    replied_at: datetime | None
    error: str | None
    created_at: datetime


class SendReplyRequest(BaseModel):
    # Omit to send the draft as it stands.
    reply: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)] | None = None


class PollRequest(BaseModel):
    brand_id: uuid.UUID

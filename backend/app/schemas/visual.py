import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.visual import VisualStatus, VisualType


class VisualGenerateRequest(BaseModel):
    post_id: uuid.UUID
    visual_type: VisualType
    # 1:1, 4:5, 16:9, 9:16 or 1.91:1. Defaults to what suits the post's platform.
    aspect_ratio: str | None = None


class VisualAsset(BaseModel):
    kind: str  # slide | pdf | thumbnail
    index: int
    url: str
    width: int | None = None
    height: int | None = None


class VisualRead(BaseModel):
    """`id` is the job ID too: poll GET /visuals/{id} until `status` is succeeded or failed."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    brand_id: uuid.UUID
    post_id: uuid.UUID | None
    visual_type: VisualType
    aspect_ratio: str
    status: VisualStatus
    provider: str
    concept: dict
    alt_text: str | None
    assets: list[VisualAsset]
    asset_url: str | None
    thumbnail_url: str | None
    issues: list[dict]
    error: str | None
    created_at: datetime
    updated_at: datetime

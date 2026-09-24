import uuid
from enum import StrEnum

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin, str_enum


class VisualType(StrEnum):
    # Rendered from the brand kit with Pillow.
    QUOTE_CARD = "quote_card"
    MINIMAL_GRAPHIC = "minimal_graphic"
    INFOGRAPHIC = "infographic"
    CAROUSEL = "carousel"
    # Need an AI image model (not on the Gemini free tier).
    ILLUSTRATION = "illustration"
    CARTOON = "cartoon"
    PHOTO = "photo"
    MEME = "meme"
    PRODUCT_SHOWCASE = "product_showcase"


RENDERED_TYPES = {VisualType.QUOTE_CARD, VisualType.MINIMAL_GRAPHIC, VisualType.INFOGRAPHIC, VisualType.CAROUSEL}


class VisualStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Visual(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A graphic for a post (spec section 41). Carousels have one asset per slide plus a PDF."""

    __tablename__ = "visuals"

    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    post_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("posts.id", ondelete="SET NULL"), index=True)
    visual_type: Mapped[VisualType] = mapped_column(str_enum(VisualType, "visual_type"))
    aspect_ratio: Mapped[str] = mapped_column(String(10))
    status: Mapped[VisualStatus] = mapped_column(
        str_enum(VisualStatus, "visual_status"), default=VisualStatus.QUEUED, server_default="queued"
    )
    # "pillow" for rendered designs; an AI provider name for generated images.
    provider: Mapped[str] = mapped_column(String(40), default="pillow")
    # The on-image copy: {"slides": [...]} as rendered.
    concept: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Prompt sent to an image model, for AI image types.
    prompt: Mapped[str | None] = mapped_column(Text)
    alt_text: Mapped[str | None] = mapped_column(String(300))
    # [{"kind": "slide" | "pdf", "index", "key", "url", "width", "height"}]
    assets: Mapped[list] = mapped_column(JSONB, default=list)
    asset_url: Mapped[str | None] = mapped_column(String(2048))
    thumbnail_url: Mapped[str | None] = mapped_column(String(2048))
    # Brand-check findings: [{"severity", "type", "detail"}].
    issues: Mapped[list] = mapped_column(JSONB, default=list)
    error: Mapped[str | None] = mapped_column(String(500))

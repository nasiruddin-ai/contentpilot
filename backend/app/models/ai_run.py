import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import UUIDPrimaryKeyMixin, str_enum


class AIRunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    # The model answered, but the answer didn't pass schema validation.
    INVALID_OUTPUT = "invalid_output"


class AIRun(UUIDPrimaryKeyMixin, Base):
    """One call to an AI provider, for cost control and debugging (spec section 69)."""

    __tablename__ = "ai_runs"

    # Kept when a user or brand is deleted: cost history must survive.
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    brand_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("brands.id", ondelete="SET NULL"), index=True)
    task_type: Mapped[str] = mapped_column(String(60), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    # USD, estimated from list prices; NULL when the model's price is unknown.
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    duration_ms: Mapped[int]
    attempts: Mapped[int] = mapped_column(default=1)
    status: Mapped[AIRunStatus] = mapped_column(str_enum(AIRunStatus, "ai_run_status"))
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

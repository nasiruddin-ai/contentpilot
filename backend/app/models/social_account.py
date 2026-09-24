import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin, str_enum


class SocialAccountStatus(StrEnum):
    ACTIVE = "active"
    # The token expired or was revoked; the user must reconnect.
    RECONNECT_REQUIRED = "reconnect_required"


class SocialAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A connected social profile (spec section 20). Tokens are encrypted and never
    leave the backend."""

    __tablename__ = "social_accounts"
    __table_args__ = (UniqueConstraint("brand_id", "platform"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(20))
    # The platform's ID for the account, e.g. the LinkedIn member ID.
    external_account_id: Mapped[str] = mapped_column(String(200))
    account_name: Mapped[str] = mapped_column(String(200))
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[SocialAccountStatus] = mapped_column(
        str_enum(SocialAccountStatus, "social_account_status"),
        default=SocialAccountStatus.ACTIVE,
        server_default="active",
    )

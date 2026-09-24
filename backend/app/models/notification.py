import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import UUIDPrimaryKeyMixin


class Notification(UUIDPrimaryKeyMixin, Base):
    """An in-app notification (spec section 71). Email delivery comes with an email provider."""

    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    brand_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"))
    post_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("posts.id", ondelete="SET NULL"))
    # e.g. post_published, publish_failed, publish_uncertain, reconnect_required
    type: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(String(1000))
    link: Mapped[str | None] = mapped_column(String(2048))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

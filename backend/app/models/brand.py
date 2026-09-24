import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.content_pillar import BrandContentPillar
    from app.models.user import User

COLOR_COLUMNS = ("primary_color", "secondary_color", "accent_color")


def _string_list() -> Mapped[list[str]]:
    return mapped_column(ARRAY(Text), default=list, server_default=text("'{}'"))


class Brand(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "brands"
    __table_args__ = tuple(
        CheckConstraint(f"{column} ~ '^#[0-9A-Fa-f]{{6}}$'", name=f"{column}_hex")
        for column in COLOR_COLUMNS
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    name: Mapped[str] = mapped_column(String(120))
    website: Mapped[str | None] = mapped_column(String(2048))
    industry: Mapped[str | None] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    audience: Mapped[str | None] = mapped_column(Text)
    market: Mapped[str | None] = mapped_column(String(120))
    goals: Mapped[list[str]] = _string_list()

    # Lists, matching the brand kit shape in spec section 64.
    tone: Mapped[list[str]] = _string_list()
    visual_style: Mapped[list[str]] = _string_list()

    logo_url: Mapped[str | None] = mapped_column(String(2048))
    primary_color: Mapped[str | None] = mapped_column(String(7))
    secondary_color: Mapped[str | None] = mapped_column(String(7))
    accent_color: Mapped[str | None] = mapped_column(String(7))
    heading_font: Mapped[str | None] = mapped_column(String(80))
    body_font: Mapped[str | None] = mapped_column(String(80))

    preferred_words: Mapped[list[str]] = _string_list()
    banned_words: Mapped[list[str]] = _string_list()

    user: Mapped["User"] = relationship(back_populates="brands")
    # selectin: loaded eagerly, since lazy loading isn't available on async sessions.
    content_pillars: Mapped[list["BrandContentPillar"]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        order_by="(BrandContentPillar.weight.desc(), BrandContentPillar.pillar)",
    )

    def __repr__(self) -> str:
        return f"<Brand {self.id} {self.name!r}>"

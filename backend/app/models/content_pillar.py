import uuid
from enum import StrEnum

from sqlalchemy import CheckConstraint, Enum, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import UUIDPrimaryKeyMixin


class ContentPillar(StrEnum):
    EDUCATIONAL = "educational"
    OPINION = "opinion"
    STORY = "story"
    HOW_TO = "how_to"
    CASE_STUDY = "case_study"
    COMPARISON = "comparison"
    INDUSTRY_INSIGHT = "industry_insight"
    FAQ = "faq"
    BEHIND_THE_SCENES = "behind_the_scenes"
    PROMOTION = "promotion"
    COMMUNITY = "community"


# Spec section 36 example distribution, applied to new brands that don't choose one.
DEFAULT_PILLAR_WEIGHTS: dict[ContentPillar, int] = {
    ContentPillar.EDUCATIONAL: 40,
    ContentPillar.OPINION: 20,
    ContentPillar.CASE_STUDY: 15,
    ContentPillar.STORY: 15,
    ContentPillar.PROMOTION: 10,
}


class BrandContentPillar(UUIDPrimaryKeyMixin, Base):
    """A brand's share of content for one pillar, as a percentage of its posts."""

    __tablename__ = "brand_content_pillars"
    __table_args__ = (
        UniqueConstraint("brand_id", "pillar"),
        CheckConstraint("weight BETWEEN 0 AND 100", name="weight_percent"),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    pillar: Mapped[ContentPillar] = mapped_column(
        Enum(
            ContentPillar,
            name="content_pillar",
            native_enum=False,
            create_constraint=True,
            length=40,
            values_callable=lambda members: [m.value for m in members],
        )
    )
    weight: Mapped[int]

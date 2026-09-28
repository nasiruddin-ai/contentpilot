import uuid
from datetime import datetime
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    UrlConstraints,
    model_validator,
)

from app.core.language import ContentLanguage
from app.models.content_pillar import ContentPillar


def _dedupe(values: list[str]) -> list[str]:
    """Collapse whitespace, drop blanks, and remove case-insensitive duplicates, keeping order."""
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        value = " ".join(value.split())
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            cleaned.append(value)
    return cleaned


def _string_list(max_items: int):
    item = Annotated[str, StringConstraints(max_length=60)]
    return Annotated[list[item], Field(max_length=max_items), AfterValidator(_dedupe)]


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
FontName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$"), AfterValidator(str.upper)]
WebUrl = Annotated[HttpUrl, UrlConstraints(max_length=2048), AfterValidator(str)]
Tags = _string_list(20)
WordList = _string_list(200)


class PillarWeight(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    pillar: ContentPillar
    weight: int = Field(ge=0, le=100, description="Percent of posts for this pillar.")


def _valid_distribution(items: list[PillarWeight]) -> list[PillarWeight]:
    pillars = [item.pillar for item in items]
    if len(set(pillars)) != len(pillars):
        raise ValueError("Each content pillar can appear only once.")
    if items and sum(item.weight for item in items) != 100:
        raise ValueError("Content pillar weights must add up to 100.")
    return items


PillarDistribution = Annotated[
    list[PillarWeight], Field(max_length=len(ContentPillar)), AfterValidator(_valid_distribution)
]


class BrandFields(BaseModel):
    website: WebUrl | None = None
    industry: ShortText | None = None
    description: LongText | None = None
    audience: LongText | None = None
    market: ShortText | None = None
    goals: Tags = []
    tone: Tags = []
    visual_style: Tags = []
    logo_url: WebUrl | None = None
    primary_color: HexColor | None = None
    secondary_color: HexColor | None = None
    accent_color: HexColor | None = None
    heading_font: FontName | None = None
    body_font: FontName | None = None
    preferred_words: WordList = []
    banned_words: WordList = []
    language: ContentLanguage = ContentLanguage.ENGLISH


class BrandCreate(BrandFields):
    name: Name
    # Omit to get the default mix; send [] for no preference.
    content_pillars: PillarDistribution | None = None


# Fields that can't be cleared with null on update.
NON_NULLABLE = {
    "name",
    "goals",
    "tone",
    "visual_style",
    "preferred_words",
    "banned_words",
    "content_pillars",
    "language",
}


class BrandUpdate(BrandFields):
    """PATCH: only fields present in the request are changed. `content_pillars` replaces the whole mix."""

    name: Name | None = None
    goals: Tags | None = None
    tone: Tags | None = None
    visual_style: Tags | None = None
    preferred_words: WordList | None = None
    banned_words: WordList | None = None
    language: ContentLanguage | None = None
    content_pillars: PillarDistribution | None = None

    @model_validator(mode="after")
    def _reject_null_for_required(self) -> "BrandUpdate":
        cleared = sorted(f for f in NON_NULLABLE & self.model_fields_set if getattr(self, f) is None)
        if cleared:
            raise ValueError(f"These fields can't be null: {', '.join(cleared)}")
        return self


class BrandRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    website: str | None
    industry: str | None
    description: str | None
    audience: str | None
    market: str | None
    goals: list[str]
    tone: list[str]
    visual_style: list[str]
    logo_url: str | None
    primary_color: str | None
    secondary_color: str | None
    accent_color: str | None
    heading_font: str | None
    body_font: str | None
    preferred_words: list[str]
    banned_words: list[str]
    language: str
    content_pillars: list[PillarWeight]
    created_at: datetime
    updated_at: datetime

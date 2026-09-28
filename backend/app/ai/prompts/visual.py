"""Visual agent (spec sections 31 and 42): turns a post into on-image copy for a
rendered design. Layout is decided by code, not the model."""

from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field

from app.core.language import language_rule


def _clip(limit: int):
    return BeforeValidator(lambda v: " ".join(str(v or "").split())[:limit])


def _clip_list(max_items: int, limit: int):
    return BeforeValidator(lambda values: [" ".join(str(v).split())[:limit] for v in (values or []) if str(v).strip()][:max_items])


class SlideSpec(BaseModel):
    headline: Annotated[str, _clip(120)]
    subtext: Annotated[str, _clip(220)] = ""
    points: Annotated[list[str], _clip_list(6, 90)] = []


class VisualConcept(BaseModel):
    slides: list[SlideSpec] = Field(min_length=1)
    alt_text: Annotated[str, _clip(300)]


SYSTEM = """You design on-image copy for social graphics.
Use only ideas already in the post. Do not add facts, numbers or claims.
Keep text short: people read graphics in seconds. Never use the brand's banned words."""

INSTRUCTIONS = {
    "quote_card": "1 slide. headline: the single most quotable line from the post, at most 20 words. No subtext.",
    "minimal_graphic": "1 slide. headline: the core message in at most 10 words. subtext: one supporting sentence.",
    "infographic": "1 slide. headline: a short title. points: 3-5 takeaways from the post, each at most 10 words.",
    "carousel": (
        "4-8 slides. Slide 1: the hook as headline (at most 10 words) with a short subtext. Middle slides: one idea "
        "each, headline at most 10 words plus a one or two sentence subtext. Last slide: the call to action."
    ),
}


def concept_prompt(visual_type: str, brand_name: str, banned_words: list[str], post_text: str, language: str = "en") -> str:
    banned = ", ".join(banned_words) or "none"
    rule = language_rule(language)
    rule = "\n" + rule if rule else ""
    return f"""BRAND: {brand_name}
BANNED WORDS: {banned}

POST
{post_text}

TASK
Write the on-image copy for a {visual_type.replace("_", " ")}.
{INSTRUCTIONS[visual_type]}
Also return alt_text: a plain description of the graphic for screen readers.{rule}"""

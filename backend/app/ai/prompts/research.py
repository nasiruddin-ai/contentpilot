"""Research analysis: summary, topics, keywords and entities per item (spec sections 17 and 31)."""

from typing import Annotated

from pydantic import BaseModel, BeforeValidator

from app.ai.prompts import UNTRUSTED_DATA_RULE, source_block


def _clip_text(limit: int):
    return BeforeValidator(lambda v: str(v).strip()[:limit] if v is not None else "")


def _clip_list(max_items: int, max_chars: int):
    def clip(values):
        cleaned = [" ".join(str(v).split())[:max_chars] for v in values or []]
        return list(dict.fromkeys(v for v in cleaned if v))[:max_items]

    return BeforeValidator(clip)


# Lenient on length (trimmed, not rejected) so a slightly long answer doesn't waste a call.
class ItemAnalysis(BaseModel):
    ref: str
    summary: Annotated[str, _clip_text(400)]
    topics: Annotated[list[str], _clip_list(3, 60)]
    keywords: Annotated[list[str], _clip_list(8, 60)]
    entities: Annotated[list[str], _clip_list(8, 80)]


class BatchAnalysis(BaseModel):
    items: list[ItemAnalysis]


SYSTEM = f"""You analyze articles for a content strategist.
{UNTRUSTED_DATA_RULE}
Work only from the text given. Do not add facts, numbers or claims that are not in it."""


def analysis_prompt(items: list[tuple[str, str, str]], existing_topics: list[str] | None = None) -> str:
    """`items` is (ref, title, text). `existing_topics` are the brand's current topic labels."""
    sources = "\n\n".join(source_block(ref, title, text) for ref, title, text in items)
    reuse = ""
    if existing_topics:
        # Reusing labels keeps topics from fragmenting into one-article topics.
        reuse = (
            "\nExisting topics for this brand. When an article fits one, reuse its exact label; "
            f"only create a new label when none fits: {'; '.join(existing_topics)}\n"
        )
    return f"""For each source below, return:
- ref: the source id
- summary: 1-2 neutral sentences on what it says
- topics: 1-3 topic labels, 2-4 words each, lowercase except proper nouns, general enough to recur across
  different articles (e.g. "website conversion", "css container queries"), not one-off headlines or
  narrow sub-points
- keywords: up to 8 important terms
- entities: up to 8 named organizations, products or people
{reuse}
{sources}"""

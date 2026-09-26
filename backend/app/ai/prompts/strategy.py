"""Opportunity generation: turns trending research topics into grounded content ideas (spec section 35)."""

from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, BeforeValidator

from app.ai.prompts import UNTRUSTED_DATA_RULE, neutralize, source_block
from app.models import ContentFormat, ContentPillar, Platform


def _score(value) -> int:
    try:
        return max(0, min(100, round(float(value))))
    except (TypeError, ValueError):
        return 0


def _known(enum_cls):
    def keep(values):
        allowed = {m.value for m in enum_cls}
        return list(dict.fromkeys(v for v in (values or []) if v in allowed))

    return BeforeValidator(keep)


class OpportunityDraft(BaseModel):
    topic_ref: str
    angle: str
    why_now: str
    audience: str
    recommended_format: ContentFormat
    recommended_platforms: Annotated[list[Platform], _known(Platform)]
    content_pillar: Annotated[
        ContentPillar | None, BeforeValidator(lambda v: v if v in {p.value for p in ContentPillar} else None)
    ] = None
    source_refs: list[str]
    relevance_score: Annotated[int, BeforeValidator(_score)]
    brand_fit_score: Annotated[int, BeforeValidator(_score)]


class OpportunityBatch(BaseModel):
    opportunities: list[OpportunityDraft]


@dataclass
class PromptSource:
    ref: str
    title: str
    summary: str
    published: str | None


@dataclass
class PromptTopic:
    ref: str
    name: str
    trend: str
    items_7d: int
    source_count: int
    sources: list[PromptSource]


SYSTEM = f"""You are a content strategist for the brand described below.
{UNTRUSTED_DATA_RULE}
Suggest original content angles. Never copy a source's headline or wording.
"why_now" must be grounded in the listed sources; do not invent statistics, dates, quotes or events.
Never state or imply that the brand has done specific projects, has particular clients, results, awards,
prices or services unless they appear in the BRAND section. For case_study and story ideas, describe the
kind of story the brand should tell (e.g. "Share a before-and-after from a recent redesign"), never a
made-up one.
Never use the brand's banned words."""


def opportunity_prompt(brand_context: str, topics: list[PromptTopic], count: int, performance: str = "") -> str:
    topic_blocks = []
    for topic in topics:
        sources = "\n".join(
            source_block(s.ref, s.title, s.summary, s.published) for s in topic.sources
        )
        topic_blocks.append(
            f'<topic id="{topic.ref}" name="{neutralize(topic.name)}" trend="{topic.trend}" '
            f'articles_last_7_days="{topic.items_7d}" distinct_sources="{topic.source_count}">\n{sources}\n</topic>'
        )
    formats = ", ".join(f.value for f in ContentFormat)
    platforms = ", ".join(p.value for p in Platform)
    pillars = ", ".join(p.value for p in ContentPillar)
    learned = f"\n\n{performance}" if performance else ""
    return f"""BRAND
{brand_context}{learned}

TASK
Suggest {count} distinct content opportunities for this brand, drawn from the trending topics below.
Prefer topics that fit the brand's audience and goals, and spread ideas across its content pillar mix.

For each opportunity return:
- topic_ref: the id of the topic it comes from
- angle: the brand's own take, one sentence
- why_now: why this matters to the audience now, based only on the cited sources
- audience: who exactly it is for
- recommended_format: one of {formats}
- recommended_platforms: one or more of {platforms}
- content_pillar: one of {pillars}
- source_refs: ids of the sources that support it (at least one, only ids listed below)
- relevance_score: 0-100, how relevant to the brand's audience and goals
- brand_fit_score: 0-100, how well it suits the brand's tone and positioning

TRENDING TOPICS
{chr(10).join(topic_blocks)}"""

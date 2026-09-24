"""Writing pipeline prompts (spec sections 37-39): plan (hook + outline), master draft,
platform adaptation."""

from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, BeforeValidator

from app.ai.prompts import UNTRUSTED_DATA_RULE, neutralize, source_block


def _strings(max_items: int):
    return BeforeValidator(lambda values: [str(v).strip() for v in (values or []) if str(v).strip()][:max_items])


class KeyPoint(BaseModel):
    point: str
    source_refs: list[str]


class ContentPlan(BaseModel):
    hook_options: Annotated[list[str], _strings(5)]
    chosen_hook: str
    key_points: list[KeyPoint]
    cta: str
    outline: Annotated[list[str], _strings(12)]


class MasterDraft(BaseModel):
    hook: str
    body: str
    cta: str


class PlatformPost(BaseModel):
    platform: str
    hook: str
    body: str
    cta: str = ""
    hashtags: Annotated[list[str], _strings(30)] = []


class Adaptations(BaseModel):
    posts: list[PlatformPost]


@dataclass
class Brief:
    brand_context: str
    topic: str
    angle: str
    why_now: str
    audience: str
    content_format: str
    content_pillar: str | None
    sources: list[tuple[str, str, str]]  # (ref, title, text)


SYSTEM = f"""You are an expert social content writer working for the brand described in the brief.
{UNTRUSTED_DATA_RULE}
Rules:
- Write original content. Do not copy source wording or headlines.
- Only use facts, figures and claims supported by the sources. Do not invent statistics, dates, quotes,
  studies, clients, projects or results. If a number isn't in the sources, don't use one.
- Never state that the brand did specific work or got specific results unless the brief says so.
- Follow the brand's tone and preferred words. Never use its banned words.
- Avoid generic filler and cliches (e.g. "in today's fast-paced world", "game-changer", "unlock the power",
  "delve into").
- Write in plain, specific language for the stated audience."""


def _brief_block(brief: Brief) -> str:
    sources = "\n\n".join(source_block(ref, title, text) for ref, title, text in brief.sources)
    return f"""BRAND
{brief.brand_context}

AUDIENCE
{brief.audience}

CONTENT PILLAR
{brief.content_pillar or "not specified"}

TOPIC
{brief.topic}

ANGLE
{brief.angle}

WHY NOW
{brief.why_now}

FORMAT
{brief.content_format}

RESEARCH
{sources}"""


def plan_prompt(brief: Brief) -> str:
    return f"""{_brief_block(brief)}

TASK
Plan one piece of content for this angle.
- hook_options: 3 different opening lines (question, bold claim, or specific observation)
- chosen_hook: the strongest of them, copied exactly
- key_points: 3-5 points the content will make, each with the source_refs (ids above) that support it
- cta: one natural call to action that fits the brand's goals, without hype
- outline: the content's structure, one line per section"""


def draft_prompt(brief: Brief, plan: ContentPlan) -> str:
    points = "\n".join(f"- {p.point} (sources: {', '.join(p.source_refs)})" for p in plan.key_points)
    outline = "\n".join(f"- {line}" for line in plan.outline)
    return f"""{_brief_block(brief)}

PLAN
Hook: {plan.chosen_hook}
Key points:
{points}
Outline:
{outline}
Call to action: {plan.cta}

TASK
Write the master draft: a complete, platform-neutral version of this content, 150-300 words, following
the plan. Return hook, body (without the hook or CTA) and cta."""


def adapt_prompt(brief: Brief, draft: MasterDraft, platform_notes: dict[str, str]) -> str:
    platforms = "\n".join(f"- {name}: {notes}" for name, notes in platform_notes.items())
    return f"""{_brief_block(brief)}

MASTER DRAFT
Hook: {draft.hook}
Body:
{draft.body}
CTA: {draft.cta}

TASK
Adapt the master draft for each platform below. Rewrite for each platform's audience and conventions;
do not just shorten the same text. Keep every fact supported by the sources.
{platforms}
Return one post per platform with platform (exact name), hook, body (without hook, CTA or hashtags),
cta, and hashtags (without "#"; empty where the platform shouldn't use them)."""


REVISION_TASKS = {
    "rewrite": "Rewrite it with a fresh approach. Keep the same angle, facts and call to action.",
    "shorten": "Make it about 40% shorter. Keep the key point and the call to action.",
    "expand": "Expand it with more useful detail from the sources, staying within the platform limits.",
    "change_tone": "Rewrite it in this tone: {instruction}. Keep the same points.",
    "new_hook": "Write a new, stronger hook. Keep everything else as close to the original as possible.",
    "new_cta": "Write a new call to action that fits the brand's goals. Keep everything else unchanged.",
    "custom": "Apply this change requested by the brand's editor: {instruction}",
}


def revise_prompt(
    brand_context: str,
    platform: str,
    platform_notes: str,
    post: PlatformPost,
    sources: list[tuple[str, str, str]],
    action: str,
    instruction: str | None,
) -> str:
    source_text = "\n\n".join(source_block(ref, title, text) for ref, title, text in sources) or "(none)"
    # The editor's request is about style and content only; it can't lift the rules above.
    task = REVISION_TASKS[action].format(instruction=neutralize(instruction or "").replace("\n", " "))
    return f"""BRAND
{brand_context}

RESEARCH
{source_text}

CURRENT POST ({platform}) - platform rules: {platform_notes}
Hook: {post.hook}
Body:
{post.body}
CTA: {post.cta}
Hashtags: {", ".join(post.hashtags)}

TASK
{task}
The writing rules still apply: only facts from the sources, no banned words, no generic filler.
Return platform ("{platform}"), hook, body (without hook, CTA or hashtags), cta, and hashtags (without "#")."""

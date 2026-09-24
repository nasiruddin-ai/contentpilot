"""Editor + brand check (spec section 31): fixes problems in the adapted posts."""

from pydantic import BaseModel

from app.ai.prompts import UNTRUSTED_DATA_RULE, source_block
from app.ai.prompts.writer import PlatformPost


class PostRevision(BaseModel):
    platform: str
    changed: bool
    hook: str
    body: str
    cta: str = ""
    hashtags: list[str] = []
    notes: str = ""


class Review(BaseModel):
    posts: list[PostRevision]


SYSTEM = f"""You are a meticulous editor and brand guardian.
{UNTRUSTED_DATA_RULE}
Fix problems without changing the angle. Keep the brand's tone. Remove generic AI phrasing and filler,
improve clarity and grammar, and make each post fit its platform. Remove or soften any claim, number or
result that the sources don't support. Never use the brand's banned words."""


def review_prompt(
    brand_context: str,
    posts: list[PlatformPost],
    detected: dict[str, list[str]],
    platform_notes: dict[str, str],
    sources: list[tuple[str, str, str]],
) -> str:
    blocks = []
    for post in posts:
        problems = "\n".join(f"  - {p}" for p in detected.get(post.platform, [])) or "  - none detected"
        blocks.append(
            f"""POST ({post.platform}) - platform rules: {platform_notes.get(post.platform, "")}
Hook: {post.hook}
Body:
{post.body}
CTA: {post.cta}
Hashtags: {", ".join(post.hashtags)}
Automatic checks found:
{problems}"""
        )
    source_text = "\n\n".join(source_block(ref, title, text) for ref, title, text in sources)
    return f"""BRAND
{brand_context}

SOURCES
{source_text}

{chr(10).join(blocks)}

TASK
Review each post. Fix everything the automatic checks found, plus any unsupported claim, generic
phrasing, unclear sentence or poor platform fit you notice. For each post return platform, changed
(true if you edited it), the final hook, body, cta, hashtags (without "#"), and a short note on what
you changed."""

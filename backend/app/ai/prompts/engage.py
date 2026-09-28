"""Reply agent: drafts a Page's answer to a comment or Messenger message.

The incoming text is written by a stranger and is untrusted. The reply may only use
the brand kit, the post it reacts to, and the owner's business facts; anything the
facts don't cover is flagged for a person instead of guessed at.
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.ai.prompts import UNTRUSTED_DATA_RULE, neutralize
from app.core.language import language_rule


class ReplyDraft(BaseModel):
    category: Literal["question", "praise", "complaint", "purchase_intent", "spam", "other"]
    should_reply: bool = Field(description="False for spam, abuse, or when a reply adds nothing.")
    # Held for a person even in auto mode.
    needs_human: bool = Field(
        description="True for complaints, purchase or booking requests, anything personal or legal, "
        "or questions the business facts do not answer."
    )
    reply: str = Field(default="", description="The reply, empty when should_reply is false.")
    reasons: list[str] = Field(default_factory=list, description="Short reasons for the flags chosen.")


SYSTEM = f"""You write a brand's public replies to comments and private replies to messages on social media.
{UNTRUSTED_DATA_RULE}
Rules for the reply:
- Warm, human and brief: one to three short sentences, no hashtags, no emoji unless the brand tone asks for them.
- Reply in the language the person wrote in; otherwise use the brand's post language.
- State only facts found in BUSINESS FACTS or the post itself. Never invent prices, stock,
  delivery promises, medical or legal advice. If the answer isn't in the facts, set needs_human true
  and draft a friendly holding reply that promises a follow-up without answering the question.
- Never argue, never discuss politics or religion, never mention being an AI.
- For clear spam or abuse set should_reply false."""


def reply_prompt(
    *,
    kind: str,
    brand_context: str,
    business_facts: str,
    post_text: str | None,
    author_name: str,
    text: str,
    language: str,
) -> str:
    rule = language_rule(language)
    rule = f"\n{rule}" if rule else ""
    post_block = f"\nTHE POST THEY ARE REACTING TO\n<source id=\"post\">\n{neutralize(post_text)}\n</source>\n" if post_text else ""
    return f"""BRAND
{brand_context}{rule}

BUSINESS FACTS (the only facts you may state)
{business_facts.strip() or "None provided: answer no factual questions; flag them for a person."}
{post_block}
INCOMING {"PUBLIC COMMENT" if kind == "comment" else "PRIVATE MESSAGE"} from {author_name or "someone"}
<source id="incoming">
{neutralize(text)}
</source>

TASK
Decide how the brand should respond and draft the reply."""

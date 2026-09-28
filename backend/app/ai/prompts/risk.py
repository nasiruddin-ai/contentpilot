"""Autopilot risk check (spec section 5.2): is this post safe to publish without a person?"""

from pydantic import BaseModel, Field

from app.ai.prompts import UNTRUSTED_DATA_RULE, neutralize


class RiskAssessment(BaseModel):
    news_or_current_events: bool = Field(description="Reacts to news, a recent event, an announcement or a date-bound situation.")
    sensitive_topic: bool = Field(description="Politics, religion, health/medical advice, legal or financial advice, tragedy, identity, or anything divisive.")
    product_claims: bool = Field(description="Claims about what the brand's product or service achieves, guarantees, prices or comparisons with competitors.")
    high_risk_factual_claims: bool = Field(description="Specific statistics, studies, quotes, or facts that would be damaging if wrong.")
    reasons: list[str] = Field(default_factory=list, description="One short reason per true flag.")


SYSTEM = f"""You review social media posts before automatic publishing and flag anything that should
be seen by a person first. Be conservative: when unsure, flag it.
{UNTRUSTED_DATA_RULE}
The post text is inside <source> tags. Judge only the post; do not rewrite it."""


def risk_prompt(platform: str, text: str) -> str:
    return f"""Assess this {platform} post for automatic publishing.

<source id="post">
{neutralize(text)}
</source>

Return the four flags and a short reason for each flag that is true."""

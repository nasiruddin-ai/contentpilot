"""Deterministic quality checks for generated posts (spec sections 31 and 37).

These run on every post, before and after the AI editor, so hard rules never
depend on a model noticing them.
"""

import re
from dataclasses import dataclass

from app.core.language import ascii_digits, banned_word_pattern, strip_non_word, x_weighted_length
from app.models import ContentFormat, Platform


@dataclass(frozen=True)
class PlatformRules:
    max_chars: int  # whole rendered post
    max_hashtags: int
    hook_max: int | None = None  # where the hook is a separate title field
    part_max: int | None = None  # per post in an X thread
    notes: str = ""


# Official platform limits (standard accounts).
PLATFORM_RULES: dict[Platform, PlatformRules] = {
    Platform.LINKEDIN: PlatformRules(3000, 5, notes="Short paragraphs, line breaks, a question or CTA to close."),
    Platform.X: PlatformRules(
        280, 2, part_max=280, notes="One tight post of at most 280 characters including hashtags."
    ),
    Platform.INSTAGRAM: PlatformRules(2200, 30, notes="Caption. Strong first line; hashtags at the end."),
    Platform.FACEBOOK: PlatformRules(63206, 5, notes="Conversational; invite comments."),
    Platform.REDDIT: PlatformRules(
        40000, 0, hook_max=300, notes="Hook is the post title. Discussion-first, no hashtags, no hard sell."
    ),
    Platform.YOUTUBE: PlatformRules(
        5000, 15, hook_max=100, notes="Hook is the video title; body is the description."
    ),
}

# Phrases that make copy read as generic AI output.
GENERIC_PHRASES = [
    "in today's fast-paced world",
    "in today's digital age",
    "in the ever-evolving",
    "ever-changing landscape",
    "navigate the landscape",
    "unlock the power",
    "unleash the power",
    "game-changer",
    "game changer",
    "delve into",
    "dive deep into",
    "it's important to note",
    "it is important to note",
    "in conclusion",
    "a testament to",
    "rich tapestry",
    "revolutionize",
    "supercharge",
    "elevate your",
    "harness the power",
    "seamless experience",
    "look no further",
    # Bengali equivalents of the same filler.
    "আজকের দ্রুতগতির বিশ্বে",
    "আজকের ডিজিটাল যুগে",
    "প্রতিনিয়ত পরিবর্তনশীল",
    "গেম চেঞ্জার",
    "বিপ্লব ঘটাবে",
    "উল্লেখ্য যে",
    "এটা মনে রাখা গুরুত্বপূর্ণ",
    "পরিশেষে বলা যায়",
    "সর্বোপরি বলা যায়",
    "আর দেরি না করে",
]

# Small counts and list markers aren't claims worth checking.
_NUMBER = re.compile(r"(?<![\w.])\$?\d[\d,]*(?:\.\d+)?%?")


@dataclass
class Issue:
    severity: str  # "error" must be fixed before publishing; "warning" should be reviewed
    type: str
    detail: str

    def as_dict(self) -> dict:
        return {"severity": self.severity, "type": self.type, "detail": self.detail}


def normalize_hashtags(values: list[str]) -> list[str]:
    tags = []
    for value in values:
        tag = strip_non_word(value.lstrip("#"))
        if tag and tag.lower() not in {t.lower() for t in tags}:
            tags.append(tag)
    return tags


def render(hook: str, body: str, cta: str | None, hashtags: list[str], platform: Platform) -> str:
    """The post as it would be published. Title-style platforms keep the hook separate."""
    parts = [] if PLATFORM_RULES[platform].hook_max else [hook]
    parts += [body]
    if cta:
        parts.append(cta)
    if hashtags:
        parts.append(" ".join(f"#{tag}" for tag in hashtags))
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def check_post(
    *,
    platform: Platform,
    content_type: ContentFormat,
    hook: str,
    body: str,
    cta: str | None,
    hashtags: list[str],
    banned_words: list[str],
    source_text: str,
) -> list[Issue]:
    rules = PLATFORM_RULES[platform]
    issues: list[Issue] = []
    length = x_weighted_length if platform == Platform.X else len
    text = render(hook, body, cta, hashtags, platform)
    everything = "\n".join([hook, body, cta or ""])

    if not body.strip():
        issues.append(Issue("error", "empty", "The post body is empty."))

    if platform == Platform.X and content_type == ContentFormat.THREAD:
        for number, part in enumerate(re.split(r"\n\s*\n", body), start=1):
            if length(part) > rules.part_max:
                issues.append(Issue("error", "too_long", f"Thread post {number} is {length(part)} characters (max {rules.part_max})."))
    elif length(text) > rules.max_chars:
        issues.append(Issue("error", "too_long", f"{length(text)} characters (max {rules.max_chars} on {platform})."))

    if rules.hook_max and len(hook) > rules.hook_max:
        issues.append(Issue("error", "title_too_long", f"Title is {len(hook)} characters (max {rules.hook_max})."))

    if len(hashtags) > rules.max_hashtags:
        issues.append(
            Issue("error", "too_many_hashtags", f"{len(hashtags)} hashtags (max {rules.max_hashtags} on {platform}).")
        )

    for word in banned_words:
        if word and banned_word_pattern(word).search(everything):
            issues.append(Issue("error", "banned_word", f'Uses the banned word "{word}".'))

    lowered = everything.lower()
    for phrase in GENERIC_PHRASES:
        if phrase in lowered:
            issues.append(Issue("warning", "generic_phrase", f'Generic phrasing: "{phrase}".'))

    issues.extend(_unsupported_numbers(everything, source_text))
    return issues


def _unsupported_numbers(text: str, source_text: str) -> list[Issue]:
    """Flags figures that appear in the post but nowhere in its sources: likely invented."""
    available = {_plain(n) for n in _NUMBER.findall(source_text)}
    flagged = []
    for raw in dict.fromkeys(_NUMBER.findall(text)):
        value = _plain(raw)
        is_small_count = value.isdigit() and int(value) <= 10 and "%" not in raw and "$" not in raw
        if not is_small_count and value not in available:
            flagged.append(Issue("warning", "unsupported_number", f'"{raw}" does not appear in the cited sources.'))
    return flagged


def _plain(number: str) -> str:
    return ascii_digits(number).replace(",", "").replace("$", "").rstrip("%")

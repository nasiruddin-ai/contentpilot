"""Content languages a brand can post in, and text helpers that work across scripts.

Python's `\\w` treats Bengali vowel signs and the virama as non-word characters, so plain
`\\w` regexes split Bengali words in the middle. Use WORD_CHARS instead wherever a
"word" boundary or character matters.
"""

import re
from enum import StrEnum


class ContentLanguage(StrEnum):
    ENGLISH = "en"
    BENGALI = "bn"


LANGUAGE_NAMES = {
    ContentLanguage.ENGLISH: "English",
    ContentLanguage.BENGALI: "Bengali (বাংলা)",
}

# Word characters: \w plus combining marks and letters of the Indic blocks (Devanagari to
# Sinhala), minus the danda punctuation (। ॥), plus the zero-width joiners Bengali uses.
WORD_CHARS = r"\w\u0900-\u0963\u0966-\u0DFF\u200C\u200D"  # re resolves the \u escapes
_NOT_WORD = re.compile(rf"[^{WORD_CHARS}]")

BENGALI_TEXT = re.compile(r"[\u0980-\u09FF]")

# Bengali digits ০-৯ → 0-9, so "২৫%" in a post matches "25%" in an English source.
_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def ascii_digits(text: str) -> str:
    return text.translate(_DIGITS)


def strip_non_word(text: str) -> str:
    return _NOT_WORD.sub("", text)


def banned_word_pattern(word: str) -> re.Pattern[str]:
    """Matches `word` as a whole word in any script."""
    return re.compile(rf"(?<![{WORD_CHARS}]){re.escape(word)}(?![{WORD_CHARS}])", re.IGNORECASE)


def has_bengali(text: str | None) -> bool:
    return bool(text and BENGALI_TEXT.search(text))


def x_weighted_length(text: str) -> int:
    """Post length as X counts it (twitter-text v3): most scripts, Bengali included, count 1
    per character; others, such as CJK and emoji, count 2."""
    total = 0
    for char in text:
        code = ord(char)
        light = code <= 0x10FF or 0x2000 <= code <= 0x200D or 0x2010 <= code <= 0x201F or 0x2032 <= code <= 0x2037
        total += 1 if light else 2
    return total


def language_rule(language: str | None) -> str:
    """Prompt instruction for the output language. Empty for English, the default."""
    if language != ContentLanguage.BENGALI:
        return ""
    return (
        "OUTPUT LANGUAGE: Write every text value the reader will see (hooks, body, calls to action, hashtags, "
        "headlines, angles, explanations, alt text) in natural, fluent Bengali (বাংলা) in Bengali script, as a "
        "native Bangladeshi writer would. Do not translate word by word from English. Keep JSON keys and any "
        "fixed enum values (platform names, formats, pillars, scores) exactly as specified in English. Keep brand "
        "names, product names and URLs unchanged. Numbers may use Bengali digits, but their values must match "
        "the sources exactly. Hashtags: Bengali words without spaces, or widely used English tags."
    )

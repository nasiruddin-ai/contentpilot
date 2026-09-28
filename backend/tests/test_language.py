"""Bengali support: text helpers, quality checks, prompts and rendering."""

from types import SimpleNamespace

import pytest

from app.ai.prompts.brand import brand_context
from app.ai.prompts.visual import concept_prompt
from app.core.language import (
    ascii_digits,
    banned_word_pattern,
    has_bengali,
    language_rule,
    strip_non_word,
    x_weighted_length,
)
from app.models import ContentFormat, Platform
from app.research.deduplication import _WORD
from app.services.content_quality import check_post, normalize_hashtags
from app.visuals import fonts, render

KRISHI = "কৃষি"  # agriculture: consonant + vowel sign + conjunct
BANGLADESH = "বাংলাদেশের"
SOURCE = "Rice exports rose 25% in 2026, the ministry said."


def issues(platform=Platform.FACEBOOK, **overrides):
    post = {"hook": "ধানের দাম বাড়ছে।", "body": "কৃষকদের জন্য ভালো খবর।", "cta": "আপনার মত কী?", "hashtags": []}
    post.update(overrides)
    found = check_post(
        platform=platform, content_type=ContentFormat.TEXT_POST, banned_words=[KRISHI], source_text=SOURCE, **post
    )
    return {(i.severity, i.type) for i in found}


def test_bengali_words_stay_whole():
    assert strip_non_word(f"#{BANGLADESH}_{KRISHI}!") == f"{BANGLADESH}_{KRISHI}"
    assert normalize_hashtags([f"#{KRISHI}", KRISHI, "বাংলা খবর"]) == [KRISHI, "বাংলাখবর"]
    assert _WORD.findall(f"{BANGLADESH} {KRISHI}।") == [BANGLADESH, KRISHI]


def test_banned_words_match_whole_bengali_words_only():
    assert banned_word_pattern(KRISHI).search(f"{BANGLADESH} {KRISHI} ভালো")
    # "কৃষ" is a prefix of "কৃষি": not the same word.
    assert not banned_word_pattern("কৃষ").search(f"{BANGLADESH} {KRISHI}")
    assert ("error", "banned_word") in issues(body=f"{KRISHI} নিয়ে কথা।")
    assert ("error", "banned_word") not in issues(body="কৃষকদের জন্য ভালো খবর।")


def test_bengali_generic_phrases_are_flagged():
    assert ("warning", "generic_phrase") in issues(body="আজকের ডিজিটাল যুগে সবকিছু বদলে যাচ্ছে।")


def test_bengali_digits_are_checked_against_sources():
    assert ascii_digits("২০২৬ সালে ২৫%") == "2026 সালে 25%"
    assert ("warning", "unsupported_number") not in issues(body="২০২৬ সালে রপ্তানি ২৫% বেড়েছে।")
    assert ("warning", "unsupported_number") in issues(body="রপ্তানি ৭২% বেড়েছে।")


def test_x_counts_like_x_does():
    assert x_weighted_length("abc") == 3
    assert x_weighted_length(KRISHI) == len(KRISHI)  # Bengali is a light script on X
    assert x_weighted_length("日本") == 4
    assert x_weighted_length("🌱") == 2
    assert ("error", "too_long") not in issues(Platform.X, hook="", body="ক" * 270, cta="")
    assert ("error", "too_long") in issues(Platform.X, hook="", body="🌱" * 141, cta="")


def _brand(language):
    return SimpleNamespace(
        name="Easy Garden",
        website=None,
        industry="Gardening",
        description=None,
        audience=None,
        market="Bangladesh",
        goals=[],
        tone=[],
        visual_style=[],
        preferred_words=[],
        banned_words=[],
        content_pillars=[],
        language=language,
    )


def test_prompts_ask_for_bengali_only_when_chosen():
    assert language_rule("en") == ""
    english = brand_context(_brand("en"))
    assert "OUTPUT LANGUAGE" not in english and "Post language" not in english

    bengali = brand_context(_brand("bn"))
    assert "Post language: Bengali" in bengali
    assert "OUTPUT LANGUAGE" in bengali and "JSON keys" in bengali

    assert "OUTPUT LANGUAGE" in concept_prompt("quote_card", "Easy Garden", [], "post", "bn")
    assert "OUTPUT LANGUAGE" not in concept_prompt("quote_card", "Easy Garden", [], "post")


def test_bengali_text_uses_a_bengali_font():
    assert has_bengali(KRISHI) and not has_bengali("Easy Garden")
    path = fonts.find_bengali_font(False)
    if path is None:
        pytest.skip("No Bengali font installed here (the Docker image has Noto Sans Bengali).")
    mixed = fonts.load(None, 40, text=f"{KRISHI} Easy Garden tip")
    assert mixed.path == path
    assert fonts.load(None, 40, text="Easy Garden").path != path
    # Bengali fonts have no Latin letters, so English words go to the brand font.
    runs = [(part, font is mixed.latin) for part, font in mixed._runs(f"{KRISHI} (Easy Garden) ২৫%")]
    assert runs == [(f"{KRISHI} (", False), ("Easy", True), (" ", False), ("Garden", True), (") ২৫%", False)]


def test_bengali_slides_render():
    style = render.BrandStyle(name="ইজি গার্ডেন")
    slide = render.Slide(layout="title_points", headline="শীতের সবজি", points=["মাটি তৈরি করুন", "বীজ বপন করুন"])
    result = render.render_slide(slide, style, "1:1")
    assert result.image.size == render.SIZES["1:1"]
    assert result.truncated == []

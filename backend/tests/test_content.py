import json
import re
import uuid
from datetime import UTC, datetime

import pytest

from app.ai.base import AIProvider, AIProviderError, EmbeddingResult, TextResult
from app.ai.prompts.writer import Brief
from app.ai.service import AIContext, AIService, ModelTier
from app.core.database import sync_session
from app.models import ContentFormat, ContentOpportunity, Platform, ResearchItem
from app.services import content_service, topic_service
from app.services.content_quality import check_post, normalize_hashtags, render
from tests.conftest import sign_up

SOURCE = "Sites with one clear call to action saw 38% more enquiries in 2026, according to the survey."


# --- Deterministic checks ----------------------------------------------------------


def issues(platform=Platform.LINKEDIN, content_type=ContentFormat.TEXT_POST, **overrides):
    post = {"hook": "Clarity sells.", "body": "Keep one clear call to action.", "cta": "Thoughts?", "hashtags": []}
    post.update(overrides)
    found = check_post(
        platform=platform, content_type=content_type, banned_words=["synergy"], source_text=SOURCE, **post
    )
    return {(i.severity, i.type) for i in found}


def test_render_keeps_title_separate_where_needed():
    assert render("Hook", "Body", "CTA", ["seo"], Platform.LINKEDIN) == "Hook\n\nBody\n\nCTA\n\n#seo"
    assert render("Title", "Body", None, [], Platform.REDDIT) == "Body"


def test_normalize_hashtags():
    assert normalize_hashtags(["#SEO", "seo", "web design", "#", "Squarespace!"]) == ["SEO", "webdesign", "Squarespace"]


def test_clean_post_has_no_issues():
    assert issues() == set()


def test_platform_limits():
    assert ("error", "too_long") in issues(Platform.X, body="x" * 281)
    assert ("error", "too_long") not in issues(Platform.X, body="x" * 200, cta="")
    thread = "a" * 270 + "\n\n" + "b" * 300
    assert ("error", "too_long") in issues(Platform.X, ContentFormat.THREAD, body=thread, cta="")
    assert ("error", "too_many_hashtags") in issues(Platform.REDDIT, hashtags=["seo"])
    assert ("error", "title_too_long") in issues(Platform.YOUTUBE, hook="t" * 101)
    assert ("error", "too_many_hashtags") in issues(Platform.LINKEDIN, hashtags=[f"t{i}" for i in range(6)])


def test_banned_and_generic_language():
    assert ("error", "banned_word") in issues(body="Real Synergy here.")
    assert ("warning", "generic_phrase") in issues(body="In today's fast-paced world, clarity wins.")


def test_numbers_must_come_from_sources():
    assert ("warning", "unsupported_number") not in issues(body="38% more enquiries in 2026.")
    assert ("warning", "unsupported_number") in issues(body="Conversions rise 72% with clarity.")
    assert ("warning", "unsupported_number") in issues(body="Join 5,000 happy clients.")
    assert ("warning", "unsupported_number") not in issues(body="3 quick tips: 1. Clarity 2. Focus")


# --- Pipeline with a scripted AI -----------------------------------------------------

GOOD = {"hook": "One button beats five.", "body": "Visitors act when the next step is obvious.", "cta": "What is yours?"}


class WriterAI(AIProvider):
    """Answers each pipeline stage; tests override what adaptation and review return."""

    name = "fake"

    def __init__(self):
        self.adaptations: dict[str, dict] | None = None
        self.review: list[dict] | None = None
        self.fail_review = False
        self.fail_all = False
        self.stages: list[str] = []

    async def generate(self, request):
        if self.fail_all:
            raise AIProviderError("AI_AUTH_FAILED", "The Gemini API key was rejected.")
        prompt = request.prompt
        if "Plan one piece of content" in prompt:
            self.stages.append("plan")
            body = {
                "hook_options": ["A", "B", "C"],
                "chosen_hook": "B",
                "key_points": [{"point": "One clear CTA", "source_refs": ["S1"]}],
                "cta": "What is yours?",
                "outline": ["Hook", "Point", "CTA"],
            }
        elif "Write the master draft" in prompt:
            self.stages.append("draft")
            body = GOOD
        elif "Adapt the master draft" in prompt:
            self.stages.append("adapt")
            platforms = re.findall(r"^- (\w+): ", prompt.split("TASK", 1)[1], re.M)
            posts = self.adaptations or {p: {**GOOD, "hashtags": ["#Clarity"]} for p in platforms}
            body = {"posts": [{"platform": p, **post} for p, post in posts.items()]}
        else:
            self.stages.append("review")
            if self.fail_review:
                raise AIProviderError("AI_PROVIDER_ERROR", "overloaded")
            body = {"posts": self.review or []}
        return TextResult(text=json.dumps(body), model=request.model, input_tokens=10, output_tokens=10)

    async def embed(self, texts, *, model, dimensions):
        return EmbeddingResult(vectors=[[1.0] + [0.0] * (dimensions - 1) for _ in texts], model=model, input_tokens=1)


def service(provider):
    return AIService(
        provider,
        models={ModelTier.FAST: "fast", ModelTier.QUALITY: "quality"},
        embedding_model="embed",
        embedding_dimensions=768,
        recorder=lambda record: None,
        backoff_seconds=0,
        max_attempts=1,
    )


BRIEF = Brief(
    brand_context="Brand: Squareko",
    topic="website conversion",
    angle="Clarity beats decoration",
    why_now="Survey",
    audience="Founders",
    content_format="text_post",
    content_pillar="educational",
    sources=[("S1", "Survey", SOURCE)],
)


async def run(provider, platforms=(Platform.LINKEDIN, Platform.X)):
    return await content_service.write_content(
        service(provider), BRIEF, list(platforms), ContentFormat.TEXT_POST, ["synergy"], SOURCE, AIContext()
    )


async def test_pipeline_runs_every_stage():
    provider = WriterAI()
    result = await run(provider)
    assert provider.stages == ["plan", "draft", "adapt", "review"]
    assert [p.platform for p in result.posts] == [Platform.LINKEDIN, Platform.X]
    assert result.posts[0].hashtags == ["Clarity"]
    assert result.plan.hook_options == ["A", "B", "C"]
    assert all(p.issues == [] for p in result.posts)


async def test_editor_fix_is_applied():
    provider = WriterAI()
    provider.adaptations = {"linkedin": {**GOOD, "body": "Pure synergy for your site."}}
    provider.review = [{"platform": "linkedin", "changed": True, **GOOD, "hashtags": [], "notes": "removed banned word"}]
    (post,) = (await run(provider, [Platform.LINKEDIN])).posts
    assert "synergy" not in post.body.lower()
    assert post.issues == []


async def test_editor_cannot_make_things_worse():
    provider = WriterAI()
    provider.review = [{"platform": "x", "changed": True, "hook": "h", "body": "y" * 400, "cta": "", "hashtags": []}]
    (post,) = (await run(provider, [Platform.X])).posts
    assert post.body == GOOD["body"]  # the over-long "fix" was rejected


async def test_missing_adaptation_and_failed_review_are_flagged_not_fatal():
    provider = WriterAI()
    provider.adaptations = {"linkedin": {**GOOD, "hashtags": []}}  # nothing for X
    provider.fail_review = True
    result = await run(provider)
    x = next(p for p in result.posts if p.platform == Platform.X)
    types = {i.type for i in x.issues}
    assert {"not_adapted", "not_reviewed"} <= types
    assert x.body == GOOD["body"]


# --- End to end: API + worker ---------------------------------------------------------


@pytest.fixture
def writer_ai(monkeypatch):
    provider = WriterAI()
    monkeypatch.setattr(topic_service, "ai_service_factory", lambda: service(provider))
    return provider


@pytest.fixture
def opportunity(api_client, writer_ai, monkeypatch):
    enqueued = []
    monkeypatch.setattr(content_service, "enqueue_generation", enqueued.append)
    sign_up(api_client, "owner@example.com")
    brand_id = uuid.UUID(api_client.post("/api/v1/brands", json={"name": "Squareko", "banned_words": ["synergy"]}).json()["id"])
    with sync_session() as db:
        item = ResearchItem(
            brand_id=brand_id,
            title="Clarity survey",
            canonical_url="https://example.com/survey",
            fetched_at=datetime.now(UTC),
            summary="A survey about clear calls to action.",
            clean_text=SOURCE,
            content_type="article",
            content_hash="h",
            simhash=0,
        )
        db.add(item)
        db.flush()
        idea = ContentOpportunity(
            brand_id=brand_id,
            topic="website conversion",
            angle="Clarity beats decoration",
            why_now="Survey results",
            audience="Founders",
            recommended_format="text_post",
            recommended_platforms=["linkedin", "x"],
            source_ids=[item.id],
            relevance_score=80,
            freshness_score=90,
            brand_fit_score=80,
            novelty_score=100,
            priority_score=85,
            embedding=[1.0] + [0.0] * 767,
        )
        db.add(idea)
        db.flush()
        ids = {"id": str(idea.id), "brand_id": str(brand_id), "item_id": str(item.id), "enqueued": enqueued}
    return ids


def test_generate_posts_end_to_end(api_client, opportunity):
    response = api_client.post("/api/v1/posts/generate", json={"opportunity_id": opportunity["id"]})
    assert response.status_code == 202
    generation = response.json()
    assert generation["status"] == "queued" and generation["platforms"] == ["linkedin", "x"]
    assert opportunity["enqueued"] == [uuid.UUID(generation["id"])]
    # A second click while queued returns the same job.
    again = api_client.post("/api/v1/posts/generate", json={"opportunity_id": opportunity["id"]}).json()
    assert again["id"] == generation["id"]

    assert content_service.execute_generation(uuid.UUID(generation["id"]))["status"] == "succeeded"

    done = api_client.get(f"/api/v1/posts/generations/{generation['id']}").json()
    assert done["status"] == "succeeded" and len(done["post_ids"]) == 2
    assert done["hook_options"] == ["A", "B", "C"] and done["master_draft"].startswith("One button")

    posts = api_client.get("/api/v1/posts", params={"brand_id": opportunity["brand_id"]}).json()
    assert {p["platform"] for p in posts} == {"linkedin", "x"}
    linkedin = next(p for p in posts if p["platform"] == "linkedin")
    assert linkedin["status"] == "draft"
    assert linkedin["full_text"].endswith("#Clarity")

    detail = api_client.get(f"/api/v1/posts/{linkedin['id']}").json()
    assert [s["id"] for s in detail["sources"]] == [opportunity["item_id"]]

    idea = api_client.get(f"/api/v1/opportunities/{opportunity['id']}").json()
    assert idea["status"] == "used"

    only_x = api_client.get("/api/v1/posts", params={"brand_id": opportunity["brand_id"], "platform": "x"}).json()
    assert [p["platform"] for p in only_x] == ["x"]

    assert api_client.delete(f"/api/v1/posts/{linkedin['id']}").status_code == 204
    assert api_client.get(f"/api/v1/posts/{linkedin['id']}").status_code == 404


def test_platform_choice_overrides_recommendation(api_client, opportunity):
    generation = api_client.post(
        "/api/v1/posts/generate", json={"opportunity_id": opportunity["id"], "platforms": ["reddit", "reddit"]}
    ).json()
    assert generation["platforms"] == ["reddit"]
    assert api_client.post(
        "/api/v1/posts/generate", json={"opportunity_id": opportunity["id"], "platforms": ["myspace"]}
    ).status_code == 422


def test_ai_failure_marks_generation_failed(api_client, opportunity, writer_ai):
    writer_ai.fail_all = True
    generation = api_client.post("/api/v1/posts/generate", json={"opportunity_id": opportunity["id"]}).json()
    assert content_service.execute_generation(uuid.UUID(generation["id"]))["status"] == "failed"
    stored = api_client.get(f"/api/v1/posts/generations/{generation['id']}").json()
    assert stored["status"] == "failed" and "rejected" in stored["error"]
    assert api_client.get("/api/v1/posts", params={"brand_id": opportunity["brand_id"]}).json() == []
    # The opportunity stays available.
    assert api_client.get(f"/api/v1/opportunities/{opportunity['id']}").json()["status"] == "new"


def test_posts_are_private(api_client, opportunity):
    generation = api_client.post("/api/v1/posts/generate", json={"opportunity_id": opportunity["id"]}).json()
    content_service.execute_generation(uuid.UUID(generation["id"]))
    post_id = api_client.get("/api/v1/posts", params={"brand_id": opportunity["brand_id"]}).json()[0]["id"]

    sign_up(api_client, "intruder@example.com")
    assert api_client.get("/api/v1/posts", params={"brand_id": opportunity["brand_id"]}).status_code == 404
    assert api_client.get(f"/api/v1/posts/{post_id}").status_code == 404
    assert api_client.delete(f"/api/v1/posts/{post_id}").status_code == 404
    assert api_client.get(f"/api/v1/posts/generations/{generation['id']}").status_code == 404
    assert api_client.post("/api/v1/posts/generate", json={"opportunity_id": opportunity["id"]}).status_code == 404

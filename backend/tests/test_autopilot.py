import json
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from app.ai.base import AIProvider, AIProviderError, EmbeddingResult, TextResult
from app.ai.service import AIService, ModelTier
from app.core.database import sync_session
from app.models import AutopilotMode, AutopilotSettings, ContentOpportunity, ResearchItem
from app.services import autopilot_service, publishing_service, research_service, topic_service
from app.services.autopilot_service import decide, open_slots
from tests.conftest import sign_up
from tests.test_facebook import FakeGraph

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)  # a Saturday


def settings_obj(**overrides) -> AutopilotSettings:
    values = {"days_of_week": ["0", "2", "4"], "post_time": "09:00", "timezone": "Europe/Amsterdam", "horizon_days": 7, "max_posts_per_run": 3}
    values.update(overrides)
    return AutopilotSettings(brand_id=uuid.uuid4(), **values)


# --- Slot planning ---------------------------------------------------------------------------


def test_open_slots_follow_schedule_and_timezone():
    slots = open_slots(settings_obj(), [], NOW, limit=3)
    # Mon 28, Wed 30 Sep, Fri 2 Oct at 09:00 Amsterdam (UTC+2) = 07:00 UTC
    assert [s.isoformat() for s in slots] == ["2026-09-28T07:00:00+00:00", "2026-09-30T07:00:00+00:00", "2026-10-02T07:00:00+00:00"]


def test_open_slots_skip_taken_and_past_slots():
    taken = [datetime(2026, 9, 28, 7, 30, tzinfo=UTC)]  # within 2h of the Monday slot
    slots = open_slots(settings_obj(), taken, NOW, limit=5)
    assert slots[0].isoformat() == "2026-09-30T07:00:00+00:00"
    assert open_slots(settings_obj(days_of_week=[]), [], NOW, limit=3) == []
    late_today = open_slots(settings_obj(days_of_week=["5"], post_time="13:50", timezone="UTC"), [], NOW, limit=1)
    assert late_today[0].isoformat() == "2026-09-26T13:50:00+00:00"
    too_soon = open_slots(settings_obj(days_of_week=["5"], post_time="12:10", timezone="UTC"), [], NOW, limit=1)
    assert too_soon[0].isoformat() == "2026-10-03T12:10:00+00:00"  # 10 minutes ahead is too close; next week


# --- Rule engine -----------------------------------------------------------------------------

SAFE_RISK = {"news_or_current_events": False, "sensitive_topic": False, "product_claims": False, "high_risk_factual_claims": False, "reasons": []}
RULES = {"auto_approve_pillars": ["educational", "how_to"], "always_review_pillars": ["promotion"], "review_if": {}}


def test_copilot_mode_reviews_everything():
    d = decide(AutopilotMode.COPILOT, RULES, content_pillar="educational", quality_issues=[], risk=SAFE_RISK)
    assert d.action == "review" and "Copilot" in d.reasons[0]


def test_autopilot_schedules_only_safe_evergreen_posts():
    assert decide(AutopilotMode.AUTOPILOT, RULES, content_pillar="educational", quality_issues=[], risk=SAFE_RISK).action == "schedule"
    assert decide(AutopilotMode.AUTOPILOT, RULES, content_pillar="promotion", quality_issues=[], risk=SAFE_RISK).action == "review"
    assert decide(AutopilotMode.AUTOPILOT, RULES, content_pillar="story", quality_issues=[], risk=SAFE_RISK).action == "review"
    flagged = decide(AutopilotMode.AUTOPILOT, RULES, content_pillar="educational", quality_issues=[], risk={**SAFE_RISK, "news_or_current_events": True, "reasons": ["Mentions this week's update"]})
    assert flagged.action == "review" and "news" in flagged.reasons[0] and "this week's update" in flagged.reasons[1]
    warned = decide(AutopilotMode.AUTOPILOT, RULES, content_pillar="educational", quality_issues=[{"severity": "warning", "type": "unsupported_number", "detail": '"72%" not in sources'}], risk=SAFE_RISK)
    assert warned.action == "review" and "72%" in warned.reasons[0]
    assert decide(AutopilotMode.AUTOPILOT, RULES, content_pillar="educational", quality_issues=[], risk=None).action == "review"


def test_quality_errors_reject_in_any_mode():
    issues = [{"severity": "error", "type": "banned_word", "detail": "Uses synergy"}]
    assert decide(AutopilotMode.AUTOPILOT, RULES, content_pillar="educational", quality_issues=issues, risk=SAFE_RISK).action == "reject"
    assert decide(AutopilotMode.COPILOT, RULES, content_pillar="educational", quality_issues=issues, risk=None).action == "reject"


def test_rules_can_switch_checks_off():
    lenient = {**RULES, "review_if": {"news_or_current_events": False, "quality_warnings": False}}
    d = decide(AutopilotMode.AUTOPILOT, lenient, content_pillar="how_to", quality_issues=[{"severity": "warning", "type": "generic_phrase", "detail": "x"}], risk={**SAFE_RISK, "news_or_current_events": True})
    assert d.action == "schedule"


# --- Fakes for the full cycle -------------------------------------------------------------------


class AutopilotAI(AIProvider):
    """Answers every stage the cycle touches. Risk flags are keyed by hook text."""

    name = "fake"

    def __init__(self):
        self.pillar = "educational"
        self.risk_flag = False
        self.calls: list[str] = []

    async def generate(self, request):
        p = request.prompt
        if "For each source below" in p:
            self.calls.append("analysis")
            items = [{"ref": ref, "summary": "s", "topics": [f"clarity topic {ref}"], "keywords": [], "entities": []} for ref in __import__("re").findall(r'<source id="(R\d+)"', p)]
            body = {"items": items}
        elif "TRENDING TOPICS" in p:
            self.calls.append("opportunities")
            import re

            topics = re.findall(r'<topic id="(T\d+)".*?<source id="(S\d+)"', p, re.S)
            body = {"opportunities": [
                {"topic_ref": t, "angle": f"Angle {i}", "why_now": "w", "audience": "founders", "recommended_format": "text_post",
                 "recommended_platforms": ["facebook"], "content_pillar": self.pillar, "source_refs": [s], "relevance_score": 80, "brand_fit_score": 80}
                for i, (t, s) in enumerate(topics[:6])
            ]}
        elif "Plan one piece of content" in p:
            self.calls.append("plan")
            body = {"hook_options": ["A"], "chosen_hook": "A", "key_points": [{"point": "p", "source_refs": ["S1"]}], "cta": "c", "outline": ["o"]}
        elif "Write the master draft" in p:
            self.calls.append("draft")
            body = {"hook": "Clarity wins.", "body": "Say what you do in the first line.", "cta": "Thoughts?"}
        elif "Adapt the master draft" in p:
            self.calls.append("adapt")
            body = {"posts": [{"platform": "facebook", "hook": "Clarity wins.", "body": "Say what you do in the first line.", "cta": "Thoughts?", "hashtags": []}]}
        elif "Assess this" in p:
            self.calls.append("risk")
            body = {**SAFE_RISK, "news_or_current_events": self.risk_flag, "reasons": ["Reacts to a launch"] if self.risk_flag else []}
        else:
            self.calls.append("review")
            body = {"posts": []}
        return TextResult(text=json.dumps(body), model=request.model, input_tokens=5, output_tokens=5)

    async def embed(self, texts, *, model, dimensions):
        import hashlib

        vectors = []
        for t in texts:
            v = [0.0] * dimensions
            v[int(hashlib.md5(t.encode()).hexdigest(), 16) % dimensions] = 1.0
            vectors.append(v)
        return EmbeddingResult(vectors=vectors, model=model, input_tokens=1)


@pytest.fixture
def ai(monkeypatch):
    provider = AutopilotAI()
    service = AIService(provider, models={ModelTier.FAST: "fast", ModelTier.QUALITY: "quality"}, embedding_model="e", embedding_dimensions=768, recorder=lambda r: None, backoff_seconds=0, max_attempts=1)
    monkeypatch.setattr(topic_service, "ai_service_factory", lambda: service)
    return provider


@pytest.fixture
def brand(api_client, ai, monkeypatch):
    from app.integrations import facebook

    graph = FakeGraph()
    monkeypatch.setattr(facebook, "transport_factory", lambda: httpx.MockTransport(graph.handle))
    monkeypatch.setattr(publishing_service, "enqueue_publish", lambda post_id: None)
    monkeypatch.setattr(research_service, "enqueue_run", lambda run_id: None)
    queued = []
    monkeypatch.setattr(autopilot_service, "enqueue_run", queued.append)
    sign_up(api_client, "owner@example.com")
    brand = api_client.post("/api/v1/brands", json={"name": "Easy Garden", "banned_words": ["synergy"]}).json()
    url = api_client.post("/api/v1/social/facebook/connect", json={"brand_id": brand["id"]}).json()["authorization_url"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    assert api_client.get("/api/v1/social/facebook/callback", params={"code": "fb-code", "state": state}).status_code == 200
    with sync_session() as db:
        for i in range(3):
            db.add(ResearchItem(brand_id=uuid.UUID(brand["id"]), title=f"Article {i}", canonical_url=f"https://example.com/{i}", fetched_at=datetime.now(UTC), summary="s", clean_text="Clear sites convert.", content_type="article", content_hash=str(i), simhash=0))
    brand["queued"] = queued
    return brand


def configure(client, brand_id, **extra):
    body = {"mode": "autopilot", "platforms": ["facebook"], "days_of_week": [0, 1, 2, 3, 4, 5, 6], "post_time": "09:00", "timezone": "UTC", "max_posts_per_run": 2, **extra}
    response = client.patch("/api/v1/autopilot/settings", params={"brand_id": brand_id}, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def run_cycle(client, brand_id):
    response = client.post("/api/v1/autopilot/run", json={"brand_id": brand_id})
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    result = autopilot_service.execute_cycle(uuid.UUID(run_id))
    return client.get(f"/api/v1/autopilot/runs/{run_id}").json(), result


# --- Settings API ------------------------------------------------------------------------------


def test_settings_defaults_and_validation(api_client, brand):
    settings = api_client.get("/api/v1/autopilot/settings", params={"brand_id": brand["id"]}).json()
    assert settings["mode"] == "off" and settings["approval_rules"]["auto_approve_pillars"] == ["educational", "how_to", "faq"]

    def patch(body):
        return api_client.patch("/api/v1/autopilot/settings", params={"brand_id": brand["id"]}, json=body)

    assert patch({"mode": "autopilot"}).json()["error"]["code"] == "AUTOPILOT_INCOMPLETE"
    assert patch({"mode": "autopilot", "platforms": ["linkedin"], "days_of_week": [0]}).json()["error"]["code"] == "ACCOUNT_NOT_CONNECTED"
    assert patch({"platforms": ["instagram"]}).status_code == 422
    assert patch({"post_time": "9am"}).status_code == 422
    assert patch({"timezone": "Mars/Olympus"}).json()["error"]["code"] == "INVALID_TIMEZONE"
    assert patch({"approval_rules": {"auto_approve_pillars": ["memes"]}}).status_code == 422
    assert api_client.post("/api/v1/autopilot/run", json={"brand_id": brand["id"]}).json()["error"]["code"] == "AUTOPILOT_OFF"

    updated = configure(api_client, brand["id"], approval_rules={"auto_approve_pillars": ["educational"], "always_review_pillars": ["promotion"], "review_if": {"quality_warnings": False}})
    assert updated["mode"] == "autopilot" and updated["days_of_week"] == [0, 1, 2, 3, 4, 5, 6]
    assert updated["approval_rules"]["review_if"]["quality_warnings"] is False
    assert updated["approval_rules"]["review_if"]["sensitive_topic"] is True
    slots = api_client.get("/api/v1/autopilot/slots", params={"brand_id": brand["id"]}).json()["slots"]
    assert len(slots) == 2


# --- Full cycle ---------------------------------------------------------------------------------


def test_autopilot_cycle_schedules_safe_posts(api_client, brand, ai):
    configure(api_client, brand["id"])
    run, result = run_cycle(api_client, brand["id"])

    assert run["status"] == "succeeded", run
    assert run["slots_open"] == 2 and run["opportunities_created"] >= 2
    assert run["posts_created"] == 2 and run["posts_scheduled"] == 2 and run["posts_for_review"] == 0
    assert all(d["decision"] == "schedule" and "auto-approved" in d["reasons"][0] for d in run["decisions"])
    assert "risk" in ai.calls and "opportunities" in ai.calls

    posts = api_client.get("/api/v1/posts", params={"brand_id": brand["id"], "status": "scheduled"}).json()
    assert len(posts) == 2 and posts[0]["scheduled_at"] != posts[1]["scheduled_at"]
    assert all(p["approved_at"] and p["review_note"].startswith("Autopilot") for p in posts)

    calendar = api_client.get("/api/v1/calendar", params={"brand_id": brand["id"], "start": datetime.now(UTC).isoformat(), "end": (datetime.now(UTC) + timedelta(days=8)).isoformat()}).json()
    assert len(calendar) == 2
    types = [n["type"] for n in api_client.get("/api/v1/notifications").json()]
    assert "autopilot_scheduled" in types and "approval_required" not in types

    # Ideas that became posts are marked used; a second run finds the slots taken.
    with sync_session() as db:
        used = db.scalars(select(ContentOpportunity.status).where(ContentOpportunity.brand_id == uuid.UUID(brand["id"]))).all()
    assert "used" in used
    # A second run moves on to the next free days and never doubles up a slot.
    run2, _ = run_cycle(api_client, brand["id"])
    assert run2["status"] == "succeeded" and run2["slots_open"] == 2
    scheduled = [p["scheduled_at"] for p in api_client.get("/api/v1/posts", params={"brand_id": brand["id"], "status": "scheduled", "limit": 50}).json()]
    assert len(scheduled) == len(set(scheduled)) and len(scheduled) >= 2


def test_flagged_posts_wait_for_review(api_client, brand, ai):
    ai.risk_flag = True
    configure(api_client, brand["id"], max_posts_per_run=1)
    run, _ = run_cycle(api_client, brand["id"])
    assert run["posts_created"] == 1 and run["posts_for_review"] == 1 and run["posts_scheduled"] == 0
    (decision,) = run["decisions"]
    assert decision["decision"] == "review" and any("news" in r for r in decision["reasons"])
    (post,) = api_client.get("/api/v1/posts", params={"brand_id": brand["id"], "status": "review"}).json()
    assert post["scheduled_at"] is None and "held this for review" in post["review_note"]
    assert "approval_required" in [n["type"] for n in api_client.get("/api/v1/notifications").json()]
    # A person can still approve it the normal way.
    assert api_client.post(f"/api/v1/posts/{post['id']}/approve").json()["status"] == "approved"


def test_copilot_mode_never_schedules(api_client, brand, ai):
    configure(api_client, brand["id"], mode="copilot", max_posts_per_run=1)
    run, _ = run_cycle(api_client, brand["id"])
    assert run["posts_for_review"] == 1 and run["posts_scheduled"] == 0
    assert "risk" not in ai.calls  # no AI risk call needed when everything is reviewed anyway


def test_pillar_rules_hold_promotion(api_client, brand, ai):
    ai.pillar = "promotion"
    configure(api_client, brand["id"], max_posts_per_run=1)
    run, _ = run_cycle(api_client, brand["id"])
    assert run["posts_for_review"] == 1
    assert any("always need review" in r for r in run["decisions"][0]["reasons"])


def test_failed_risk_check_holds_for_review(api_client, brand, ai, monkeypatch):
    configure(api_client, brand["id"], max_posts_per_run=1)
    original = ai.generate

    async def flaky(request):
        if "Assess this" in request.prompt:
            raise AIProviderError("AI_PROVIDER_ERROR", "overloaded", retryable=True)
        return await original(request)

    monkeypatch.setattr(ai, "generate", flaky)
    run, _ = run_cycle(api_client, brand["id"])
    assert run["posts_for_review"] == 1 and "risk check couldn't run" in run["decisions"][0]["reasons"][-1]


def test_research_is_refreshed_when_stale(api_client, brand, ai):
    from tests.conftest import add_source

    add_source(api_client, brand["id"], url="https://example.com/feed")
    configure(api_client, brand["id"], max_posts_per_run=1)
    run, _ = run_cycle(api_client, brand["id"])
    assert run["research_queued"] == 1
    run2, _ = run_cycle(api_client, brand["id"])
    assert run2["research_queued"] == 0  # already queued and still pending


def test_cycle_without_ideas_or_research_fails_clearly(api_client, brand, ai):
    with sync_session() as db:
        for item in db.scalars(select(ResearchItem)).all():
            db.delete(item)
    configure(api_client, brand["id"], max_posts_per_run=1)
    run, _ = run_cycle(api_client, brand["id"])
    assert run["status"] == "failed" and "content ideas" in run["error"]


def test_tick_runs_due_brands_once_a_day(api_client, brand):
    configure(api_client, brand["id"])
    assert autopilot_service.tick() == {"queued": 1}
    assert autopilot_service.tick() == {"queued": 0}
    with sync_session() as db:
        settings = db.scalar(select(AutopilotSettings))
        settings.last_run_at = datetime.now(UTC) - timedelta(hours=24)
    # The previous queued run is still active, so it isn't doubled up.
    assert autopilot_service.tick() == {"queued": 0}


def test_autopilot_is_private(api_client, brand):
    configure(api_client, brand["id"], max_posts_per_run=1)
    run = api_client.post("/api/v1/autopilot/run", json={"brand_id": brand["id"]}).json()
    sign_up(api_client, "intruder@example.com")
    assert api_client.get("/api/v1/autopilot/settings", params={"brand_id": brand["id"]}).status_code == 404
    assert api_client.patch("/api/v1/autopilot/settings", params={"brand_id": brand["id"]}, json={"mode": "off"}).status_code == 404
    assert api_client.get(f"/api/v1/autopilot/runs/{run['id']}").status_code == 404
    assert api_client.post("/api/v1/autopilot/run", json={"brand_id": brand["id"]}).status_code == 404

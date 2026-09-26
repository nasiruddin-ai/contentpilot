import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from app.ai.prompts.strategy import PromptSource, PromptTopic, opportunity_prompt
from app.core.database import sync_session
from app.integrations import facebook, x
from app.models import ContentOpportunity, Post
from app.models.analytics import PostMetric
from app.services import analytics_service, publishing_service
from app.services.analytics_service import ScoredPost, engagement_score, group_report, learn, weights_for
from tests.conftest import sign_up

NOW = datetime(2026, 9, 26, tzinfo=UTC)


# --- Scoring and learning (pure) ---------------------------------------------------------


def test_weights_follow_the_brand_goal():
    assert weights_for(["Brand awareness"])["clicks"] == 2
    assert weights_for(["Lead generation"])["clicks"] == 5
    assert weights_for(["More website traffic"])["clicks"] == 5


def test_engagement_score_is_weighted_not_just_likes():
    w = weights_for([])
    assert engagement_score({"likes": 10}, w) == 10
    assert engagement_score({"likes": 10, "comments": 2, "shares": 1, "clicks": 3}, w) == 10 + 6 + 4 + 6
    assert engagement_score({"likes": None, "comments": None}, w) == 0


def scored(score, *, pillar="educational", fmt="text_post", topic="clarity", platform="facebook", days_ago=1):
    return ScoredPost(
        post_id=uuid.uuid4(), platform=platform, content_type=fmt, hook="h", published_at=NOW - timedelta(days=days_ago),
        external_post_id="x", topic=topic, content_pillar=pillar, likes=score, comments=None, shares=None, clicks=None,
        impressions=None, engagement_score=score, engagement_rate=None, synced_at=NOW,
    )


def test_group_report_compares_with_brand_average():
    posts = [scored(100, pillar="educational")] * 3 + [scored(10, pillar="promotion")] * 3 + [scored(50, pillar="story")] * 2
    rows = {r["content_pillar"]: r for r in group_report(posts, "content_pillar")}
    assert rows["educational"]["vs_brand_average"] == "above_average"
    assert rows["promotion"]["vs_brand_average"] == "below_average"
    assert rows["story"]["vs_brand_average"] == "not_enough_data"  # only 2 posts
    assert rows["educational"]["posts"] == 3 and rows["educational"]["avg_engagement_score"] == 100.0


def test_learning_needs_enough_posts_and_is_bounded():
    assert learn([scored(100)] * 4).summary_text == ""
    posts = [scored(100, pillar="educational", fmt="carousel")] * 4 + [scored(2, pillar="promotion", fmt="text_post")] * 4
    learning = learn(posts)
    assert learning.posts_scored == 8
    assert "Strong pillars: educational" in learning.summary_text
    assert "Weak pillars: promotion" in learning.summary_text
    assert learning.boosts[("content_pillar", "educational")] == 10  # capped
    assert learning.boosts[("content_pillar", "promotion")] == -10
    assert learning.boosts[("content_type", "carousel")] == 10


def test_learning_text_goes_into_the_strategy_prompt():
    topic = PromptTopic(ref="T1", name="clarity", trend="new", items_7d=1, source_count=1, sources=[PromptSource("S1", "t", "s", None)])
    assert "WHAT HAS WORKED" not in opportunity_prompt("Brand: X", [topic], 3)
    assert "WHAT HAS WORKED (last 90" in opportunity_prompt("Brand: X", [topic], 3, learn([scored(100)] * 3 + [scored(1, pillar="promotion")] * 3).summary_text)


# --- Fake Facebook Graph for metrics ---------------------------------------------------------


class MetricsGraph:
    def __init__(self):
        self.user_content = True  # pages_read_user_content granted
        self.insights = True  # read_insights granted
        self.deleted: set[str] = set()
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/v25.0")
        q = dict(request.url.params)
        if path == "/oauth/access_token":
            return httpx.Response(200, json={"access_token": "fb-long", "expires_in": 5183944})
        if path == "/me/permissions":
            return httpx.Response(200, json={"data": [{"permission": p, "status": "granted"} for p in facebook.SCOPES]})
        if path == "/me/accounts":
            return httpx.Response(200, json={"data": [{"id": "111", "name": "Easy Garden", "access_token": "fb-page", "tasks": ["CREATE_CONTENT"]}]})
        if path == "/111/feed":
            return httpx.Response(200, json={"id": f"111_{len(self.requests)}"})
        post_id = path.strip("/").split("/")[0]
        if post_id in self.deleted:
            return httpx.Response(400, json={"error": {"message": "Object does not exist", "type": "GraphMethodException", "code": 10}})
        if path.endswith("/insights"):
            if not self.insights:
                return httpx.Response(200, json={"data": []})
            return httpx.Response(200, json={"data": [
                {"name": "post_clicks", "values": [{"value": 7}]},
                {"name": "post_reactions_by_type_total", "values": [{"value": {"like": 4, "love": 1}}]},
            ], "paging": {"next": "https://graph.facebook.com/x?access_token=SHOULD-NOT-BE-STORED"}})
        fields = q.get("fields", "")
        if "comments" in fields:
            if not self.user_content:
                return httpx.Response(400, json={"error": {"message": "requires pages_read_user_content", "type": "OAuthException", "code": 10}})
            return httpx.Response(200, json={"comments": {"summary": {"total_count": 3}}, "reactions": {"summary": {"total_count": 5}}})
        return httpx.Response(200, json={"id": post_id, "shares": {"count": 2}})


@pytest.fixture
def graph(monkeypatch):
    fake = MetricsGraph()
    monkeypatch.setattr(facebook, "transport_factory", lambda: httpx.MockTransport(fake.handle))
    return fake


async def test_facebook_metrics_with_full_permissions(graph):
    m = await facebook.FacebookClient("fb-page").post_metrics("111_1")
    assert (m.likes, m.comments, m.shares, m.clicks) == (5, 3, 2, 7)
    assert m.reactions_by_type == {"like": 4, "love": 1} and m.missing_permissions == []


async def test_facebook_metrics_degrade_without_permissions(graph):
    graph.user_content = False
    graph.insights = False
    m = await facebook.FacebookClient("fb-page").post_metrics("111_1")
    assert m.shares == 2 and m.likes is None and m.comments is None and m.clicks is None
    assert sorted(m.missing_permissions) == ["pages_read_user_content", "read_insights"]


async def test_x_metrics_mapping(monkeypatch):
    def handle(request):
        assert dict(request.url.params)["tweet.fields"] == "public_metrics"
        return httpx.Response(200, json={"data": [{"id": "9", "public_metrics": {"like_count": 4, "reply_count": 1, "retweet_count": 2, "quote_count": 1, "impression_count": 300}}]})

    monkeypatch.setattr(x, "transport_factory", lambda: httpx.MockTransport(handle))
    out = await x.XClient("tok").post_metrics(["9", "missing"])
    assert out["9"]["likes"] == 4 and out["9"]["comments"] == 1 and out["9"]["shares"] == 3 and out["9"]["impressions"] == 300
    assert "missing" not in out
    assert await x.XClient("tok").post_metrics([]) == {}


# --- Sync + reports, end to end ------------------------------------------------------------------


@pytest.fixture
def brand(api_client, graph, monkeypatch):
    monkeypatch.setattr(publishing_service, "enqueue_publish", lambda post_id: None)
    monkeypatch.setattr(analytics_service, "enqueue_sync", lambda sync_id: None)
    sign_up(api_client, "owner@example.com")
    brand = api_client.post("/api/v1/brands", json={"name": "Easy Garden", "goals": ["Lead generation"]}).json()
    url = api_client.post("/api/v1/social/facebook/connect", json={"brand_id": brand["id"]}).json()["authorization_url"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    assert api_client.get("/api/v1/social/facebook/callback", params={"code": "c", "state": state}).status_code == 200
    return brand


def published_post(brand_id: str, external_id: str, *, pillar="educational", fmt="text_post", topic="clarity", days_ago=2, platform="facebook") -> uuid.UUID:
    with sync_session() as db:
        idea = ContentOpportunity(
            brand_id=uuid.UUID(brand_id), topic=topic, angle="a", why_now="w", audience="au", recommended_format=fmt,
            recommended_platforms=[platform], content_pillar=pillar, source_ids=[], relevance_score=50, freshness_score=50,
            brand_fit_score=50, novelty_score=50, priority_score=50, embedding=[0.0] * 768,
        )
        db.add(idea)
        db.flush()
        post = Post(
            brand_id=uuid.UUID(brand_id), opportunity_id=idea.id, platform=platform, content_type=fmt, hook=f"Post {external_id}",
            body="b", status="published", external_post_id=external_id, published_at=datetime.now(UTC) - timedelta(days=days_ago),
        )
        db.add(post)
        db.flush()
        return post.id


def run_sync(client, brand_id):
    response = client.post("/api/v1/analytics/sync", json={"brand_id": brand_id})
    assert response.status_code == 202, response.text
    sync_id = response.json()["id"]
    result = analytics_service.execute_sync(uuid.UUID(sync_id))
    return sync_id, result


def test_sync_stores_snapshots_and_reports(api_client, brand, graph):
    a = published_post(brand["id"], "111_1", pillar="educational")
    published_post(brand["id"], "111_2", pillar="promotion", fmt="carousel", topic="offers")
    sync_id, result = run_sync(api_client, brand["id"])
    assert result["status"] == "succeeded" and result["synced"] == 2 and result["failed"] == 0

    stored = api_client.get(f"/api/v1/analytics/syncs/{sync_id}").json()
    assert stored["status"] == "succeeded" and stored["posts_synced"] == 2

    with sync_session() as db:
        metric = db.scalar(select(PostMetric).where(PostMetric.post_id == a))
        # Lead-generation goal: clicks weigh 5. likes 5 + comments 3*3 + shares 2*4 + clicks 7*5
        assert metric.engagement_score == 5 + 9 + 8 + 35
        assert metric.engagement_rate is None and metric.impressions is None
        assert "SHOULD-NOT-BE-STORED" not in str(metric.raw)

    overview = api_client.get("/api/v1/analytics/overview", params={"brand_id": brand["id"]}).json()
    assert (overview["posts_published"], overview["posts_with_metrics"]) == (2, 2)
    assert overview["likes"] == 10 and overview["clicks"] == 14 and overview["impressions"] is None
    assert overview["best_post"]["post_id"] == str(a) or overview["best_post"]["engagement_score"] == 57
    assert any("impressions or reach" in n for n in overview["notes"])

    posts = api_client.get("/api/v1/analytics/posts", params={"brand_id": brand["id"]}).json()
    assert len(posts) == 2 and posts[0]["published_url"].startswith("https://www.facebook.com/")

    platforms = api_client.get("/api/v1/analytics/platforms", params={"brand_id": brand["id"]}).json()
    assert platforms[0]["platform"] == "facebook" and platforms[0]["posts"] == 2

    topics = api_client.get("/api/v1/analytics/topics", params={"brand_id": brand["id"]}).json()
    assert {t["topic"] for t in topics["topics"]} == {"clarity", "offers"}
    assert topics["learning_active"] is False  # only 2 posts


def test_resync_keeps_history_and_uses_latest(api_client, brand, graph):
    post_id = published_post(brand["id"], "111_1")
    run_sync(api_client, brand["id"])
    run_sync(api_client, brand["id"])
    with sync_session() as db:
        assert db.scalar(select(PostMetric.post_id).where(PostMetric.post_id == post_id)) is not None
        assert len(db.scalars(select(PostMetric).where(PostMetric.post_id == post_id)).all()) == 2
    assert api_client.get("/api/v1/analytics/overview", params={"brand_id": brand["id"]}).json()["posts_with_metrics"] == 1


def test_missing_permissions_are_reported_not_fatal(api_client, brand, graph):
    graph.user_content = False
    published_post(brand["id"], "111_1")
    _, result = run_sync(api_client, brand["id"])
    assert result["synced"] == 1
    overview = api_client.get("/api/v1/analytics/overview", params={"brand_id": brand["id"]}).json()
    assert overview["missing_permissions"] == ["pages_read_user_content"]
    assert any("pages_read_user_content" in n for n in overview["notes"])
    assert overview["likes"] == 5  # falls back to reactions-by-type from insights


def test_deleted_post_is_skipped(api_client, brand, graph):
    published_post(brand["id"], "111_1")
    published_post(brand["id"], "111_gone")
    graph.deleted.add("111_gone")
    _, result = run_sync(api_client, brand["id"])
    assert (result["synced"], result["failed"]) == (1, 1)


def test_linkedin_posts_are_skipped_with_a_reason(api_client, brand, graph):
    published_post(brand["id"], "urn:li:share:1", platform="linkedin")
    _, result = run_sync(api_client, brand["id"])
    assert "linkedin" in result["skipped"] and "approved partners" in result["skipped"]["linkedin"]


def test_sync_guards(api_client, graph):
    sign_up(api_client, "nobody@example.com")
    brand_id = api_client.post("/api/v1/brands", json={"name": "Unconnected"}).json()["id"]
    response = api_client.post("/api/v1/analytics/sync", json={"brand_id": brand_id})
    assert response.status_code == 409 and response.json()["error"]["code"] == "ACCOUNT_NOT_CONNECTED"


def test_learning_feeds_opportunity_priority(api_client, brand, graph, monkeypatch):
    """Five scored posts: educational carousels win, so those get a boost next time."""
    for i in range(3):
        published_post(brand["id"], f"111_edu{i}", pillar="educational", fmt="carousel")
    for i in range(3):
        published_post(brand["id"], f"111_promo{i}", pillar="promotion", fmt="text_post")
    graph.insights = True

    def per_post(request):  # promotion posts get nothing, educational posts get the full numbers
        path = request.url.path
        if "promo" in path and not path.endswith("/insights"):
            return httpx.Response(200, json={"id": "p", "shares": {"count": 0}, "comments": {"summary": {"total_count": 0}}, "reactions": {"summary": {"total_count": 0}}})
        if "promo" in path:
            return httpx.Response(200, json={"data": []})
        return graph.handle(request)

    monkeypatch.setattr(facebook, "transport_factory", lambda: httpx.MockTransport(per_post))
    run_sync(api_client, brand["id"])

    with sync_session() as db:
        learning = analytics_service.learning_for_brand(db, uuid.UUID(brand["id"]))
    assert learning.posts_scored == 6
    assert learning.boosts[("content_pillar", "educational")] > 0
    assert learning.boosts[("content_pillar", "promotion")] < 0
    assert learning.boosts[("content_type", "carousel")] > 0
    topics = api_client.get("/api/v1/analytics/topics", params={"brand_id": brand["id"]}).json()
    assert topics["learning_active"] is True and "Strong pillars: educational" in topics["learning_summary"]


def test_analytics_are_private(api_client, brand, graph):
    published_post(brand["id"], "111_1")
    sync_id, _ = run_sync(api_client, brand["id"])
    sign_up(api_client, "intruder@example.com")
    for path in ("/api/v1/analytics/overview", "/api/v1/analytics/posts", "/api/v1/analytics/platforms", "/api/v1/analytics/topics"):
        assert api_client.get(path, params={"brand_id": brand["id"]}).status_code == 404
    assert api_client.get(f"/api/v1/analytics/syncs/{sync_id}").status_code == 404
    assert api_client.post("/api/v1/analytics/sync", json={"brand_id": brand["id"]}).status_code == 404

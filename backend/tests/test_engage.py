"""Engagement: polling Page comments/messages, AI reply drafts, approval and auto mode."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest

from app.ai.base import EmbeddingResult, TextResult
from app.ai.service import AIService, ModelTier
from app.core.database import sync_session
from app.integrations import facebook
from app.models.engage import EngageSettings, InboxItem, InboxKind
from app.services import engage_service, publishing_service, topic_service
from tests.conftest import sign_up
from tests.test_facebook import FakeGraph, connect, make_post, publish

SETTINGS, INBOX = "/api/v1/engage/settings", "/api/v1/engage/inbox"


def graph_time(delta: timedelta = timedelta(0)) -> str:
    return (datetime.now(UTC) + delta).strftime("%Y-%m-%dT%H:%M:%S+0000")


class EngageGraph(FakeGraph):
    """Adds comments, conversations and reply endpoints to the OAuth/publish fake."""

    def __init__(self):
        super().__init__()
        self.post_comments: dict[str, list[dict]] = {}
        self.convos: list[dict] = []
        self.replies: list[tuple[str, str]] = []
        self.messages: list[tuple[str, str]] = []
        self.reply_error: dict | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v25.0")
        if request.method == "GET" and path.endswith("/comments"):
            self.requests.append(request)
            post_id = path.strip("/").removesuffix("/comments")
            return httpx.Response(200, json={"data": self.post_comments.get(post_id, [])})
        if request.method == "POST" and path.endswith("/comments"):
            self.requests.append(request)
            if self.reply_error:
                return httpx.Response(400, json={"error": self.reply_error})
            comment_id = path.strip("/").removesuffix("/comments")
            message = parse_qs(request.content.decode())["message"][0]
            self.replies.append((comment_id, message))
            return httpx.Response(200, json={"id": f"reply-{len(self.replies)}"})
        if path == "/111/conversations":
            self.requests.append(request)
            return httpx.Response(200, json={"data": self.convos})
        if request.method == "POST" and path == "/111/messages":
            self.requests.append(request)
            body = json.loads(request.content)
            assert body["messaging_type"] == "RESPONSE"
            self.messages.append((body["recipient"]["id"], body["message"]["text"]))
            return httpx.Response(200, json={"recipient_id": body["recipient"]["id"], "message_id": f"m-{len(self.messages)}"})
        return super().handle(request)


class EngageAI:
    """Reply agent: praise gets a safe reply, questions need a person, spam gets none."""

    name = "scripted"

    async def generate(self, request):
        assert "INCOMING" in request.prompt
        incoming = request.prompt.split('<source id="incoming">', 1)[1].split("</source>")[0].lower()
        if "buy followers" in incoming:
            body = {"category": "spam", "should_reply": False, "needs_human": False, "reply": "", "reasons": ["spam"]}
        elif "price" in incoming or "?" in incoming:
            body = {
                "category": "question",
                "should_reply": True,
                "needs_human": True,
                "reply": "Great question! We'll get back to you shortly.",
                "reasons": ["not covered by the business facts"],
            }
        else:
            body = {"category": "praise", "should_reply": True, "needs_human": False, "reply": "Thank you so much!", "reasons": []}
        return TextResult(text=json.dumps(body), model=request.model, input_tokens=5, output_tokens=5)

    async def embed(self, texts, *, model, dimensions):
        return EmbeddingResult(vectors=[[1.0] + [0.0] * (dimensions - 1) for _ in texts], model=model, input_tokens=1)


@pytest.fixture
def graph(monkeypatch):
    fake = EngageGraph()
    monkeypatch.setattr(facebook, "transport_factory", lambda: httpx.MockTransport(fake.handle))
    return fake


@pytest.fixture
def ai(monkeypatch):
    service = AIService(
        EngageAI(),
        models={ModelTier.FAST: "fast", ModelTier.QUALITY: "quality"},
        embedding_model="e",
        embedding_dimensions=768,
        recorder=lambda r: None,
        backoff_seconds=0,
        max_attempts=1,
    )
    monkeypatch.setattr(topic_service, "ai_service_factory", lambda: service)


@pytest.fixture
def brand(api_client, graph, ai, monkeypatch):
    monkeypatch.setattr(publishing_service, "enqueue_publish", lambda post_id: None)
    monkeypatch.setattr(publishing_service, "enqueue_retry", lambda post_id, delay: None)
    monkeypatch.setattr(engage_service, "enqueue_poll", lambda brand_id: None)
    sign_up(api_client, "owner@example.com")
    brand = api_client.post("/api/v1/brands", json={"name": "Squareko"}).json()
    assert connect(api_client, brand["id"]).status_code == 200
    return brand


def published_post(api_client, brand_id: str) -> str:
    """Publishes one post through the fake Graph; returns its external ID."""
    post_id = make_post(brand_id)
    result = publish(api_client, post_id)
    assert result["status"] == "published"
    return result["external_post_id"]


def enable(api_client, brand_id: str, mode: str = "review", **extra) -> dict:
    response = api_client.patch(SETTINGS, params={"brand_id": brand_id}, json={"mode": mode, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def comment(id: str, text: str, *, author=("42", "Rahim"), when=timedelta(minutes=1), parent=None) -> dict:
    payload = {"id": id, "message": text, "created_time": graph_time(when), "from": {"id": author[0], "name": author[1]}}
    if parent:
        payload["parent"] = {"id": parent}
    return payload


# --- Settings ------------------------------------------------------------------------------


def test_settings_defaults_and_activation(api_client, brand):
    settings = api_client.get(SETTINGS, params={"brand_id": brand["id"]}).json()
    assert (settings["mode"], settings["reply_to_comments"], settings["reply_to_messages"]) == ("off", True, False)
    assert settings["activated_at"] is None

    settings = enable(api_client, brand["id"], business_facts="We deliver in Dhaka.")
    assert settings["mode"] == "review" and settings["activated_at"] is not None
    assert settings["business_facts"] == "We deliver in Dhaka."


def test_turning_on_requires_facebook(api_client, graph, ai, monkeypatch):
    monkeypatch.setattr(engage_service, "enqueue_poll", lambda brand_id: None)
    sign_up(api_client, "other@example.com")
    brand = api_client.post("/api/v1/brands", json={"name": "No FB"}).json()
    response = api_client.patch(SETTINGS, params={"brand_id": brand["id"]}, json={"mode": "review"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ACCOUNT_NOT_CONNECTED"


# --- Polling comments ---------------------------------------------------------------------


def test_poll_drafts_replies_and_dedupes(api_client, brand, graph):
    external = published_post(api_client, brand["id"])
    enable(api_client, brand["id"])
    graph.post_comments[external] = [
        comment("c1", "Lovely garden inspiration!"),
        comment("c2", "our own page note", author=("111", "Squareko")),  # the Page itself
        comment("c3", "old praise", when=timedelta(days=-2)),  # before activation
        comment("c4", "nested reply", parent="c1"),
        comment("c5", "What's the price of the rose pot?"),
    ]

    result = engage_service.poll_brand(uuid.UUID(brand["id"]))
    assert (result["review"], result["auto_sent"], result["skipped"]) == (2, 0, 0)
    assert graph.replies == []  # review mode never sends

    items = api_client.get(INBOX, params={"brand_id": brand["id"], "status": "review"}).json()
    by_text = {i["text"]: i for i in items}
    assert set(by_text) == {"Lovely garden inspiration!", "What's the price of the rose pot?"}
    assert by_text["Lovely garden inspiration!"]["draft_reply"] == "Thank you so much!"
    assert by_text["What's the price of the rose pot?"]["assessment"]["needs_human"] is True
    assert by_text["Lovely garden inspiration!"]["post_id"] is not None

    # Same comments again: nothing new.
    assert engage_service.poll_brand(uuid.UUID(brand["id"]))["review"] == 0
    assert len(api_client.get(INBOX, params={"brand_id": brand["id"]}).json()) == 2

    titles = [n["title"] for n in api_client.get("/api/v1/notifications").json()]
    assert any("waiting for your review" in t for t in titles)


def test_auto_mode_sends_safe_replies_only(api_client, brand, graph):
    external = published_post(api_client, brand["id"])
    enable(api_client, brand["id"], mode="auto")
    graph.post_comments[external] = [
        comment("c1", "Lovely post, thanks!"),
        comment("c2", "What's the price?"),
        comment("c3", "buy followers at spam.example"),
    ]

    result = engage_service.poll_brand(uuid.UUID(brand["id"]))
    assert (result["review"], result["auto_sent"], result["skipped"]) == (1, 1, 1)
    assert graph.replies == [("c1", "Thank you so much!")]

    items = {i["text"]: i for i in api_client.get(INBOX, params={"brand_id": brand["id"]}).json()}
    sent = items["Lovely post, thanks!"]
    assert (sent["status"], sent["sent_automatically"], sent["sent_reply"]) == ("sent", True, "Thank you so much!")
    assert items["What's the price?"]["status"] == "review"
    assert items["buy followers at spam.example"]["status"] == "skipped"


def test_auto_mode_respects_daily_cap(api_client, brand, graph):
    external = published_post(api_client, brand["id"])
    enable(api_client, brand["id"], mode="auto", max_replies_per_day=1)
    graph.post_comments[external] = [comment("c1", "Lovely!"), comment("c2", "Wonderful!")]

    result = engage_service.poll_brand(uuid.UUID(brand["id"]))
    assert result["auto_sent"] == 1 and result["review"] == 1
    assert len(graph.replies) == 1


# --- Approving, editing, dismissing --------------------------------------------------------


def seeded_item(api_client, brand, graph, text="Lovely!") -> dict:
    external = published_post(api_client, brand["id"])
    enable(api_client, brand["id"])
    graph.post_comments[external] = [comment("c1", text)]
    engage_service.poll_brand(uuid.UUID(brand["id"]))
    (item,) = api_client.get(INBOX, params={"brand_id": brand["id"], "status": "review"}).json()
    return item


def test_send_uses_edited_reply(api_client, brand, graph):
    item = seeded_item(api_client, brand, graph)
    response = api_client.post(f"{INBOX}/{item['id']}/send", json={"reply": "Thanks! New stock lands Friday."})
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["sent_reply"], body["sent_automatically"]) == ("sent", "Thanks! New stock lands Friday.", False)
    assert graph.replies == [("c1", "Thanks! New stock lands Friday.")]

    # A second send is refused.
    assert api_client.post(f"{INBOX}/{item['id']}/send", json={}).status_code == 409


def test_dismiss(api_client, brand, graph):
    item = seeded_item(api_client, brand, graph)
    assert api_client.post(f"{INBOX}/{item['id']}/dismiss").json()["status"] == "dismissed"
    assert graph.replies == []


def test_missing_reply_permission_surfaces(api_client, brand, graph):
    item = seeded_item(api_client, brand, graph)
    graph.reply_error = {"message": "(#200) Requires pages_manage_engagement", "code": 200, "type": "OAuthException"}
    response = api_client.post(f"{INBOX}/{item['id']}/send", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"
    (row,) = api_client.get(INBOX, params={"brand_id": brand["id"], "status": "review"}).json()
    assert "PERMISSION_DENIED" in row["error"]


# --- Messenger -----------------------------------------------------------------------------


def test_messages_drafted_and_sent(api_client, brand, graph):
    enable(api_client, brand["id"], reply_to_messages=True, reply_to_comments=False)
    graph.convos = [
        {  # newest message from a person → answer it
            "id": "t_1",
            "updated_time": graph_time(),
            "messages": {"data": [{"id": "m1", "message": "Do you open on Friday?", "created_time": graph_time(timedelta(minutes=1)), "from": {"id": "77", "name": "Karim"}}]},
        },
        {  # the Page already has the last word → leave it alone
            "id": "t_2",
            "updated_time": graph_time(),
            "messages": {"data": [{"id": "m2", "message": "Yes we do!", "created_time": graph_time(), "from": {"id": "111", "name": "Squareko"}}]},
        },
    ]
    result = engage_service.poll_brand(uuid.UUID(brand["id"]))
    assert result["review"] == 1

    (item,) = api_client.get(INBOX, params={"brand_id": brand["id"], "status": "review"}).json()
    assert item["kind"] == "message" and item["author_name"] == "Karim"
    sent = api_client.post(f"{INBOX}/{item['id']}/send", json={"reply": "Yes, 9 to 5!"})
    assert sent.status_code == 200
    assert graph.messages == [("77", "Yes, 9 to 5!")]


def test_message_outside_24h_window_is_refused(api_client, brand, graph):
    enable(api_client, brand["id"], reply_to_messages=True)
    with sync_session() as db:
        db.add(
            InboxItem(
                brand_id=uuid.UUID(brand["id"]),
                kind=InboxKind.MESSAGE,
                external_id="m-old",
                thread_external_id="t_9",
                author_external_id="88",
                author_name="Old",
                text="hello?",
                received_at=datetime.now(UTC) - timedelta(hours=25),
                draft_reply="Hi!",
            )
        )
    items = api_client.get(INBOX, params={"brand_id": brand["id"], "status": "review"}).json()
    old = next(i for i in items if i["external_id"] == "m-old")
    response = api_client.post(f"{INBOX}/{old['id']}/send", json={})
    assert response.status_code == 409 and response.json()["error"]["code"] == "WINDOW_CLOSED"
    assert graph.messages == []


# --- Scheduling and small units -------------------------------------------------------------


def test_tick_queues_due_brands(api_client, brand, monkeypatch):
    enable(api_client, brand["id"])
    queued = []
    monkeypatch.setattr(engage_service, "enqueue_poll", queued.append)
    with sync_session() as db:
        settings = db.query(EngageSettings).one()
        settings.last_polled_at = None
    assert engage_service.tick() == {"queued": 1}
    assert queued == [uuid.UUID(brand["id"])]
    # Just polled: not due again.
    assert engage_service.tick() == {"queued": 0}


def test_parse_graph_time():
    parsed = engage_service.parse_graph_time("2026-09-26T10:00:00+0000")
    assert parsed == datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
    assert engage_service.parse_graph_time("nonsense") is None


def test_inbox_is_private(api_client, brand, graph):
    item = seeded_item(api_client, brand, graph)
    sign_up(api_client, "intruder@example.com")
    assert api_client.get(INBOX, params={"brand_id": brand["id"]}).status_code == 404
    assert api_client.post(f"{INBOX}/{item['id']}/send", json={}).status_code == 404


def test_deleted_facebook_post_is_skipped(api_client, brand, graph, monkeypatch):
    """A post deleted on Facebook must not abort the poll for the remaining posts."""
    from app.models import Post

    gone, still_there = "111_gone", "111_live"
    for external in (gone, still_there):
        post_id = make_post(brand["id"])
        with sync_session() as db:
            db.query(Post).filter(Post.id == post_id).update(
                {"external_post_id": external, "status": "published", "published_at": datetime.now(UTC)}
            )
    enable(api_client, brand["id"])

    original = graph.handle

    def handle(request):
        if request.method == "GET" and request.url.path.endswith(f"{gone}/comments"):
            return httpx.Response(400, json={"error": {"message": "Unsupported get request.", "code": 100}})
        return original(request)

    monkeypatch.setattr(facebook, "transport_factory", lambda: httpx.MockTransport(handle))
    graph.post_comments[still_there] = [comment("c-live", "Still lovely!")]

    result = engage_service.poll_brand(uuid.UUID(brand["id"]))
    assert result["review"] == 1 and result["error"] is None

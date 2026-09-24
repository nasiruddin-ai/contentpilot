import hashlib
import hmac
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import text, update

from app.core import crypto
from app.core.database import sync_session
from app.integrations import facebook
from app.models import Post
from app.services import publishing_service
from tests.conftest import sign_up
from tests.test_social import make_post as social_make_post

SHORT, LONG, PAGE_TOKEN = "fb-short-user-token", "fb-long-user-token", "fb-page-token-never-expires"


def proof(token: str) -> str:
    return hmac.new(b"test-facebook-secret", token.encode(), hashlib.sha256).hexdigest()


class FakeGraph:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.permissions = ["pages_show_list", "pages_manage_posts", "pages_read_engagement"]
        self.pages = [{"id": "111", "name": "Squareko", "access_token": PAGE_TOKEN, "tasks": ["CREATE_CONTENT", "MANAGE"]}]
        self.feed_error: dict | None = None
        self.photo_count = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, query = request.url.path, dict(request.url.params)
        assert path.startswith("/v25.0/")
        path = path.removeprefix("/v25.0")
        token = query.get("access_token")
        if token:
            # Every authenticated call is signed with appsecret_proof.
            assert query["appsecret_proof"] == proof(token)
        if path == "/oauth/access_token":
            assert query["client_secret"] == "test-facebook-secret"
            if query.get("grant_type") == "fb_exchange_token":
                assert query["fb_exchange_token"] == SHORT
                return httpx.Response(200, json={"access_token": LONG, "token_type": "bearer", "expires_in": 5183944})
            assert query["code"] == "fb-code"
            return httpx.Response(200, json={"access_token": SHORT, "token_type": "bearer", "expires_in": 5400})
        if path == "/me/permissions":
            return httpx.Response(200, json={"data": [{"permission": p, "status": "granted"} for p in self.permissions]})
        if path == "/me/accounts":
            assert token == LONG
            return httpx.Response(200, json={"data": self.pages})
        if path == "/111/photos":
            assert token == PAGE_TOKEN and b'name="published"' in request.content and b"false" in request.content
            self.photo_count += 1
            return httpx.Response(200, json={"id": f"photo{self.photo_count}"})
        if path == "/111/feed":
            if self.feed_error:
                return httpx.Response(400 if self.feed_error.get("code") != 2 else 500, json={"error": self.feed_error})
            return httpx.Response(200, json={"id": "111_987654321"})
        return httpx.Response(404, json={"error": {"message": "not found", "code": 803}})

    def to(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == f"/v25.0{path}"]


@pytest.fixture
def graph(monkeypatch):
    fake = FakeGraph()
    monkeypatch.setattr(facebook, "transport_factory", lambda: httpx.MockTransport(fake.handle))
    return fake


@pytest.fixture
def brand(api_client, graph, monkeypatch):
    monkeypatch.setattr(publishing_service, "enqueue_publish", lambda post_id: None)
    monkeypatch.setattr(publishing_service, "enqueue_retry", lambda post_id, delay: None)
    sign_up(api_client, "owner@example.com")
    return api_client.post("/api/v1/brands", json={"name": "Squareko"}).json()


def connect(client, brand_id):
    url = client.post("/api/v1/social/facebook/connect", json={"brand_id": brand_id}).json()["authorization_url"]
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert f"{parts.netloc}{parts.path}" == "www.facebook.com/v25.0/dialog/oauth"
    assert query["scope"] == ["pages_show_list,pages_manage_posts,pages_read_engagement"]
    assert query["client_id"] == ["1234567890"]
    return client.get("/api/v1/social/facebook/callback", params={"code": "fb-code", "state": query["state"][0]})


def make_post(brand_id: str, visual: str | None = None) -> uuid.UUID:
    post_id = uuid.UUID(social_make_post(brand_id, visual=visual))
    with sync_session() as db:
        db.execute(update(Post).where(Post.id == post_id).values(platform="facebook"))
    return post_id


def publish(client, post_id):
    assert client.post(f"/api/v1/publishing/{post_id}/publish").status_code == 202
    return publishing_service.execute_publish(post_id)


def test_connect_stores_non_expiring_page_token(api_client, brand, graph, db_engine):
    page = connect(api_client, brand["id"])
    assert page.status_code == 200 and "Facebook is connected as Squareko" in page.text

    (account,) = api_client.get("/api/v1/social/accounts", params={"brand_id": brand["id"]}).json()
    assert (account["platform"], account["account_name"], account["status"]) == ("facebook", "Squareko", "active")
    assert account["token_expires_at"] is None
    with db_engine.connect() as conn:
        stored, page_id = conn.execute(text("SELECT access_token_encrypted, external_account_id FROM social_accounts")).one()
    assert crypto.decrypt(stored) == PAGE_TOKEN and PAGE_TOKEN not in stored and page_id == "111"


def test_login_for_business_uses_config_id(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "facebook_login_config_id", "cfg-42")
    query = parse_qs(urlsplit(facebook.authorization_url("http://localhost/cb", "s")).query)
    assert query["config_id"] == ["cfg-42"] and query["override_default_response_type"] == ["true"]
    assert "scope" not in query and query["response_type"] == ["code"]


def test_missing_permission_or_page_is_refused(api_client, brand, graph):
    graph.permissions = ["pages_show_list"]
    page = connect(api_client, brand["id"])
    assert page.status_code == 400 and "pages_manage_posts" in page.text

    graph.permissions = ["pages_show_list", "pages_manage_posts"]
    graph.pages = []
    page = connect(api_client, brand["id"])
    assert page.status_code == 400 and "select your Page" in page.text
    assert api_client.get("/api/v1/social/accounts", params={"brand_id": brand["id"]}).json() == []


def test_publish_text_post(api_client, brand, graph):
    connect(api_client, brand["id"])
    post_id = make_post(brand["id"])
    assert publish(api_client, post_id) == {"status": "published", "external_post_id": "111_987654321"}

    (feed,) = graph.to("/111/feed")
    form = parse_qs(feed.content.decode())
    assert form["message"] == ["Clarity sells (really).\n\nOne clear call to action wins.\n\nThoughts?\n\n#WebDesign"]
    assert "attached_media[0]" not in form
    post = api_client.get(f"/api/v1/posts/{post_id}").json()
    assert post["published_url"] == "https://www.facebook.com/111_987654321"


def test_carousel_becomes_multi_photo_post(api_client, brand, graph):
    connect(api_client, brand["id"])
    post_id = make_post(brand["id"], visual="carousel")
    assert publish(api_client, post_id)["status"] == "published"

    assert len(graph.to("/111/photos")) == 1  # the test carousel has one slide (the PDF is skipped)
    form = parse_qs(graph.to("/111/feed")[0].content.decode())
    assert form["attached_media[0]"] == ['{"media_fbid":"photo1"}']


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ({"code": 190, "message": "Error validating access token"}, "RECONNECT_REQUIRED"),
        ({"code": 200, "message": "Permissions error"}, "PERMISSION_DENIED"),
        ({"code": 4, "message": "Application request limit reached"}, "retrying"),
        # A 500 on the create call may still have created the post: flagged, never blindly retried.
        ({"code": 2, "message": "Service temporarily unavailable"}, "PUBLISH_UNCERTAIN"),
    ],
)
def test_graph_errors(api_client, brand, graph, error, expected):
    connect(api_client, brand["id"])
    graph.feed_error = error
    result = publish(api_client, make_post(brand["id"]))
    assert expected in (result["status"], result.get("error"))
    assert PAGE_TOKEN not in str(result)


def test_bad_app_secret_is_not_called_temporary(api_client, brand, graph, monkeypatch):
    original = graph.handle

    def bad_secret(request):
        if request.url.path.endswith("/oauth/access_token"):
            graph.requests.append(request)
            return httpx.Response(400, json={"error": {"message": "Error validating client secret.", "type": "OAuthException", "code": 1}})
        return original(request)

    monkeypatch.setattr(facebook, "transport_factory", lambda: httpx.MockTransport(bad_secret))
    page = connect(api_client, brand["id"])
    assert page.status_code == 502
    assert "Error validating client secret" in page.text and "temporary" not in page.text


def test_request_urls_with_secrets_are_not_logged(caplog):
    import logging

    from app.core.logging import configure_logging

    configure_logging("INFO")
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING

import json
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy import text

from app.core import crypto
from app.core.config import Settings, get_settings
from app.core.database import sync_session
from app.integrations import linkedin
from app.models import Post, SocialAccount, Visual
from app.services import publishing_service
from app.storage import get_storage
from tests.conftest import sign_up

TOKEN = "AQX-real-looking-linkedin-access-token-123"


# --- LinkedIn text format ---------------------------------------------------------------


def test_reserved_characters_are_escaped():
    assert linkedin.escape_text("Hi (there) [x] {y} @z #tag *b* _i_ ~s~ a|b <c> \\") == (
        "Hi \\(there\\) \\[x\\] \\{y\\} \\@z \\#tag \\*b\\* \\_i\\_ \\~s\\~ a\\|b \\<c\\> \\\\"
    )
    assert linkedin.escape_text("Plain text, with punctuation!") == "Plain text, with punctuation!"


def test_commentary_uses_hashtag_templates():
    text = linkedin.commentary("Hook (1)", "Body", "Ask us.", ["Squarespace", "Web_Design"])
    assert text == "Hook \\(1\\)\n\nBody\n\nAsk us.\n\n{hashtag|\\#|Squarespace} {hashtag|\\#|Web\\_Design}"


def test_authorization_url():
    parts = urlsplit(linkedin.authorization_url("http://localhost:8000/api/v1/social/linkedin/callback", "abc"))
    query = parse_qs(parts.query)
    assert parts.netloc == "www.linkedin.com" and parts.path == "/oauth/v2/authorization"
    assert query["scope"] == ["openid profile w_member_social"]
    assert query["state"] == ["abc"] and query["response_type"] == ["code"]
    assert query["client_id"] == ["test-client-id"]


# --- Token encryption --------------------------------------------------------------------------


def test_encryption_round_trip_and_rotation(monkeypatch):
    sealed = crypto.encrypt(TOKEN)
    assert TOKEN not in sealed and crypto.decrypt(sealed) == TOKEN

    old_key = get_settings().token_encryption_key.get_secret_value()
    new_key = Fernet.generate_key().decode()
    monkeypatch.setattr(get_settings(), "token_encryption_key", type(get_settings().token_encryption_key)(f"{new_key},{old_key}"))
    assert crypto.decrypt(sealed) == TOKEN  # old data still readable after rotation

    monkeypatch.setattr(get_settings(), "token_encryption_key", type(get_settings().token_encryption_key)(new_key))
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(sealed)


def test_production_requires_encryption_key():
    with pytest.raises(ValidationError, match="TOKEN_ENCRYPTION_KEY"):
        Settings(app_env="production", jwt_secret="x" * 40, token_encryption_key=None)


# --- A fake LinkedIn --------------------------------------------------------------------------


class FakeLinkedIn:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.scope = "openid,profile,w_member_social"
        self.name = "Jane Doe"
        self.post_status = 201
        self.upload_host = "www.linkedin.com"

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/oauth/v2/accessToken":
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 5184000, "scope": self.scope})
        if path == "/v2/userinfo":
            return httpx.Response(200, json={"sub": "abc123", "name": self.name})
        if path in ("/rest/images", "/rest/documents"):
            kind = "image" if path == "/rest/images" else "document"
            return httpx.Response(
                200, json={"value": {"uploadUrl": f"https://{self.upload_host}/dms-uploads/{kind}", kind: f"urn:li:{kind}:C1"}}
            )
        if path.startswith("/dms-uploads/"):
            return httpx.Response(201)
        if path == "/rest/posts":
            if self.post_status != 201:
                return httpx.Response(self.post_status, json={"message": f"Invalid token {TOKEN}"})
            return httpx.Response(201, headers={"x-restli-id": "urn:li:share:6844785523593134080"})
        return httpx.Response(404)

    def to(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == path]


@pytest.fixture
def fake_linkedin(monkeypatch):
    fake = FakeLinkedIn()
    monkeypatch.setattr(linkedin, "transport_factory", lambda: httpx.MockTransport(fake.handle))
    return fake


@pytest.fixture
def brand(api_client, fake_linkedin, monkeypatch):
    enqueued = []
    monkeypatch.setattr(publishing_service, "enqueue_publish", enqueued.append)
    sign_up(api_client, "owner@example.com")
    brand = api_client.post("/api/v1/brands", json={"name": "Squareko"}).json()
    brand["enqueued"] = enqueued
    return brand


def connect(client, brand_id) -> httpx.Response:
    url = client.post("/api/v1/social/linkedin/connect", json={"brand_id": brand_id}).json()["authorization_url"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    return client.get("/api/v1/social/linkedin/callback", params={"code": "auth-code", "state": state}), state


# --- OAuth ---------------------------------------------------------------------------------------


def test_connect_stores_encrypted_token(api_client, brand, fake_linkedin, db_engine):
    page, state = connect(api_client, brand["id"])
    assert page.status_code == 200 and "connected as Jane Doe" in page.text

    exchange = fake_linkedin.to("/oauth/v2/accessToken")[0]
    form = parse_qs(exchange.content.decode())
    assert form["code"] == ["auth-code"] and form["client_secret"] == ["test-client-secret"]
    assert form["redirect_uri"] == ["http://localhost:8000/api/v1/social/linkedin/callback"]

    accounts = api_client.get("/api/v1/social/accounts", params={"brand_id": brand["id"]})
    assert TOKEN not in accounts.text
    (account,) = accounts.json()
    assert (account["platform"], account["account_name"], account["status"]) == ("linkedin", "Jane Doe", "active")
    assert account["token_expires_at"]

    with db_engine.connect() as conn:
        stored = conn.scalar(text("SELECT access_token_encrypted FROM social_accounts"))
    assert TOKEN not in stored and crypto.decrypt(stored) == TOKEN

    # The state is single-use: replaying the callback fails.
    replay = api_client.get("/api/v1/social/linkedin/callback", params={"code": "auth-code", "state": state})
    assert replay.status_code == 400 and "already used" in replay.text


def test_callback_rejects_bad_state_denial_and_missing_scope(api_client, brand, fake_linkedin):
    forged = api_client.get("/api/v1/social/linkedin/callback", params={"code": "x", "state": "made-up"})
    assert forged.status_code == 400

    url = api_client.post("/api/v1/social/linkedin/connect", json={"brand_id": brand["id"]}).json()["authorization_url"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    denied = api_client.get("/api/v1/social/linkedin/callback", params={"error": "user_cancelled_authorize", "state": state})
    assert denied.status_code == 400 and "wasn" in denied.text

    fake_linkedin.scope = "openid,profile"
    page, _ = connect(api_client, brand["id"])
    assert page.status_code == 400 and "Share on LinkedIn" in page.text
    assert api_client.get("/api/v1/social/accounts", params={"brand_id": brand["id"]}).json() == []


def test_callback_page_escapes_names(api_client, brand, fake_linkedin):
    fake_linkedin.name = "<script>alert(1)</script>"
    page, _ = connect(api_client, brand["id"])
    assert "<script>" not in page.text and "&lt;script&gt;" in page.text


def test_connect_needs_configuration(api_client, brand, monkeypatch):
    monkeypatch.setattr(get_settings(), "linkedin_client_id", "")
    response = api_client.post("/api/v1/social/linkedin/connect", json={"brand_id": brand["id"]})
    assert response.status_code == 503 and response.json()["error"]["code"] == "LINKEDIN_NOT_CONFIGURED"
    assert api_client.post("/api/v1/social/myspace/connect", json={"brand_id": brand["id"]}).status_code == 422


def test_disconnect(api_client, brand, fake_linkedin):
    connect(api_client, brand["id"])
    assert api_client.post("/api/v1/social/linkedin/disconnect", json={"brand_id": brand["id"]}).status_code == 204
    assert api_client.get("/api/v1/social/accounts", params={"brand_id": brand["id"]}).json() == []
    assert api_client.post("/api/v1/social/linkedin/disconnect", json={"brand_id": brand["id"]}).status_code == 404


# --- Publishing ------------------------------------------------------------------------------------


def make_post(brand_id: str, *, status="approved", platform="linkedin", visual: str | None = None) -> str:
    with sync_session() as db:
        visual_id = None
        if visual:
            storage = get_storage()
            prefix = f"visuals/{brand_id}/{uuid.uuid4()}/v1"
            assets = [{"kind": "slide", "index": 1, "key": f"{prefix}/slide-1.png", "url": storage.save(f"{prefix}/slide-1.png", b"PNG", "image/png")}]
            if visual == "carousel":
                assets.append({"kind": "pdf", "index": 0, "key": f"{prefix}/carousel.pdf", "url": storage.save(f"{prefix}/carousel.pdf", b"%PDF", "application/pdf")})
            row = Visual(
                brand_id=uuid.UUID(brand_id),
                visual_type=visual,
                aspect_ratio="4:5",
                status="succeeded",
                alt_text="Navy slide with a white headline.",
                assets=assets,
            )
            db.add(row)
            db.flush()
            visual_id = row.id
        post = Post(
            brand_id=uuid.UUID(brand_id),
            platform=platform,
            content_type="text_post",
            hook="Clarity sells (really).",
            body="One clear call to action wins.",
            cta="Thoughts?",
            hashtags=["WebDesign"],
            status=status,
            visual_id=visual_id,
        )
        db.add(post)
        db.flush()
        return str(post.id)


def publish(client, post_id):
    return client.post(f"/api/v1/publishing/{post_id}/publish")


def test_publish_text_post(api_client, brand, fake_linkedin):
    connect(api_client, brand["id"])
    post_id = make_post(brand["id"])

    response = publish(api_client, post_id)
    assert response.status_code == 202 and response.json()["status"] == "publishing"
    assert brand["enqueued"] == [uuid.UUID(post_id)]
    assert publish(api_client, post_id).status_code == 409  # already on its way

    result = publishing_service.execute_publish(uuid.UUID(post_id))
    assert result == {"status": "published", "external_post_id": "urn:li:share:6844785523593134080"}

    (request,) = fake_linkedin.to("/rest/posts")
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["linkedin-version"] == "202609"
    assert request.headers["x-restli-protocol-version"] == "2.0.0"
    body = json.loads(request.content)
    assert body["author"] == "urn:li:person:abc123"
    assert body["commentary"].startswith("Clarity sells \\(really\\).")
    assert body["visibility"] == "PUBLIC" and "content" not in body

    post = api_client.get(f"/api/v1/posts/{post_id}").json()
    assert post["status"] == "published" and post["published_at"]
    assert post["external_post_id"] == "urn:li:share:6844785523593134080"

    # Running the job again (e.g. redelivered) never posts twice.
    assert publishing_service.execute_publish(uuid.UUID(post_id))["status"] == "skipped"
    assert len(fake_linkedin.to("/rest/posts")) == 1
    assert publish(api_client, post_id).json()["error"]["code"] in {"POST_NOT_PUBLISHABLE", "ALREADY_PUBLISHED"}


def test_carousel_publishes_as_document(api_client, brand, fake_linkedin):
    connect(api_client, brand["id"])
    post_id = make_post(brand["id"], visual="carousel")
    publish(api_client, post_id)
    publishing_service.execute_publish(uuid.UUID(post_id))

    (upload,) = fake_linkedin.to("/dms-uploads/document")
    assert upload.method == "PUT" and upload.content == b"%PDF"
    body = json.loads(fake_linkedin.to("/rest/posts")[0].content)
    assert body["content"]["media"] == {"id": "urn:li:document:C1", "title": "Clarity sells (really)."}


def test_image_publishes_with_alt_text(api_client, brand, fake_linkedin):
    connect(api_client, brand["id"])
    post_id = make_post(brand["id"], visual="quote_card")
    publish(api_client, post_id)
    publishing_service.execute_publish(uuid.UUID(post_id))

    init = json.loads(fake_linkedin.to("/rest/images")[0].content)
    assert init == {"initializeUploadRequest": {"owner": "urn:li:person:abc123"}}
    body = json.loads(fake_linkedin.to("/rest/posts")[0].content)
    assert body["content"]["media"] == {"id": "urn:li:image:C1", "altText": "Navy slide with a white headline."}


def test_token_is_never_sent_to_a_foreign_upload_host(api_client, brand, fake_linkedin):
    connect(api_client, brand["id"])
    fake_linkedin.upload_host = "evil.example"
    post_id = make_post(brand["id"], visual="quote_card")
    publish(api_client, post_id)

    result = publishing_service.execute_publish(uuid.UUID(post_id))
    assert result["status"] == "failed" and result["error"] == "UPLOAD_FAILED"
    assert not any(r.url.host == "evil.example" for r in fake_linkedin.requests)


def test_expired_login_needs_reconnect(api_client, brand, fake_linkedin):
    connect(api_client, brand["id"])
    fake_linkedin.post_status = 401
    post_id = make_post(brand["id"])
    publish(api_client, post_id)

    result = publishing_service.execute_publish(uuid.UUID(post_id))
    assert result["error"] == "RECONNECT_REQUIRED"
    post = api_client.get(f"/api/v1/posts/{post_id}").json()
    assert post["status"] == "failed" and post["publish_error"].startswith("RECONNECT_REQUIRED")
    assert TOKEN not in post["publish_error"]
    (account,) = api_client.get("/api/v1/social/accounts", params={"brand_id": brand["id"]}).json()
    assert account["status"] == "reconnect_required"

    blocked = publish(api_client, make_post(brand["id"]))
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "RECONNECT_REQUIRED"


def test_expiry_date_is_enforced(api_client, brand, fake_linkedin):
    connect(api_client, brand["id"])
    with sync_session() as db:
        db.query(SocialAccount).update({"token_expires_at": datetime.now(UTC) - timedelta(minutes=1)})
    response = publish(api_client, make_post(brand["id"]))
    assert response.status_code == 409 and response.json()["error"]["code"] == "RECONNECT_REQUIRED"


def test_publish_guards(api_client, brand, fake_linkedin):
    assert publish(api_client, make_post(brand["id"])).json()["error"]["code"] == "ACCOUNT_NOT_CONNECTED"
    connect(api_client, brand["id"])
    assert publish(api_client, make_post(brand["id"], status="draft")).json()["error"]["code"] == "POST_NOT_PUBLISHABLE"
    assert publish(api_client, make_post(brand["id"], platform="reddit")).json()["error"]["code"] == "PLATFORM_NOT_SUPPORTED"


def test_social_and_publishing_are_private(api_client, brand, fake_linkedin):
    connect(api_client, brand["id"])
    post_id = make_post(brand["id"])
    sign_up(api_client, "intruder@example.com")
    assert api_client.get("/api/v1/social/accounts", params={"brand_id": brand["id"]}).status_code == 404
    assert api_client.post("/api/v1/social/linkedin/connect", json={"brand_id": brand["id"]}).status_code == 404
    assert api_client.post("/api/v1/social/linkedin/disconnect", json={"brand_id": brand["id"]}).status_code == 404
    assert publish(api_client, post_id).status_code == 404


def test_linkedin_setup_errors_are_shown_not_disguised(api_client, brand):
    url = api_client.post("/api/v1/social/linkedin/connect", json={"brand_id": brand["id"]}).json()["authorization_url"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    page = api_client.get(
        "/api/v1/social/linkedin/callback",
        params={"error": "unauthorized_scope_error", "error_description": "Scope \"w_member_social\" is not authorized for your application", "state": state},
    )
    assert page.status_code == 400
    assert "LinkedIn reported a problem" in page.text and "w_member_social" in page.text

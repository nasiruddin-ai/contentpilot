import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import text

from app.core import crypto
from app.core.database import sync_session
from app.integrations import x
from app.models import Post, SocialAccount
from app.services import publishing_service
from tests.conftest import sign_up

ACCESS, REFRESH = "x-access-token-1", "x-refresh-token-1"


def test_pkce_challenge_matches_rfc7636_example():
    # Appendix B of RFC 7636.
    assert x.code_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    verifier = x.new_code_verifier()
    assert 43 <= len(verifier) <= 128


def test_authorization_url():
    url = x.authorization_url("http://localhost:8000/api/v1/social/x/callback", "state-1", "v" * 50)
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert f"{parts.netloc}{parts.path}" == "x.com/i/oauth2/authorize"
    assert query["scope"] == ["tweet.read tweet.write users.read offline.access"]
    assert query["code_challenge_method"] == ["S256"] and query["code_challenge"] == [x.code_challenge("v" * 50)]
    assert query["client_id"] == ["test-x-client-id"] and query["state"] == ["state-1"]


class FakeX:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.challenge: str | None = None
        self.scope = "tweet.read tweet.write users.read offline.access"
        self.post_status = 201
        self.refresh_ok = True
        self.issued = 1

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/2/oauth2/token":
            form = parse_qs(request.content.decode())
            expected = "Basic " + base64.b64encode(b"test-x-client-id:test-x-client-secret").decode()
            assert request.headers["authorization"] == expected
            if form["grant_type"] == ["authorization_code"]:
                # The verifier must match the challenge sent to the authorize URL (PKCE).
                assert x.code_challenge(form["code_verifier"][0]) == self.challenge
            elif not self.refresh_ok:
                return httpx.Response(400, json={"error": "invalid_request", "error_description": "Value passed for the token was invalid."})
            else:
                self.issued += 1
            return httpx.Response(
                200,
                json={
                    "token_type": "bearer",
                    "expires_in": 7200,
                    "access_token": f"x-access-token-{self.issued}",
                    "refresh_token": f"x-refresh-token-{self.issued}",
                    "scope": self.scope,
                },
            )
        if path == "/2/users/me":
            return httpx.Response(200, json={"data": {"id": "2244994945", "name": "Squareko", "username": "squareko"}})
        if path == "/2/tweets":
            if self.post_status == 402:
                return httpx.Response(402, json={"title": "CreditsDepleted", "detail": "Your account has no credits."})
            return httpx.Response(201, json={"data": {"id": "1445880548472328192", "text": json.loads(request.content)["text"]}})
        return httpx.Response(404)

    def to(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == path]


@pytest.fixture
def fake_x(monkeypatch):
    fake = FakeX()
    monkeypatch.setattr(x, "transport_factory", lambda: httpx.MockTransport(fake.handle))
    return fake


@pytest.fixture
def brand(api_client, fake_x, monkeypatch):
    monkeypatch.setattr(publishing_service, "enqueue_publish", lambda post_id: None)
    sign_up(api_client, "owner@example.com")
    return api_client.post("/api/v1/brands", json={"name": "Squareko"}).json()


def connect(client, brand_id, fake_x):
    url = client.post("/api/v1/social/x/connect", json={"brand_id": brand_id}).json()["authorization_url"]
    query = parse_qs(urlsplit(url).query)
    fake_x.challenge = query["code_challenge"][0]
    return client.get("/api/v1/social/x/callback", params={"code": "x-code", "state": query["state"][0]})


def make_post(brand_id: str) -> str:
    with sync_session() as db:
        post = Post(
            brand_id=uuid.UUID(brand_id),
            platform="x",
            content_type="text_post",
            hook="One button beats five.",
            body="Give each page one clear next step.",
            cta=None,
            hashtags=["WebDesign"],
            status="approved",
        )
        db.add(post)
        db.flush()
        return str(post.id)


def publish(client, post_id):
    response = client.post(f"/api/v1/publishing/{post_id}/publish")
    assert response.status_code == 202, response.text
    return publishing_service.execute_publish(uuid.UUID(post_id))


def test_connect_with_pkce_and_refresh_token(api_client, brand, fake_x, db_engine):
    page = connect(api_client, brand["id"], fake_x)
    assert page.status_code == 200 and "X is connected as Squareko (@squareko)" in page.text

    (account,) = api_client.get("/api/v1/social/accounts", params={"brand_id": brand["id"]}).json()
    assert (account["platform"], account["status"]) == ("x", "active")
    with db_engine.connect() as conn:
        access, refresh = conn.execute(text("SELECT access_token_encrypted, refresh_token_encrypted FROM social_accounts")).one()
    assert crypto.decrypt(access) == ACCESS and crypto.decrypt(refresh) == REFRESH
    assert ACCESS not in access and REFRESH not in refresh


def test_missing_offline_access_is_refused(api_client, brand, fake_x):
    fake_x.scope = "tweet.read tweet.write users.read"
    page = connect(api_client, brand["id"], fake_x)
    assert page.status_code == 400 and "offline.access" in page.text


def test_publish_text_post(api_client, brand, fake_x):
    connect(api_client, brand["id"], fake_x)
    post_id = make_post(brand["id"])
    assert publish(api_client, post_id) == {"status": "published", "external_post_id": "1445880548472328192"}

    (tweet,) = fake_x.to("/2/tweets")
    assert tweet.headers["authorization"] == f"Bearer {ACCESS}"
    assert json.loads(tweet.content) == {"text": "One button beats five.\n\nGive each page one clear next step.\n\n#WebDesign"}
    post = api_client.get(f"/api/v1/posts/{post_id}").json()
    assert post["status"] == "published" and post["external_post_id"] == "1445880548472328192"


def test_token_is_refreshed_before_expiry(api_client, brand, fake_x, db_engine):
    connect(api_client, brand["id"], fake_x)
    with sync_session() as db:
        db.query(SocialAccount).update({"token_expires_at": datetime.now(UTC) + timedelta(minutes=2)})

    publish(api_client, make_post(brand["id"]))
    (tweet,) = fake_x.to("/2/tweets")
    assert tweet.headers["authorization"] == "Bearer x-access-token-2"
    with db_engine.connect() as conn:
        access, refresh, expires = conn.execute(
            text("SELECT access_token_encrypted, refresh_token_encrypted, token_expires_at FROM social_accounts")
        ).one()
    # The rotated refresh token is saved; the old one is single-use.
    assert crypto.decrypt(access) == "x-access-token-2" and crypto.decrypt(refresh) == "x-refresh-token-2"
    assert expires > datetime.now(UTC) + timedelta(hours=1)


def test_expired_token_with_refresh_token_still_publishes(api_client, brand, fake_x):
    connect(api_client, brand["id"], fake_x)
    with sync_session() as db:
        db.query(SocialAccount).update({"token_expires_at": datetime.now(UTC) - timedelta(hours=1)})
    assert publish(api_client, make_post(brand["id"]))["status"] == "published"


def test_failed_refresh_needs_reconnect(api_client, brand, fake_x):
    connect(api_client, brand["id"], fake_x)
    fake_x.refresh_ok = False
    with sync_session() as db:
        db.query(SocialAccount).update({"token_expires_at": datetime.now(UTC) - timedelta(hours=1)})

    result = publish(api_client, make_post(brand["id"]))
    assert result["status"] == "failed" and result["error"] == "RECONNECT_REQUIRED"
    (account,) = api_client.get("/api/v1/social/accounts", params={"brand_id": brand["id"]}).json()
    assert account["status"] == "reconnect_required"
    assert fake_x.to("/2/tweets") == []


def test_no_credits_is_explained(api_client, brand, fake_x):
    connect(api_client, brand["id"], fake_x)
    fake_x.post_status = 402
    post_id = make_post(brand["id"])
    result = publish(api_client, post_id)
    assert result["error"] == "NO_CREDITS"
    assert "console.x.com" in api_client.get(f"/api/v1/posts/{post_id}").json()["publish_error"]

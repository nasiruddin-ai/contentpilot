import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import update

from app.core.database import sync_session
from app.core.redis import get_sync_redis
from app.integrations import linkedin
from app.models import Post
from app.services import publishing_service
from tests.conftest import sign_up
from tests.test_social import FakeLinkedIn


class ScriptedLinkedIn(FakeLinkedIn):
    """FakeLinkedIn whose create-post and upload calls can be made to fail in specific ways."""

    def __init__(self):
        super().__init__()
        self.create_failures: list = []  # each: an int status or an exception class
        self.upload_status = 200

    def handle(self, request):
        path = request.url.path
        if path == "/rest/posts" and self.create_failures:
            self.requests.append(request)
            failure = self.create_failures.pop(0)
            if isinstance(failure, int):
                return httpx.Response(failure, json={"message": "LinkedIn error"})
            raise failure("simulated", request=request)
        if path == "/rest/images" and self.upload_status != 200:
            self.requests.append(request)
            return httpx.Response(self.upload_status, json={"message": "upload error"})
        return super().handle(request)


@pytest.fixture
def li(monkeypatch):
    fake = ScriptedLinkedIn()
    monkeypatch.setattr(linkedin, "transport_factory", lambda: httpx.MockTransport(fake.handle))
    return fake


@pytest.fixture
def queued(monkeypatch):
    calls = {"publish": [], "retry": []}
    monkeypatch.setattr(publishing_service, "enqueue_publish", calls["publish"].append)
    monkeypatch.setattr(publishing_service, "enqueue_retry", lambda post_id, delay: calls["retry"].append((post_id, delay)))
    return calls


@pytest.fixture
def brand(api_client, li, queued):
    sign_up(api_client, "owner@example.com")
    brand = api_client.post("/api/v1/brands", json={"name": "Squareko"}).json()
    url = api_client.post("/api/v1/social/linkedin/connect", json={"brand_id": brand["id"]}).json()["authorization_url"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    assert api_client.get("/api/v1/social/linkedin/callback", params={"code": "c", "state": state}).status_code == 200
    return brand


def make_post(brand_id, *, status="approved", scheduled_in: timedelta | None = None, with_image=False) -> uuid.UUID:
    from tests.test_social import make_post as social_make_post

    post_id = uuid.UUID(social_make_post(brand_id, status=status, visual="quote_card" if with_image else None))
    if scheduled_in is not None:
        with sync_session() as db:
            db.execute(update(Post).where(Post.id == post_id).values(scheduled_at=datetime.now(UTC) + scheduled_in))
    return post_id


def start(client, post_id):
    assert client.post(f"/api/v1/publishing/{post_id}/publish").status_code == 202


def notifications(client):
    return client.get("/api/v1/notifications").json()


# --- Scheduler ----------------------------------------------------------------------------


def test_due_posts_are_queued(api_client, brand, queued):
    due = make_post(brand["id"], status="scheduled", scheduled_in=-timedelta(minutes=1))
    later = make_post(brand["id"], status="scheduled", scheduled_in=timedelta(hours=2))
    draft = make_post(brand["id"], status="draft", scheduled_in=-timedelta(minutes=5))

    assert publishing_service.queue_due_posts() == {"queued": 1, "stuck": 0}
    assert queued["publish"] == [due]
    statuses = {pid: api_client.get(f"/api/v1/posts/{pid}").json()["status"] for pid in (due, later, draft)}
    assert statuses == {due: "publishing", later: "scheduled", draft: "draft"}
    assert publishing_service.queue_due_posts()["queued"] == 0  # not queued twice


def test_interrupted_publishing_is_flagged_not_retried(api_client, brand, queued):
    post_id = make_post(brand["id"], status="publishing")
    with sync_session() as db:
        db.execute(update(Post).where(Post.id == post_id).values(updated_at=datetime.now(UTC) - timedelta(hours=1)))

    assert publishing_service.queue_due_posts()["stuck"] == 1
    post = api_client.get(f"/api/v1/posts/{post_id}").json()
    assert post["status"] == "failed" and post["publish_error"].startswith("PUBLISH_UNCERTAIN")
    assert queued["publish"] == [] and notifications(api_client)[0]["type"] == "publish_uncertain"


def test_scheduled_post_publishes_end_to_end(api_client, brand, li, queued):
    post_id = make_post(brand["id"], status="scheduled", scheduled_in=-timedelta(seconds=5))
    publishing_service.queue_due_posts()
    assert publishing_service.execute_publish(post_id)["status"] == "published"

    post = api_client.get(f"/api/v1/posts/{post_id}").json()
    assert post["published_url"] == "https://www.linkedin.com/feed/update/urn:li:share:6844785523593134080/"
    (note,) = notifications(api_client)
    assert (note["type"], note["title"], note["link"]) == ("post_published", "Published to LinkedIn", post["published_url"])


# --- Retries, backoff and duplicate safety -----------------------------------------------------


def test_temporary_outage_retries_with_backoff_then_gives_up(api_client, brand, li, queued):
    li.create_failures = [503, 503, 503, 503]
    post_id = make_post(brand["id"])
    start(api_client, post_id)

    for attempt, delay in enumerate((60, 240, 960), start=1):
        result = publishing_service.execute_publish(post_id)
        assert result == {"status": "retrying", "error": "PROVIDER_ERROR", "delay": delay, "attempt": attempt}
        post = api_client.get(f"/api/v1/posts/{post_id}").json()
        assert post["status"] == "publishing" and f"Retrying in {delay // 60} min" in post["publish_error"]
    assert queued["retry"] == [(post_id, 60), (post_id, 240), (post_id, 960)]

    final = publishing_service.execute_publish(post_id)
    assert final["status"] == "failed" and "Gave up after 4 attempts" in final["message"]
    assert notifications(api_client)[0]["type"] == "publish_failed"


def test_retry_recovers(api_client, brand, li, queued):
    li.create_failures = [429]
    post_id = make_post(brand["id"])
    start(api_client, post_id)
    assert publishing_service.execute_publish(post_id)["status"] == "retrying"
    assert publishing_service.execute_publish(post_id)["status"] == "published"
    assert len(li.to("/rest/posts")) == 2


@pytest.mark.parametrize("failure", [500, httpx.ReadTimeout, httpx.RemoteProtocolError])
def test_ambiguous_create_failures_are_not_retried(api_client, brand, li, queued, failure):
    li.create_failures = [failure]
    post_id = make_post(brand["id"])
    start(api_client, post_id)

    result = publishing_service.execute_publish(post_id)
    assert result["error"] == "PUBLISH_UNCERTAIN" and queued["retry"] == []
    post = api_client.get(f"/api/v1/posts/{post_id}").json()
    assert post["status"] == "failed" and "may or may not be live" in post["publish_error"]
    assert notifications(api_client)[0]["type"] == "publish_uncertain"

    # After checking their profile, the user can retry by hand.
    start(api_client, post_id)
    assert publishing_service.execute_publish(post_id)["status"] == "published"


def test_connection_failures_are_safe_to_retry(api_client, brand, li, queued):
    li.create_failures = [httpx.ConnectError]
    post_id = make_post(brand["id"])
    start(api_client, post_id)
    assert publishing_service.execute_publish(post_id)["status"] == "retrying"


def test_upload_failures_are_safe_to_retry(api_client, brand, li, queued):
    li.upload_status = 500
    post_id = make_post(brand["id"], with_image=True)
    start(api_client, post_id)
    result = publishing_service.execute_publish(post_id)
    assert result["status"] == "retrying"  # no post was created yet
    assert li.to("/rest/posts") == []


def test_concurrent_runs_are_prevented(api_client, brand, li, queued):
    post_id = make_post(brand["id"])
    start(api_client, post_id)
    lock = get_sync_redis().lock(f"lock:publish:{post_id}", timeout=30)
    assert lock.acquire(blocking=False)
    try:
        assert publishing_service.execute_publish(post_id) == {"status": "skipped", "reason": "already running"}
    finally:
        lock.release()
    assert li.to("/rest/posts") == []


def test_reconnect_is_announced_once(api_client, brand, li, queued):
    li.post_status = 401
    for _ in range(2):
        post_id = make_post(brand["id"])
        with sync_session() as db:
            db.execute(update(Post).where(Post.id == post_id).values(status="publishing"))
        publishing_service.execute_publish(post_id)
    types = [n["type"] for n in notifications(api_client)]
    assert types.count("reconnect_required") == 1
    assert types.count("publish_failed") == 2


# --- Notifications API ---------------------------------------------------------------------------


def test_notifications_api(api_client, brand, li, queued):
    for _ in range(2):
        post_id = make_post(brand["id"])
        start(api_client, post_id)
        publishing_service.execute_publish(post_id)

    assert api_client.get("/api/v1/notifications/unread-count").json() == {"unread": 2}
    first = notifications(api_client)[0]
    read = api_client.post(f"/api/v1/notifications/{first['id']}/read").json()
    assert read["read_at"]
    assert [n["id"] for n in api_client.get("/api/v1/notifications", params={"unread_only": True}).json()] != [first["id"]]
    assert api_client.post("/api/v1/notifications/read-all").json() == {"updated": 1}
    assert api_client.get("/api/v1/notifications/unread-count").json() == {"unread": 0}

    sign_up(api_client, "intruder@example.com")
    assert api_client.get("/api/v1/notifications").json() == []
    assert api_client.post(f"/api/v1/notifications/{first['id']}/read").status_code == 404

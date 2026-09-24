import pytest

from tests.conftest import add_source, sign_up
from tests.fakes import RSS, page
from tests.test_parser import ARTICLE
from tests.test_parser import RSS as RSS_BODY

SOURCES = "/api/v1/sources"


def test_sources_require_login(api_client):
    assert api_client.post(SOURCES, json={}).status_code == 401


def test_create_source_normalizes_url(api_client, brand_id):
    response = add_source(api_client, brand_id, url="HTTPS://Example.com/feed#latest")
    assert response.status_code == 201
    source = response.json()
    assert source["url"] == "https://example.com/feed"
    assert source["status"] == "pending"
    assert source["fetch_frequency"] == "daily"
    assert source["active"] is True


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("http://localhost/admin", "URL_HOST_NOT_ALLOWED"),
        ("http://169.254.169.254/latest/meta-data/", "URL_HOST_NOT_ALLOWED"),
        ("https://intranet.example/", "URL_HOST_NOT_ALLOWED"),  # public-looking name, private DNS
        ("http://postgres:5432/", "URL_PORT_NOT_ALLOWED"),
        ("https://no-such-site.example/", "URL_DNS_FAILED"),
        ("file:///etc/passwd", "URL_SCHEME_NOT_ALLOWED"),
    ],
)
def test_unsafe_urls_rejected_at_creation(api_client, brand_id, url, code):
    response = add_source(api_client, brand_id, url=url)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code


def test_unsupported_source_type(api_client, brand_id):
    response = add_source(api_client, brand_id, source_type="youtube")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SOURCE_TYPE_NOT_SUPPORTED"


def test_duplicate_url_after_normalization(api_client, brand_id):
    add_source(api_client, brand_id, url="https://example.com/feed")
    response = add_source(api_client, brand_id, url="https://EXAMPLE.com:443/feed#x")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SOURCE_EXISTS"


def test_list_is_per_brand(api_client, brand_id):
    other_brand = api_client.post("/api/v1/brands", json={"name": "Other"}).json()["id"]
    add_source(api_client, brand_id, url="https://example.com/a")
    add_source(api_client, other_brand, url="https://example.com/b")

    listed = api_client.get(SOURCES, params={"brand_id": brand_id}).json()
    assert [s["url"] for s in listed] == ["https://example.com/a"]
    assert api_client.get(SOURCES).status_code == 422  # brand_id is required


def test_other_users_cannot_touch_sources(api_client, brand_id):
    source = add_source(api_client, brand_id).json()
    url = f"{SOURCES}/{source['id']}"

    sign_up(api_client, "intruder@example.com")
    assert api_client.get(SOURCES, params={"brand_id": brand_id}).json()["error"]["code"] == "BRAND_NOT_FOUND"
    assert add_source(api_client, brand_id, url="https://example.com/x").status_code == 404
    for response in (
        api_client.get(url),
        api_client.patch(url, json={"name": "Mine now"}),
        api_client.post(f"{url}/test"),
        api_client.delete(url),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "SOURCE_NOT_FOUND"


def test_patch_url_revalidates_and_resets_status(api_client, brand_id, net):
    net.add("example.com", "/feed", page(RSS_BODY, RSS))
    source = add_source(api_client, brand_id).json()
    url = f"{SOURCES}/{source['id']}"
    api_client.post(f"{url}/test")
    assert api_client.get(url).json()["status"] == "ok"

    assert api_client.patch(url, json={"url": "http://127.0.0.1/"}).status_code == 422
    updated = api_client.patch(url, json={"url": "https://other.example/rss", "fetch_frequency": "hourly"}).json()
    assert updated["url"] == "https://other.example/rss"
    assert updated["status"] == "pending"
    assert updated["fetch_frequency"] == "hourly"

    assert api_client.patch(url, json={"name": None}).status_code == 422


def test_test_feed_source(api_client, brand_id, net):
    net.add("example.com", "/feed", page(RSS_BODY, RSS))
    source = add_source(api_client, brand_id).json()

    result = api_client.post(f"{SOURCES}/{source['id']}/test").json()
    assert result["ok"] is True
    assert result["kind"] == "feed"
    assert result["title"] == "Web Design Weekly"
    assert [i["title"] for i in result["items"]] == ["Clarity beats decoration", "Second post"]

    stored = api_client.get(f"{SOURCES}/{source['id']}").json()
    assert stored["status"] == "ok" and stored["error_count"] == 0


def test_test_page_source_suggests_feed(api_client, brand_id, net):
    net.add("example.com", "/blog/clarity", page(ARTICLE))
    source = add_source(api_client, brand_id, url="https://example.com/blog/clarity", source_type="website").json()

    result = api_client.post(f"{SOURCES}/{source['id']}/test").json()
    assert result["ok"] is True
    assert result["kind"] == "page"
    assert "clear headline" in result["excerpt"]
    assert len(result["excerpt"]) <= 501
    assert result["feed_urls"] == ["https://example.com/feed.xml"]


def test_failed_test_records_error(api_client, brand_id, net):
    source = add_source(api_client, brand_id, url="https://example.com/gone").json()
    url = f"{SOURCES}/{source['id']}"

    for expected_count in (1, 2):
        result = api_client.post(f"{url}/test").json()
        assert result["ok"] is False
        assert result["error"]["code"] == "HTTP_ERROR"
        stored = api_client.get(url).json()
        assert stored["status"] == "error"
        assert stored["error_count"] == expected_count
        assert "404" in stored["last_error"]

    # Recovery clears the error.
    net.add("example.com", "/gone", page(RSS_BODY, RSS))
    assert api_client.post(f"{url}/test").json()["ok"] is True
    stored = api_client.get(url).json()
    assert stored["status"] == "ok" and stored["error_count"] == 0 and stored["last_error"] is None


def test_deleting_brand_deletes_its_sources(api_client, brand_id):
    source = add_source(api_client, brand_id).json()
    assert api_client.delete(f"/api/v1/brands/{brand_id}").status_code == 204
    assert api_client.get(f"{SOURCES}/{source['id']}").status_code == 404


def test_delete_source(api_client, brand_id):
    source = add_source(api_client, brand_id).json()
    assert api_client.delete(f"{SOURCES}/{source['id']}").status_code == 204
    assert api_client.get(SOURCES, params={"brand_id": brand_id}).json() == []

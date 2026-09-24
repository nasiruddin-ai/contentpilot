import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core.database import sync_session
from app.models import Brand, ResearchRun, Source, User
from app.models.source import SourceType
from app.research.deduplication import content_hash, hamming_distance, simhash
from app.research.fetcher import clear_robots_cache
from app.research.pipeline import CollectedItem, collect
from app.services import research_service
from app.utils.urls import strip_tracking_params
from tests.conftest import add_source, sign_up
from tests.fakes import RSS, FakeInternet, page

ARTICLE_TEXT = (
    "Most small business websites try to say everything at once. Visitors arrive, scan for a few "
    "seconds, and leave when they cannot tell what the business does or who it helps. A clear "
    "headline, one primary call to action and a short explanation of the offer consistently "
    "outperform busy layouts. Decoration should support the message rather than compete with it."
)


def _long_article(seed: int, words: int = 600) -> str:
    """Deterministic article-length text; SimHash is only meaningful at this length."""
    import random

    vocabulary = (
        "website conversion clarity headline visitor offer layout design brand message search content "
        "audience trust service small business page call action value price client result growth traffic"
    ).split()
    rng = random.Random(seed)
    return " ".join(rng.choice(vocabulary) + ("." if rng.random() < 0.1 else "") for _ in range(words))


LONG_ARTICLE = _long_article(1)


def _edit(text: str) -> str:
    words = text.split()
    words[len(words) // 2] = "styling"
    return " ".join(words)


def article_html(title: str, body: str = ARTICLE_TEXT, canonical: str | None = None) -> str:
    link = f'<link rel="canonical" href="{canonical}">' if canonical else ""
    return f"<html><head><title>{title}</title>{link}</head><body><article><h1>{title}</h1><p>{body}</p><p>{body}</p></article></body></html>"


def rss(*entries: tuple[str, str, str]) -> str:
    items = "".join(
        f"<item><title>{t}</title><link>{link}</link><description>{d}</description></item>" for t, link, d in entries
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>Design Feed</title>{items}</channel></rss>'


@pytest.fixture(autouse=True)
def _fresh_robots_cache():
    clear_robots_cache()
    yield
    clear_robots_cache()


# --- Deduplication primitives ------------------------------------------------


def test_content_hash_ignores_case_whitespace_and_punctuation():
    assert content_hash("Hello,   World!") == content_hash("hello world")
    assert content_hash("hello world") != content_hash("hello there")


def test_simhash_separates_near_and_different_texts():
    assert hamming_distance(simhash(LONG_ARTICLE), simhash(_edit(LONG_ARTICLE))) <= 3
    assert hamming_distance(simhash(LONG_ARTICLE), simhash(_long_article(99))) > 15


def test_strip_tracking_params():
    url = "https://example.com/post?id=7&utm_source=x&utm_medium=y&fbclid=abc&page=2"
    assert strip_tracking_params(url) == "https://example.com/post?id=7&page=2"


# --- Pipeline (network + parsing) --------------------------------------------


async def test_feed_run_fetches_articles_safely():
    net = FakeInternet()
    net.add(
        "example.com",
        "/feed",
        page(
            rss(
                ("Full article", "https://example.com/a?utm_source=rss", "Short teaser"),
                ("Unreachable article", "https://example.com/gone", "The feed summary is all we can get here."),
                ("Metadata grab", "http://169.254.169.254/latest/meta-data/", "attack"),
                ("Already stored", "https://example.com/old", "seen before"),
            ),
            RSS,
        ),
    )
    net.add("example.com", "/a", page(article_html("Full article")))

    collection = await collect("https://example.com/feed", SourceType.RSS, net.fetcher(), {"https://example.com/old"})

    by_title = {i.title: i for i in collection.items}
    assert set(by_title) == {"Full article", "Unreachable article"}
    assert by_title["Full article"].content_type == "article"
    assert by_title["Full article"].url == "https://example.com/a"  # tracking params stripped
    assert "clear headline" in by_title["Full article"].text
    assert by_title["Unreachable article"].content_type == "feed_summary"
    assert collection.known_skipped == 1
    # The metadata URL and the already-stored article are never requested.
    assert not any(r.url.host == "169.254.169.254" for r in net.requests)
    assert net.requests_to("/old") == []


async def test_site_with_feed_is_read_through_its_feed():
    net = FakeInternet()
    net.add(
        "example.com",
        "/",
        page('<html><head><link rel="alternate" type="application/rss+xml" href="/feed"></head><body>Home</body></html>'),
    )
    net.add("example.com", "/feed", page(rss(("Post", "https://example.com/p", "teaser")), RSS))
    net.add("example.com", "/p", page(article_html("Post")))

    collection = await collect("https://example.com/", SourceType.WEBSITE, net.fetcher(), set())
    assert [i.title for i in collection.items] == ["Post"]


async def test_page_without_feed_is_one_snapshot():
    net = FakeInternet()
    net.add("example.com", "/about", page(article_html("About us", canonical="https://www.example.com/about-us")))
    net.add("example.com", "/pricing", page(article_html("Pricing", canonical="https://evil.example/stolen")))

    about = (await collect("https://example.com/about", SourceType.WEBSITE, net.fetcher(), set())).items
    assert len(about) == 1 and about[0].content_type == "page"
    assert about[0].url == "https://www.example.com/about-us"  # same-site canonical honoured

    pricing = (await collect("https://example.com/pricing", SourceType.WEBSITE, net.fetcher(), set())).items
    assert pricing[0].url == "https://example.com/pricing"  # cross-site canonical ignored


# --- Storage and deduplication -----------------------------------------------


def item(url: str, text: str = ARTICLE_TEXT, title: str = "T") -> CollectedItem:
    return CollectedItem(url=url, title=title, text=text, summary=text[:100], content_type="article")


@pytest.fixture
def brand(db_session):
    user = User(name="U", email="u@example.com", password_hash="x")
    brand = Brand(user=user, name="B")
    db_session.add(brand)
    db_session.flush()
    return brand


def test_store_deduplicates(db_session, brand):
    store = research_service.store_items
    first = store(db_session, brand.id, None, [item("https://example.com/1", LONG_ARTICLE)])
    assert (first.new, first.updated, first.duplicate) == (1, 0, 0)

    again = store(
        db_session,
        brand.id,
        None,
        [
            item("https://example.com/1", LONG_ARTICLE),  # same URL, same text
            item("https://example.com/copy", LONG_ARTICLE),  # same text elsewhere
            item("https://example.com/edit", _edit(LONG_ARTICLE)),  # near-duplicate
            item("https://example.com/new", _long_article(99)),  # different article
        ],
    )
    assert (again.new, again.updated, again.duplicate) == (1, 0, 3)

    changed = store(db_session, brand.id, None, [item("https://example.com/1", "Rewritten. " + "Fresh text. " * 40)])
    assert (changed.new, changed.updated) == (0, 1)


def test_short_texts_skip_near_duplicate_check(db_session, brand):
    counts = research_service.store_items(
        db_session,
        brand.id,
        None,
        [item("https://example.com/a", "Weekly roundup number one"), item("https://example.com/b", "Weekly roundup number two")],
    )
    assert counts.new == 2


# --- API + worker, end to end --------------------------------------------------

FEED = rss(
    ("Clarity beats decoration", "https://example.com/a", "teaser"),
    ("Second post", "https://example.com/b", "Only a summary is available for this one."),
)


def test_fetch_queues_one_run_per_source(api_client, brand_id, net):
    source = add_source(api_client, brand_id).json()

    first = api_client.post(f"/api/v1/sources/{source['id']}/fetch")
    assert first.status_code == 202
    assert first.json()["status"] == "queued" and first.json()["trigger"] == "manual"

    # A second click while it's queued returns the same run instead of queueing another.
    second = api_client.post(f"/api/v1/sources/{source['id']}/fetch")
    assert second.json()["id"] == first.json()["id"]
    assert net.enqueued == [uuid.UUID(first.json()["id"])]


def test_run_end_to_end(api_client, brand_id, net):
    net.add("example.com", "/feed", page(FEED, RSS))
    net.add("example.com", "/a", page(article_html("Clarity beats decoration")))
    source = add_source(api_client, brand_id).json()
    run_id = api_client.post(f"/api/v1/sources/{source['id']}/fetch").json()["id"]

    assert research_service.execute_run(uuid.UUID(run_id))["status"] == "succeeded"

    run = api_client.get(f"/api/v1/research/runs/{run_id}").json()
    assert run["status"] == "succeeded"
    assert (run["items_found"], run["items_new"], run["items_duplicate"]) == (2, 2, 0)
    assert run["started_at"] and run["finished_at"]

    items = api_client.get("/api/v1/research", params={"brand_id": brand_id}).json()
    assert {i["title"] for i in items} == {"Clarity beats decoration", "Second post"}
    assert "clean_text" not in items[0]

    article = next(i for i in items if i["title"] == "Clarity beats decoration")
    detail = api_client.get(f"/api/v1/research/{article['id']}").json()
    assert "clear headline" in detail["clean_text"]
    assert detail["content_type"] == "article"
    assert detail["source_metadata"]["feed_title"] == "Design Feed"

    stored = api_client.get(f"/api/v1/sources/{source['id']}").json()
    assert stored["status"] == "ok" and stored["last_fetched_at"]
    next_fetch = datetime.fromisoformat(stored["next_fetch_at"])
    assert timedelta(hours=23) < next_fetch - datetime.now(UTC) <= timedelta(days=1)

    # Running again stores nothing new and doesn't re-download known articles.
    net.requests.clear()
    rerun_id = api_client.post(f"/api/v1/sources/{source['id']}/fetch").json()["id"]
    assert rerun_id != run_id
    research_service.execute_run(uuid.UUID(rerun_id))
    rerun = api_client.get(f"/api/v1/research/runs/{rerun_id}").json()
    assert (rerun["items_new"], rerun["items_duplicate"]) == (0, 2)
    assert net.requests_to("/a") == []

    # Executing a finished run again is a no-op (safe if Celery redelivers it).
    assert research_service.execute_run(uuid.UUID(run_id)) == {"status": "skipped"}


def test_failed_run_backs_off(api_client, brand_id, net):
    source = add_source(api_client, brand_id, url="https://example.com/missing").json()
    run_id = api_client.post(f"/api/v1/sources/{source['id']}/fetch").json()["id"]

    assert research_service.execute_run(uuid.UUID(run_id))["status"] == "failed"
    run = api_client.get(f"/api/v1/research/runs/{run_id}").json()
    assert run["status"] == "failed" and "404" in run["error"]

    stored = api_client.get(f"/api/v1/sources/{source['id']}").json()
    assert stored["status"] == "error" and stored["error_count"] == 1


def test_research_search_and_filters(api_client, brand_id, net):
    net.add("example.com", "/feed", page(FEED, RSS))
    net.add("example.com", "/a", page(article_html("Clarity beats decoration")))
    source = add_source(api_client, brand_id).json()
    research_service.execute_run(uuid.UUID(api_client.post(f"/api/v1/sources/{source['id']}/fetch").json()["id"]))

    def titles(**params):
        return [i["title"] for i in api_client.get("/api/v1/research", params={"brand_id": brand_id, **params}).json()]

    assert titles(q="clarity") == ["Clarity beats decoration"]
    assert titles(q="100%_") == []  # LIKE wildcards are escaped
    assert len(titles(limit=1)) == 1
    assert titles(source_id=str(uuid.uuid4())) == []


def test_brand_research_run_queues_active_sources_only(api_client, brand_id, net):
    active = add_source(api_client, brand_id, url="https://example.com/a").json()
    add_source(api_client, brand_id, url="https://example.com/b", active=False)

    response = api_client.post("/api/v1/research/run", json={"brand_id": brand_id})
    assert response.status_code == 202
    assert [r["source_id"] for r in response.json()["runs"]] == [active["id"]]


def test_research_is_private(api_client, brand_id, net):
    net.add("example.com", "/feed", page(FEED, RSS))
    source = add_source(api_client, brand_id).json()
    run_id = api_client.post(f"/api/v1/sources/{source['id']}/fetch").json()["id"]
    research_service.execute_run(uuid.UUID(run_id))
    item_id = api_client.get("/api/v1/research", params={"brand_id": brand_id}).json()[0]["id"]

    sign_up(api_client, "intruder@example.com")
    assert api_client.get("/api/v1/research", params={"brand_id": brand_id}).status_code == 404
    assert api_client.get(f"/api/v1/research/{item_id}").status_code == 404
    assert api_client.get(f"/api/v1/research/runs/{run_id}").status_code == 404
    assert api_client.get("/api/v1/research/runs", params={"brand_id": brand_id}).status_code == 404
    assert api_client.post(f"/api/v1/sources/{source['id']}/fetch").status_code == 404
    assert api_client.post("/api/v1/research/run", json={"brand_id": brand_id}).status_code == 404


def test_scheduler_picks_due_sources(api_client, brand_id, net):
    due = add_source(api_client, brand_id, url="https://example.com/due").json()
    later = add_source(api_client, brand_id, url="https://example.com/later").json()
    add_source(api_client, brand_id, url="https://example.com/off", active=False)
    busy = add_source(api_client, brand_id, url="https://example.com/busy").json()
    api_client.post(f"/api/v1/sources/{busy['id']}/fetch")  # already has a queued run

    with sync_session() as db:
        db.execute(
            update(Source)
            .where(Source.id == uuid.UUID(later["id"]))
            .values(next_fetch_at=datetime.now(UTC) + timedelta(hours=3))
        )

    with sync_session() as db:
        run_ids = research_service.schedule_due_runs(db)
    with sync_session() as db:
        scheduled = {str(r.source_id) for r in db.scalars(select(ResearchRun).where(ResearchRun.id.in_(run_ids)))}
        assert all(r.trigger == "scheduled" for r in db.scalars(select(ResearchRun).where(ResearchRun.id.in_(run_ids))))
    assert scheduled == {due["id"]}

    # Its next check was pushed out, so the next tick doesn't queue it twice.
    with sync_session() as db:
        assert research_service.schedule_due_runs(db) == []

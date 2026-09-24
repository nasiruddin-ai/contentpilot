"""Collects research from one source: Fetch → Parse → Clean → Metadata (spec section 21).

Network and parsing only; deduplication and storage happen in research_service.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlsplit

from app.models.source import SourceType
from app.research.cleaner import clean_text
from app.research.fetcher import FetchError, FetchResult, SafeFetcher
from app.research.parser import FeedItem, ParseError, ParsedPage, is_feed, parse_feed, parse_page
from app.utils.urls import UnsafeUrlError, parse_public_url, strip_tracking_params

logger = logging.getLogger(__name__)

MAX_FEED_ITEMS = 20
SUMMARY_CHARS = 500
# Below this, the article page probably didn't extract well; the feed summary may be better.
MIN_ARTICLE_CHARS = 200
# After this long, stop opening articles and keep feed summaries, so a run stays bounded.
ARTICLE_BUDGET_SECONDS = 240


@dataclass
class CollectedItem:
    url: str
    title: str
    text: str
    summary: str
    content_type: str  # ResearchContentType value
    author: str | None = None
    published_at: datetime | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class Collection:
    items: list[CollectedItem]
    # Feed entries skipped because they are already stored.
    known_skipped: int = 0


async def collect(
    source_url: str, source_type: SourceType, fetcher: SafeFetcher, known_urls: set[str]
) -> Collection:
    fetched = await fetcher.fetch(source_url)
    if is_feed(fetched):
        return await _collect_feed(fetched, fetcher, known_urls)

    page = parse_page(fetched)
    if source_type != SourceType.USER_URL and page.feed_urls:
        # Prefer the site's own feed over scraping its homepage (spec section 24).
        try:
            feed_fetch = await fetcher.fetch(page.feed_urls[0])
            if is_feed(feed_fetch):
                return await _collect_feed(feed_fetch, fetcher, known_urls)
        except (FetchError, ParseError) as exc:
            logger.info("advertised_feed_unusable", extra={"url": page.feed_urls[0], "error": str(exc)})

    return Collection(items=[_page_item(fetched, page)] if page.text else [])


async def _collect_feed(fetched: FetchResult, fetcher: SafeFetcher, known_urls: set[str]) -> Collection:
    feed = parse_feed(fetched)
    started = time.monotonic()
    items: list[CollectedItem] = []
    known_skipped = 0

    for entry in feed.items[:MAX_FEED_ITEMS]:
        url = _canonical(entry.url)
        if url is None:
            continue  # missing, or points somewhere we'd never fetch
        if url in known_urls:
            known_skipped += 1
            continue

        page = None
        if time.monotonic() - started < ARTICLE_BUDGET_SECONDS:
            try:
                page = parse_page(await fetcher.fetch(url))
            except (FetchError, ParseError) as exc:
                logger.info("article_fetch_failed", extra={"url": url, "error": getattr(exc, "code", str(exc))})

        item = _feed_item(entry, url, page, feed.title)
        if item is not None:
            items.append(item)
    return Collection(items=items, known_skipped=known_skipped)


def _feed_item(entry: FeedItem, url: str, page: ParsedPage | None, feed_title: str) -> CollectedItem | None:
    metadata = {"feed_title": feed_title} if feed_title else {}
    if page is not None and len(page.text) >= max(MIN_ARTICLE_CHARS, len(entry.summary)):
        return CollectedItem(
            url=url,
            title=entry.title or page.title or url,
            text=page.text,
            summary=entry.summary or clean_text(page.text, SUMMARY_CHARS),
            content_type="article",
            author=page.author,
            published_at=entry.published or _parse_date(page.published),
            metadata={**metadata, **({"site_name": page.site_name} if page.site_name else {})},
        )
    if not entry.summary:
        return None
    return CollectedItem(
        url=url,
        title=entry.title or url,
        text=entry.summary,
        summary=clean_text(entry.summary, SUMMARY_CHARS),
        content_type="feed_summary",
        published_at=entry.published,
        metadata=metadata,
    )


def _page_item(fetched: FetchResult, page: ParsedPage) -> CollectedItem:
    url = _canonical(fetched.url) or fetched.url
    # Honour <link rel=canonical> only on the same site, so a page can't claim another site's URL.
    if page.canonical_url and _same_site(page.canonical_url, fetched.url):
        url = _canonical(page.canonical_url) or url
    return CollectedItem(
        url=url,
        title=page.title or url,
        text=page.text,
        summary=clean_text(page.text, SUMMARY_CHARS),
        content_type="page",
        author=page.author,
        published_at=_parse_date(page.published),
        metadata={"site_name": page.site_name} if page.site_name else {},
    )


def _canonical(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return strip_tracking_params(parse_public_url(url).url)
    except UnsafeUrlError:
        return None


def _same_site(a: str, b: str) -> bool:
    def host(url: str) -> str:
        return (urlsplit(url).hostname or "").removeprefix("www.")

    return host(a) == host(b)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

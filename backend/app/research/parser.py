"""Turns fetched bytes into feeds or article text. All input is untrusted:
only plain text is kept, never HTML or scripts."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urljoin

import feedparser
import trafilatura
from bs4 import BeautifulSoup

from app.research.cleaner import clean_text
from app.research.fetcher import FEED_TYPES, FetchResult

MAX_TEXT_CHARS = 100_000
FEED_LINK_TYPES = {"application/rss+xml", "application/atom+xml"}


class ParseError(Exception):
    pass


@dataclass
class FeedItem:
    title: str
    url: str | None
    published: datetime | None
    summary: str


@dataclass
class ParsedFeed:
    title: str
    items: list[FeedItem]


@dataclass
class ParsedPage:
    title: str
    text: str
    author: str | None
    published: str | None
    canonical_url: str | None = None
    site_name: str | None = None
    feed_urls: list[str] = field(default_factory=list)


def is_feed(result: FetchResult) -> bool:
    if result.content_type in {"application/rss+xml", "application/atom+xml", "application/rdf+xml"}:
        return True
    head = result.body[:2000].lstrip().lower()
    return result.content_type in FEED_TYPES and (b"<rss" in head or b"<feed" in head or b"<rdf" in head)


def parse_feed(result: FetchResult) -> ParsedFeed:
    parsed = feedparser.parse(result.body)
    if not parsed.entries and parsed.bozo:
        raise ParseError("This doesn't look like a valid RSS or Atom feed.")

    items = []
    for entry in parsed.entries:
        link = entry.get("link")
        summary_html = entry.get("summary", "")
        items.append(
            FeedItem(
                title=clean_text(entry.get("title", ""), 300),
                url=urljoin(result.url, link) if link else None,
                published=_feed_date(entry),
                summary=clean_text(_html_to_text(summary_html), 1000),
            )
        )
    return ParsedFeed(title=clean_text(parsed.feed.get("title", ""), 300), items=items)


def parse_page(result: FetchResult) -> ParsedPage:
    html = _decode(result)
    extracted = trafilatura.extract(html, include_comments=False, include_tables=False, favor_precision=True)
    metadata = trafilatura.extract_metadata(html)
    soup = BeautifulSoup(html, "lxml")

    text = extracted or _html_to_text(html)
    title = (metadata.title if metadata and metadata.title else None) or (
        soup.title.get_text() if soup.title else ""
    )
    return ParsedPage(
        title=clean_text(title, 300),
        text=clean_text(text, MAX_TEXT_CHARS),
        author=clean_text(metadata.author, 200) if metadata and metadata.author else None,
        published=metadata.date if metadata and metadata.date else None,
        canonical_url=_canonical_link(soup, result.url),
        site_name=clean_text(metadata.sitename, 200) if metadata and metadata.sitename else None,
        feed_urls=_feed_links(soup, result.url),
    )


def _decode(result: FetchResult) -> str:
    try:
        return result.body.decode(result.charset or "utf-8", errors="replace")
    except LookupError:  # unknown charset name
        return result.body.decode("utf-8", errors="replace")


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "noscript", "template", "iframe"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def _feed_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Feeds the page advertises; RSS is the preferred way to follow a site (spec section 24)."""
    links = []
    for link in soup.find_all("link", href=True):
        rel = {r.lower() for r in (link.get("rel") or [])}
        if "alternate" in rel and (link.get("type") or "").lower() in FEED_LINK_TYPES:
            links.append(urljoin(base_url, link["href"]))
    return list(dict.fromkeys(links))[:5]


def _canonical_link(soup: BeautifulSoup, base_url: str) -> str | None:
    for link in soup.find_all("link", href=True):
        if "canonical" in {r.lower() for r in (link.get("rel") or [])}:
            return urljoin(base_url, link["href"])
    return None


def _feed_date(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=UTC)
    except (TypeError, ValueError):
        return None

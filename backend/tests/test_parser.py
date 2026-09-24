import pytest

from app.research.cleaner import clean_text
from app.research.fetcher import FetchResult
from app.research.parser import ParseError, is_feed, parse_feed, parse_page

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Web Design Weekly</title>
  <item>
    <title>Clarity beats decoration</title>
    <link>/posts/clarity</link>
    <pubDate>Tue, 22 Sep 2026 10:00:00 GMT</pubDate>
    <description>&lt;p&gt;Why &lt;b&gt;simple&lt;/b&gt; sites convert.&lt;/p&gt;&lt;script&gt;alert(1)&lt;/script&gt;</description>
  </item>
  <item><title>Second post</title><link>https://example.com/two</link></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>SEO Notes</title>
  <entry><title>Core updates</title><link href="https://example.com/core"/><updated>2026-09-20T08:00:00Z</updated></entry>
</feed>"""

ARTICLE = """<!doctype html><html><head>
<title>Why clarity converts | Example Blog</title>
<link rel="alternate" type="application/rss+xml" href="/feed.xml">
<link rel="stylesheet" href="/style.css">
<script>var secret = "do not keep";</script>
</head><body>
<nav>Home About Contact</nav>
<article>
<h1>Why clarity converts</h1>
<p>Most small business websites try to say everything at once. Visitors arrive, scan for a few seconds,
and leave when they can't tell what the business does or who it helps.</p>
<p>A clear headline, one primary call to action and a short explanation of the offer consistently
outperform busy layouts. Decoration should support the message rather than compete with it.</p>
<p>Before adding another section, ask whether it helps a visitor decide. If it doesn't, cut it.</p>
</article>
<footer>Copyright Example</footer>
</body></html>"""


def result(body: bytes, content_type: str, url: str = "https://example.com/feed") -> FetchResult:
    return FetchResult(url=url, status_code=200, content_type=content_type, charset="utf-8", body=body)


def test_parses_rss():
    fetched = result(RSS, "application/rss+xml")
    assert is_feed(fetched)
    feed = parse_feed(fetched)

    assert feed.title == "Web Design Weekly"
    first = feed.items[0]
    assert first.title == "Clarity beats decoration"
    assert first.url == "https://example.com/posts/clarity"  # relative link resolved
    assert first.published.isoformat() == "2026-09-22T10:00:00+00:00"
    assert "simple" in first.summary and "sites convert" in first.summary
    assert "alert" not in first.summary and "<" not in first.summary


def test_parses_atom_served_as_generic_xml():
    fetched = result(ATOM, "application/xml")
    assert is_feed(fetched)
    feed = parse_feed(fetched)
    assert feed.title == "SEO Notes"
    assert feed.items[0].url == "https://example.com/core"


def test_invalid_feed_raises():
    with pytest.raises(ParseError):
        parse_feed(result(b"<rss><not closed", "application/rss+xml"))


def test_html_is_not_a_feed():
    assert not is_feed(result(ARTICLE.encode(), "text/html", "https://example.com/blog/clarity"))


def test_extracts_article_text_and_feed_links():
    fetched = result(ARTICLE.encode(), "text/html", "https://example.com/blog/clarity")
    parsed = parse_page(fetched)

    assert "clarity converts" in parsed.title.lower()
    assert "clear headline" in parsed.text
    assert "do not keep" not in parsed.text  # scripts dropped
    assert "<p>" not in parsed.text
    assert parsed.feed_urls == ["https://example.com/feed.xml"]


def test_clean_text():
    messy = "  Hello  world\x00\x07 \r\n\r\n\r\n\n  next   line  "
    assert clean_text(messy) == "Hello world\n\nnext line"
    assert clean_text("one two three four", max_chars=10) == "one two…"

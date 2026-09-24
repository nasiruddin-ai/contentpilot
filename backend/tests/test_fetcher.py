import pytest

from app.research.fetcher import FetchError, clear_robots_cache
from tests.fakes import PUBLIC_IP, FakeInternet, page, redirect


@pytest.fixture(autouse=True)
def _fresh_robots_cache():
    clear_robots_cache()
    yield
    clear_robots_cache()


@pytest.fixture
def net():
    return FakeInternet()


async def fetch_error(fetcher, url) -> FetchError:
    with pytest.raises(FetchError) as exc:
        await fetcher.fetch(url)
    return exc.value


async def test_connects_to_checked_ip_but_keeps_real_hostname(net):
    net.add("example.com", "/post", page("<html><body>Hi</body></html>"))
    result = await net.fetcher().fetch("https://example.com/post")

    request = net.requests_to("/post")[0]
    assert request.url.host == PUBLIC_IP  # pinned: no second DNS lookup can swap the address
    assert request.headers["host"] == "example.com"
    assert request.extensions["sni_hostname"] == "example.com"  # TLS verifies the real name
    assert request.headers["user-agent"].startswith("ContentPilotBot/")
    assert result.url == "https://example.com/post"
    assert result.body == b"<html><body>Hi</body></html>"


async def test_host_resolving_to_private_address_is_never_contacted():
    net = FakeInternet(dns={"evil.example": ["10.0.0.5"]})
    error = await fetch_error(net.fetcher(), "https://evil.example/")
    assert error.code == "URL_HOST_NOT_ALLOWED"
    assert net.requests == []


async def test_unsafe_url_rejected_before_any_request(net):
    error = await fetch_error(net.fetcher(), "ftp://example.com/")
    assert error.code == "URL_SCHEME_NOT_ALLOWED"
    assert net.requests == []


async def test_redirect_to_metadata_service_is_blocked(net):
    net.add("example.com", "/go", redirect("http://169.254.169.254/latest/meta-data/"))
    error = await fetch_error(net.fetcher(), "https://example.com/go")
    assert error.code == "REDIRECT_BLOCKED"
    assert all(r.url.host == PUBLIC_IP for r in net.requests)


async def test_redirect_to_host_with_private_dns_is_blocked():
    net = FakeInternet(dns={"intranet.example": ["192.168.0.10"]})
    net.add("example.com", "/go", redirect("https://intranet.example/admin"))
    error = await fetch_error(net.fetcher(), "https://example.com/go")
    assert error.code == "URL_HOST_NOT_ALLOWED"
    assert net.requests_to("/admin") == []


async def test_follows_safe_relative_redirects(net):
    net.add("example.com", "/old", redirect("/new", 301))
    net.add("example.com", "/new", page("<p>moved</p>"))
    result = await net.fetcher().fetch("https://example.com/old")
    assert result.url == "https://example.com/new"


async def test_redirect_loops_stop(net):
    net.add("example.com", "/a", redirect("/b"))
    net.add("example.com", "/b", redirect("/a"))
    assert (await fetch_error(net.fetcher(), "https://example.com/a")).code == "TOO_MANY_REDIRECTS"


async def test_declared_oversize_body_rejected(net):
    net.add("example.com", "/big", page("x" * 200))
    error = await fetch_error(net.fetcher(max_bytes=100), "https://example.com/big")
    assert error.code == "TOO_LARGE"


async def test_streamed_oversize_body_rejected(net):
    import httpx

    async def endless():
        for _ in range(100):
            yield b"x" * 50

    net.add("example.com", "/stream", lambda _: httpx.Response(200, headers={"content-type": "text/html"}, content=endless()))
    error = await fetch_error(net.fetcher(max_bytes=1000), "https://example.com/stream")
    assert error.code == "TOO_LARGE"


async def test_non_html_content_rejected(net):
    net.add("example.com", "/file.pdf", page(b"%PDF-1.7", content_type="application/pdf"))
    assert (await fetch_error(net.fetcher(), "https://example.com/file.pdf")).code == "UNSUPPORTED_CONTENT_TYPE"


async def test_retries_temporary_failures(net):
    responses = iter([page("busy", status=503), page("<p>ok</p>")])
    net.add("example.com", "/flaky", lambda _: next(responses))
    result = await net.fetcher().fetch("https://example.com/flaky")
    assert result.body == b"<p>ok</p>"
    assert len(net.requests_to("/flaky")) == 2


async def test_does_not_retry_permanent_failures(net):
    error = await fetch_error(net.fetcher(), "https://example.com/missing")
    assert error.code == "HTTP_ERROR" and error.status == 404
    assert len(net.requests_to("/missing")) == 1


async def test_gives_up_after_retries(net):
    net.add("example.com", "/down", page("down", status=502))
    error = await fetch_error(net.fetcher(retries=2), "https://example.com/down")
    assert error.status == 502
    assert len(net.requests_to("/down")) == 3


async def test_respects_robots_txt(net):
    net.add("example.com", "/robots.txt", page("User-agent: ContentPilotBot\nDisallow: /private/\n", "text/plain"))
    net.add("example.com", "/private/page", page("<p>secret</p>"))
    net.add("example.com", "/public", page("<p>hello</p>"))
    fetcher = net.fetcher()

    assert (await fetch_error(fetcher, "https://example.com/private/page")).code == "ROBOTS_DISALLOWED"
    assert net.requests_to("/private/page") == []
    assert (await fetcher.fetch("https://example.com/public")).body == b"<p>hello</p>"


async def test_missing_robots_txt_allows_everything(net):
    net.add("example.com", "/anything", page("<p>ok</p>"))
    assert (await net.fetcher().fetch("https://example.com/anything")).status_code == 200


async def test_broken_robots_txt_means_wait(net):
    net.add("example.com", "/robots.txt", page("oops", status=500))
    net.add("example.com", "/page", page("<p>ok</p>"))
    assert (await fetch_error(net.fetcher(retries=0), "https://example.com/page")).code == "ROBOTS_UNAVAILABLE"
    assert net.requests_to("/page") == []


async def test_per_host_rate_limit(net):
    async def deny(_host):
        return False

    assert (await fetch_error(net.fetcher(host_limiter=deny), "https://example.com/")).code == "HOST_RATE_LIMITED"
    assert net.requests == []

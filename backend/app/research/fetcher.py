"""SSRF-safe HTTP fetching for user-supplied URLs.

Each hop (including every redirect) is validated, resolved, and then connected
to the exact IP that passed validation, so DNS can't be switched to an internal
address between the check and the connection. TLS still verifies the real
hostname via SNI.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import httpx

from app.core import rate_limit
from app.utils.urls import Resolver, SafeUrl, UnsafeUrlError, parse_public_url, resolve_public_ips

logger = logging.getLogger(__name__)

USER_AGENT = "ContentPilotBot/1.0"
ROBOTS_AGENT = "ContentPilotBot"

HTML_TYPES = {"text/html", "application/xhtml+xml"}
FEED_TYPES = {"application/rss+xml", "application/atom+xml", "application/xml", "text/xml", "application/rdf+xml"}
DEFAULT_ACCEPT = HTML_TYPES | FEED_TYPES

MAX_BYTES = 2_000_000
MAX_ROBOTS_BYTES = 500_000
MAX_REDIRECTS = 5
RETRY_STATUSES = {429, 502, 503, 504}
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
OVERALL_DEADLINE_SECONDS = 60
PER_HOST_LIMIT = (30, 60)  # requests per seconds, per host

ROBOTS_TTL_SECONDS = 24 * 60 * 60
_robots_cache: dict[str, tuple[float, RobotFileParser | None]] = {}


class FetchError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status = status


@dataclass
class FetchResult:
    url: str  # final URL after redirects
    status_code: int
    content_type: str
    charset: str | None
    body: bytes


HostLimiter = Callable[[str], Awaitable[bool]]


async def _default_host_limiter(host: str) -> bool:
    return await rate_limit.allow(f"fetch:host:{host}", *PER_HOST_LIMIT)


class SafeFetcher:
    def __init__(
        self,
        *,
        resolver: Resolver = resolve_public_ips,
        transport: httpx.AsyncBaseTransport | None = None,
        host_limiter: HostLimiter = _default_host_limiter,
        max_bytes: int = MAX_BYTES,
        retries: int = 2,
        backoff_seconds: float = 0.5,
        respect_robots: bool = True,
    ) -> None:
        self._resolver = resolver
        self._transport = transport
        self._host_limiter = host_limiter
        self._max_bytes = max_bytes
        self._retries = retries
        self._backoff = backoff_seconds
        self._respect_robots = respect_robots

    async def fetch(self, url: str, accept: set[str] = DEFAULT_ACCEPT) -> FetchResult:
        started = time.perf_counter()
        try:
            async with asyncio.timeout(OVERALL_DEADLINE_SECONDS):
                async with self._client() as client:
                    result = await self._fetch_with_retries(client, url, accept)
        except TimeoutError:
            raise FetchError("TIMEOUT", "The website took too long to respond.", retryable=True) from None
        except FetchError as exc:
            logger.info("fetch_failed", extra={"url": url, "error": exc.code})
            raise
        logger.info(
            "fetch_ok",
            extra={
                "url": url,
                "final_url": result.url,
                "status": result.status_code,
                "bytes": len(result.body),
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return result

    def _client(self) -> httpx.AsyncClient:
        # trust_env=False: never route through proxies from the environment.
        return httpx.AsyncClient(
            transport=self._transport, timeout=TIMEOUT, follow_redirects=False, trust_env=False
        )

    async def _fetch_with_retries(self, client: httpx.AsyncClient, url: str, accept: set[str]) -> FetchResult:
        for attempt in range(self._retries + 1):
            try:
                return await self._fetch_once(client, url, accept, self._max_bytes)
            except FetchError as exc:
                if not exc.retryable or attempt == self._retries:
                    raise
                await asyncio.sleep(self._backoff * 2**attempt)
        raise AssertionError("unreachable")

    async def _fetch_once(
        self, client: httpx.AsyncClient, url: str, accept: set[str] | None, max_bytes: int, *, robots: bool = True
    ) -> FetchResult:
        target = _parse(url)
        for _ in range(MAX_REDIRECTS + 1):
            if robots and self._respect_robots and not await self._allowed_by_robots(client, target):
                raise FetchError("ROBOTS_DISALLOWED", "This website's robots.txt doesn't allow us to read this page.")

            response = await self._send(client, target)
            try:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise FetchError("HTTP_ERROR", "The website sent a broken redirect.")
                    try:
                        target = parse_public_url(urljoin(target.url, location))
                    except UnsafeUrlError as exc:
                        raise FetchError("REDIRECT_BLOCKED", f"Redirect blocked: {exc.message}") from None
                    continue
                return await self._read(response, target, accept, max_bytes)
            finally:
                await response.aclose()
        raise FetchError("TOO_MANY_REDIRECTS", "The website redirected too many times.")

    async def _send(self, client: httpx.AsyncClient, target: SafeUrl) -> httpx.Response:
        if not await self._host_limiter(target.host):
            raise FetchError("HOST_RATE_LIMITED", "Too many requests to this website. Try again shortly.")
        try:
            ips = await self._resolver(target.host, target.port)
        except UnsafeUrlError as exc:
            raise FetchError(exc.code, exc.message) from None

        ip = ips[0]
        ip_host = f"[{ip}]" if ":" in ip else ip
        request = client.build_request(
            "GET",
            f"{target.scheme}://{ip_host}:{target.port}{target.path_and_query}",
            headers={
                "Host": target.netloc,
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/rss+xml,application/atom+xml,application/xml;q=0.9,*/*;q=0.1",
            },
            extensions={"sni_hostname": target.host},
        )
        try:
            return await client.send(request, stream=True)
        except httpx.TimeoutException:
            raise FetchError("TIMEOUT", "The website took too long to respond.", retryable=True) from None
        except httpx.TransportError:
            raise FetchError("NETWORK_ERROR", "Couldn't connect to the website.", retryable=True) from None

    async def _read(
        self, response: httpx.Response, target: SafeUrl, accept: set[str] | None, max_bytes: int
    ) -> FetchResult:
        status = response.status_code
        if status in RETRY_STATUSES:
            raise FetchError("HTTP_ERROR", f"The website returned HTTP {status}.", retryable=True, status=status)
        if status >= 400:
            raise FetchError("HTTP_ERROR", f"The website returned HTTP {status}.", status=status)

        content_type, charset = _parse_content_type(response.headers.get("content-type", ""))
        if accept is not None and content_type not in accept:
            raise FetchError(
                "UNSUPPORTED_CONTENT_TYPE", f"Expected a web page or feed, got {content_type or 'unknown type'}."
            )

        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise FetchError("TOO_LARGE", "The page is too large to process.")

        chunks: list[bytes] = []
        total = 0
        try:
            # Counts decompressed bytes, so compression bombs hit the limit too.
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise FetchError("TOO_LARGE", "The page is too large to process.")
                chunks.append(chunk)
        except httpx.TimeoutException:
            raise FetchError("TIMEOUT", "The website took too long to respond.", retryable=True) from None
        except httpx.TransportError:
            raise FetchError("NETWORK_ERROR", "The connection dropped while reading.", retryable=True) from None

        return FetchResult(target.url, status, content_type, charset, b"".join(chunks))

    async def _allowed_by_robots(self, client: httpx.AsyncClient, target: SafeUrl) -> bool:
        now = time.monotonic()
        cached = _robots_cache.get(target.origin)
        if cached is None or cached[0] < now:
            parser = await self._load_robots(client, target)
            if len(_robots_cache) > 1000:
                _robots_cache.clear()
            _robots_cache[target.origin] = (now + ROBOTS_TTL_SECONDS, parser)
            cached = _robots_cache[target.origin]
        parser = cached[1]
        return parser is None or parser.can_fetch(ROBOTS_AGENT, target.url)

    async def _load_robots(self, client: httpx.AsyncClient, target: SafeUrl) -> RobotFileParser | None:
        """None means no rules apply (no robots.txt)."""
        try:
            result = await self._fetch_once(
                client, f"{target.origin}/robots.txt", None, MAX_ROBOTS_BYTES, robots=False
            )
        except FetchError as exc:
            if exc.status is not None and 400 <= exc.status < 500 and not exc.retryable:
                return None  # 4xx: the site has no robots.txt
            if exc.retryable or (exc.status is not None and exc.status >= 500):
                # Server errors or unreachable: be conservative and don't crawl for now.
                raise FetchError(
                    "ROBOTS_UNAVAILABLE", "Couldn't read this website's robots.txt. Try again later.", retryable=True
                ) from None
            raise  # security blocks and rate limits keep their own reason
        parser = RobotFileParser()
        parser.parse(result.body.decode("utf-8", errors="replace").splitlines())
        return parser


def _parse(url: str) -> SafeUrl:
    try:
        return parse_public_url(url)
    except UnsafeUrlError as exc:
        raise FetchError(exc.code, exc.message) from None


def _parse_content_type(header: str) -> tuple[str, str | None]:
    mime, _, params = header.partition(";")
    charset = None
    for param in params.split(";"):
        key, _, value = param.strip().partition("=")
        if key.lower() == "charset" and value:
            charset = value.strip("\"' ").lower()
    return mime.strip().lower(), charset


def clear_robots_cache() -> None:
    _robots_cache.clear()

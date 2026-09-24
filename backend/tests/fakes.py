"""A fake internet for fetcher tests: fake DNS plus an in-memory HTTP server."""

import ipaddress

import httpx

from app.research.fetcher import SafeFetcher
from app.utils.urls import UnsafeUrlError, is_public_ip

PUBLIC_IP = "93.184.216.34"

HTML = "text/html; charset=utf-8"
RSS = "application/rss+xml"


def page(body: str | bytes, content_type: str = HTML, status: int = 200, **headers) -> httpx.Response:
    content = body.encode() if isinstance(body, str) else body
    return httpx.Response(status, headers={"content-type": content_type, **headers}, content=content)


def redirect(location: str, status: int = 302) -> httpx.Response:
    return httpx.Response(status, headers={"location": location})


class FakeInternet:
    def __init__(self, dns: dict[str, list[str]] | None = None) -> None:
        self.dns = {"example.com": [PUBLIC_IP], "other.example": ["93.184.216.35"], **(dns or {})}
        self.routes: dict[tuple[str, str], object] = {}
        self.requests: list[httpx.Request] = []

    def add(self, host: str, path: str, response) -> None:
        """`response` is an httpx.Response or a callable(request) -> httpx.Response."""
        self.routes[(host, path)] = response

    async def resolve(self, host: str, port: int) -> list[str]:
        if host not in self.dns:
            raise UnsafeUrlError("URL_DNS_FAILED", "This website's address could not be found.")
        addresses = self.dns[host]
        if not all(is_public_ip(ipaddress.ip_address(a)) for a in addresses):
            raise UnsafeUrlError("URL_HOST_NOT_ALLOWED", "This website resolves to a private or reserved address.")
        return addresses

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get((request.headers["host"], request.url.path))
        if route is None:
            return httpx.Response(404)
        return route(request) if callable(route) else route

    def requests_to(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == path]

    def fetcher(self, **overrides) -> SafeFetcher:
        async def allow_all(_host: str) -> bool:
            return True

        options = {
            "resolver": self.resolve,
            "transport": httpx.MockTransport(self._handle),
            "host_limiter": allow_all,
            "backoff_seconds": 0,
            **overrides,
        }
        return SafeFetcher(**options)

import asyncio
import socket

import pytest

from app.utils.urls import UnsafeUrlError, parse_public_url, resolve_public_ips


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTPS://Example.COM:443/a?b=1#frag", "https://example.com/a?b=1"),
        ("https://example.com", "https://example.com/"),
        ("  http://example.com/path  ", "http://example.com/path"),
        ("https://bücher.de/", "https://xn--bcher-kva.de/"),
        ("https://8.8.8.8/dns", "https://8.8.8.8/dns"),
        ("https://[2606:4700:4700::1111]/", "https://[2606:4700:4700::1111]/"),
    ],
)
def test_normalizes_public_urls(raw, expected):
    assert parse_public_url(raw).url == expected


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("", "URL_INVALID"),
        ("http://", "URL_INVALID"),
        ("ftp://example.com/file", "URL_SCHEME_NOT_ALLOWED"),
        ("file:///etc/passwd", "URL_SCHEME_NOT_ALLOWED"),
        ("javascript:alert(1)", "URL_SCHEME_NOT_ALLOWED"),
        ("gopher://example.com/", "URL_SCHEME_NOT_ALLOWED"),
        ("http://user:pass@example.com/", "URL_CREDENTIALS_NOT_ALLOWED"),
        ("http://example.com:8080/", "URL_PORT_NOT_ALLOWED"),
        ("http://example.com:22/", "URL_PORT_NOT_ALLOWED"),
        ("http://example.com:6379/", "URL_PORT_NOT_ALLOWED"),
        ("http://localhost/", "URL_HOST_NOT_ALLOWED"),
        ("http://admin.localhost/", "URL_HOST_NOT_ALLOWED"),
        ("http://db.internal/", "URL_HOST_NOT_ALLOWED"),
        ("http://printer.local/", "URL_HOST_NOT_ALLOWED"),
        ("http://127.0.0.1/", "URL_HOST_NOT_ALLOWED"),
        ("http://10.0.0.5/", "URL_HOST_NOT_ALLOWED"),
        ("http://172.16.0.1/", "URL_HOST_NOT_ALLOWED"),
        ("http://192.168.1.1/", "URL_HOST_NOT_ALLOWED"),
        ("http://169.254.169.254/latest/meta-data/", "URL_HOST_NOT_ALLOWED"),
        ("http://100.64.0.1/", "URL_HOST_NOT_ALLOWED"),
        ("http://0.0.0.0/", "URL_HOST_NOT_ALLOWED"),
        ("http://[::1]/", "URL_HOST_NOT_ALLOWED"),
        ("http://[::ffff:127.0.0.1]/", "URL_HOST_NOT_ALLOWED"),
        ("http://[fd00::1]/", "URL_HOST_NOT_ALLOWED"),
        ("http://[fe80::1]/", "URL_HOST_NOT_ALLOWED"),
        ("http://224.0.0.1/", "URL_HOST_NOT_ALLOWED"),
    ],
)
def test_rejects_unsafe_urls(raw, code):
    with pytest.raises(UnsafeUrlError) as exc:
        parse_public_url(raw)
    assert exc.value.code == code


def _fake_getaddrinfo(mapping):
    async def getaddrinfo(host, port, **_):
        if host not in mapping:
            raise socket.gaierror("not found")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in mapping[host]]

    return getaddrinfo


async def test_resolve_returns_public_addresses(monkeypatch):
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", _fake_getaddrinfo({"example.com": ["93.184.216.34", "93.184.216.34"]}))
    assert await resolve_public_ips("example.com", 443) == ["93.184.216.34"]


@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],  # e.g. a public name pointed at loopback, or "2130706433"
        ["10.1.2.3"],
        ["93.184.216.34", "10.1.2.3"],  # one bad address poisons the lot
        ["169.254.169.254"],
        ["fe80::1%eth0"],
    ],
)
async def test_resolve_rejects_any_private_address(monkeypatch, addresses):
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", _fake_getaddrinfo({"sneaky.example": addresses}))
    with pytest.raises(UnsafeUrlError) as exc:
        await resolve_public_ips("sneaky.example", 443)
    assert exc.value.code == "URL_HOST_NOT_ALLOWED"


async def test_resolve_reports_unknown_hosts(monkeypatch):
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", _fake_getaddrinfo({}))
    with pytest.raises(UnsafeUrlError) as exc:
        await resolve_public_ips("nope.example", 443)
    assert exc.value.code == "URL_DNS_FAILED"

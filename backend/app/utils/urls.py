"""URL validation and SSRF protection (spec section 67).

A user-supplied URL is only ever fetched if it is http(s), on a standard port,
carries no credentials, and its host resolves exclusively to public addresses.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ALLOWED_SCHEMES = {"http", "https"}
DEFAULT_PORTS = {"http": 80, "https": 443}
# Internal services commonly listen on other ports; outside sites rarely do.
ALLOWED_PORTS = {80, 443}
BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".corp")
MAX_URL_LENGTH = 2048


class UnsafeUrlError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SafeUrl:
    url: str  # normalized, fragment removed
    scheme: str
    host: str  # lowercase ASCII (IDNA) or bare IP literal
    port: int

    @property
    def origin(self) -> str:
        return f"{self.scheme}://{self.netloc}"

    @property
    def netloc(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return host if self.port == DEFAULT_PORTS[self.scheme] else f"{host}:{self.port}"

    @property
    def path_and_query(self) -> str:
        parts = urlsplit(self.url)
        return parts.path + (f"?{parts.query}" if parts.query else "")


def is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        # IPv6 forms that embed an IPv4 address are judged by that address.
        embedded = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if embedded is not None:
            return is_public_ip(embedded)
    return ip.is_global and not ip.is_multicast


def parse_public_url(raw: str) -> SafeUrl:
    """Syntax and policy checks. Does not touch the network."""
    raw = raw.strip()
    if not raw or len(raw) > MAX_URL_LENGTH:
        raise UnsafeUrlError("URL_INVALID", "Enter a valid URL.")

    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        raise UnsafeUrlError("URL_INVALID", "Enter a valid URL.") from None

    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError("URL_SCHEME_NOT_ALLOWED", "Only http and https URLs are allowed.")
    if parts.username is not None or parts.password is not None:
        raise UnsafeUrlError("URL_CREDENTIALS_NOT_ALLOWED", "URLs can't contain a username or password.")

    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise UnsafeUrlError("URL_INVALID", "Enter a valid URL.")

    port = port or DEFAULT_PORTS[scheme]
    if port not in ALLOWED_PORTS:
        raise UnsafeUrlError("URL_PORT_NOT_ALLOWED", "Only standard web ports (80 and 443) are allowed.")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None

    if ip is not None:
        if not is_public_ip(ip):
            raise UnsafeUrlError("URL_HOST_NOT_ALLOWED", "This address is private or reserved.")
        host = str(ip)
    else:
        if host == "localhost" or host.endswith(BLOCKED_HOST_SUFFIXES):
            raise UnsafeUrlError("URL_HOST_NOT_ALLOWED", "Internal hostnames are not allowed.")
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            raise UnsafeUrlError("URL_INVALID", "Enter a valid URL.") from None

    safe = SafeUrl(url="", scheme=scheme, host=host, port=port)
    url = urlunsplit((scheme, safe.netloc, parts.path or "/", parts.query, ""))
    return SafeUrl(url=url, scheme=scheme, host=host, port=port)


Resolver = Callable[[str, int], Awaitable[list[str]]]


async def resolve_public_ips(host: str, port: int) -> list[str]:
    """Every address the host resolves to must be public, or none are used."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not is_public_ip(ip):
            raise UnsafeUrlError("URL_HOST_NOT_ALLOWED", "This address is private or reserved.")
        return [str(ip)]

    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise UnsafeUrlError("URL_DNS_FAILED", "This website's address could not be found.") from None

    addresses = list(dict.fromkeys(info[4][0].split("%")[0] for info in infos))
    if not addresses or not all(is_public_ip(ipaddress.ip_address(a)) for a in addresses):
        raise UnsafeUrlError("URL_HOST_NOT_ALLOWED", "This website resolves to a private or reserved address.")
    return addresses


async def check_public_url(raw: str, resolver: Resolver = resolve_public_ips) -> SafeUrl:
    """Full check for user input: syntax, policy and DNS."""
    safe = parse_public_url(raw)
    await resolver(safe.host, safe.port)
    return safe


TRACKING_PARAMS = {"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "igshid", "ref", "ref_src", "_hsenc", "_hsmi"}


def strip_tracking_params(url: str) -> str:
    """Removes utm_* and other click-tracking parameters so the same article has one URL."""
    parts = urlsplit(url)
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMS
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))

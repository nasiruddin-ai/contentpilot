"""LinkedIn: OAuth 2.0 (3-legged) and posting to a member's profile via the Posts API.

Docs (read 2026-09-24):
- OAuth: https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow
- Posts: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api
- Text format: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/little-text-format

Self-serve products: "Share on LinkedIn" (w_member_social) and "Sign In with LinkedIn
using OpenID Connect" (openid, profile). Access tokens last 60 days; refresh tokens are
only for approved partners, so members reconnect when a token expires.
"""

from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

import httpx

from app.core.config import get_settings

AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
API_URL = "https://api.linkedin.com"
SCOPES = ("openid", "profile", "w_member_social")

TIMEOUT = httpx.Timeout(30.0, connect=10.0)
UPLOAD_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
# Upload URLs come from LinkedIn's API; our token is only ever sent to LinkedIn's own hosts.
UPLOAD_HOSTS = ("linkedin.com", "licdn.com")

# "little" text format: these characters must be backslash-escaped to stay plain text.
_RESERVED = set("\\|{}@[]()<>#*_~")

# Swapped in tests for an httpx.MockTransport.
transport_factory = lambda: None  # noqa: E731


class LinkedInError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status = status


@dataclass
class TokenGrant:
    access_token: str
    expires_in: int
    scope: str
    refresh_token: str | None = None
    refresh_token_expires_in: int | None = None


@dataclass
class Member:
    id: str
    name: str

    @property
    def urn(self) -> str:
        return f"urn:li:person:{self.id}"


def escape_text(text: str) -> str:
    return "".join(f"\\{c}" if c in _RESERVED else c for c in text)


def hashtag(tag: str) -> str:
    return "{hashtag|\\#|" + escape_text(tag) + "}"


def commentary(hook: str, body: str, cta: str | None, hashtags: list[str]) -> str:
    parts = [escape_text(p.strip()) for p in (hook, body, cta or "") if p and p.strip()]
    if hashtags:
        parts.append(" ".join(hashtag(t) for t in hashtags))
    return "\n\n".join(parts)


def post_url(post_urn: str) -> str:
    return f"https://www.linkedin.com/feed/update/{post_urn}/"


def authorization_url(redirect_uri: str, state: str) -> str:
    settings = get_settings()
    query = {
        "response_type": "code",
        "client_id": settings.linkedin_client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": " ".join(SCOPES),
    }
    return f"{AUTH_URL}?{urlencode(query)}"


class LinkedInClient:
    def __init__(self, access_token: str | None = None) -> None:
        self._token = access_token

    def _client(self, timeout: httpx.Timeout = TIMEOUT) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport_factory(), timeout=timeout, trust_env=False)

    async def exchange_code(self, code: str, redirect_uri: str) -> TokenGrant:
        settings = get_settings()
        secret = settings.linkedin_client_secret.get_secret_value() if settings.linkedin_client_secret else ""
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": settings.linkedin_client_id,
            "client_secret": secret,
            "redirect_uri": redirect_uri,
        }
        async with self._client() as client:
            response = await self._send(client, "POST", TOKEN_URL, data=data, versioned=False, auth=False)
        body = response.json()
        return TokenGrant(
            access_token=body["access_token"],
            expires_in=int(body.get("expires_in", 0)),
            scope=body.get("scope", ""),
            refresh_token=body.get("refresh_token"),
            refresh_token_expires_in=body.get("refresh_token_expires_in"),
        )

    async def member(self) -> Member:
        async with self._client() as client:
            response = await self._send(client, "GET", f"{API_URL}/v2/userinfo", versioned=False)
        body = response.json()
        return Member(id=str(body["sub"]), name=body.get("name") or "LinkedIn member")

    async def upload_image(self, owner_urn: str, data: bytes) -> str:
        return await self._upload("images", "image", owner_urn, data)

    async def upload_document(self, owner_urn: str, data: bytes) -> str:
        return await self._upload("documents", "document", owner_urn, data)

    async def create_post(self, author_urn: str, text: str, media: dict | None = None) -> str:
        """Publishes and returns the post URN. `media` is {"id": asset URN, "title"?, "altText"?}."""
        payload: dict = {
            "author": author_urn,
            "commentary": text,
            "visibility": "PUBLIC",
            "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        if media:
            payload["content"] = {"media": {key: value for key, value in media.items() if value}}
        async with self._client() as client:
            response = await self._send(client, "POST", f"{API_URL}/rest/posts", json=payload)
        post_urn = response.headers.get("x-restli-id")
        if not post_urn:
            raise LinkedInError("PUBLISH_UNCONFIRMED", "LinkedIn accepted the post but returned no post ID.")
        return post_urn

    async def _upload(self, resource: str, field: str, owner_urn: str, data: bytes) -> str:
        async with self._client(UPLOAD_TIMEOUT) as client:
            response = await self._send(
                client,
                "POST",
                f"{API_URL}/rest/{resource}?action=initializeUpload",
                json={"initializeUploadRequest": {"owner": owner_urn}},
            )
            value = response.json().get("value", {})
            upload_url, urn = value.get("uploadUrl"), value.get(field)
            if not upload_url or not urn:
                raise LinkedInError("UPLOAD_FAILED", f"LinkedIn didn't return an upload slot for the {field}.")
            host = urlsplit(upload_url).hostname or ""
            if urlsplit(upload_url).scheme != "https" or not any(
                host == h or host.endswith("." + h) for h in UPLOAD_HOSTS
            ):
                raise LinkedInError("UPLOAD_FAILED", "LinkedIn returned an unexpected upload address.")
            await self._send(client, "PUT", upload_url, content=data, versioned=False)
        return urn

    async def _send(
        self, client: httpx.AsyncClient, method: str, url: str, *, versioned: bool = True, auth: bool = True, **kwargs
    ) -> httpx.Response:
        headers = {}
        if auth:
            if not self._token:
                raise LinkedInError("NOT_CONNECTED", "No LinkedIn access token.")
            headers["Authorization"] = f"Bearer {self._token}"
        if versioned:
            headers["LinkedIn-Version"] = get_settings().linkedin_api_version
            headers["X-Restli-Protocol-Version"] = "2.0.0"
        try:
            response = await client.request(method, url, headers=headers, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise LinkedInError("CONNECT_FAILED", "Couldn't connect to LinkedIn.", retryable=True) from None
        except httpx.TimeoutException:
            raise LinkedInError("TIMEOUT", "LinkedIn took too long to respond.", retryable=True) from None
        except httpx.TransportError:
            raise LinkedInError("NETWORK_ERROR", "Couldn't reach LinkedIn.", retryable=True) from None
        if response.status_code < 300:
            return response
        raise self._error(response)

    def _error(self, response: httpx.Response) -> LinkedInError:
        status = response.status_code
        try:
            body = response.json()
            detail = str(body.get("message") or body.get("error_description") or body.get("error") or "")
        except ValueError:
            detail = ""
        if self._token:
            detail = detail.replace(self._token, "[redacted]")
        detail = detail[:300]
        if status == 401:
            return LinkedInError("RECONNECT_REQUIRED", "LinkedIn rejected the saved login. Reconnect LinkedIn.", status=status)
        if status == 403:
            return LinkedInError(
                "PERMISSION_DENIED", f"LinkedIn refused this action for the connected account. {detail}".strip(), status=status
            )
        if status == 429:
            return LinkedInError("RATE_LIMITED", "LinkedIn's rate limit was reached.", retryable=True, status=status)
        if status >= 500:
            return LinkedInError("PROVIDER_ERROR", f"LinkedIn had an error (HTTP {status}).", retryable=True, status=status)
        return LinkedInError("REJECTED", f"LinkedIn rejected the request. {detail}".strip(), status=status)

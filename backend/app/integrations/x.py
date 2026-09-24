"""X (Twitter): OAuth 2.0 authorization code flow with PKCE, token refresh, and posting.

Docs (read 2026-09-24):
- OAuth: https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code
- Create post: https://docs.x.com/x-api/posts/create-post

X API access is pay-per-use for new developers (no free tier since February 2026).
Access tokens last 2 hours; with `offline.access` X issues a refresh token, and each
refresh returns a new one, so the newest must always be saved.
"""

import base64
import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings

AUTH_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
API_URL = "https://api.x.com"
SCOPES = ("tweet.read", "tweet.write", "users.read", "offline.access")
TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# Swapped in tests for an httpx.MockTransport.
transport_factory = lambda: None  # noqa: E731


class XError(Exception):
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


@dataclass
class XUser:
    id: str
    name: str
    username: str


def new_code_verifier() -> str:
    """PKCE secret: 43-128 URL-safe characters."""
    return secrets.token_urlsafe(64)[:96]


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorization_url(redirect_uri: str, state: str, verifier: str) -> str:
    query = {
        "response_type": "code",
        "client_id": get_settings().x_client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": code_challenge(verifier),
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urlencode(query)}"


def post_url(post_id: str) -> str:
    return f"https://x.com/i/web/status/{post_id}"


class XClient:
    def __init__(self, access_token: str | None = None) -> None:
        self._token = access_token

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport_factory(), timeout=TIMEOUT, trust_env=False)

    async def exchange_code(self, code: str, redirect_uri: str, verifier: str) -> TokenGrant:
        return await self._token_request(
            {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri, "code_verifier": verifier}
        )

    async def refresh(self, refresh_token: str) -> TokenGrant:
        return await self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})

    async def me(self) -> XUser:
        response = await self._api("GET", "/2/users/me")
        data = response.json().get("data") or {}
        return XUser(id=str(data["id"]), name=data.get("name") or "", username=data.get("username") or "")

    async def create_post(self, text: str) -> str:
        response = await self._api("POST", "/2/tweets", json={"text": text})
        post_id = (response.json().get("data") or {}).get("id")
        if not post_id:
            raise XError("PUBLISH_UNCONFIRMED", "X accepted the post but returned no post ID.")
        return str(post_id)

    async def _token_request(self, data: dict) -> TokenGrant:
        settings = get_settings()
        secret = settings.x_client_secret.get_secret_value() if settings.x_client_secret else ""
        # Confidential (Web App) clients authenticate with HTTP Basic.
        auth = httpx.BasicAuth(settings.x_client_id, secret)
        async with self._client() as client:
            try:
                response = await client.post(TOKEN_URL, data={**data, "client_id": settings.x_client_id}, auth=auth)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                raise XError("CONNECT_FAILED", "Couldn't connect to X.", retryable=True) from None
            except httpx.TimeoutException:
                raise XError("TIMEOUT", "X took too long to respond.", retryable=True) from None
            except httpx.TransportError:
                raise XError("NETWORK_ERROR", "Couldn't reach X.", retryable=True) from None
        if response.status_code >= 300:
            if response.status_code in (400, 401):
                # Bad or expired code/refresh token: only a fresh sign-in helps.
                raise XError("RECONNECT_REQUIRED", "X rejected the sign-in. Reconnect X.", status=response.status_code)
            raise self._error(response)
        body = response.json()
        return TokenGrant(
            access_token=body["access_token"],
            expires_in=int(body.get("expires_in", 0)),
            scope=body.get("scope", ""),
            refresh_token=body.get("refresh_token"),
        )

    async def _api(self, method: str, path: str, **kwargs) -> httpx.Response:
        if not self._token:
            raise XError("NOT_CONNECTED", "No X access token.")
        async with self._client() as client:
            try:
                response = await client.request(
                    method, f"{API_URL}{path}", headers={"Authorization": f"Bearer {self._token}"}, **kwargs
                )
            except (httpx.ConnectError, httpx.ConnectTimeout):
                raise XError("CONNECT_FAILED", "Couldn't connect to X.", retryable=True) from None
            except httpx.TimeoutException:
                raise XError("TIMEOUT", "X took too long to respond.", retryable=True) from None
            except httpx.TransportError:
                raise XError("NETWORK_ERROR", "Couldn't reach X.", retryable=True) from None
        if response.status_code < 300:
            return response
        raise self._error(response)

    def _error(self, response: httpx.Response) -> XError:
        status = response.status_code
        try:
            body = response.json()
            detail = str(body.get("detail") or body.get("title") or body.get("error_description") or body.get("error") or "")
        except ValueError:
            detail = ""
        if self._token:
            detail = detail.replace(self._token, "[redacted]")
        detail = detail[:300]
        if status == 401:
            return XError("RECONNECT_REQUIRED", "X rejected the saved sign-in. Reconnect X.", status=status)
        if status == 402:
            return XError("NO_CREDITS", f"X needs API credits to post. Add credits at console.x.com. {detail}".strip(), status=status)
        if status == 403:
            return XError("PERMISSION_DENIED", f"X refused this action. {detail}".strip(), status=status)
        if status == 429:
            return XError("RATE_LIMITED", "X's rate limit was reached.", retryable=True, status=status)
        if status >= 500:
            return XError("PROVIDER_ERROR", f"X had an error (HTTP {status}).", retryable=True, status=status)
        return XError("REJECTED", f"X rejected the request. {detail}".strip(), status=status)

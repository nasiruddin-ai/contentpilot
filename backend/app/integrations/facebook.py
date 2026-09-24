"""Facebook Pages: Facebook Login, long-lived Page tokens, and publishing to a Page.

Docs (read 2026-09-24):
- Login: https://developers.facebook.com/docs/facebook-login/guides/advanced/manual-flow
- Long-lived tokens: https://developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived
- Page posts: https://developers.facebook.com/docs/pages-api/posts

Flow: login code → short-lived user token → long-lived user token (~60 days) →
Page access token from /me/accounts. Page tokens derived from a long-lived user token
don't expire; they're invalidated only by events such as revoked access.
"""

import hashlib
import hmac
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings

SCOPES = ("pages_show_list", "pages_manage_posts", "pages_read_engagement")
REQUIRED = {"pages_show_list", "pages_manage_posts"}
MAX_PHOTOS = 10
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
UPLOAD_TIMEOUT = httpx.Timeout(120.0, connect=10.0)

# Graph error codes (https://developers.facebook.com/docs/graph-api/guides/error-handling).
_TOKEN_INVALID = {190, 102}
_PERMISSION = {10, 200, 3}
_THROTTLED = {4, 17, 32, 613, 80001}
_TRANSIENT = {1, 2}

# Swapped in tests for an httpx.MockTransport.
transport_factory = lambda: None  # noqa: E731


class FacebookError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status = status


@dataclass
class Page:
    id: str
    name: str
    access_token: str
    tasks: list[str]


def _graph() -> str:
    return f"https://graph.facebook.com/{get_settings().facebook_graph_version}"


def _secret() -> str:
    secret = get_settings().facebook_client_secret
    return secret.get_secret_value() if secret else ""


def appsecret_proof(token: str) -> str:
    """Signs server calls, so a leaked token alone can't be used (recommended by Meta)."""
    return hmac.new(_secret().encode(), token.encode(), hashlib.sha256).hexdigest()


def authorization_url(redirect_uri: str, state: str) -> str:
    settings = get_settings()
    query = {
        "client_id": settings.facebook_client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "response_type": "code",
    }
    if settings.facebook_login_config_id:
        # Facebook Login for Business: "config_id has replaced scope".
        query["config_id"] = settings.facebook_login_config_id
        query["override_default_response_type"] = "true"
    else:
        query["scope"] = ",".join(SCOPES)
    return f"https://www.facebook.com/{settings.facebook_graph_version}/dialog/oauth?{urlencode(query)}"


def post_url(post_id: str) -> str:
    return f"https://www.facebook.com/{post_id}"


class FacebookClient:
    def __init__(self, access_token: str | None = None) -> None:
        self._token = access_token

    def _client(self, timeout: httpx.Timeout = TIMEOUT) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport_factory(), timeout=timeout, trust_env=False)

    async def exchange_code(self, code: str, redirect_uri: str) -> str:
        """Returns a long-lived user token."""
        settings = get_settings()
        short = await self._call(
            "GET",
            "/oauth/access_token",
            params={"client_id": settings.facebook_client_id, "client_secret": _secret(), "redirect_uri": redirect_uri, "code": code},
            auth=False,
        )
        long = await self._call(
            "GET",
            "/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": settings.facebook_client_id,
                "client_secret": _secret(),
                "fb_exchange_token": short["access_token"],
            },
            auth=False,
        )
        return long["access_token"]

    async def granted_permissions(self) -> set[str]:
        data = await self._call("GET", "/me/permissions")
        return {p["permission"] for p in data.get("data", []) if p.get("status") == "granted"}

    async def pages(self) -> list[Page]:
        data = await self._call("GET", "/me/accounts", params={"fields": "id,name,access_token,tasks", "limit": "100"})
        return [
            Page(id=str(p["id"]), name=p.get("name", ""), access_token=p["access_token"], tasks=p.get("tasks") or [])
            for p in data.get("data", [])
            if p.get("access_token")
        ]

    async def upload_photo(self, page_id: str, image: bytes) -> str:
        """Uploads an unpublished photo, to attach to a feed post."""
        data = await self._call(
            "POST",
            f"/{page_id}/photos",
            data={"published": "false"},
            files={"source": ("image.png", image, "image/png")},
            timeout=UPLOAD_TIMEOUT,
        )
        return str(data["id"])

    async def create_post(self, page_id: str, message: str, photo_ids: list[str] | None = None) -> str:
        form = {"message": message}
        for index, photo_id in enumerate(photo_ids or []):
            form[f"attached_media[{index}]"] = f'{{"media_fbid":"{photo_id}"}}'
        data = await self._call("POST", f"/{page_id}/feed", data=form)
        post_id = data.get("id")
        if not post_id:
            raise FacebookError("PUBLISH_UNCONFIRMED", "Facebook accepted the post but returned no post ID.")
        return str(post_id)

    async def _call(self, method: str, path: str, *, params: dict | None = None, auth: bool = True, timeout=TIMEOUT, **kwargs) -> dict:
        params = dict(params or {})
        if auth:
            if not self._token:
                raise FacebookError("NOT_CONNECTED", "No Facebook access token.")
            params["access_token"] = self._token
            params["appsecret_proof"] = appsecret_proof(self._token)
        async with self._client(timeout) as client:
            try:
                response = await client.request(method, f"{_graph()}{path}", params=params, **kwargs)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                raise FacebookError("CONNECT_FAILED", "Couldn't connect to Facebook.", retryable=True) from None
            except httpx.TimeoutException:
                raise FacebookError("TIMEOUT", "Facebook took too long to respond.", retryable=True) from None
            except httpx.TransportError:
                raise FacebookError("NETWORK_ERROR", "Couldn't reach Facebook.", retryable=True) from None
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code < 300 and "error" not in body:
            return body
        raise self._error(response.status_code, body.get("error") or {})

    def _error(self, status: int, error: dict) -> FacebookError:
        code = error.get("code")
        # Code 1 is also used for bad app credentials ("Error validating client secret"),
        # which is a setup problem, not a temporary one.
        if error.get("type") == "OAuthException" and code == 1:
            code = None
        detail = str(error.get("message") or "")
        for secret in (self._token, _secret()):
            if secret:
                detail = detail.replace(secret, "[redacted]")
        detail = detail[:300]
        if code in _TOKEN_INVALID or status == 401:
            return FacebookError("RECONNECT_REQUIRED", "Facebook rejected the saved login. Reconnect Facebook.", status=status)
        if code in _PERMISSION or status == 403:
            return FacebookError("PERMISSION_DENIED", f"Facebook refused this action. {detail}".strip(), status=status)
        if code in _THROTTLED or status == 429:
            return FacebookError("RATE_LIMITED", "Facebook's rate limit was reached.", retryable=True, status=status)
        if code in _TRANSIENT or status >= 500:
            return FacebookError("PROVIDER_ERROR", f"Facebook had a temporary error. {detail}".strip(), retryable=True, status=status if status >= 500 else 503)
        return FacebookError("REJECTED", f"Facebook rejected the request. {detail}".strip(), status=status)

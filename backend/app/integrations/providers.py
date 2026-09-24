"""One interface over each social platform's OAuth flow (spec sections 45-46)."""

from dataclasses import dataclass

from app.core.config import get_settings
from app.integrations import facebook, linkedin, x


class ProviderError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass
class Connection:
    account_id: str
    account_name: str
    access_token: str
    refresh_token: str | None
    expires_in: int
    scope: str


def _granted(scope: str) -> set[str]:
    return set(scope.replace(",", " ").split())


class LinkedInProvider:
    name = "linkedin"

    def configured(self) -> bool:
        settings = get_settings()
        return bool(settings.linkedin_client_id and settings.linkedin_client_secret)

    def authorization_url(self, redirect_uri: str, state: str, verifier: str) -> str:
        return linkedin.authorization_url(redirect_uri, state)

    async def connect(self, code: str, redirect_uri: str, verifier: str) -> Connection:
        try:
            grant = await linkedin.LinkedInClient().exchange_code(code, redirect_uri)
            if "w_member_social" not in _granted(grant.scope):
                raise ProviderError(
                    "OAUTH_MISSING_SCOPE",
                    "LinkedIn didn't grant posting permission. Add the 'Share on LinkedIn' product to your LinkedIn app.",
                )
            member = await linkedin.LinkedInClient(grant.access_token).member()
        except linkedin.LinkedInError as exc:
            raise ProviderError(exc.code, exc.message, retryable=exc.retryable) from None
        return Connection(member.id, member.name, grant.access_token, grant.refresh_token, grant.expires_in, grant.scope)


class XProvider:
    name = "x"

    def configured(self) -> bool:
        settings = get_settings()
        return bool(settings.x_client_id and settings.x_client_secret)

    def authorization_url(self, redirect_uri: str, state: str, verifier: str) -> str:
        return x.authorization_url(redirect_uri, state, verifier)

    async def connect(self, code: str, redirect_uri: str, verifier: str) -> Connection:
        try:
            grant = await x.XClient().exchange_code(code, redirect_uri, verifier)
            missing = {"tweet.write", "offline.access"} - _granted(grant.scope)
            if missing:
                raise ProviderError(
                    "OAUTH_MISSING_SCOPE",
                    f"X didn't grant {', '.join(sorted(missing))}. Set the app's permissions to 'Read and write'.",
                )
            user = await x.XClient(grant.access_token).me()
        except x.XError as exc:
            raise ProviderError(exc.code, exc.message, retryable=exc.retryable) from None
        name = f"{user.name} (@{user.username})" if user.username else user.name
        return Connection(user.id, name, grant.access_token, grant.refresh_token, grant.expires_in, grant.scope)


class FacebookProvider:
    name = "facebook"

    def configured(self) -> bool:
        settings = get_settings()
        return bool(settings.facebook_client_id and settings.facebook_client_secret)

    def authorization_url(self, redirect_uri: str, state: str, verifier: str) -> str:
        return facebook.authorization_url(redirect_uri, state)

    async def connect(self, code: str, redirect_uri: str, verifier: str) -> Connection:
        try:
            user_token = await facebook.FacebookClient().exchange_code(code, redirect_uri)
            user = facebook.FacebookClient(user_token)
            granted = await user.granted_permissions()
            missing = facebook.REQUIRED - granted
            if missing:
                raise ProviderError(
                    "OAUTH_MISSING_SCOPE",
                    f"Facebook didn't grant {', '.join(sorted(missing))}. It granted: {', '.join(sorted(granted)) or 'nothing'}. "
                    "Add the missing permission to your Facebook Login configuration, then reconnect.",
                )
            pages = [p for p in await user.pages() if "CREATE_CONTENT" in p.tasks or not p.tasks]
        except facebook.FacebookError as exc:
            raise ProviderError(exc.code, exc.message, retryable=exc.retryable) from None
        if not pages:
            raise ProviderError(
                "OAUTH_MISSING_SCOPE",
                "No Facebook Page was shared, or you can't create content on it. Reconnect and select your Page.",
            )
        # One Page per brand: the first one granted. Select only the brand's Page in Facebook's dialog.
        page = pages[0]
        # Page tokens made from a long-lived user token don't expire (expires_in 0 = no expiry).
        return Connection(page.id, page.name, page.access_token, None, 0, ",".join(sorted(granted)))


PROVIDERS = {provider.name: provider for provider in (LinkedInProvider(), XProvider(), FacebookProvider())}

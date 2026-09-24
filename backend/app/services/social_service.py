"""Connecting social accounts with OAuth (spec section 46).

Flow: connect → platform consent → callback → exchange code → encrypt tokens → save.
The `state` value is random, bound to the user and brand in Redis, and single-use,
so a forged or replayed callback can't attach an account to someone else's brand.
For platforms using PKCE (X), the code verifier lives only in that Redis entry.
"""

import json
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.redis import get_redis
from app.integrations import x
from app.integrations.providers import PROVIDERS, ProviderError
from app.models import Brand, User
from app.models.social_account import SocialAccount, SocialAccountStatus
from app.services import brand_service

logger = logging.getLogger(__name__)

STATE_TTL_SECONDS = 10 * 60
SUPPORTED_PLATFORMS = tuple(PROVIDERS)
# The member said no; anything else is a setup or platform-side problem.
USER_CANCELLED = {"user_cancelled_login", "user_cancelled_authorize", "access_denied"}


def redirect_uri(platform: str) -> str:
    return f"{get_settings().api_url.rstrip('/')}/api/v1/social/{platform}/callback"


def _provider(platform: str):
    provider = PROVIDERS.get(platform)
    if provider is None:
        raise AppError("PLATFORM_NOT_SUPPORTED", f"Connecting {platform} isn't available yet.", 422)
    return provider


LABELS = {"linkedin": "LinkedIn", "x": "X", "facebook": "Facebook"}


def platform_label(platform: str) -> str:
    return LABELS.get(platform, platform.title())


def is_usable(account: SocialAccount) -> bool:
    """Active, and either unexpired or renewable with a refresh token."""
    if account.status != SocialAccountStatus.ACTIVE:
        return False
    expired = account.token_expires_at is not None and account.token_expires_at <= datetime.now(UTC)
    return not expired or bool(account.refresh_token_encrypted)


async def start_connect(db: AsyncSession, user: User, brand_id: uuid.UUID, platform: str) -> str:
    """Returns the URL to send the user's browser to."""
    provider = _provider(platform)
    await brand_service.get_brand(db, user, brand_id)
    if not provider.configured():
        name = platform.upper()
        raise AppError(f"{name}_NOT_CONFIGURED", f"Add {name}_CLIENT_ID and {name}_CLIENT_SECRET to .env.", 503)
    if not get_settings().token_encryption_key:
        raise AppError("ENCRYPTION_NOT_CONFIGURED", "Add TOKEN_ENCRYPTION_KEY to .env.", 503)

    state, verifier = secrets.token_urlsafe(32), x.new_code_verifier()
    payload = json.dumps(
        {"user_id": str(user.id), "brand_id": str(brand_id), "platform": platform, "verifier": verifier}
    )
    try:
        await get_redis().set(f"oauth:state:{state}", payload, ex=STATE_TTL_SECONDS)
    except RedisError:
        raise AppError("SERVICE_UNAVAILABLE", "Couldn't start the connection. Try again shortly.", 503) from None
    return provider.authorization_url(redirect_uri(platform), state, verifier)


async def complete_connect(
    db: AsyncSession,
    platform: str,
    *,
    code: str | None,
    state: str | None,
    error: str | None,
    error_description: str | None = None,
) -> SocialAccount:
    provider = _provider(platform)
    label = platform_label(platform)
    if not state:
        raise AppError("OAUTH_STATE_INVALID", "This connection link is invalid. Start again from ContentPilot.", 400)
    try:
        # GETDEL makes the state single-use.
        raw = await get_redis().getdel(f"oauth:state:{state}")
    except RedisError:
        raise AppError("SERVICE_UNAVAILABLE", "Couldn't finish the connection. Try again shortly.", 503) from None
    if raw is None:
        raise AppError("OAUTH_STATE_INVALID", "This connection link has expired or was already used. Start again.", 400)
    context = json.loads(raw)
    if context["platform"] != platform:
        raise AppError("OAUTH_STATE_INVALID", "This connection link is invalid. Start again from ContentPilot.", 400)
    if error in USER_CANCELLED:
        raise AppError("OAUTH_DENIED", f"{label} access wasn't granted, so nothing was connected.", 400)
    if error:
        raise AppError("OAUTH_ERROR", f"{label} reported a problem: {(error_description or error)[:300]}", 400)
    if not code:
        raise AppError("OAUTH_STATE_INVALID", f"{label} didn't return an authorization code. Start again.", 400)

    user_id, brand_id = uuid.UUID(context["user_id"]), uuid.UUID(context["brand_id"])
    brand = await db.scalar(select(Brand).where(Brand.id == brand_id, Brand.user_id == user_id))
    if brand is None:
        raise AppError("BRAND_NOT_FOUND", "The brand no longer exists.", 404)

    try:
        connection = await provider.connect(code, redirect_uri(platform), context.get("verifier", ""))
    except ProviderError as exc:
        if exc.code == "OAUTH_MISSING_SCOPE":
            raise AppError(exc.code, exc.message, 400) from None
        raise AppError(f"{platform.upper()}_{exc.code}", exc.message, 502) from None

    account = await db.scalar(
        select(SocialAccount).where(SocialAccount.brand_id == brand_id, SocialAccount.platform == platform)
    )
    if account is None:
        account = SocialAccount(user_id=user_id, brand_id=brand_id, platform=platform)
        db.add(account)
    store_tokens(account, connection.access_token, connection.refresh_token, connection.expires_in)
    account.external_account_id = connection.account_id
    account.account_name = connection.account_name[:200]
    account.scopes = connection.scope[:500]
    await db.commit()
    await db.refresh(account)
    logger.info("social_account_connected", extra={"brand_id": str(brand_id), "platform": platform})
    return account


def store_tokens(account: SocialAccount, access_token: str, refresh_token: str | None, expires_in: int) -> None:
    """Encrypts and saves tokens. Keeps the old refresh token if the platform didn't send a new one."""
    account.access_token_encrypted = crypto.encrypt(access_token)
    if refresh_token:
        account.refresh_token_encrypted = crypto.encrypt(refresh_token)
    account.token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in) if expires_in else None
    account.status = SocialAccountStatus.ACTIVE


async def list_accounts(db: AsyncSession, user: User, brand_id: uuid.UUID) -> list[SocialAccount]:
    await brand_service.get_brand(db, user, brand_id)
    result = await db.scalars(
        select(SocialAccount).where(SocialAccount.brand_id == brand_id).order_by(SocialAccount.platform)
    )
    return list(result)


async def disconnect(db: AsyncSession, user: User, brand_id: uuid.UUID, platform: str) -> None:
    await brand_service.get_brand(db, user, brand_id)
    account = await db.scalar(
        select(SocialAccount).where(SocialAccount.brand_id == brand_id, SocialAccount.platform == platform)
    )
    if account is None:
        raise AppError("ACCOUNT_NOT_FOUND", f"No {platform} account is connected to this brand.", 404)
    await db.delete(account)
    await db.commit()

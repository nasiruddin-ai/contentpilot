"""Registration, login and session (refresh token) lifecycle."""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core import rate_limit
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import (
    burn_password_check,
    create_access_token,
    hash_password,
    hash_token,
    new_refresh_token,
    password_needs_rehash,
    verify_password,
)
from app.models import RefreshToken, User
from app.schemas.auth import RegisterRequest

logger = logging.getLogger(__name__)

LOGIN_ATTEMPTS_PER_EMAIL = 5
LOGIN_WINDOW_SECONDS = 15 * 60


@dataclass
class AuthSession:
    user: User
    access_token: str
    refresh_token: str


def _invalid_credentials() -> AppError:
    return AppError("INVALID_CREDENTIALS", "Email or password is incorrect.", 401)


def _session_expired() -> AppError:
    return AppError("SESSION_EXPIRED", "Your session has ended. Please sign in again.", 401)


def _email_taken() -> AppError:
    return AppError("EMAIL_TAKEN", "An account with this email already exists.", 409)


async def register(db: AsyncSession, data: RegisterRequest) -> AuthSession:
    email = data.email.strip().lower()
    if await db.scalar(select(User.id).where(User.email == email)):
        raise _email_taken()

    # Argon2 is deliberately slow; keep it off the event loop.
    password_hash = await run_in_threadpool(hash_password, data.password)
    user = User(name=data.name.strip(), email=email, password_hash=password_hash)
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        # Lost a race with a simultaneous registration for the same email.
        await db.rollback()
        raise _email_taken() from None

    session = await _start_session(db, user)
    await db.commit()
    logger.info("user_registered", extra={"user_id": str(user.id)})
    return session


async def login(db: AsyncSession, email: str, password: str) -> AuthSession:
    email = email.strip().lower()
    if not await rate_limit.allow(f"login:email:{email}", LOGIN_ATTEMPTS_PER_EMAIL, LOGIN_WINDOW_SECONDS):
        raise AppError("RATE_LIMITED", "Too many sign-in attempts. Try again in a few minutes.", 429)

    user = await db.scalar(select(User).where(User.email == email))
    if user is None:
        await run_in_threadpool(burn_password_check, password)
        raise _invalid_credentials()
    if not await run_in_threadpool(verify_password, password, user.password_hash):
        raise _invalid_credentials()
    if not user.is_active:
        raise AppError("ACCOUNT_DISABLED", "This account has been disabled.", 403)

    if password_needs_rehash(user.password_hash):
        user.password_hash = await run_in_threadpool(hash_password, password)

    session = await _start_session(db, user)
    await db.commit()
    logger.info("user_logged_in", extra={"user_id": str(user.id)})
    return session


async def refresh(db: AsyncSession, raw_token: str | None) -> AuthSession:
    if not raw_token:
        raise _session_expired()

    token = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token)).with_for_update()
    )
    now = datetime.now(UTC)
    if token is None:
        raise _session_expired()

    if token.revoked_at is not None:
        # An already-rotated token came back: assume it was stolen and end the whole session.
        await _revoke_family(db, token.family_id, now)
        await db.commit()
        logger.warning("refresh_token_reused", extra={"user_id": str(token.user_id)})
        raise _session_expired()

    if token.expires_at <= now:
        raise _session_expired()

    user = await db.get(User, token.user_id)
    if user is None or not user.is_active:
        raise _session_expired()

    token.revoked_at = now
    new_raw = _issue_refresh_token(db, user.id, token.family_id)
    await db.commit()
    return AuthSession(user, create_access_token(user.id), new_raw)


async def logout(db: AsyncSession, raw_token: str | None) -> None:
    if not raw_token:
        return
    token = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token)))
    if token is not None:
        await _revoke_family(db, token.family_id, datetime.now(UTC))
        await db.commit()


async def get_active_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    user = await db.get(User, user_id)
    return user if user is not None and user.is_active else None


def purge_stale_refresh_tokens(db: Session) -> int:
    """Deletes tokens that expired or were revoked over a day ago. Returns rows deleted."""
    cutoff = datetime.now(UTC) - timedelta(days=1)
    result = db.execute(
        delete(RefreshToken).where(or_(RefreshToken.expires_at < cutoff, RefreshToken.revoked_at < cutoff))
    )
    return result.rowcount


async def _start_session(db: AsyncSession, user: User) -> AuthSession:
    refresh_token = _issue_refresh_token(db, user.id, uuid.uuid4())
    return AuthSession(user, create_access_token(user.id), refresh_token)


def _issue_refresh_token(db: AsyncSession, user_id: uuid.UUID, family_id: uuid.UUID) -> str:
    raw = new_refresh_token()
    ttl = timedelta(days=get_settings().refresh_token_ttl_days)
    db.add(
        RefreshToken(
            user_id=user_id,
            family_id=family_id,
            token_hash=hash_token(raw),
            expires_at=datetime.now(UTC) + ttl,
        )
    )
    return raw


async def _revoke_family(db: AsyncSession, family_id: uuid.UUID, now: datetime) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )

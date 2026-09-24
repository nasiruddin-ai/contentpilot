"""Shared route dependencies: DB session, current user, auth cookies, rate limits."""

from typing import Annotated

from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rate_limit as limiter
from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import AppError
from app.core.security import decode_access_token
from app.models import User
from app.services import auth_service
from app.services.auth_service import AuthSession

ACCESS_COOKIE = "cp_access"
REFRESH_COOKIE = "cp_refresh"
# The refresh cookie is only ever sent to the auth endpoints.
REFRESH_COOKIE_PATH = "/api/v1/auth"

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(request: Request, db: DbSession) -> User:
    token = request.cookies.get(ACCESS_COOKIE)
    user_id = decode_access_token(token) if token else None
    user = await auth_service.get_active_user(db, user_id) if user_id else None
    if user is None:
        raise AppError("UNAUTHORIZED", "Sign in to continue.", 401)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def rate_limit(name: str, limit: int, window_seconds: int):
    """Per-client-IP limit for a route: `dependencies=[rate_limit(...)]`."""

    async def check(request: Request) -> None:
        ip = request.client.host if request.client else "unknown"
        if not await limiter.allow(f"{name}:ip:{ip}", limit, window_seconds):
            raise AppError("RATE_LIMITED", "Too many attempts. Try again later.", 429)

    return Depends(check)


def set_auth_cookies(response: Response, session: AuthSession) -> None:
    settings = get_settings()
    response.set_cookie(
        ACCESS_COOKIE,
        session.access_token,
        max_age=settings.access_token_ttl_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        session.refresh_token,
        max_age=settings.refresh_token_ttl_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path=REFRESH_COOKIE_PATH,
    )


def clear_auth_cookies(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(ACCESS_COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax")
    response.delete_cookie(
        REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, secure=settings.cookie_secure, httponly=True, samesite="strict"
    )

from fastapi import APIRouter, Request, Response, status

from app.api.deps import (
    REFRESH_COOKIE,
    DbSession,
    clear_auth_cookies,
    rate_limit,
    set_auth_cookies,
)
from app.schemas.auth import AuthResponse, LoginRequest, RegisterRequest
from app.schemas.user import UserRead
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    dependencies=[rate_limit("register", limit=5, window_seconds=3600)],
)
async def register(body: RegisterRequest, response: Response, db: DbSession) -> AuthResponse:
    session = await auth_service.register(db, body)
    set_auth_cookies(response, session)
    return AuthResponse(user=UserRead.model_validate(session.user))


@router.post("/login", dependencies=[rate_limit("login", limit=20, window_seconds=900)])
async def login(body: LoginRequest, response: Response, db: DbSession) -> AuthResponse:
    session = await auth_service.login(db, body.email, body.password)
    set_auth_cookies(response, session)
    return AuthResponse(user=UserRead.model_validate(session.user))


@router.post("/refresh", dependencies=[rate_limit("refresh", limit=60, window_seconds=900)])
async def refresh(request: Request, response: Response, db: DbSession) -> AuthResponse:
    session = await auth_service.refresh(db, request.cookies.get(REFRESH_COOKIE))
    set_auth_cookies(response, session)
    return AuthResponse(user=UserRead.model_validate(session.user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: DbSession) -> None:
    await auth_service.logout(db, request.cookies.get(REFRESH_COOKIE))
    clear_auth_cookies(response)

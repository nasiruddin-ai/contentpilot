import html
import logging
import uuid

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.api.deps import CurrentUser, DbSession, rate_limit
from app.core.errors import AppError
from app.schemas.social import ConnectRequest, ConnectResponse, DisconnectRequest, SocialAccountRead
from app.services import social_service

router = APIRouter(prefix="/social", tags=["social"])
logger = logging.getLogger(__name__)


def _read(account) -> SocialAccountRead:
    read = SocialAccountRead.model_validate(account)
    if not social_service.is_usable(account):
        read.status = "reconnect_required"
    return read


def _page(title: str, message: str, status: int) -> HTMLResponse:
    # Shown in the browser tab LinkedIn redirects back to. Everything is escaped.
    body = (
        "<!doctype html><html><head><meta charset=utf-8><title>ContentPilot</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem;color:#0f172a}</style>"
        f"</head><body><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p></body></html>"
    )
    return HTMLResponse(body, status_code=status)


@router.get("/accounts")
async def list_accounts(brand_id: uuid.UUID, user: CurrentUser, db: DbSession) -> list[SocialAccountRead]:
    return [_read(a) for a in await social_service.list_accounts(db, user, brand_id)]


@router.post("/{platform}/connect", dependencies=[rate_limit("social_connect", limit=20, window_seconds=3600)])
async def connect(platform: str, body: ConnectRequest, user: CurrentUser, db: DbSession) -> ConnectResponse:
    url = await social_service.start_connect(db, user, body.brand_id, platform)
    return ConnectResponse(authorization_url=url)


@router.get("/{platform}/callback", include_in_schema=False)
async def callback(
    platform: str,
    db: DbSession,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> HTMLResponse:
    """LinkedIn redirects the browser here. Identity comes from the single-use state, not a cookie."""
    try:
        account = await social_service.complete_connect(
            db, platform, code=code, state=state, error=error, error_description=error_description
        )
    except AppError as exc:
        # The code, state and tokens are never logged.
        logger.warning(
            "social_connect_failed",
            extra={
                "platform": platform,
                "error": exc.code,
                "detail": exc.message[:300],
                "provider_error": error,
                # Which parameters arrived (names only, never values).
                "received": [name for name, value in (("code", code), ("state", state), ("error", error)) if value],
            },
        )
        return _page("Not connected", exc.message, exc.status_code)
    except Exception:
        # The one-time link is already used up by now, so say so plainly and keep the details in the logs.
        logger.exception("social_connect_crashed", extra={"platform": platform})
        return _page("Not connected", "Something went wrong while finishing the connection. Start again from ContentPilot.", 500)
    label = social_service.platform_label(platform)
    return _page("Connected", f"{label} is connected as {account.account_name}. You can close this tab.", 200)


@router.post("/{platform}/disconnect", status_code=204)
async def disconnect(platform: str, body: DisconnectRequest, user: CurrentUser, db: DbSession) -> None:
    await social_service.disconnect(db, user, body.brand_id, platform)

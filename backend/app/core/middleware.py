"""HTTP middleware: request IDs + request logging, and cross-site origin checks."""

import logging
import re
import time
import uuid

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id_ctx

logger = logging.getLogger("app.request")

REQUEST_ID_HEADER = "x-request-id"
# Only trust caller-supplied IDs that can't smuggle anything into logs.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(REQUEST_ID_HEADER.encode(), b"").decode("latin-1")
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex

        # Exception handlers run outside this middleware, so they read it from request.state.
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_ctx.set(request_id)
        started = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                # Error handlers may have set it already.
                if not any(name.lower() == REQUEST_ID_HEADER.encode() for name, _ in headers):
                    headers.append((REQUEST_ID_HEADER.encode(), request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            logger.info(
                "request",
                extra={
                    "method": scope["method"],
                    "endpoint": scope["path"],
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
            request_id_ctx.reset(token)


class OriginCheckMiddleware:
    """CSRF defense in depth on top of SameSite cookies: a browser request that
    changes state and comes from a site we don't serve is rejected."""

    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

    def __init__(self, app: ASGIApp, allowed_origins: list[str]) -> None:
        self.app = app
        self.allowed_origins = {origin.rstrip("/") for origin in allowed_origins}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in self.SAFE_METHODS:
            origin = dict(scope["headers"]).get(b"origin", b"").decode("latin-1")
            # Non-browser clients send no Origin; cookies are what CSRF abuses, and they have none.
            if origin and origin.rstrip("/") not in self.allowed_origins:
                request_id = scope.get("state", {}).get("request_id")
                response = JSONResponse(
                    {
                        "success": False,
                        "error": {
                            "code": "FORBIDDEN_ORIGIN",
                            "message": "Request origin is not allowed.",
                            "request_id": request_id,
                        },
                    },
                    status_code=403,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)

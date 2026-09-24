"""Dependency checks for the readiness probe."""

import asyncio
import logging

from sqlalchemy import text

from app.core.database import get_async_engine
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

CHECK_TIMEOUT_SECONDS = 3


async def check_database() -> bool:
    async def ping() -> None:
        async with get_async_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))

    return await _run_check("database", ping)


async def check_redis() -> bool:
    async def ping() -> None:
        await get_redis().ping()

    return await _run_check("redis", ping)


async def _run_check(name: str, check) -> bool:
    try:
        await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_SECONDS)
        return True
    except Exception as exc:
        # Details stay in the logs; the probe response only says ok/error.
        logger.warning("health_check_failed", extra={"dependency": name, "error": repr(exc)})
        return False


async def readiness() -> dict[str, str]:
    database, redis = await asyncio.gather(check_database(), check_redis())
    return {
        "database": "ok" if database else "error",
        "redis": "ok" if redis else "error",
    }

"""Shared Redis clients, created lazily: async for the API, sync for Celery workers."""

import redis
from redis.asyncio import Redis

from app.core.config import get_settings

_client: Redis | None = None
_sync_client: redis.Redis | None = None


def get_redis() -> Redis:
    global _client
    if _client is None:
        _client = Redis.from_url(
            get_settings().redis_url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def get_sync_redis() -> redis.Redis:
    global _sync_client
    if _sync_client is None:
        _sync_client = redis.Redis.from_url(
            get_settings().redis_url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
    return _sync_client


def close_sync_redis() -> None:
    global _sync_client
    if _sync_client is not None:
        _sync_client.close()
        _sync_client = None

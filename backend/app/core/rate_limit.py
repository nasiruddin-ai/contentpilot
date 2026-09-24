"""Fixed-window rate limiting in Redis."""

import logging

from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.redis import get_redis

logger = logging.getLogger(__name__)


async def allow(key: str, limit: int, window_seconds: int) -> bool:
    """Counts one hit against `key`. Returns False once `limit` is exceeded in the window."""
    full_key = f"rl:{get_settings().app_env}:{key}"
    try:
        pipe = get_redis().pipeline()
        pipe.incr(full_key)
        pipe.expire(full_key, window_seconds, nx=True)
        count, _ = await pipe.execute()
    except RedisError as exc:
        # Fail open: a Redis outage shouldn't lock everyone out of their accounts.
        logger.warning("rate_limit_unavailable", extra={"error": repr(exc)})
        return True
    return count <= limit

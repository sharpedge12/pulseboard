import redis

from shared.core.config import settings

_redis_client: redis.Redis | None = None


def get_redis_client() -> redis.Redis:
    """Return a module-level singleton Redis client to avoid connection leaks."""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client

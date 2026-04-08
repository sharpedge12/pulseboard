"""
Redis client singleton for the PulseBoard microservice platform.

WHY THIS FILE EXISTS:
    PulseBoard uses Redis as the backbone for real-time communication between
    services.  When a user posts a reply or sends a chat message, the backend
    service publishes an event to a Redis pub/sub channel.  The API Gateway
    subscribes to those channels and pushes updates to connected browsers over
    WebSockets.  This is the "Redis-to-WebSocket bridge" pattern.

    Every service (Core, Community, Gateway) needs a Redis connection.  Rather
    than creating a new connection on every request (which would exhaust file
    descriptors and socket resources under load), we use the **Singleton
    pattern** — a single, module-level Redis client instance that is created
    once and reused for the lifetime of the process.

INTERVIEW TALKING POINTS:
    - Singleton vs. dependency injection: We use a module-level private variable
      (`_redis_client`) instead of a class-based singleton.  This is idiomatic
      Python — modules are themselves singletons (imported once, cached in
      `sys.modules`).
    - `redis.from_url()` is a factory method that parses a connection string
      like `redis://redis:6379/0` and returns a configured `Redis` instance.
      This keeps connection details in config, not code.
    - `decode_responses=True` tells redis-py to return `str` instead of `bytes`,
      which avoids `.decode()` calls everywhere we read from Redis.
    - Connection pooling: Under the hood, `redis.Redis` already uses a
      connection pool (default 2**31 connections).  The singleton ensures all
      callers share the same pool.

SEE ALSO:
    - services/shared/shared/core/events.py  -- uses this client for pub/sub
    - services/gateway/app/main.py           -- subscribes to Redis channels
    - services/shared/shared/core/config.py  -- where `settings.redis_url` is defined
"""

import redis

from shared.core.config import settings

# Module-level private variable holding the singleton Redis client.
# Starts as None; lazily initialized on first call to get_redis_client().
# WHY lazy initialization?  Because at import time the config/environment
# might not be fully loaded yet (e.g., in tests or during module scanning).
_redis_client: redis.Redis | None = None


def get_redis_client() -> redis.Redis:
    """Return a module-level singleton Redis client to avoid connection leaks.

    This function implements lazy initialization: the Redis connection is only
    created the first time this function is called.  Every subsequent call
    returns the same instance.

    WHY a function instead of a bare module-level variable?
        - Control over initialization timing (lazy, not eager).
        - Easy to mock in tests: `patch('shared.core.redis.get_redis_client')`.
        - The `global` keyword lets us write to the module-level variable from
          inside the function scope.

    Returns:
        redis.Redis: A connected Redis client with string (not bytes) responses.
    """
    # `global` is required because we are *reassigning* the module-level
    # variable, not just reading it.  Without `global`, Python would create
    # a local variable instead.
    global _redis_client

    # Classic singleton check: only create the client if it doesn't exist yet.
    # Note: this is NOT thread-safe in the strictest sense, but in practice
    # Python's GIL prevents true simultaneous execution, and the worst case
    # is two clients being created (one immediately garbage-collected).
    if _redis_client is None:
        # `redis.from_url` is a factory method that parses the URL scheme
        # (redis:// or rediss:// for TLS) and returns a configured client.
        # `decode_responses=True` means we get Python `str` back instead of
        # raw `bytes`, so we don't need `.decode('utf-8')` everywhere.
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)

    return _redis_client

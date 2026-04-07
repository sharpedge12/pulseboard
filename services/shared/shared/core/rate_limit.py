"""Simple in-memory rate limiter for FastAPI.

Uses a sliding-window counter per IP address.  This is suitable for
single-process deployments; for multi-process / multi-container setups
consider a Redis-backed rate limiter instead.

Usage:
    from shared.core.rate_limit import RateLimitMiddleware

    app.add_middleware(RateLimitMiddleware, rate_limit=20, window_seconds=60,
                       paths=["/api/v1/auth/"])
"""

import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP sliding-window rate limiter scoped to specific path prefixes.

    Args:
        app: The ASGI application.
        rate_limit: Maximum number of requests allowed per window.
        window_seconds: Length of the sliding window in seconds.
        paths: List of URL path prefixes to rate-limit.  If empty, all
               paths are rate-limited.
    """

    def __init__(
        self,
        app,
        rate_limit: int = 20,
        window_seconds: int = 60,
        paths: list[str] | None = None,
    ) -> None:
        super().__init__(app)
        self.rate_limit = rate_limit
        self.window_seconds = window_seconds
        self.paths = paths or []
        # IP -> list of request timestamps
        self._requests: dict[str, list[float]] = defaultdict(list)

    def _should_limit(self, path: str) -> bool:
        """Return True if the request path matches a rate-limited prefix."""
        if not self.paths:
            return True
        return any(path.startswith(p) for p in self.paths)

    def _clean_and_count(self, ip: str) -> int:
        """Remove expired entries and return current request count for *ip*."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        entries = self._requests[ip]
        # Remove entries outside the window
        self._requests[ip] = [t for t in entries if t > cutoff]
        return len(self._requests[ip])

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if not self._should_limit(request.url.path):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        count = self._clean_and_count(client_ip)

        if count >= self.rate_limit:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many requests. Please try again later.",
                },
                headers={
                    "Retry-After": str(self.window_seconds),
                },
            )

        self._requests[client_ip].append(time.monotonic())
        return await call_next(request)

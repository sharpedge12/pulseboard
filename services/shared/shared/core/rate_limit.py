"""
Simple in-memory rate limiter for FastAPI.

WHY THIS FILE EXISTS:
    PulseBoard exposes authentication endpoints (login, register, password
    reset) that are prime targets for brute-force attacks.  Without rate
    limiting, an attacker could try thousands of password guesses per second.
    This middleware enforces a maximum number of requests per IP address
    within a sliding time window (default: 20 requests per 60 seconds on
    auth endpoints).

ALGORITHM — SLIDING WINDOW COUNTER:
    Unlike a fixed-window counter (which resets at clock boundaries and
    allows burst traffic at window edges), a sliding window tracks the
    exact timestamp of each request and only counts requests within the
    last N seconds from "right now."

    Example with rate_limit=3, window_seconds=10:
        t=0   request -> [0]           count=1  ALLOW
        t=3   request -> [0, 3]        count=2  ALLOW
        t=7   request -> [0, 3, 7]     count=3  ALLOW
        t=8   request -> [0, 3, 7]     count=3  DENY (>= limit)
        t=11  request -> [3, 7, 11]    count=3  ALLOW (t=0 expired)

    The cleanup step (list comprehension in _clean_and_count) removes
    timestamps older than `now - window_seconds` before counting.

LIMITATIONS (important for interviews):
    - **In-memory only**: The request counters live in a Python dict inside
      a single process.  If you run multiple uvicorn workers or multiple
      Docker containers, each has its own counter — an attacker could get
      N * rate_limit requests through by rotating across workers/containers.
    - **No persistence**: Restarting the service resets all counters.
    - **Memory growth**: Under a DDoS with millions of unique IPs, the dict
      could grow large.  Production systems use Redis with TTL-based keys
      to handle this (e.g., `redis.incr()` + `redis.expire()`).
    - **Not async-safe in theory**: Multiple concurrent requests from the
      same IP could race on the list append.  In practice, Python's GIL
      prevents true data corruption, and Starlette's middleware runs
      sequentially per request.

    For PulseBoard's scale (dev/demo environment, single-process deployment),
    this in-memory approach is perfectly adequate and avoids adding Redis as
    a hard dependency for rate limiting.

HOW IT'S APPLIED IN PULSEBOARD:
    - Gateway (port 8000): rate-limits `/api/v1/auth/` at 20 req/min per IP.
    - Core (port 8001):    rate-limits `/api/v1/auth/` at 20 req/min per IP.
    - Both layers enforce limits because requests can bypass the gateway in
      internal/dev setups where services are directly accessible.

Usage:
    from shared.core.rate_limit import RateLimitMiddleware

    app.add_middleware(RateLimitMiddleware, rate_limit=20, window_seconds=60,
                       paths=["/api/v1/auth/"])

SEE ALSO:
    - services/gateway/app/main.py   -- adds this middleware to the gateway
    - services/core/app/main.py      -- adds this middleware to the core service
"""

import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP sliding-window rate limiter scoped to specific path prefixes.

    This is implemented as Starlette middleware, which means it runs BEFORE
    the request reaches any FastAPI route handler.  If the rate limit is
    exceeded, the middleware short-circuits the request and returns a 429
    response without ever touching the route logic or database.

    WHY middleware instead of a FastAPI dependency?
        - Middleware runs earlier in the request lifecycle, before routing
          and dependency injection.  This saves CPU on rejected requests.
        - It applies uniformly to all routes matching the path prefix,
          so you can't accidentally forget to add it to a new endpoint.
        - Middleware can also modify responses (e.g., add Retry-After header).

    Args:
        app: The ASGI application (injected by Starlette when adding middleware).
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
        # BaseHTTPMiddleware.__init__ stores `self.app` for call_next.
        super().__init__(app)

        # Configuration: how many requests are allowed in what time span.
        self.rate_limit = rate_limit
        self.window_seconds = window_seconds

        # Which URL prefixes to apply rate limiting to.
        # Empty list = rate-limit everything (but we scope it to auth endpoints).
        self.paths = paths or []

        # The core data structure: maps each client IP address to a list of
        # request timestamps (as floats from time.monotonic).
        # WHY defaultdict(list)?  So we don't need `if ip not in dict` checks.
        # WHY time.monotonic instead of time.time?  monotonic() is immune to
        # system clock changes (NTP adjustments, daylight saving, manual changes).
        # time.time() could jump backward, which would break our window math.
        # IP -> list of request timestamps
        self._requests: dict[str, list[float]] = defaultdict(list)

    def _should_limit(self, path: str) -> bool:
        """Return True if the request path matches a rate-limited prefix.

        Uses startswith() matching so "/api/v1/auth/" catches all sub-routes:
        /api/v1/auth/login, /api/v1/auth/register, /api/v1/auth/refresh, etc.

        Args:
            path: The URL path of the incoming request.

        Returns:
            True if this request should be checked against the rate limit.
        """
        # If no paths are configured, rate-limit everything.
        if not self.paths:
            return True
        # Check if the request path starts with any of the configured prefixes.
        return any(path.startswith(p) for p in self.paths)

    def _clean_and_count(self, ip: str) -> int:
        """Remove expired entries and return current request count for *ip*.

        This is the heart of the sliding window algorithm:
        1. Calculate the cutoff time (now - window_seconds).
        2. Filter out all timestamps older than the cutoff.
        3. Return how many timestamps remain (= requests in current window).

        WHY rebuild the list instead of using a deque with popleft?
            A list comprehension is simpler and fast enough for our scale.
            For very high traffic, a deque with bisect would be O(log n)
            instead of O(n), but that's premature optimization here.

        Args:
            ip: The client's IP address.

        Returns:
            Number of requests from this IP within the current time window.
        """
        now = time.monotonic()
        # Any request older than this cutoff is outside the sliding window.
        cutoff = now - self.window_seconds
        entries = self._requests[ip]

        # List comprehension filters to only timestamps within the window.
        # This is the "sliding" part — the window moves forward with time,
        # and old entries naturally fall off.
        self._requests[ip] = [t for t in entries if t > cutoff]
        return len(self._requests[ip])

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Process each incoming request through the rate limiter.

        This method is called by Starlette for every HTTP request that passes
        through this middleware.  The flow is:
        1. Check if the request path is rate-limited (if not, pass through).
        2. Extract the client's IP address.
        3. Clean expired entries and count recent requests.
        4. If over the limit, return 429 (Too Many Requests).
        5. If under the limit, record this request's timestamp and pass through.

        Args:
            request: The incoming HTTP request.
            call_next: Callable that forwards the request to the next middleware
                       or the actual route handler.

        Returns:
            Either a 429 JSON error or the normal route response.
        """
        # Step 1: Skip rate limiting for non-matching paths (e.g., GET /api/v1/threads).
        if not self._should_limit(request.url.path):
            return await call_next(request)

        # Step 2: Extract client IP.  `request.client` can be None in edge cases
        # (e.g., Unix socket connections), so we fall back to "unknown".
        # In production behind a reverse proxy, you'd want to read X-Forwarded-For
        # instead — but for PulseBoard's Docker setup, direct client.host works.
        client_ip = request.client.host if request.client else "unknown"

        # Step 3: Sliding window — clean old entries and count recent ones.
        count = self._clean_and_count(client_ip)

        # Step 4: If the count is already at (or over) the limit, reject.
        # Note: we check >= (not >) because count represents requests ALREADY
        # recorded.  If count == rate_limit, this new request would be the
        # (rate_limit + 1)th, which exceeds the limit.
        if count >= self.rate_limit:
            # HTTP 429 "Too Many Requests" is the standard status code.
            # The Retry-After header tells the client how long to wait before
            # retrying.  Well-behaved clients (and browser fetch APIs) respect
            # this header.  Attackers won't, but the limit still protects the
            # server from processing their requests.
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many requests. Please try again later.",
                },
                headers={
                    "Retry-After": str(self.window_seconds),
                },
            )

        # Step 5: Under the limit — record this request's timestamp and proceed.
        # We append AFTER the check so that the current request is counted
        # for future checks but not against itself.
        self._requests[client_ip].append(time.monotonic())
        return await call_next(request)

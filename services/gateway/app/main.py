"""
PulseBoard API Gateway — reverse proxy + WebSocket hub.

INTERVIEW CONCEPTS:
    - **API Gateway Pattern**: A single entry point for all frontend requests.
      Instead of the frontend talking to multiple backend services directly,
      everything goes through the gateway on port 8000. The gateway inspects
      the URL path and forwards ("proxies") the request to the correct
      backend microservice (Core on 8001 or Community on 8002).

    - **Reverse Proxy**: The client never sees the backend service URLs.
      The gateway receives the request, forwards it to the appropriate service,
      and returns the response as if it handled it itself. This decouples the
      frontend from the backend topology — you can move, scale, or split
      services without changing any frontend code.

    - **WebSocket Hub**: The gateway also manages persistent WebSocket
      connections for real-time features. It maintains 4 channel types:
        1. thread:<id>   — live updates for a specific thread (new posts)
        2. chat:<id>     — live chat messages in a room
        3. notifications:<user_id> — per-user notification delivery
        4. global        — app-wide events (e.g. new community created)

    - **Redis Pub/Sub Bridge**: Backend services publish events to Redis
      channels (e.g. when a user posts a reply). The gateway subscribes to
      those Redis channels and broadcasts the messages to connected WebSocket
      clients. This decouples producers (backend services) from consumers
      (browser clients) — a classic publish-subscribe architecture.

    - **CORS (Cross-Origin Resource Sharing)**: Since the frontend (port 5173)
      and gateway (port 8000) run on different origins, the browser blocks
      cross-origin requests by default. CORS middleware tells the browser
      which origins are allowed to make requests.

Architecture:
    Frontend (React, port 5173)
         |
         v
    Gateway (this file, port 8000)
         |
         +--> Core Service (port 8001) — auth, users, uploads, notifications
         +--> Community Service (port 8002) — forums, chat, admin, search
         +--> Redis (pub/sub) — event bridge to WebSocket clients
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from shared.core.config import settings
from shared.core.logging import configure_logging
from shared.core.security import safe_decode_token
from shared.core.events import connection_manager

configure_logging()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Route mapping: URL prefix -> backend service base URL
# ---------------------------------------------------------------------------
# INTERVIEW NOTE:
# This is the core of the reverse proxy pattern. Each tuple maps a URL prefix
# to the internal service that handles it. The order matters — the first
# matching prefix wins. This is essentially a routing table, similar to what
# nginx or Envoy does, but implemented in Python for simplicity.
#
# Why a list of tuples instead of a dict?
#   - We need ordered matching (first match wins)
#   - Multiple prefixes can map to the same service
#   - A dict would work but doesn't guarantee insertion order in older Python

ROUTE_MAP: list[tuple[str, str]] = [
    # Auth, uploads, user profiles, notifications -> Core service (port 8001)
    ("/api/v1/auth", settings.core_service_url),
    ("/api/v1/uploads", settings.core_service_url),
    ("/api/v1/users", settings.core_service_url),
    ("/api/v1/notifications", settings.core_service_url),
    # Forums, threads, posts, search, admin, chat -> Community service (port 8002)
    ("/api/v1/categories", settings.community_service_url),
    ("/api/v1/threads", settings.community_service_url),
    ("/api/v1/posts", settings.community_service_url),
    ("/api/v1/search", settings.community_service_url),
    ("/api/v1/admin", settings.community_service_url),
    ("/api/v1/chat", settings.community_service_url),
]


def _resolve_backend(path: str) -> str | None:
    """Return the full backend URL for a given request path.

    Iterates through ROUTE_MAP and returns the first match by prefix.
    For example, ``/api/v1/auth/login`` matches prefix ``/api/v1/auth``
    and returns ``http://core:8001/api/v1/auth/login``.

    Returns:
        The full backend URL string, or None if no prefix matches.

    INTERVIEW NOTE:
        This is O(n) where n is the number of route prefixes. For a small
        route table this is fine. At scale (hundreds of routes), you'd use
        a trie or radix tree for O(k) lookup where k is the path length.
    """
    for prefix, service_url in ROUTE_MAP:
        if path.startswith(prefix):
            # Concatenate the service base URL with the full request path.
            # e.g. "http://core:8001" + "/api/v1/auth/login"
            return service_url + path
    return None


# ---------------------------------------------------------------------------
# Redis -> WebSocket bridge
# ---------------------------------------------------------------------------
# INTERVIEW NOTE on Pub/Sub Architecture:
# Backend services (Core, Community) publish events to Redis when something
# happens (new post, new message, etc.). The gateway subscribes to these
# Redis channels and forwards the events to WebSocket clients.
#
# This decoupling is important because:
# 1. Backend services don't need to know about WebSocket connections
# 2. The gateway doesn't need to know about business logic
# 3. You can add more subscribers (e.g. a mobile push service) without
#    changing the publishers

# Redis pub/sub channel patterns that the gateway subscribes to.
# The "*" wildcard allows matching all channels that start with a prefix.
# For example, "thread:*" matches "thread:1", "thread:42", etc.
_REDIS_CHANNEL_PATTERNS: list[str] = [
    "thread:*",  # Real-time updates for any thread
    "chat:room:*",  # Real-time chat messages for any room
    "notifications:*",  # Per-user notification delivery
    "global",  # App-wide broadcasts (no wildcard needed — exact match)
]


def _redis_channel_to_ws_channel(redis_channel: str) -> str:
    """Map a Redis pub/sub channel name to the ConnectionManager channel name.

    INTERVIEW NOTE:
    There's a naming mismatch between Redis channels and WebSocket channels.
    Backend services publish to ``chat:room:<id>`` (3 segments), but
    WebSocket clients subscribe via ``/ws/chat/<id>`` which maps to
    channel ``chat:<id>`` (2 segments). This function bridges that gap.

    Why the mismatch? Redis uses ``chat:room:*`` to be explicit about the
    entity type (room vs. future DM channels, etc.), while the WebSocket
    API keeps it simple for frontend consumers.

    Examples:
        ``chat:room:42``    -> ``chat:42``     (room prefix stripped)
        ``thread:7``        -> ``thread:7``    (unchanged)
        ``notifications:3`` -> ``notifications:3`` (unchanged)
        ``global``          -> ``global``      (unchanged)
    """
    if redis_channel.startswith("chat:room:"):
        # Split on ":" with maxsplit=2 to get ["chat", "room", "42"]
        # Then take the third element (the room ID)
        room_id = redis_channel.split(":", 2)[2]
        return f"chat:{room_id}"
    return redis_channel


async def _redis_subscriber_loop() -> None:
    """Long-running background task that bridges Redis pub/sub to WebSocket clients.

    INTERVIEW CONCEPTS:

    1. **Why asyncio.to_thread?**
       The ``redis-py`` library is synchronous — its ``get_message()`` call
       blocks the calling thread while waiting for data. If we called it
       directly in an async function, it would block the entire asyncio event
       loop and freeze ALL other concurrent operations (HTTP requests,
       WebSocket handling, etc.).

       ``asyncio.to_thread()`` runs the blocking call in a separate OS thread,
       allowing the event loop to continue processing other tasks. This is a
       common pattern for integrating sync libraries into async applications
       without adding ``aioredis`` as an extra dependency.

    2. **Reconnection with backoff**:
       If Redis goes down, the loop catches the exception, waits 2 seconds,
       and tries to reconnect. This is a simple retry strategy. In production,
       you'd use exponential backoff with jitter to avoid thundering herd
       problems (all clients reconnecting simultaneously).

    3. **Pattern subscriptions (psubscribe)**:
       Redis supports two subscription modes:
       - ``subscribe("thread:5")`` — exact channel match
       - ``psubscribe("thread:*")`` — pattern match using glob-style wildcards
       We use psubscribe for wildcard patterns and subscribe for exact names.

    4. **Graceful shutdown via CancelledError**:
       When the FastAPI app shuts down, it cancels this task. The
       ``asyncio.CancelledError`` exception is caught to log a clean message
       and exit the loop without a stack trace.
    """
    import redis as _redis

    def _get_next(pubsub: _redis.client.PubSub):
        """Blocking poll — runs in a worker thread via asyncio.to_thread.

        Returns a single message dict, None (timeout, no message), or
        the string "ERROR" if the connection is broken.

        The timeout=1.0 means this call blocks for at most 1 second before
        returning None. This allows the async loop to periodically check
        for cancellation instead of blocking forever.
        """
        try:
            return pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        except Exception:
            # Connection lost, socket error, etc. — signal the caller to reconnect
            return "ERROR"

    # Outer infinite loop: handles reconnection after errors
    while True:
        pubsub = None
        client = None
        try:
            # Create a fresh Redis client and pub/sub subscription
            client = _redis.from_url(settings.redis_url, decode_responses=True)
            pubsub = client.pubsub()

            # Separate wildcard patterns from exact channel names because
            # Redis uses different commands for each:
            #   PSUBSCRIBE for patterns (e.g. "thread:*")
            #   SUBSCRIBE for exact names (e.g. "global")
            patterns = [p for p in _REDIS_CHANNEL_PATTERNS if "*" in p]
            channels = [p for p in _REDIS_CHANNEL_PATTERNS if "*" not in p]
            if patterns:
                pubsub.psubscribe(*patterns)
            if channels:
                pubsub.subscribe(*channels)

            logger.info(
                "Redis subscriber started (patterns=%s, channels=%s).",
                patterns,
                channels,
            )

            # Inner loop: poll for messages until an error occurs
            while True:
                # Run the blocking Redis poll in a thread so the event loop
                # stays free to handle HTTP/WebSocket requests concurrently
                result = await asyncio.to_thread(_get_next, pubsub)

                if result == "ERROR":
                    # Connection lost or error — break inner loop to reconnect
                    break

                if result is None:
                    # Timeout expired with no message — loop and poll again.
                    # This is normal and happens every ~1 second during idle periods.
                    continue

                # Redis pub/sub messages have a "type" field:
                # - "subscribe"/"psubscribe" = confirmation of subscription
                # - "message" = message on an exact-subscribed channel
                # - "pmessage" = message on a pattern-subscribed channel
                # We only care about actual data messages.
                if result.get("type") not in ("message", "pmessage"):
                    continue

                # Extract the channel name and raw JSON data from the message
                redis_channel: str = result.get("channel", "")
                data_raw: str = result.get("data", "")

                # Translate Redis channel name to WebSocket channel name
                # (e.g. "chat:room:42" -> "chat:42")
                ws_channel = _redis_channel_to_ws_channel(redis_channel)

                # Optimization: skip JSON parsing and broadcasting if nobody
                # is listening on this WebSocket channel. This avoids wasting
                # CPU on messages that would be dropped anyway.
                if ws_channel not in connection_manager.connections:
                    continue
                if not connection_manager.connections[ws_channel]:
                    continue

                try:
                    payload = json.loads(data_raw)
                except (json.JSONDecodeError, TypeError):
                    # Malformed JSON from Redis — skip this message silently
                    continue

                # Broadcast the parsed event to all WebSocket clients on this channel.
                # The ConnectionManager handles iterating over connections and
                # cleaning up any dead/disconnected ones.
                await connection_manager.broadcast(ws_channel, payload)

        except asyncio.CancelledError:
            # Graceful shutdown — the FastAPI lifespan manager cancelled this task
            logger.info("Redis subscriber shutting down.")
            break
        except Exception:
            # Any other error (Redis down, network issue, etc.)
            # Log it and retry after a delay to avoid tight error loops
            logger.warning(
                "Redis subscriber error — reconnecting in 2 s.", exc_info=True
            )
            await asyncio.sleep(2)
        finally:
            # Always clean up the pub/sub connection, even on errors
            if pubsub is not None:
                try:
                    pubsub.close()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Application lifespan management
# ---------------------------------------------------------------------------
# INTERVIEW NOTE:
# FastAPI's ``lifespan`` context manager replaces the older ``on_startup`` /
# ``on_shutdown`` events. It runs setup code before "yield" and teardown code
# after "yield". This is the recommended pattern in modern FastAPI.


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Manage application startup and shutdown.

    On startup:
        1. Create a shared httpx.AsyncClient for proxying HTTP requests.
           Reusing a single client is more efficient than creating one per
           request because it maintains a connection pool internally.
        2. Launch the Redis subscriber background task.

    On shutdown:
        1. Cancel the Redis subscriber task and wait for it to finish.
        2. Close the HTTP client (releases connection pool resources).
    """
    global _http_client
    # Create a single reusable HTTP client with a 30-second timeout.
    # This client is used by the proxy endpoint to forward requests.
    # Connection pooling means we reuse TCP connections to backend services.
    _http_client = httpx.AsyncClient(timeout=30.0)

    # Launch the Redis-to-WebSocket bridge as a background task.
    # asyncio.create_task() schedules it to run concurrently with the
    # main application — it doesn't block startup.
    subscriber_task = asyncio.create_task(_redis_subscriber_loop())
    try:
        yield  # Application is running and serving requests
    finally:
        # --- Shutdown sequence ---
        # Cancel the Redis subscriber and wait for clean exit
        subscriber_task.cancel()
        try:
            await subscriber_task
        except asyncio.CancelledError:
            pass  # Expected — we just cancelled it
        # Close the HTTP client to release connection pool resources
        await _http_client.aclose()
        _http_client = None


# Module-level reusable HTTP client — initialized during lifespan startup,
# set to None during shutdown. Using a module-level variable allows all
# route handlers to share the same connection pool.
_http_client: httpx.AsyncClient | None = None


# ---------------------------------------------------------------------------
# FastAPI application instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title="PulseBoard API Gateway",
    version="0.1.0",
    lifespan=lifespan,  # Register the startup/shutdown manager
)

# CORS middleware — required because the React frontend (port 5173) and the
# gateway (port 8000) are on different origins. Without CORS, the browser
# would block all API requests from the frontend.
#
# INTERVIEW NOTE:
# - allow_origins: which domains can make requests (configured in settings)
# - allow_credentials: allow cookies/auth headers in cross-origin requests
# - allow_methods: which HTTP methods are allowed (["*"] = all)
# - allow_headers: which request headers are allowed (["*"] = all)
# In production, you'd restrict these to only what's needed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# These imports are placed after app creation (noqa: E402) because the
# middleware classes need to be added after the CORS middleware.
# Middleware execution order in FastAPI/Starlette is LIFO (last added = first
# executed), so SecurityHeaders runs first, then RateLimit, then CORS.
from shared.core.security_headers import SecurityHeadersMiddleware  # noqa: E402
from shared.core.rate_limit import RateLimitMiddleware  # noqa: E402

# Security headers middleware adds protective HTTP headers to every response:
# X-Content-Type-Options, X-Frame-Options, CSP, etc.
app.add_middleware(SecurityHeadersMiddleware)

# Rate limiting middleware — sliding-window counter per IP address.
# Applied only to auth endpoints to prevent brute-force login attempts.
# 20 requests per 60-second window; returns 429 Too Many Requests if exceeded.
app.add_middleware(
    RateLimitMiddleware,
    rate_limit=20,
    window_seconds=60,
    paths=["/api/v1/auth/"],  # Only rate-limit auth endpoints
)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    """Simple health check endpoint for load balancers and monitoring.

    Returns a 200 OK with a JSON body. Used by Docker HEALTHCHECK,
    Kubernetes liveness probes, or uptime monitoring tools to verify
    the gateway is running.
    """
    return {"status": "ok", "service": "gateway"}


# ---------------------------------------------------------------------------
# Static asset proxy — forward /uploads/* to the Core service
# ---------------------------------------------------------------------------
# INTERVIEW NOTE:
# Why proxy uploads instead of serving files directly from the gateway?
# In Docker, each container has its own filesystem. The Core service writes
# uploaded files to its container's /app/uploads/ directory. The gateway
# container doesn't have access to those files. Rather than sharing volumes
# (which causes issues on Windows/WSL2), we proxy the request to Core and
# let it serve the file. This is a common pattern in microservices.


@app.get("/uploads/{file_path:path}")
async def proxy_uploads(file_path: str) -> Response:
    """Proxy uploaded file requests (avatars, attachments) to the Core service.

    Args:
        file_path: The path after ``/uploads/``, e.g. ``avatars/abc123.jpg``.

    Returns:
        The file content from the Core service, with original headers preserved.

    INTERVIEW NOTE:
        The ``{file_path:path}`` parameter type allows slashes in the value,
        so ``/uploads/avatars/abc.jpg`` captures ``avatars/abc.jpg``.

        We strip ``content-encoding``, ``transfer-encoding``, and
        ``content-length`` from the proxied response because our Response
        object recalculates them. Forwarding the original values would cause
        the client to misinterpret the response body.
    """
    backend_url = f"{settings.core_service_url}/uploads/{file_path}"
    assert _http_client is not None, "HTTP client not initialised"
    try:
        response = await _http_client.get(backend_url)
    except httpx.ConnectError:
        # Core service is down — return 503 Service Unavailable
        return Response(
            content='{"detail":"Service unavailable"}',
            status_code=503,
            media_type="application/json",
        )
    # Remove hop-by-hop headers that shouldn't be forwarded through a proxy.
    # These headers describe the connection between gateway and backend,
    # not between gateway and client.
    excluded_headers = {"content-encoding", "transfer-encoding", "content-length"}
    response_headers = {
        k: v for k, v in response.headers.items() if k.lower() not in excluded_headers
    }
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=response_headers,
        media_type=response.headers.get("content-type"),
    )


# ---------------------------------------------------------------------------
# HTTP reverse proxy — the heart of the gateway
# ---------------------------------------------------------------------------
# INTERVIEW NOTE:
# ``api_route`` with all HTTP methods acts as a catch-all handler.
# Any request to /api/v1/* that doesn't match a more specific route
# (like /health or /uploads) lands here and gets forwarded to the
# appropriate backend service based on the ROUTE_MAP.


@app.api_route(
    "/api/v1/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy(request: Request, path: str) -> Response:
    """Forward any ``/api/v1/*`` request to the corresponding backend service.

    This is the main reverse proxy handler. It:
    1. Looks up which backend service handles this URL prefix
    2. Forwards the request with all headers, query params, and body
    3. Returns the backend's response to the client

    Args:
        request: The incoming HTTP request from the client.
        path: The URL path after ``/api/v1/``.

    Returns:
        The proxied response from the backend service.

    INTERVIEW NOTE on reverse proxy implementation:
        - Headers are forwarded except ``Host`` (which must reflect the
          backend service, not the gateway)
        - Query string is appended to the backend URL
        - Request body is forwarded as raw bytes (works for JSON, form data,
          file uploads — any content type)
        - The response is returned with status code, headers, and body intact
    """
    # Step 1: Resolve which backend service handles this path
    backend_url = _resolve_backend(f"/api/v1/{path}")
    if backend_url is None:
        return Response(
            content='{"detail":"Route not found"}',
            status_code=404,
            media_type="application/json",
        )

    # Step 2: Append query string (e.g. ?page=2&sort=new)
    if request.url.query:
        backend_url = f"{backend_url}?{request.url.query}"

    # Step 3: Forward all headers except "Host"
    # The Host header must reflect the backend service's address, not the
    # gateway's address. httpx will set the correct Host automatically.
    headers = dict(request.headers)
    headers.pop("host", None)

    # Step 4: Read the request body as raw bytes
    # This preserves the original content regardless of Content-Type
    body = await request.body()

    # Step 5: Forward the request to the backend service
    assert _http_client is not None, "HTTP client not initialised"
    try:
        response = await _http_client.request(
            method=request.method,
            url=backend_url,
            headers=headers,
            content=body,
        )
    except httpx.ConnectError:
        # Backend service is unreachable — return 503 to the client
        return Response(
            content='{"detail":"Service unavailable"}',
            status_code=503,
            media_type="application/json",
        )

    # Step 6: Return the backend response to the client
    # Strip hop-by-hop headers (same reason as in proxy_uploads)
    excluded_headers = {"content-encoding", "transfer-encoding", "content-length"}
    response_headers = {
        k: v for k, v in response.headers.items() if k.lower() not in excluded_headers
    }

    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=response_headers,
        media_type=response.headers.get("content-type"),
    )


# ---------------------------------------------------------------------------
# WebSocket hub (handled directly by the gateway — NOT proxied)
# ---------------------------------------------------------------------------
# INTERVIEW NOTE:
# Unlike HTTP requests which are proxied to backend services, WebSocket
# connections are managed directly by the gateway. This is because:
#
# 1. WebSockets are long-lived connections (minutes to hours), and proxying
#    them through an intermediary adds complexity and latency.
# 2. The gateway already runs the Redis subscriber, so it can directly
#    push events to connected clients without another network hop.
# 3. All 4 WebSocket channels (thread, chat, notifications, global) share
#    the same ConnectionManager instance for efficient broadcasting.
#
# The pattern is:
#   Client connects -> gateway accepts and registers in ConnectionManager ->
#   Redis bridge receives event -> gateway broadcasts to registered clients


def _extract_user_id_from_token(token: str | None) -> int | None:
    """Decode a JWT access token and return the user ID (the ``sub`` claim).

    Args:
        token: A JWT access token string, or None.

    Returns:
        The integer user ID from the token's ``sub`` claim, or None if the
        token is missing, invalid, expired, or not an access token.

    INTERVIEW NOTE:
        JWT tokens contain a payload with claims. The ``sub`` (subject) claim
        holds the user ID. The ``type`` claim distinguishes access tokens from
        refresh tokens. ``safe_decode_token`` handles signature verification
        and expiry checking — it returns None for any invalid token instead
        of raising an exception.
    """
    if not token:
        return None
    payload = safe_decode_token(token)
    if not payload or payload.get("type") != "access":
        return None
    subject = payload.get("sub")
    return int(subject) if subject else None


@app.websocket("/ws/notifications")
async def notifications_websocket(websocket: WebSocket) -> None:
    """WebSocket endpoint for per-user notification delivery.

    The client passes a JWT token as a query parameter (``?token=...``)
    because WebSocket connections don't support custom headers in browsers.

    Each user gets their own channel (``notifications:<user_id>``), so
    notifications are delivered only to the intended recipient.

    INTERVIEW NOTE:
        - Close code 1008 = "Policy Violation" — used here to indicate
          that authentication failed.
        - The ``while True: receive_text()`` loop keeps the connection alive.
          We don't expect the client to send data, but WebSocket protocol
          requires reading from the connection to detect disconnection.
    """
    # Authenticate: extract user ID from the JWT token in query params
    user_id = _extract_user_id_from_token(websocket.query_params.get("token"))
    if user_id is None:
        await websocket.close(code=1008)  # Policy Violation = auth failed
        return

    # Create a per-user channel so notifications go only to this user
    channel = f"notifications:{user_id}"
    await connection_manager.connect(channel, websocket)
    try:
        # Keep the connection alive by reading (we discard the data)
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        # Client disconnected or connection error — clean up
        connection_manager.disconnect(channel, websocket)


@app.websocket("/ws/threads/{thread_id}")
async def thread_websocket(websocket: WebSocket, thread_id: int) -> None:
    """WebSocket endpoint for real-time thread updates (new posts, edits).

    No authentication required — thread content is public. Any connected
    client receives live updates for the thread they're viewing.

    The channel name ``thread:<id>`` matches the Redis pattern ``thread:*``
    that the subscriber loop listens to.
    """
    channel = f"thread:{thread_id}"
    await connection_manager.connect(channel, websocket)
    try:
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        connection_manager.disconnect(channel, websocket)


@app.websocket("/ws/chat/{room_id}")
async def chat_websocket(websocket: WebSocket, room_id: int) -> None:
    """WebSocket endpoint for real-time chat messages in a specific room.

    INTERVIEW NOTE on authorization check:
        Unlike threads (which are public), chat rooms can be private (DMs).
        Before accepting the WebSocket connection, we verify that the user
        is actually a member of the chat room by making an HTTP request to
        the Community service's room detail endpoint.

        This is a server-to-server call (gateway -> community) using a
        short-lived httpx client. If the user isn't a member, we close the
        WebSocket with code 4403 (custom code mimicking HTTP 403 Forbidden).

        Close code 1011 = "Unexpected Condition" — used when the Community
        service is unreachable.
    """
    # Authenticate the user via JWT token in query params
    token = websocket.query_params.get("token")
    user_id = _extract_user_id_from_token(token)
    if user_id is None:
        await websocket.close(code=1008)  # Auth failed
        return

    # Authorization: verify the user is a member of this chat room.
    # This prevents eavesdropping on private DMs by guessing room IDs.
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{settings.community_service_url}/api/v1/chat/rooms/{room_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code != 200:
                # User is not a member or room doesn't exist
                await websocket.close(code=4403)  # Custom: Forbidden
                return
    except httpx.ConnectError:
        # Community service is down — can't verify membership
        await websocket.close(code=1011)  # Unexpected Condition
        return

    # Connection authorized — register in the ConnectionManager.
    # Note: "chat:<room_id>" matches what _redis_channel_to_ws_channel()
    # produces from "chat:room:<room_id>".
    channel = f"chat:{room_id}"
    await connection_manager.connect(channel, websocket)
    try:
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        connection_manager.disconnect(channel, websocket)


@app.websocket("/ws/global")
async def global_websocket(websocket: WebSocket) -> None:
    """Public WebSocket channel for app-wide events (e.g. new communities).

    No authentication required — these are public broadcasts visible to
    all connected users. Useful for events like "a new category was created"
    that should update every user's sidebar in real time.
    """
    channel = "global"
    await connection_manager.connect(channel, websocket)
    try:
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        connection_manager.disconnect(channel, websocket)

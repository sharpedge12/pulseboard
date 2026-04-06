"""PulseBoard API Gateway — reverse proxy + WebSocket hub.

Routes all frontend requests to the appropriate backend microservice and
handles WebSocket connections directly via the shared ConnectionManager.

Includes a Redis pub/sub bridge that subscribes to event channels published
by backend services and forwards them to connected WebSocket clients.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response
from fastapi.staticfiles import StaticFiles

from shared.core.config import settings
from shared.core.logging import configure_logging
from shared.core.security import safe_decode_token
from shared.core.events import connection_manager

configure_logging()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Route mapping: URL prefix -> backend service base URL
# ---------------------------------------------------------------------------

ROUTE_MAP: list[tuple[str, str]] = [
    ("/api/v1/auth", settings.core_service_url),
    ("/api/v1/uploads", settings.core_service_url),
    ("/api/v1/users", settings.core_service_url),
    ("/api/v1/notifications", settings.core_service_url),
    ("/api/v1/categories", settings.community_service_url),
    ("/api/v1/threads", settings.community_service_url),
    ("/api/v1/posts", settings.community_service_url),
    ("/api/v1/search", settings.community_service_url),
    ("/api/v1/admin", settings.community_service_url),
    ("/api/v1/chat", settings.community_service_url),
]


def _resolve_backend(path: str) -> str | None:
    """Return the full backend URL for a given request path."""
    for prefix, service_url in ROUTE_MAP:
        if path.startswith(prefix):
            return service_url + path
    return None


# ---------------------------------------------------------------------------
# Redis → WebSocket bridge
# ---------------------------------------------------------------------------

# Redis pub/sub channel patterns that the gateway subscribes to.
# Messages received on these channels are forwarded to WebSocket clients.
_REDIS_CHANNEL_PATTERNS: list[str] = [
    "thread:*",
    "chat:room:*",
    "notifications:*",
    "global",
]


def _redis_channel_to_ws_channel(redis_channel: str) -> str:
    """Map a Redis pub/sub channel name to the ConnectionManager channel.

    Backend services publish to ``chat:room:<id>`` but WebSocket clients
    connect to the gateway on channel ``chat:<id>``.  All other channel
    names are used as-is.
    """
    if redis_channel.startswith("chat:room:"):
        # chat:room:42 → chat:42
        room_id = redis_channel.split(":", 2)[2]
        return f"chat:{room_id}"
    return redis_channel


async def _redis_subscriber_loop() -> None:
    """Long-running task that bridges Redis pub/sub → WebSocket clients.

    Uses the synchronous ``redis-py`` client in a thread so we don't need
    ``aioredis`` as an extra dependency.  ``get_message(timeout=...)``
    runs inside ``asyncio.to_thread`` to avoid blocking the event loop
    while still allowing clean shutdown on task cancellation.
    """
    import redis as _redis

    def _get_next(pubsub: _redis.client.PubSub):
        """Blocking poll — returns a single message or ``None``."""
        try:
            return pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        except Exception:
            return "ERROR"

    while True:
        pubsub = None
        client = None
        try:
            client = _redis.from_url(settings.redis_url, decode_responses=True)
            pubsub = client.pubsub()

            # Subscribe using patterns for wildcard channels, subscribe for
            # exact channel names.
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

            while True:
                result = await asyncio.to_thread(_get_next, pubsub)

                if result == "ERROR":
                    # Connection lost or error — break to reconnect
                    break

                if result is None:
                    # Timeout, no message — loop and poll again
                    continue

                if result.get("type") not in ("message", "pmessage"):
                    continue

                # Determine the actual channel name
                redis_channel: str = result.get("channel", "")
                data_raw: str = result.get("data", "")

                ws_channel = _redis_channel_to_ws_channel(redis_channel)

                # Only forward if there are WebSocket clients on this channel
                if ws_channel not in connection_manager.connections:
                    continue
                if not connection_manager.connections[ws_channel]:
                    continue

                try:
                    payload = json.loads(data_raw)
                except (json.JSONDecodeError, TypeError):
                    continue

                await connection_manager.broadcast(ws_channel, payload)

        except asyncio.CancelledError:
            logger.info("Redis subscriber shutting down.")
            break
        except Exception:
            logger.warning(
                "Redis subscriber error — reconnecting in 2 s.", exc_info=True
            )
            await asyncio.sleep(2)
        finally:
            if pubsub is not None:
                try:
                    pubsub.close()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global _http_client
    _http_client = httpx.AsyncClient(timeout=30.0)
    subscriber_task = asyncio.create_task(_redis_subscriber_loop())
    try:
        yield
    finally:
        subscriber_task.cancel()
        try:
            await subscriber_task
        except asyncio.CancelledError:
            pass
        await _http_client.aclose()
        _http_client = None


# Module-level reusable HTTP client (initialised in lifespan)
_http_client: httpx.AsyncClient | None = None


app = FastAPI(
    title="PulseBoard API Gateway",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve uploads directory (shared volume in Docker)
upload_root = Path(settings.upload_dir)
upload_root.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(upload_root)), name="uploads")


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "gateway"}


# ---------------------------------------------------------------------------
# HTTP reverse proxy
# ---------------------------------------------------------------------------


@app.api_route(
    "/api/v1/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy(request: Request, path: str) -> Response:
    """Forward any /api/v1/* request to the corresponding backend service."""
    backend_url = _resolve_backend(f"/api/v1/{path}")
    if backend_url is None:
        return Response(
            content='{"detail":"Route not found"}',
            status_code=404,
            media_type="application/json",
        )

    # Rebuild the backend URL with query string
    if request.url.query:
        backend_url = f"{backend_url}?{request.url.query}"

    # Forward headers (except Host)
    headers = dict(request.headers)
    headers.pop("host", None)

    body = await request.body()

    assert _http_client is not None, "HTTP client not initialised"
    try:
        response = await _http_client.request(
            method=request.method,
            url=backend_url,
            headers=headers,
            content=body,
        )
    except httpx.ConnectError:
        return Response(
            content='{"detail":"Service unavailable"}',
            status_code=503,
            media_type="application/json",
        )

    # Forward the response back
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
# WebSocket hub (handled directly by the gateway)
# ---------------------------------------------------------------------------


def _extract_user_id_from_token(token: str | None) -> int | None:
    """Decode a JWT access token and return the user ID, or None."""
    if not token:
        return None
    payload = safe_decode_token(token)
    if not payload or payload.get("type") != "access":
        return None
    subject = payload.get("sub")
    return int(subject) if subject else None


@app.websocket("/ws/notifications")
async def notifications_websocket(websocket: WebSocket) -> None:
    user_id = _extract_user_id_from_token(websocket.query_params.get("token"))
    if user_id is None:
        await websocket.close(code=1008)
        return

    channel = f"notifications:{user_id}"
    await connection_manager.connect(channel, websocket)
    try:
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        connection_manager.disconnect(channel, websocket)


@app.websocket("/ws/threads/{thread_id}")
async def thread_websocket(websocket: WebSocket, thread_id: int) -> None:
    channel = f"thread:{thread_id}"
    await connection_manager.connect(channel, websocket)
    try:
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        connection_manager.disconnect(channel, websocket)


@app.websocket("/ws/chat/{room_id}")
async def chat_websocket(websocket: WebSocket, room_id: int) -> None:
    token = websocket.query_params.get("token")
    user_id = _extract_user_id_from_token(token)
    if user_id is None:
        await websocket.close(code=1008)
        return

    # Verify the user is a member of this chat room before allowing
    # the WebSocket connection (prevents eavesdropping on private DMs).
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{settings.community_service_url}/api/v1/chat/rooms/{room_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code != 200:
                await websocket.close(code=4403)
                return
    except httpx.ConnectError:
        await websocket.close(code=1011)
        return

    channel = f"chat:{room_id}"
    await connection_manager.connect(channel, websocket)
    try:
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        connection_manager.disconnect(channel, websocket)


@app.websocket("/ws/global")
async def global_websocket(websocket: WebSocket) -> None:
    """Public channel for app-wide events (e.g. new communities)."""
    channel = "global"
    await connection_manager.connect(channel, websocket)
    try:
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        connection_manager.disconnect(channel, websocket)

"""
Redis pub/sub event publishing and WebSocket connection management for PulseBoard.

WHY THIS FILE EXISTS:
    PulseBoard is a microservice architecture with 2 backend services (Core,
    Community) behind an API Gateway.  When something happens in a backend
    service — a new post, a chat message, a notification — the frontend needs
    to learn about it instantly WITHOUT polling.  This module provides the
    two halves of that real-time pipeline:

    1. **publish_event()** — Backend services call this to push events into
       Redis pub/sub channels (e.g., `thread:42`, `chat:room:7`,
       `notifications:user:3`).  The Gateway subscribes to these channels and
       forwards events to browsers over WebSockets.

    2. **ConnectionManager** — The Gateway uses this class to track which
       WebSocket connections are listening on which channels, and to broadcast
       messages to all listeners on a channel.  Dead connections are
       automatically cleaned up during broadcast.

REAL-TIME EVENT FLOW (end to end):
    Browser  <--WebSocket-->  Gateway  <--Redis pub/sub-->  Core/Community
                                |
                  ConnectionManager groups
                  WebSockets by channel name

    Example: User A posts a reply in thread 42.
      1. Community service calls `publish_event("thread:42", {...})`.
      2. Redis delivers the message to all subscribers of the `thread:42` channel.
      3. Gateway's Redis listener (running in `asyncio.to_thread`) picks it up.
      4. Gateway calls `connection_manager.broadcast("thread:42", {...})`.
      5. All browsers viewing thread 42 receive the new post via WebSocket.

INTERVIEW TALKING POINTS:
    - Redis pub/sub is fire-and-forget: if no one is subscribed, the message is
      lost.  This is fine for live updates (if you're not online, you don't need
      the push — you'll fetch on next page load).
    - The `publish_event` function silently swallows Redis errors.  This is
      intentional: a Redis outage should NOT break post creation.  Real-time
      updates are a nice-to-have, not a hard requirement (graceful degradation).
    - ConnectionManager uses the Observer pattern: WebSocket clients subscribe
      to channels, and broadcast notifies all observers.
    - Dead connection cleanup prevents memory leaks from abandoned WebSockets
      (e.g., user closes their browser tab without a clean disconnect).

SEE ALSO:
    - services/shared/shared/core/redis.py       -- Redis singleton used here
    - services/gateway/app/main.py                -- Redis subscriber + WS endpoints
    - frontend/src/hooks/useThreadLiveUpdates.js  -- browser-side WebSocket consumer
"""

import json
import logging
from collections import defaultdict

from fastapi import WebSocket
from fastapi.encoders import jsonable_encoder
from redis.exceptions import RedisError

from shared.core.redis import get_redis_client

# Module-level logger — uses Python's hierarchical logging so messages appear
# as "shared.core.events | WARNING | ..." in structured log output.
logger = logging.getLogger(__name__)


def publish_event(channel: str, payload: dict[str, object]) -> None:
    """Publish a JSON event to a Redis pub/sub channel.

    This is the WRITE side of PulseBoard's real-time pipeline.  Backend
    services call this after mutating state (creating posts, sending
    messages, etc.) so that the Gateway can relay the update to browsers.

    WHY the try/except swallowing errors?
        Redis is used ONLY for real-time push notifications here.  If Redis
        is down, the core operation (saving the post to the database) has
        already succeeded.  We log a warning and move on rather than failing
        the entire HTTP request.  This is a deliberate trade-off:
        availability > real-time consistency.

    WHY `jsonable_encoder` before `json.dumps`?
        FastAPI's `jsonable_encoder` converts SQLAlchemy models, datetime
        objects, Pydantic models, and other non-JSON-native types into
        plain dicts/lists/strings that `json.dumps` can handle.  Without it,
        `json.dumps` would raise TypeError on datetime fields.

    Args:
        channel: Redis pub/sub channel name.  Convention:
                 - "thread:<id>"          for thread live updates
                 - "chat:room:<id>"       for chat room messages
                 - "notifications:<user>" for per-user notifications
                 - "global"               for system-wide broadcasts
        payload: Event data (must be JSON-serializable after encoding).
    """
    try:
        # Get the singleton Redis client (see redis.py for why singleton).
        client = get_redis_client()

        # Two-step serialization:
        # 1. jsonable_encoder: Python objects -> JSON-compatible dicts
        # 2. json.dumps: dict -> JSON string (Redis pub/sub sends strings)
        client.publish(channel, json.dumps(jsonable_encoder(payload)))
    except RedisError:
        # Silently degrade: log a warning but do NOT re-raise.
        # The caller (e.g., create_post) has already committed to the DB.
        # Tests also rely on this: they run without a Redis server, and
        # publish_event silently no-ops so tests don't need Redis mocking.
        logger.warning("Redis publish skipped for channel=%s", channel)


class ConnectionManager:
    """Manages WebSocket connections grouped by channel name.

    This class implements the Gateway's side of the real-time pipeline.
    It tracks which WebSocket clients are listening on which channels
    and provides methods to broadcast messages to all clients on a channel.

    WHY group by channel?
        Different users are viewing different pages.  A user on thread 42
        only needs updates for thread 42, not for thread 99.  Grouping by
        channel lets us send targeted updates instead of broadcasting
        everything to everyone (which would waste bandwidth and leak data).

    INTERVIEW TALKING POINTS:
        - This is essentially the Observer/Pub-Sub pattern at the application
          layer: channels are topics, WebSocket connections are subscribers.
        - `defaultdict(list)` avoids KeyError when a channel has no
          subscribers yet — it auto-creates an empty list on first access.
        - The broadcast method implements dead connection cleanup, which is
          critical for long-lived WebSocket servers.  Without it, the
          connections dict would grow indefinitely as users close tabs.
    """

    def __init__(self) -> None:
        # Channel name -> list of active WebSocket connections.
        # defaultdict(list) means we never need to check "does this channel
        # exist?" before appending — it auto-initializes to an empty list.
        self.connections: dict[str, list[WebSocket]] = defaultdict(list)

    async def connect(self, channel: str, websocket: WebSocket) -> None:
        """Accept a WebSocket handshake and register it on a channel.

        WHY `accept()` here?
            The WebSocket protocol requires a server-side "accept" to complete
            the handshake.  Until `accept()` is called, the connection is in a
            pending state and cannot send/receive data.  We do it here so that
            callers don't need to remember to call it separately.

        Args:
            channel: The channel this client wants to subscribe to.
            websocket: The FastAPI WebSocket object from the endpoint handler.
        """
        # Complete the WebSocket handshake (HTTP 101 Switching Protocols).
        await websocket.accept()
        # Register this connection under the given channel.
        self.connections[channel].append(websocket)

    def disconnect(self, channel: str, websocket: WebSocket) -> None:
        """Remove a WebSocket from a channel's subscriber list.

        Called when a client cleanly disconnects (WebSocketDisconnect exception)
        or when broadcast detects a dead connection.

        WHY the `if` check?
            In rare race conditions, disconnect could be called twice for the
            same socket (once from the endpoint's except block, once from
            broadcast's dead connection cleanup).  The `if` guard prevents a
            ValueError from `list.remove()` on an already-removed item.

        Args:
            channel: The channel the client was subscribed to.
            websocket: The WebSocket to remove.
        """
        if websocket in self.connections[channel]:
            self.connections[channel].remove(websocket)

    async def broadcast(self, channel: str, message: dict[str, object]) -> None:
        """Send a message to ALL WebSocket connections subscribed to a channel.

        This is the core fan-out mechanism: one Redis event becomes N WebSocket
        pushes (one per connected client viewing that channel).

        DEAD CONNECTION CLEANUP:
            WebSocket connections can die silently (user closes laptop lid,
            network drops, browser crash).  When we try to send to a dead
            socket, it raises an exception.  Instead of failing the entire
            broadcast, we catch the error, collect dead connections, and remove
            them after the loop.

        WHY `list(self.connections[channel])`?
            We iterate over a COPY of the list because we might modify the
            original list during iteration (via disconnect).  Iterating over
            a list while modifying it leads to skipped items or IndexError.

        Args:
            channel: The channel to broadcast on.
            message: JSON-serializable dict to send to all subscribers.
        """
        # Collect dead connections to remove AFTER the loop — modifying a list
        # while iterating over it is unsafe even with a copy, so we batch the
        # removals.
        dead: list[WebSocket] = []

        # Iterate over a snapshot (copy) of the connections list.
        for connection in list(self.connections[channel]):
            try:
                # send_json serializes the dict to JSON and sends it as a
                # WebSocket text frame.  This is a FastAPI/Starlette helper.
                await connection.send_json(message)
            except Exception:
                # Any exception means this connection is dead or broken.
                # Common causes: client disconnected, network timeout, broken pipe.
                # We don't log here to avoid log spam from normal disconnects.
                dead.append(connection)

        # Clean up all dead connections we found during this broadcast pass.
        for connection in dead:
            self.disconnect(channel, connection)

    async def send_to_channel(self, channel: str, message: dict[str, object]) -> None:
        """Alias for broadcast — exists for semantic clarity in calling code.

        Some callers use `send_to_channel` when the intent is "push to a
        specific channel" vs. `broadcast` which sounds like "push to everyone".
        They do the same thing, but the name helps readability at the call site.

        Args:
            channel: The channel to send to.
            message: JSON-serializable dict to send.
        """
        await self.broadcast(channel, message)


# Module-level singleton instance of ConnectionManager.
# WHY a module-level singleton?  The Gateway process needs exactly ONE manager
# that all WebSocket endpoint handlers share.  If each endpoint created its own
# ConnectionManager, they wouldn't see each other's connections and broadcasts
# would only reach a fraction of subscribers.
# Importing `from shared.core.events import connection_manager` anywhere in the
# Gateway gives access to the same instance (Python modules are singletons).
connection_manager = ConnectionManager()

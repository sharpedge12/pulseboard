import json
import logging
from collections import defaultdict

from fastapi import WebSocket
from fastapi.encoders import jsonable_encoder
from redis.exceptions import RedisError

from shared.core.redis import get_redis_client

logger = logging.getLogger(__name__)


def publish_event(channel: str, payload: dict[str, object]) -> None:
    """Publish a JSON event to a Redis pub/sub channel."""
    try:
        client = get_redis_client()
        client.publish(channel, json.dumps(jsonable_encoder(payload)))
    except RedisError:
        logger.warning("Redis publish skipped for channel=%s", channel)


class ConnectionManager:
    """Manages WebSocket connections grouped by channel name."""

    def __init__(self) -> None:
        self.connections: dict[str, list[WebSocket]] = defaultdict(list)

    async def connect(self, channel: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections[channel].append(websocket)

    def disconnect(self, channel: str, websocket: WebSocket) -> None:
        if websocket in self.connections[channel]:
            self.connections[channel].remove(websocket)

    async def broadcast(self, channel: str, message: dict[str, object]) -> None:
        dead: list[WebSocket] = []
        for connection in list(self.connections[channel]):
            try:
                await connection.send_json(message)
            except Exception:
                dead.append(connection)
        for connection in dead:
            self.disconnect(channel, connection)

    async def send_to_channel(self, channel: str, message: dict[str, object]) -> None:
        await self.broadcast(channel, message)


connection_manager = ConnectionManager()

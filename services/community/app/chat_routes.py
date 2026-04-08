"""
Chat Routes — HTTP API Endpoints for Chat Rooms and Messages.

This module defines the ``/api/v1/chat`` router, which powers the real-time
chat feature in PulseBoard.  The chat system supports two room types:

    - **Group rooms** — named rooms that any user can join (like Discord
      channels or Slack channels).
    - **Direct rooms** — private 1-on-1 conversations between exactly two
      users (like DMs).

Architecture notes:
    - **Thin routes, fat services**: Route handlers parse HTTP input and
      handle real-time event broadcasting; all business logic (room creation,
      message persistence, notifications, bot triggers) lives in
      ``chat_services.py``.
    - **Real-time events**: After creating a message, the route broadcasts
      a ``message_created`` event on two channels:
      1. ``chat:{room_id}`` (in-process WebSocket via ``ConnectionManager``)
      2. ``chat:room:{room_id}`` (Redis pub/sub → gateway → all browsers).
      Individual notification events are also sent to each recipient's
      personal ``notifications:{user_id}`` channel.
    - **Auth required**: All chat endpoints require authentication.
      Message creation additionally requires ``require_can_participate``
      (blocks suspended/banned users).

Endpoint summary:
    - ``GET  /chat/rooms``                    — list rooms the user belongs to.
    - ``POST /chat/rooms``                    — create a new room.
    - ``POST /chat/direct/{target_username}`` — create or return a DM room.
    - ``POST /chat/rooms/{room_id}/members``  — join a group room.
    - ``GET  /chat/rooms/{room_id}``          — get room details.
    - ``GET  /chat/rooms/{room_id}/messages`` — list room messages.
    - ``POST /chat/rooms/{room_id}/messages`` — send a message.

Called from:
    The API gateway reverse-proxies ``/api/v1/chat/*`` to this service.
"""

from fastapi import APIRouter, Depends, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from shared.core.database import get_db
from shared.core.auth_helpers import get_current_user, require_can_participate
from shared.core.events import connection_manager, publish_event
from shared.models.user import User
from shared.schemas.chat import (
    ChatMessageCreateRequest,
    ChatMessageResponse,
    ChatRoomCreateRequest,
    ChatRoomResponse,
)
from app.chat_services import (
    create_chat_message,
    create_direct_room_with_user,
    create_chat_room,
    get_chat_room_response,
    join_chat_room,
    list_chat_rooms as list_chat_rooms_service,
    list_room_messages,
)

router = APIRouter()


# ===========================================================================
# Room listing and creation
# ===========================================================================


@router.get("/rooms", response_model=list[ChatRoomResponse])
def list_chat_rooms(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ChatRoomResponse]:
    """
    GET /api/v1/chat/rooms

    List all chat rooms the current user is a member of.

    Rooms are sorted by ``updated_at`` (most recently active first) and
    include a ``last_message`` preview for the chat sidebar.  For direct
    rooms, the ``display_name`` is set to the other user's username
    instead of the auto-generated room name.

    Requires: authentication.
    """
    return list_chat_rooms_service(db, current_user)


@router.post(
    "/rooms", response_model=ChatRoomResponse, status_code=status.HTTP_201_CREATED
)
def create_room(
    payload: ChatRoomCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatRoomResponse:
    """
    POST /api/v1/chat/rooms

    Create a new chat room (group or direct).

    For direct rooms:
        - Must have exactly 2 members (the creator + one other user).
        - If a direct room between these two users already exists, the
          existing room is returned instead of creating a duplicate.

    For group rooms:
        - The creator is automatically added as a member.
        - Room name is required.

    After creation, a ``room_created`` event is published to Redis for
    real-time updates.

    Requires: authentication + active account (not suspended/banned).
    """
    require_can_participate(current_user)
    room = create_chat_room(db, payload, current_user)
    # Publish event to Redis so the gateway can notify relevant clients.
    publish_event(f"chat:room:{room.id}", {"event": "room_created", "room_id": room.id})
    return room


@router.post("/direct/{target_username}", response_model=ChatRoomResponse)
def create_direct_room(
    target_username: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatRoomResponse:
    """
    POST /api/v1/chat/direct/{target_username}

    Create (or retrieve) a direct message room with the specified user.

    This is a convenience endpoint that wraps ``create_chat_room`` with
    ``room_type="direct"``.  If a DM room between the two users already
    exists, the existing room is returned (idempotent).

    Args:
        target_username: The username of the user to DM.

    Requires: authentication + active account.

    Raises:
        HTTPException(404) if the target user does not exist.
        HTTPException(400) if trying to DM yourself.
    """
    require_can_participate(current_user)
    room = create_direct_room_with_user(db, current_user, target_username)
    publish_event(
        f"chat:room:{room.id}", {"event": "direct_room_created", "room_id": room.id}
    )
    return room


# ===========================================================================
# Room membership
# ===========================================================================


@router.post("/rooms/{room_id}/members", response_model=ChatRoomResponse)
def add_room_member(
    room_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatRoomResponse:
    """
    POST /api/v1/chat/rooms/{room_id}/members

    Join a group chat room.  The current user is added as a member.

    - Direct rooms cannot be joined this way (HTTP 403).
    - Joining an already-joined room is idempotent (no-op).

    After joining, a ``member_joined`` event is published for real-time
    member list updates.
    """
    room = join_chat_room(db, room_id, current_user.id)
    publish_event(
        f"chat:room:{room.id}", {"event": "member_joined", "user_id": current_user.id}
    )
    return room


# ===========================================================================
# Room details and messages
# ===========================================================================


@router.get("/rooms/{room_id}", response_model=ChatRoomResponse)
def get_room(
    room_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatRoomResponse:
    """
    GET /api/v1/chat/rooms/{room_id}

    Get details for a specific chat room (name, members, last message).

    Requires: the current user must be a member of the room (HTTP 403
    otherwise).
    """
    return get_chat_room_response(db, room_id, current_user)


@router.get("/rooms/{room_id}/messages", response_model=list[ChatMessageResponse])
def get_room_messages(
    room_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ChatMessageResponse]:
    """
    GET /api/v1/chat/rooms/{room_id}/messages

    List all messages in a chat room, sorted chronologically (oldest first).

    Requires: the current user must be a member of the room.
    """
    return list_room_messages(db, room_id, current_user)


# ===========================================================================
# Message creation
# ===========================================================================


@router.post(
    "/rooms/{room_id}/messages",
    response_model=ChatMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_room_message(
    room_id: int,
    payload: ChatMessageCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatMessageResponse:
    """
    POST /api/v1/chat/rooms/{room_id}/messages

    Send a message in a chat room.

    Features:
        - Supports reply threading via ``reply_to_message_id``.
        - Supports file attachments via ``attachment_ids``.
        - Detects ``@pulse`` mentions to trigger the AI bot.
        - Sends notifications to all other room members.
        - Parses ``@username`` mentions for targeted notifications.

    After creation, two types of real-time events are broadcast:
        1. ``message_created`` on the room's WebSocket channel (so all
           users viewing the room see the new message instantly).
        2. ``notification_created`` on each recipient's personal
           notification channel (for the notification bell in the navbar).

    Requires: authentication + active account (not suspended/banned).
    """
    require_can_participate(current_user)

    # create_chat_message returns the serialised message AND a list of
    # user IDs that should receive notification events.
    message, recipient_ids = create_chat_message(
        db,
        room_id,
        payload.body,
        current_user,
        payload.reply_to_message_id,
        payload.attachment_ids,
    )

    # --- Real-time broadcasting ---
    # 1. Broadcast the new message to everyone viewing this chat room.
    chat_event = jsonable_encoder(
        {
            "event": "message_created",
            "room_id": room_id,
            "message": message.model_dump(),
        }
    )
    await connection_manager.broadcast(f"chat:{room_id}", chat_event)
    publish_event(f"chat:room:{room_id}", chat_event)

    # 2. Send individual notification events to each recipient's personal
    #    notification channel (used by the notification bell in the navbar).
    for recipient_id in recipient_ids:
        notification_event = jsonable_encoder(
            {
                "event": "notification_created",
                "notification_type": "direct_message",
                "room_id": room_id,
                "title": f"New message from {current_user.username}",
            }
        )
        await connection_manager.broadcast(
            f"notifications:{recipient_id}", notification_event
        )
        publish_event(f"notifications:{recipient_id}", notification_event)

    return message

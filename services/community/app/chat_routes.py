"""Chat routes — API endpoints for chat rooms and messages."""

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


@router.get("/rooms", response_model=list[ChatRoomResponse])
def list_chat_rooms(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ChatRoomResponse]:
    return list_chat_rooms_service(db, current_user)


@router.post(
    "/rooms", response_model=ChatRoomResponse, status_code=status.HTTP_201_CREATED
)
def create_room(
    payload: ChatRoomCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatRoomResponse:
    require_can_participate(current_user)
    room = create_chat_room(db, payload, current_user)
    publish_event(f"chat:room:{room.id}", {"event": "room_created", "room_id": room.id})
    return room


@router.post("/direct/{target_username}", response_model=ChatRoomResponse)
def create_direct_room(
    target_username: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatRoomResponse:
    require_can_participate(current_user)
    room = create_direct_room_with_user(db, current_user, target_username)
    publish_event(
        f"chat:room:{room.id}", {"event": "direct_room_created", "room_id": room.id}
    )
    return room


@router.post("/rooms/{room_id}/members", response_model=ChatRoomResponse)
def add_room_member(
    room_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatRoomResponse:
    room = join_chat_room(db, room_id, current_user.id)
    publish_event(
        f"chat:room:{room.id}", {"event": "member_joined", "user_id": current_user.id}
    )
    return room


@router.get("/rooms/{room_id}", response_model=ChatRoomResponse)
def get_room(
    room_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatRoomResponse:
    return get_chat_room_response(db, room_id, current_user)


@router.get("/rooms/{room_id}/messages", response_model=list[ChatMessageResponse])
def get_room_messages(
    room_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ChatMessageResponse]:
    return list_room_messages(db, room_id, current_user)


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
    require_can_participate(current_user)
    message, recipient_ids = create_chat_message(
        db,
        room_id,
        payload.body,
        current_user,
        payload.reply_to_message_id,
        payload.attachment_ids,
    )

    chat_event = jsonable_encoder(
        {
            "event": "message_created",
            "room_id": room_id,
            "message": message.model_dump(),
        }
    )
    await connection_manager.broadcast(f"chat:{room_id}", chat_event)
    publish_event(f"chat:room:{room_id}", chat_event)

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

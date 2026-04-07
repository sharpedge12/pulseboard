"""Chat service business logic."""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from shared.models.chat import ChatRoom, ChatRoomMember, Message
from shared.models.user import User
from shared.schemas.chat import (
    ChatMessageResponse,
    ChatMessageSenderResponse,
    ChatRoomCreateRequest,
    ChatRoomMemberResponse,
    ChatRoomResponse,
)
from shared.services.bot import (
    schedule_chat_bot_reply,
    should_invoke_bot,
)
from shared.services.mentions import create_mention_notifications
from shared.services.notifications import create_notification
from shared.services.attachments import assign_attachments_to_entity, list_attachments
from shared.services.audit import record as audit_record
from shared.services import audit as audit_actions


def _serialize_message(
    message: Message, attachment_map: dict[int, list] | None = None
) -> ChatMessageResponse:
    return ChatMessageResponse(
        id=message.id,
        room_id=message.room_id,
        body=message.body,
        reply_to_message_id=message.reply_to_message_id,
        created_at=message.created_at,
        updated_at=message.updated_at,
        sender=ChatMessageSenderResponse(
            id=message.sender.id,
            username=message.sender.username,
            role=message.sender.role.value,
            avatar_url=message.sender.avatar_url,
        ),
        attachments=[
            item.model_dump() for item in (attachment_map or {}).get(message.id, [])
        ],
    )


def _serialize_room(
    room: ChatRoom, current_user: User | None = None
) -> ChatRoomResponse:
    attachment_map = {message.id: [] for message in room.messages}
    ordered_messages = sorted(
        room.messages, key=lambda message: (message.created_at, message.id)
    )
    last_message = (
        _serialize_message(ordered_messages[-1], attachment_map)
        if ordered_messages
        else None
    )
    display_name = room.name
    if room.room_type == "direct" and current_user is not None:
        other_member = next(
            (
                member
                for member in room.members
                if member.user_id != current_user.id
                and getattr(member.user, "username", None)
            ),
            None,
        )
        if other_member and other_member.user:
            display_name = other_member.user.username

    return ChatRoomResponse(
        id=room.id,
        name=room.name,
        display_name=display_name,
        room_type=room.room_type,
        created_at=room.created_at,
        updated_at=room.updated_at,
        members=[
            ChatRoomMemberResponse(
                user_id=member.user_id,
                username=getattr(member.user, "username", None),
                avatar_url=getattr(member.user, "avatar_url", None),
            )
            for member in room.members
        ],
        last_message=last_message,
    )


def _ensure_room_member(room: ChatRoom, current_user: User) -> None:
    member_ids = {member.user_id for member in room.members}
    if current_user.id not in member_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this room.",
        )


def list_chat_rooms(db: Session, current_user: User) -> list[ChatRoomResponse]:
    rooms = (
        db.execute(
            select(ChatRoom)
            .join(ChatRoom.members)
            .where(ChatRoomMember.user_id == current_user.id)
            .options(
                selectinload(ChatRoom.members),
                selectinload(ChatRoom.messages).selectinload(Message.sender),
                selectinload(ChatRoom.members).selectinload(ChatRoomMember.user),
            )
            .order_by(ChatRoom.updated_at.desc())
        )
        .scalars()
        .unique()
        .all()
    )
    return [_serialize_room(room, current_user) for room in rooms]


def create_chat_room(
    db: Session,
    payload: ChatRoomCreateRequest,
    current_user: User,
) -> ChatRoomResponse:
    member_ids = set(payload.member_ids)
    member_ids.add(current_user.id)

    if payload.room_type == "direct" and len(member_ids) != 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Direct rooms must contain exactly two members.",
        )

    if payload.room_type == "direct":
        sorted_ids = sorted(member_ids)
        existing_rooms = (
            db.execute(
                select(ChatRoom)
                .where(ChatRoom.room_type == "direct")
                .options(
                    selectinload(ChatRoom.members),
                    selectinload(ChatRoom.messages).selectinload(Message.sender),
                    selectinload(ChatRoom.members).selectinload(ChatRoomMember.user),
                )
            )
            .scalars()
            .unique()
            .all()
        )
        for room in existing_rooms:
            room_member_ids = sorted(member.user_id for member in room.members)
            if room_member_ids == sorted_ids:
                return _serialize_room(room, current_user)

    room = ChatRoom(
        name=payload.name, room_type=payload.room_type, created_by_id=current_user.id
    )
    db.add(room)
    db.flush()

    for member_id in member_ids:
        db.add(ChatRoomMember(room_id=room.id, user_id=member_id))

    audit_record(
        db,
        actor_id=current_user.id,
        action=audit_actions.CHAT_ROOM_CREATE,
        entity_type="chat_room",
        entity_id=room.id,
        details={"name": payload.name, "room_type": payload.room_type},
    )
    db.commit()
    created_room = db.execute(
        select(ChatRoom)
        .where(ChatRoom.id == room.id)
        .options(
            selectinload(ChatRoom.members),
            selectinload(ChatRoom.messages).selectinload(Message.sender),
            selectinload(ChatRoom.members).selectinload(ChatRoomMember.user),
        )
    ).scalar_one()
    return _serialize_room(created_room, current_user)


def get_chat_room(db: Session, room_id: int, current_user: User) -> ChatRoom:
    room = db.execute(
        select(ChatRoom)
        .where(ChatRoom.id == room_id)
        .options(
            selectinload(ChatRoom.members),
            selectinload(ChatRoom.messages).selectinload(Message.sender),
            selectinload(ChatRoom.members).selectinload(ChatRoomMember.user),
        )
    ).scalar_one_or_none()
    if not room:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chat room not found."
        )

    _ensure_room_member(room, current_user)
    return room


def list_room_messages(
    db: Session, room_id: int, current_user: User
) -> list[ChatMessageResponse]:
    room = get_chat_room(db, room_id, current_user)
    ordered_messages = sorted(
        room.messages, key=lambda message: (message.created_at, message.id)
    )
    attachment_map = list_attachments(
        db, "message", [message.id for message in ordered_messages]
    )
    return [_serialize_message(message, attachment_map) for message in ordered_messages]


def create_chat_message(
    db: Session,
    room_id: int,
    body: str,
    current_user: User,
    reply_to_message_id: int | None = None,
    attachment_ids: list[int] | None = None,
) -> tuple[ChatMessageResponse, list[int]]:
    room = get_chat_room(db, room_id, current_user)

    if reply_to_message_id is not None:
        reply_target = db.execute(
            select(Message).where(
                Message.id == reply_to_message_id,
                Message.room_id == room_id,
            )
        ).scalar_one_or_none()
        if not reply_target:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Reply target not found."
            )

    message = Message(
        room_id=room.id,
        sender_id=current_user.id,
        body=body,
        reply_to_message_id=reply_to_message_id,
    )
    db.add(message)
    db.flush()
    assign_attachments_to_entity(
        db,
        current_user,
        attachment_ids or [],
        "message",
        message.id,
    )

    recipient_ids = [
        member.user_id for member in room.members if member.user_id != current_user.id
    ]
    for recipient_id in recipient_ids:
        create_notification(
            db,
            user_id=recipient_id,
            notification_type="direct_message"
            if room.room_type == "direct"
            else "group_message",
            title=f"New message from {current_user.username}",
            payload={
                "room_id": room.id,
                "message_id": message.id,
                "room_name": room.name,
            },
        )

    mention_recipients = create_mention_notifications(
        db,
        body,
        current_user,
        notification_type="mention",
        title_template="{actor} mentioned you in chat",
        payload_factory=lambda _user: {"room_id": room.id, "message_id": message.id},
    )
    for recipient_id in mention_recipients:
        if recipient_id not in recipient_ids:
            recipient_ids.append(recipient_id)

    _invoke_bot = should_invoke_bot(body)

    db.commit()

    if _invoke_bot:
        # Bot reply is generated asynchronously in a background thread.
        # The user's message is committed immediately; the bot reply will
        # appear via WebSocket once ready.
        schedule_chat_bot_reply(
            room_id=room.id,
            reply_to_message_id=message.id,
            user_message=body,
            poster_user_id=current_user.id,
        )
    created_message = db.execute(
        select(Message)
        .where(Message.id == message.id)
        .options(selectinload(Message.sender))
    ).scalar_one()
    attachment_map = list_attachments(db, "message", [created_message.id])
    return _serialize_message(created_message, attachment_map), recipient_ids


def join_chat_room(db: Session, room_id: int, user_id: int) -> ChatRoomResponse:
    room = db.execute(
        select(ChatRoom)
        .where(ChatRoom.id == room_id)
        .options(
            selectinload(ChatRoom.members),
            selectinload(ChatRoom.messages).selectinload(Message.sender),
        )
    ).scalar_one_or_none()
    if not room:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chat room not found."
        )

    if room.room_type == "direct":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Direct rooms cannot be joined by invite link.",
        )

    existing_member = db.execute(
        select(ChatRoomMember).where(
            ChatRoomMember.room_id == room_id,
            ChatRoomMember.user_id == user_id,
        )
    ).scalar_one_or_none()
    if not existing_member:
        db.add(ChatRoomMember(room_id=room_id, user_id=user_id))
        db.commit()

    refreshed_room = db.execute(
        select(ChatRoom)
        .where(ChatRoom.id == room_id)
        .options(
            selectinload(ChatRoom.members),
            selectinload(ChatRoom.messages).selectinload(Message.sender),
            selectinload(ChatRoom.members).selectinload(ChatRoomMember.user),
        )
    ).scalar_one()
    acting_user = db.execute(select(User).where(User.id == user_id)).scalar_one()
    return _serialize_room(refreshed_room, acting_user)


def create_direct_room_with_user(
    db: Session,
    current_user: User,
    target_username: str,
) -> ChatRoomResponse:
    target_user = db.execute(
        select(User).where(User.username == target_username)
    ).scalar_one_or_none()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
        )
    if target_user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot create a direct room with yourself.",
        )

    return create_chat_room(
        db,
        ChatRoomCreateRequest(
            name=f"{current_user.username} and {target_user.username}",
            room_type="direct",
            member_ids=[target_user.id],
        ),
        current_user,
    )


def get_chat_room_response(
    db: Session, room_id: int, current_user: User
) -> ChatRoomResponse:
    room = get_chat_room(db, room_id, current_user)
    return _serialize_room(room, current_user)

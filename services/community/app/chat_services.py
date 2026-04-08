"""
Chat Services — Business Logic for Chat Rooms and Messages.

This module implements all the business logic for PulseBoard's real-time
chat system.  It sits between the thin HTTP route handlers
(``chat_routes.py``) and the database models (``shared.models.chat``).

The chat system supports two room types:

    - **Group rooms** — named multi-user rooms (like Discord channels).
      Any user can join via ``join_chat_room``.
    - **Direct rooms** — private 1-on-1 conversations.  Cannot be joined
      by a third user; created via ``create_direct_room_with_user``.

Key design patterns for interviews:

    1. **Idempotent DM creation**: When creating a direct room, the system
       first checks if a DM room between the two users already exists.
       If so, it returns the existing room rather than creating a duplicate.
       This means the frontend can call "create DM with user X" repeatedly
       without side effects.

    2. **Display name resolution**: For direct rooms, ``_serialize_room``
       resolves the ``display_name`` to the *other* user's username (so
       each participant sees the room named after the person they're
       chatting with, not a generic "alice and bob" label).

    3. **Notification fan-out**: When a message is sent, notifications are
       created for all other room members.  ``@mention`` notifications are
       handled separately via ``create_mention_notifications`` and merged
       into the recipient list (avoiding duplicates).

    4. **AI bot trigger**: If the message body contains ``@pulse``, the
       bot reply is scheduled in a background daemon thread (after commit)
       so the user's message is persisted immediately while the AI
       generates a response asynchronously.

    5. **Attachment handling**: Chat messages support file attachments via
       the shared ``assign_attachments_to_entity`` service.  Attachments
       are uploaded as "drafts" first (via the upload endpoint), then
       linked to the message at creation time.

Called from:
    ``app.chat_routes`` (HTTP layer).
"""

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


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _serialize_message(
    message: Message, attachment_map: dict[int, list] | None = None
) -> ChatMessageResponse:
    """
    Convert a ``Message`` ORM object into a ``ChatMessageResponse``
    Pydantic schema.

    Includes the sender's profile (id, username, role, avatar) and any
    file attachments linked to the message.

    Args:
        message: The Message ORM object (with ``sender`` eager-loaded).
        attachment_map: Optional dict mapping ``message_id → list`` of
            attachment schema objects.  Attachments for this message are
            looked up by ``message.id``.
    """
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
    """
    Convert a ``ChatRoom`` ORM object into a ``ChatRoomResponse``
    Pydantic schema.

    Special handling for direct rooms:
        The ``display_name`` is resolved to the *other* member's username
        so each user sees a meaningful room label (e.g. "alice" instead of
        "alice and bob").  For group rooms, ``display_name`` equals the
        room's ``name``.

    Also includes:
        - The ``last_message`` preview (most recent message in the room),
          used by the chat sidebar to show conversation previews.
        - The full ``members`` list with usernames and avatars.

    Args:
        room: The ChatRoom ORM object (with ``members``, ``messages``,
            and nested ``sender`` eager-loaded).
        current_user: The viewing user (used to determine the "other"
            member in direct rooms).
    """
    # Build an attachment map for message serialisation (empty for sidebar
    # previews — full attachments are fetched in list_room_messages).
    attachment_map = {message.id: [] for message in room.messages}

    # Sort messages chronologically to find the most recent one.
    ordered_messages = sorted(
        room.messages, key=lambda message: (message.created_at, message.id)
    )
    last_message = (
        _serialize_message(ordered_messages[-1], attachment_map)
        if ordered_messages
        else None
    )

    # --- Display name resolution for direct rooms ---
    display_name = room.name
    if room.room_type == "direct" and current_user is not None:
        # Find the OTHER member in the DM (not the current user).
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


# ---------------------------------------------------------------------------
# Access control helper
# ---------------------------------------------------------------------------


def _ensure_room_member(room: ChatRoom, current_user: User) -> None:
    """
    Guard: raise HTTP 403 if the current user is not a member of the room.

    Called before any operation that reads or writes room data (messages,
    member list, etc.).  This enforces the privacy boundary between rooms.
    """
    member_ids = {member.user_id for member in room.members}
    if current_user.id not in member_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this room.",
        )


# ===========================================================================
# Room listing
# ===========================================================================


def list_chat_rooms(db: Session, current_user: User) -> list[ChatRoomResponse]:
    """
    List all chat rooms the current user belongs to.

    The query joins through ``ChatRoomMember`` to filter rooms, then
    eager-loads members, messages (with senders), and member user profiles
    to avoid N+1 queries when serialising.

    Rooms are sorted by ``updated_at`` descending (most recently active
    rooms appear first in the sidebar).

    Args:
        db: Active database session.
        current_user: The authenticated user.

    Returns:
        List of ``ChatRoomResponse`` objects with last_message previews.
    """
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
        .unique()  # Deduplicate rows from the JOIN.
        .all()
    )
    return [_serialize_room(room, current_user) for room in rooms]


# ===========================================================================
# Room creation
# ===========================================================================


def create_chat_room(
    db: Session,
    payload: ChatRoomCreateRequest,
    current_user: User,
) -> ChatRoomResponse:
    """
    Create a new chat room (group or direct).

    Direct room deduplication:
        For ``room_type="direct"``, the function checks whether a DM room
        between the two users already exists.  If found, it returns the
        existing room (idempotent).  The check sorts member IDs to ensure
        consistent comparison regardless of who initiated the DM.

    Flow:
        1. Validate member count (direct rooms must have exactly 2 members).
        2. For direct rooms, check for existing DM (return early if found).
        3. Create the ``ChatRoom`` row.
        4. Create ``ChatRoomMember`` rows for each member (including the
           creator).
        5. Record an audit log entry.
        6. Commit and re-fetch with eager-loaded relationships.

    Args:
        db: Active database session.
        payload: ``ChatRoomCreateRequest`` with name, room_type, member_ids.
        current_user: The user creating the room.

    Returns:
        ``ChatRoomResponse`` for the new (or existing) room.

    Raises:
        HTTPException(400) if a direct room doesn't have exactly 2 members.
    """
    # Ensure the creator is always included in the member set.
    member_ids = set(payload.member_ids)
    member_ids.add(current_user.id)

    # Direct rooms must have exactly 2 participants.
    if payload.room_type == "direct" and len(member_ids) != 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Direct rooms must contain exactly two members.",
        )

    # --- Idempotent DM creation: check for existing direct room ---
    if payload.room_type == "direct":
        sorted_ids = sorted(member_ids)
        # Query all direct rooms and compare member sets.
        # (A more efficient approach would use a composite hash column,
        # but for PulseBoard's scale this scan is fine.)
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
                # Found an existing DM — return it instead of creating a new one.
                return _serialize_room(room, current_user)

    # --- Create the new room ---
    room = ChatRoom(
        name=payload.name, room_type=payload.room_type, created_by_id=current_user.id
    )
    db.add(room)
    db.flush()  # Get the auto-generated room.id for member rows.

    # Add each member to the room.
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

    # Re-fetch with eager-loaded relationships for complete serialisation.
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


# ===========================================================================
# Room access (internal helper)
# ===========================================================================


def get_chat_room(db: Session, room_id: int, current_user: User) -> ChatRoom:
    """
    Fetch a chat room by ID with full eager-loading, and verify the
    current user is a member.

    This is an internal helper used by ``list_room_messages``,
    ``create_chat_message``, and ``get_chat_room_response``.  It
    centralises the membership check and eager-loading logic.

    Args:
        db: Active database session.
        room_id: The room to fetch.
        current_user: The requesting user (must be a member).

    Returns:
        The ``ChatRoom`` ORM object with members and messages loaded.

    Raises:
        HTTPException(404) if the room does not exist.
        HTTPException(403) if the user is not a member.
    """
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


# ===========================================================================
# Message listing
# ===========================================================================


def list_room_messages(
    db: Session, room_id: int, current_user: User
) -> list[ChatMessageResponse]:
    """
    List all messages in a chat room, sorted chronologically.

    Messages are sorted by ``(created_at, id)`` to ensure a stable
    ordering even when multiple messages share the same timestamp.

    Attachments are bulk-fetched for all messages in a single query
    (avoiding N+1) via ``list_attachments``.

    Args:
        db: Active database session.
        room_id: The room whose messages to list.
        current_user: The requesting user (must be a member).

    Returns:
        List of ``ChatMessageResponse`` objects (oldest first).
    """
    room = get_chat_room(db, room_id, current_user)
    ordered_messages = sorted(
        room.messages, key=lambda message: (message.created_at, message.id)
    )
    # Bulk-fetch attachments for all messages in one query.
    attachment_map = list_attachments(
        db, "message", [message.id for message in ordered_messages]
    )
    return [_serialize_message(message, attachment_map) for message in ordered_messages]


# ===========================================================================
# Message creation
# ===========================================================================


def create_chat_message(
    db: Session,
    room_id: int,
    body: str,
    current_user: User,
    reply_to_message_id: int | None = None,
    attachment_ids: list[int] | None = None,
) -> tuple[ChatMessageResponse, list[int]]:
    """
    Create a new chat message in a room and dispatch notifications.

    This function orchestrates several sub-operations:

    1. **Room access check** — verify the room exists and the user is a
       member (via ``get_chat_room``).
    2. **Reply validation** — if ``reply_to_message_id`` is given, verify
       the target message exists in this room.
    3. **Message insertion** — create the ``Message`` row.
    4. **Attachment linking** — reassign draft attachments to this message.
    5. **Notification dispatch** — create in-app notifications for:
       a. All other room members (DM: ``direct_message`` type, group:
          ``group_message`` type).
       b. @mentioned users (parsed from the message body).
       Duplicate recipients are tracked to avoid double notifications.
    6. **Bot detection** — check for ``@pulse`` mention (before commit).
    7. **Commit** — persist everything atomically.
    8. **Bot trigger** — if ``@pulse`` was detected, schedule the AI bot
       reply in a background daemon thread (after commit so the bot can
       read the committed message).

    Args:
        db: Active database session.
        room_id: The room to send the message in.
        body: The message text.
        current_user: The sender.
        reply_to_message_id: Optional message ID to reply to (threading).
        attachment_ids: Optional list of pre-uploaded attachment IDs.

    Returns:
        Tuple of:
            - ``ChatMessageResponse`` — the serialised new message.
            - ``list[int]`` — user IDs that should receive real-time
              notification events (used by the route for WebSocket
              broadcasting).

    Raises:
        HTTPException(404) if the room or reply target doesn't exist.
        HTTPException(403) if the user is not a room member.
    """
    # Verify room access (also eager-loads members and messages).
    room = get_chat_room(db, room_id, current_user)

    # Validate the reply target if provided.
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

    # --- Create the message ---
    message = Message(
        room_id=room.id,
        sender_id=current_user.id,
        body=body,
        reply_to_message_id=reply_to_message_id,
    )
    db.add(message)
    db.flush()  # Get the auto-generated message.id for attachment linking.

    # Link pre-uploaded draft attachments to this message.
    assign_attachments_to_entity(
        db,
        current_user,
        attachment_ids or [],
        "message",
        message.id,
    )

    # --- Notify all other room members ---
    recipient_ids = [
        member.user_id for member in room.members if member.user_id != current_user.id
    ]
    for recipient_id in recipient_ids:
        create_notification(
            db,
            user_id=recipient_id,
            # Notification type depends on room type: DM vs group.
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

    # --- @mention notifications ---
    # Parse @username patterns from the message body and create targeted
    # notifications.  Merge into the recipient list to avoid duplicates
    # in the WebSocket broadcast.
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

    # Check for @pulse bot mention BEFORE commit (just a string check).
    _invoke_bot = should_invoke_bot(body)

    db.commit()  # Persist message, attachments, and notifications.

    # --- AI bot trigger (AFTER commit) ---
    # The bot runs in a daemon background thread with its own DB session.
    # It needs the message to be committed before it can read it.
    if _invoke_bot:
        schedule_chat_bot_reply(
            room_id=room.id,
            reply_to_message_id=message.id,
            user_message=body,
            poster_user_id=current_user.id,
        )

    # Re-fetch the message with eager-loaded sender for serialisation.
    created_message = db.execute(
        select(Message)
        .where(Message.id == message.id)
        .options(selectinload(Message.sender))
    ).scalar_one()
    attachment_map = list_attachments(db, "message", [created_message.id])
    return _serialize_message(created_message, attachment_map), recipient_ids


# ===========================================================================
# Room joining
# ===========================================================================


def join_chat_room(db: Session, room_id: int, user_id: int) -> ChatRoomResponse:
    """
    Add a user to a group chat room.

    Rules:
        - **Direct rooms** cannot be joined (HTTP 403) — they are created
          with exactly 2 members and are permanently closed.
        - **Group rooms** are open for any authenticated user to join.
        - Joining an already-joined room is idempotent (no-op).

    After joining, the room is re-fetched with full eager-loading so the
    response includes the new member in the members list.

    Args:
        db: Active database session.
        room_id: The room to join.
        user_id: The user who wants to join.

    Returns:
        ``ChatRoomResponse`` with updated member list.

    Raises:
        HTTPException(404) if the room does not exist.
        HTTPException(403) if the room is a direct room.
    """
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

    # Direct rooms are private — no joining allowed.
    if room.room_type == "direct":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Direct rooms cannot be joined by invite link.",
        )

    # Idempotency: check if the user is already a member.
    existing_member = db.execute(
        select(ChatRoomMember).where(
            ChatRoomMember.room_id == room_id,
            ChatRoomMember.user_id == user_id,
        )
    ).scalar_one_or_none()
    if not existing_member:
        db.add(ChatRoomMember(room_id=room_id, user_id=user_id))
        db.commit()

    # Re-fetch with full eager-loading to include the new member.
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


# ===========================================================================
# Direct message convenience
# ===========================================================================


def create_direct_room_with_user(
    db: Session,
    current_user: User,
    target_username: str,
) -> ChatRoomResponse:
    """
    Create (or retrieve) a direct message room with a target user.

    This is a convenience wrapper around ``create_chat_room`` that:
        1. Looks up the target user by username.
        2. Validates they're not the current user (no self-DMs).
        3. Constructs a ``ChatRoomCreateRequest`` with ``room_type="direct"``
           and delegates to ``create_chat_room`` (which handles idempotent
           DM creation).

    The auto-generated room name follows the pattern
    ``"{current_user} and {target_user}"``, but the frontend displays
    ``display_name`` (resolved to the other user's username) instead.

    Args:
        db: Active database session.
        current_user: The user initiating the DM.
        target_username: The username of the user to DM.

    Returns:
        ``ChatRoomResponse`` for the DM room (new or existing).

    Raises:
        HTTPException(404) if the target user does not exist.
        HTTPException(400) if trying to DM yourself.
    """
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


# ===========================================================================
# Room detail (public-facing response)
# ===========================================================================


def get_chat_room_response(
    db: Session, room_id: int, current_user: User
) -> ChatRoomResponse:
    """
    Get a serialised chat room response for a specific room.

    Combines the internal ``get_chat_room`` (which handles access control
    and eager-loading) with ``_serialize_room`` (which handles display
    name resolution and last-message preview).

    Args:
        db: Active database session.
        room_id: The room to fetch.
        current_user: The requesting user (must be a member).

    Returns:
        ``ChatRoomResponse`` with full room details.
    """
    room = get_chat_room(db, room_id, current_user)
    return _serialize_room(room, current_user)

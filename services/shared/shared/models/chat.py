"""
Chat Models — Real-Time Messaging (Rooms, Members, Messages)
==============================================================

Database tables defined here:
    - "chat_rooms"        -> ChatRoom (a conversation container)
    - "chat_room_members" -> ChatRoomMember (who's in each room)
    - "messages"          -> Message (individual chat messages)

CHAT ARCHITECTURE:
    PulseBoard supports two types of real-time messaging:
      1. DIRECT MESSAGES (DMs): Private 1-on-1 conversations between two users.
      2. GROUP CHATS: Multi-user conversations (like Slack channels or Discord
         group DMs).

    Both use the same ChatRoom model — the `room_type` column distinguishes
    them. This "single table, different types" approach is simpler than having
    separate DirectMessage and GroupChat tables, and the query patterns are
    identical.

REAL-TIME FLOW:
    1. User sends a message -> HTTP POST to /api/v1/chat/rooms/{id}/messages
    2. Server creates a Message row in the database
    3. Server publishes the message to Redis pub/sub (channel: chat:room:{id})
    4. Gateway's Redis-to-WebSocket bridge picks up the message
    5. Gateway broadcasts to all WebSocket clients subscribed to that room
    6. Frontend's useChatRoom.js hook receives the message and updates the UI

    The database is the source of truth; Redis + WebSocket are for real-time
    delivery only. If a user is offline, they'll see the messages when they
    load the room (fetched from the database).

MANY-TO-MANY: USERS <-> CHAT ROOMS
    A user can be in many rooms, and a room can have many users. This is a
    classic many-to-many relationship, implemented via the ChatRoomMember
    junction table. Each row in ChatRoomMember means "User X is a member
    of Room Y".
"""

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.core.database import Base
from shared.models.base import TimestampMixin


class ChatRoom(TimestampMixin, Base):
    """
    A chat room — container for a conversation between two or more users.

    Database table: "chat_rooms"

    ROOM TYPES:
        - "direct": A private 1-on-1 DM between exactly two users.
                    Name is typically auto-generated (e.g., "alice, bob").
        - "group":  A multi-user conversation with a custom name.
                    Created explicitly by a user who invites others.

    WHY ONE TABLE FOR BOTH TYPES?
        Direct and group rooms have identical structure (name, members,
        messages). Using a single table with a room_type discriminator avoids
        code duplication. The application layer enforces type-specific rules
        (e.g., direct rooms always have exactly 2 members).

    Relationships:
        - members:  Users in this room (one-to-many to ChatRoomMember junction)
        - messages: All messages in this room (one-to-many, chronological)
    """

    __tablename__ = "chat_rooms"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Display name for the room. For group chats, this is user-chosen (e.g.,
    # "Backend Team"). For DMs, it's auto-generated from participant usernames.
    name: Mapped[str] = mapped_column(String(255))

    # Discriminator column: "direct" or "group". Indexed because we often
    # filter rooms by type (e.g., "show me all my DMs" vs "show me all groups").
    # String(50) instead of an Enum for flexibility — new room types can be
    # added without a database migration.
    room_type: Mapped[str] = mapped_column(String(50), index=True)

    # The user who created this room. For DMs, this is the user who initiated
    # the conversation. CASCADE deletes the room if the creator's account is
    # deleted (this is debatable — an alternative is SET NULL).
    created_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )

    # cascade="all, delete-orphan": deleting a room deletes all memberships
    # and messages. This is the correct behavior — a deleted room's messages
    # are meaningless without the room context.
    members = relationship(
        "ChatRoomMember", back_populates="room", cascade="all, delete-orphan"
    )
    messages = relationship(
        "Message", back_populates="room", cascade="all, delete-orphan"
    )


class ChatRoomMember(TimestampMixin, Base):
    """
    Junction table linking users to chat rooms (many-to-many membership).

    Database table: "chat_room_members"

    Each row represents one user's membership in one room. The UniqueConstraint
    prevents a user from being added to the same room twice.

    WHY A JUNCTION TABLE (not a JSON array on ChatRoom)?
        Storing members as a JSON array (e.g., members = [1, 5, 12]) would be
        simpler but has serious drawbacks:
          1. No foreign key constraints — can't guarantee the user IDs exist.
          2. No indexing — "find all rooms for user 5" requires scanning every
             room's member array. O(n) instead of O(log n).
          3. No relational JOINs — can't efficiently join with the users table
             to get member profiles.
          4. No unique constraint — the DB can't prevent duplicate members.
        A junction table solves all of these problems.

    INTERVIEW TIP:
        This is the textbook way to implement many-to-many relationships in
        relational databases. The junction table has TWO foreign keys, one to
        each table in the relationship. The composite unique constraint on
        (room_id, user_id) ensures each pair appears at most once.

    Relationships:
        - room: The chat room (many-to-one)
        - user: The member user (many-to-one)
    """

    __tablename__ = "chat_room_members"

    # The UniqueConstraint with a name allows it to be referenced in migrations
    # and error messages. The tuple + trailing comma is required by SQLAlchemy's
    # __table_args__ syntax (it must be a tuple, not a single constraint).
    __table_args__ = (UniqueConstraint("room_id", "user_id", name="uq_chat_room_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)

    # CASCADE on both FKs: if the room is deleted, memberships are cleaned up.
    # If a user is deleted, their memberships are removed (they leave all rooms).
    room_id: Mapped[int] = mapped_column(
        ForeignKey("chat_rooms.id", ondelete="CASCADE")
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    room = relationship("ChatRoom", back_populates="members")
    # No back_populates to User here — we don't need user.chat_room_memberships
    # navigation from the User side. This is a conscious design choice to keep
    # the User model's relationship list manageable.
    user = relationship("User")


class Message(TimestampMixin, Base):
    """
    A single chat message within a room.

    Database table: "messages"

    Messages are the fundamental content unit of the chat system. Each message
    belongs to exactly one room and has exactly one sender. Messages can
    optionally reply to a previous message (similar to Post's self-referential
    parent_post_id, but simpler — we don't build deep trees for chat).

    Relationships:
        - room:   The chat room this message belongs to (many-to-one)
        - sender: The user who sent this message (many-to-one)

    REAL-TIME DELIVERY:
        When a message is created, the server also publishes it to Redis
        pub/sub on channel "chat:room:{room_id}". The gateway's bridge
        forwards it to connected WebSocket clients. The database row is the
        permanent record; the WebSocket push is for instant delivery.
    """

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Room this message belongs to. CASCADE: deleting a room deletes all its
    # messages. This maintains referential integrity.
    room_id: Mapped[int] = mapped_column(
        ForeignKey("chat_rooms.id", ondelete="CASCADE")
    )

    # Who sent this message. CASCADE: deleting a user deletes their messages.
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # The message content. Text type for unlimited length (supports long
    # messages with code blocks, links, etc.).
    body: Mapped[str] = mapped_column(Text)

    # Optional reference to a previous message (for "reply to" functionality).
    # Unlike Post's parent_post_id which builds deep trees, chat replies are
    # typically flat — the UI just shows "replying to: [original message]"
    # without deep nesting.
    #
    # ondelete="SET NULL" (not CASCADE): if the original message is deleted,
    # the reply remains but loses its "replying to" reference. This is
    # intentional — deleting a message shouldn't cascade-delete all messages
    # that replied to it (unlike forum comments where the subtree relationship
    # is more important).
    reply_to_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )

    room = relationship("ChatRoom", back_populates="messages")
    sender = relationship("User", back_populates="sent_messages")

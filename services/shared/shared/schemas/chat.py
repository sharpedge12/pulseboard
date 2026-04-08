"""
Chat Room & Message Schemas
=============================

This module defines Pydantic models for real-time chat functionality:
creating chat rooms, sending messages, and response serialization.

**Interview Concept: Real-time chat data modeling**

Chat has two entity types:
- **Chat Rooms** — Containers that hold messages and members.  Can be
  ``"direct"`` (1-on-1 DM between two users) or ``"group"`` (multiple
  users).
- **Messages** — Individual chat messages within a room.  Support
  replies (``reply_to_message_id``) and file attachments.

The ``room_type`` field uses regex-based enum validation
(``pattern=r"^(direct|group)$"``) instead of a Python ``Enum`` class.
This is a design choice — regex patterns in Pydantic ``Field()`` provide
the same validation with less boilerplate.

**Interview Concept: List size limits as abuse prevention**

The ``member_ids`` field is capped at 50 members.  Without this limit,
a malicious user could create a chat room with millions of member IDs,
causing the server to:
1. Query the database for each ID to verify it exists
2. Create a ``chat_room_members`` row for each member
3. Send WebSocket notifications to each member

This is a form of **algorithmic complexity attack** — legitimate input
that causes disproportionate server-side work.  Field-level ``max_length``
on lists is a simple, effective defense.
"""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from shared.services.sanitize import sanitize_text


class ChatRoomCreateRequest(BaseModel):
    """
    Schema for creating a new chat room (POST /api/v1/chat/rooms).

    Fields:
    - ``name``: Display name for the room, 1-255 chars.  For direct
      messages, this is typically auto-generated from the participants'
      usernames.  Sanitized to prevent XSS.
    - ``room_type``: Must be exactly ``"direct"`` or ``"group"``.
      The regex pattern acts as a strict whitelist — any other value
      (including ``"admin"``, ``"system"``, or SQL injection attempts)
      is rejected with a 422 error.
    - ``member_ids``: List of user IDs to add to the room.  Capped
      at 50 to prevent abuse.  For ``"direct"`` rooms, this should
      contain exactly one other user's ID (the server adds the
      creator automatically).
    """

    name: str = Field(min_length=1, max_length=255)
    # Regex-based enum: only "direct" or "group" are accepted.
    # This is equivalent to Enum validation but done at the Field level.
    room_type: str = Field(pattern=r"^(direct|group)$")
    # max_length=50 caps the number of members, NOT string length.
    # This prevents abuse: creating a room with 100,000 members would
    # generate 100,000 DB inserts and 100,000 WebSocket notifications.
    member_ids: list[int] = Field(default_factory=list, max_length=50)

    # -- XSS Prevention for room name --
    # Room names are displayed in the chat sidebar and room header.
    # Without sanitization, a name like '<img src=x onerror=alert(1)>'
    # could execute scripts for every room member.
    @field_validator("name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        return sanitize_text(v)


class ChatMessageCreateRequest(BaseModel):
    """
    Schema for sending a chat message (POST /api/v1/chat/rooms/{id}/messages).

    Fields:
    - ``body``: Message content, 1-5000 chars.  Sanitized for XSS
      prevention.  Supports ``@mention`` syntax for tagging other users.
    - ``reply_to_message_id``: Optional reference to another message
      in the same room, enabling "reply-to" threading.  ``None`` means
      this is a standalone message, not a reply.
    - ``attachment_ids``: File IDs from prior uploads.  Capped at 20
      per message.
    """

    body: str = Field(min_length=1, max_length=5000)
    # None = standalone message; int = reply to a specific message.
    reply_to_message_id: int | None = None
    attachment_ids: list[int] = Field(default_factory=list, max_length=20)

    # -- XSS Prevention for message body --
    # Chat messages are rendered in real-time via WebSocket updates.
    # A malicious message body could execute scripts in every room
    # member's browser the instant it's sent.
    @field_validator("body")
    @classmethod
    def clean_body(cls, v: str) -> str:
        return sanitize_text(v)


class ChatRoomMemberResponse(BaseModel):
    """
    Minimal member info embedded in chat room responses.

    Only includes ``user_id``, ``username``, and ``avatar_url`` — enough
    to render member avatars in the room member list without loading
    full user profiles.
    """

    user_id: int
    username: str | None = None
    avatar_url: str | None = None


class ChatMessageSenderResponse(BaseModel):
    """
    Sender info embedded in chat message responses.

    Includes ``role`` so the frontend can display role flair (e.g.,
    "Admin" or "Moderator" badge) next to the sender's name in chat.
    """

    id: int
    username: str
    role: str
    avatar_url: str | None = None


class ChatMessageResponse(BaseModel):
    """
    Response schema for a single chat message.

    ``attachments`` uses ``list[dict[str, object]]`` instead of a typed
    schema because chat message attachments have a flexible structure
    (different file types may include different metadata fields).

    **Interview Concept: datetime fields and timezone awareness**

    ``created_at`` and ``updated_at`` are ``datetime`` objects that
    Pydantic serializes to ISO 8601 strings (e.g., "2025-01-15T10:30:00Z")
    in JSON responses.  The frontend parses these with ``new Date()``
    and formats them using the shared ``timeUtils.js`` module.
    """

    id: int
    room_id: int
    body: str
    reply_to_message_id: int | None  # None if not a reply
    created_at: datetime
    updated_at: datetime
    sender: ChatMessageSenderResponse  # Who sent this message
    attachments: list[dict[str, object]] = []


class ChatRoomResponse(BaseModel):
    """
    Response schema for a chat room, including members and last message.

    Fields:
    - ``name`` — Internal room name (e.g., "dm_alice_bob" for DMs).
    - ``display_name`` — Human-friendly name shown in the UI.  For DMs,
      this is the other user's username; for groups, the room name.
    - ``room_type`` — ``"direct"`` or ``"group"``.
    - ``members`` — List of room members with minimal info.
    - ``last_message`` — The most recent message in the room (or None
      if no messages yet).  This enables the frontend to show a preview
      in the room list without loading all messages.
    """

    id: int
    name: str
    display_name: str
    room_type: str
    created_at: datetime
    updated_at: datetime
    members: list[ChatRoomMemberResponse]
    last_message: ChatMessageResponse | None = None

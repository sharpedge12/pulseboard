"""
User Profile & Social Schemas
==============================

This module defines Pydantic models for user profiles, profile updates,
friend requests, and user reports.

**Interview Concept: Multiple response shapes for the same entity**

A common pattern in API design is having *different response schemas* for
the same database model, depending on who is asking and what context
they're in:

- ``UserMeResponse`` — Full profile for the currently authenticated user.
  Includes sensitive fields like ``email``, ``is_suspended``, ``is_banned``
  because you have the right to see your own account status.

- ``UserPublicProfileResponse`` — What other users see when they visit
  your profile.  Excludes ``email``, ``is_active``, ``is_suspended``,
  ``is_banned`` — other users don't need (and shouldn't see) these.

- ``UserListItemResponse`` — Compact format used in lists (e.g., search
  results, people page).  Includes ``email`` because admins use this
  view, but omits heavy fields.

This pattern prevents **over-fetching** (sending more data than needed)
and **data leakage** (exposing sensitive fields to unauthorized users).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from shared.services.sanitize import sanitize_text, sanitize_username


class UserUpdateRequest(BaseModel):
    """
    Schema for updating the current user's profile (PATCH /api/v1/users/me).

    Both fields are optional (``None`` = "don't change this field").
    This is a common pattern for PATCH endpoints — you only send the
    fields you want to update.

    **Interview Concept: Why sanitize user-editable text?**

    The ``bio`` field accepts freeform text that will be displayed to other
    users.  Without sanitization, an attacker could set their bio to
    ``<script>alert('XSS')</script>`` and every user who views their
    profile would execute that script.  The ``sanitize_text()`` validator
    strips dangerous HTML tags, ``javascript:`` URIs, and inline event
    handlers to prevent Stored XSS attacks.
    """

    # Username: alphanumeric + underscore only, 3-50 chars.
    # The regex pattern is a first-pass filter; the field_validator below
    # provides a second pass via sanitize_username() for defense-in-depth.
    username: str | None = Field(
        default=None, min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_]+$"
    )
    # Bio: freeform text, max 500 chars.  Sanitized to prevent XSS.
    bio: str | None = Field(default=None, max_length=500)

    # -- Username Sanitization --
    # Even though the regex already restricts characters, sanitize_username()
    # strips whitespace and forcibly removes any non-[a-zA-Z0-9_] character.
    # This guards against Unicode normalization tricks that might bypass
    # the regex (e.g., fullwidth characters that look like ASCII).
    @field_validator("username")
    @classmethod
    def clean_username(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return sanitize_username(v)

    # -- Bio Sanitization --
    # Prevents stored XSS by stripping <script> tags, javascript: URIs,
    # onerror= event handlers, and other dangerous HTML constructs.
    # Normal text, @mentions, and code snippets are preserved.
    @field_validator("bio")
    @classmethod
    def clean_bio(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return sanitize_text(v)


class UserMeResponse(BaseModel):
    """
    Full profile response for the currently authenticated user.

    Returned by GET /api/v1/users/me.  This is the only response schema
    that includes sensitive account status fields (``is_suspended``,
    ``is_banned``, ``is_active``) because users need to know their own
    account status.

    **Interview Concept: ``from_attributes=True``**

    This config option tells Pydantic to read data from SQLAlchemy model
    attributes (e.g., ``user.username``) rather than dict keys
    (``user["username"]``).  It's the bridge between your ORM layer and
    your API layer.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: EmailStr  # Only shown to the user themselves
    role: str  # "admin", "moderator", or "member"
    is_verified: bool  # Has the user confirmed their email?
    is_active: bool  # Soft-delete flag
    is_suspended: bool  # Temporarily restricted by a moderator
    is_banned: bool  # Permanently restricted by an admin
    bio: str | None = None
    avatar_url: str | None = None  # Path to uploaded avatar image
    created_at: datetime | None = None  # Account creation timestamp
    last_seen: datetime | None = None  # Updated on every authenticated request


class UserListItemResponse(BaseModel):
    """
    Compact user representation for list views (people page, search results).

    Includes ``email`` because this view is also used in admin contexts.
    Includes ``friendship_status`` so the frontend can show "Add Friend" /
    "Pending" / "Friends" buttons without a separate API call.

    ``is_online`` is computed from ``last_seen`` — if the user was active
    within the last 5 minutes, they're considered online.
    """

    id: int
    username: str
    email: str  # Included for admin visibility
    role: str
    is_verified: bool
    bio: str | None = None
    avatar_url: str | None = None
    friendship_status: str = "none"  # "none", "pending", "accepted"
    created_at: datetime | None = None
    last_seen: datetime | None = None
    is_online: bool = False  # Derived from last_seen threshold


class UserPublicProfileResponse(BaseModel):
    """
    Public profile shown when viewing another user's profile page.

    **Interview Concept: Principle of Least Privilege in API responses**

    Compare this to ``UserMeResponse``: this schema intentionally omits
    ``email``, ``is_active``, ``is_suspended``, and ``is_banned``.  Other
    users don't need to know if someone is suspended — that's between the
    user and the moderation team.  Exposing these fields would leak
    moderation decisions and could be used for social engineering.
    """

    id: int
    username: str
    role: str
    is_verified: bool
    bio: str | None = None
    avatar_url: str | None = None
    friendship_status: str = "none"
    created_at: datetime | None = None
    last_seen: datetime | None = None
    is_online: bool = False


class UserReportRequest(BaseModel):
    """
    Schema for reporting a user (POST /api/v1/users/{id}/report).

    The ``reason`` field requires at least 5 characters to prevent
    low-effort spam reports like "bad" or "x".  Sanitized to prevent
    XSS in the admin dashboard where reports are displayed.
    """

    reason: str = Field(min_length=5, max_length=500)

    # Sanitize report reason — this text is displayed to moderators in the
    # admin dashboard.  Without sanitization, a malicious reporter could
    # inject scripts that execute in the moderator's browser.
    @field_validator("reason")
    @classmethod
    def clean_reason(cls, v: str) -> str:
        return sanitize_text(v)


class UserActionResponse(BaseModel):
    """
    Generic response for user-related actions (friend request sent,
    report submitted, etc.).  Contains a single ``message`` field.
    """

    message: str


class FriendRequestResponse(BaseModel):
    """
    Response for a single friend request, embedding the other user's
    public profile.  The ``status`` field is "pending", "accepted",
    or "declined".
    """

    id: int
    status: str
    user: UserPublicProfileResponse


class FriendRequestListResponse(BaseModel):
    """
    Aggregated friend request data returned by GET /api/v1/users/me/friends.

    Groups requests into three lists so the frontend can render separate
    sections:
    - ``incoming`` — Requests other users have sent to you (you can accept/decline)
    - ``outgoing`` — Requests you've sent to others (awaiting their response)
    - ``friends``  — Accepted friends (full public profiles)
    """

    incoming: list[FriendRequestResponse]
    outgoing: list[FriendRequestResponse]
    friends: list[UserPublicProfileResponse]

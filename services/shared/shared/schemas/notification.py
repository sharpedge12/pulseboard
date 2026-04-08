"""
Notification Schemas
=====================

This module defines Pydantic models for the in-app notification system.

**Interview Concept: Notification architecture**

Notifications in PulseBoard are *stored server-side* in a ``notifications``
database table.  Each notification has:
- A ``notification_type`` (e.g., "reply", "mention", "friend_request",
  "mod_warning") that tells the frontend which icon/color to use.
- A ``payload`` dict with type-specific data (e.g., for a "reply"
  notification: ``{"thread_id": 42, "post_id": 123, "author": "alice"}``).
- An ``is_read`` flag that tracks whether the user has seen it.

This is a **pull-based** notification system — the frontend polls or
receives WebSocket pushes for new notifications and marks them as read
when the user views them.  Compare this to **push-based** systems (email,
SMS) which are fire-and-forget.

The ``payload`` field uses ``dict[str, object]`` (a flexible dict) rather
than a typed schema because different notification types carry different
data.  This is a trade-off: flexibility vs type safety.  An alternative
would be union types or a discriminated union, but the flexible dict is
simpler given the variety of notification types.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class NotificationResponse(BaseModel):
    """
    Response schema for a single notification.

    ``from_attributes=True`` enables direct construction from SQLAlchemy
    ``Notification`` model instances.

    Fields:
    - ``id`` — Unique notification ID (used for mark-as-read endpoint).
    - ``notification_type`` — Category string: "reply", "mention",
      "friend_request", "friend_accepted", "report_resolved",
      "mod_warning", etc.  The frontend maps this to an icon and color.
    - ``title`` — Human-readable summary (e.g., "alice replied to your
      thread").
    - ``payload`` — Type-specific data dict.  The frontend reads fields
      from this to construct the notification link (e.g., navigate to
      the thread where a reply was posted).
    - ``is_read`` — Whether the user has seen this notification.
    - ``created_at`` — When the notification was generated.  Displayed
      as a relative timestamp ("2 hours ago") via ``formatTimeAgo()``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    notification_type: str
    title: str
    payload: dict[str, object]  # Flexible dict — varies by notification type
    is_read: bool
    created_at: datetime


class NotificationListResponse(BaseModel):
    """
    Response for the notification list endpoint (GET /api/v1/notifications).

    Includes both the list of notifications AND the ``unread_count``.
    The ``unread_count`` is used to render the notification badge number
    in the navbar (e.g., a red circle with "5" indicating 5 unread
    notifications).  Returning it here saves a separate API call.
    """

    items: list[NotificationResponse]
    unread_count: int

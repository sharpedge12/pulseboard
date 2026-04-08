"""
Notification Model — In-App Notification System
=================================================

Database table defined here:
    - "notifications" -> Notification (in-app alerts for users)

NOTIFICATION ARCHITECTURE:
    PulseBoard has a polymorphic notification system — a SINGLE notifications
    table handles ALL notification types (reply, mention, friend request,
    moderation warning, etc.). The notification_type column identifies the type,
    and the payload column (JSON) carries type-specific data.

    This is an alternative to having separate tables for each notification type
    (reply_notifications, mention_notifications, etc.). The single-table approach
    is simpler to query ("get all notifications for user X, ordered by time")
    and requires fewer JOINs.

NOTIFICATION TYPES (examples):
    - "reply":          Someone replied to your thread or post
    - "mention":        Someone @mentioned you in a post
    - "friend_request": Someone sent you a friend request
    - "friend_accept":  Someone accepted your friend request
    - "report_status":  Your content report was resolved/dismissed
    - "mod_warning":    A moderator issued a warning to you
    - "thread_update":  A thread you're subscribed to has new activity

    Each type uses the payload JSON to carry relevant details (e.g., the thread
    ID, the post body preview, the friend's username).

REAL-TIME DELIVERY:
    When a notification is created in the database, it's also published to
    Redis pub/sub (channel: notifications:{user_id}). The gateway's WebSocket
    bridge forwards it to the user's connected clients in real-time. The
    database row is the permanent record; the WebSocket push is for instant
    delivery.

WHY JSON FOR payload (not separate columns)?
    Different notification types need different data:
      - A reply notification needs: thread_id, post_id, reply_preview
      - A friend request needs: requester_id, requester_username
      - A mod warning needs: reason, action_type

    Using separate columns for each possible field would create a wide table
    with many NULL columns (a "sparse table" anti-pattern). JSON is the right
    tool here because:
      1. Schema-flexible — each notification type can have its own payload shape.
      2. No ALTER TABLE needed when adding new notification types.
      3. Modern databases (PostgreSQL, SQLite 3.38+) support JSON querying.

    Trade-off: JSON columns can't have database-level constraints (NOT NULL,
    FK, CHECK). The application layer (Pydantic schemas) validates the payload
    shape instead.
"""

from sqlalchemy import Boolean, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.core.database import Base
from shared.models.base import TimestampMixin


class Notification(TimestampMixin, Base):
    """
    An in-app notification for a user (reply, mention, friend request, etc.).

    Database table: "notifications"

    Each notification belongs to exactly one user (the recipient). Notifications
    are never shared — if 5 users need to be notified about the same event,
    5 separate Notification rows are created.

    Relationships:
        - user: The user who receives this notification (many-to-one).
                Accessed via notification.user in Python.

    QUERY PATTERNS:
        - "Unread notifications for user X":
            SELECT * FROM notifications
            WHERE user_id = X AND is_read = FALSE
            ORDER BY created_at DESC;

        - "Mark notification as read":
            UPDATE notifications SET is_read = TRUE WHERE id = N;

        - "Mark all as read":
            UPDATE notifications SET is_read = TRUE
            WHERE user_id = X AND is_read = FALSE;
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)

    # The recipient of this notification. CASCADE: deleting a user deletes
    # all their notifications (they no longer need them).
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # Type discriminator: "reply", "mention", "friend_request", etc.
    # Indexed because we often filter by type (e.g., "show only friend
    # requests" in the notification center).
    # String(50) accommodates descriptive type names like "friend_request_accepted".
    notification_type: Mapped[str] = mapped_column(String(50), index=True)

    # Human-readable notification title shown in the UI, e.g.,
    # "alice replied to your thread" or "bob sent you a friend request".
    title: Mapped[str] = mapped_column(String(255))

    # JSON payload with type-specific data. See module docstring for why JSON
    # is used here instead of separate columns. The dict[str, object] type hint
    # tells Python type checkers this is a dictionary with string keys and
    # arbitrary values.
    payload: Mapped[dict[str, object]] = mapped_column(JSON)

    # Read/unread flag. Defaults to False (unread). The UI shows an unread
    # count badge (the red dot/number on the notification bell) by counting
    # notifications where is_read=False.
    # Using a Boolean flag is simpler than tracking read status in a separate
    # table (which would be needed if multiple users could see the same
    # notification — but our notifications are per-user, so a simple flag works).
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)

    user = relationship("User", back_populates="notifications")

"""
Notification Creation Helper — Shared Across Core and Community Services
=========================================================================

INTERVIEW CONTEXT:
    Notifications are created by MANY different operations across
    MULTIPLE services:

    - **Core service**: friend request received, friend request accepted,
      profile actions
    - **Community service**: reply to your thread, reply to your post,
      @mention in a thread/post/chat, moderation action (warn/suspend/ban),
      report filed against your content, category request reviewed

    If this function lived inside one service, the other service would
    need to make an HTTP call to create a notification — adding latency
    and complexity.  By putting it in the shared layer, both services
    can call it directly within the same database transaction.

WHY ``db.flush()`` INSTEAD OF ``db.commit()``?
    ``flush()`` sends the INSERT to the database and assigns an ID to
    the Notification object, but does NOT commit the transaction.  This
    means the notification is created **atomically** with the operation
    that triggered it:

    - If the caller commits, the notification persists.
    - If the caller rolls back (e.g., a later validation fails), the
      notification is rolled back too — no orphaned notifications.

    This is the standard pattern for "helper functions that write to the
    DB but don't own the transaction."

USED BY:
    - ``mentions.py`` (this package) — creates mention notifications
    - Core service auth routes — email verification, password reset
    - Core service friend routes — friend request notifications
    - Community service forum routes — reply notifications
    - Community service admin routes — moderation action notifications
    - Community service chat routes — message notifications
"""

from sqlalchemy.orm import Session

from shared.models.notification import Notification


def create_notification(
    db: Session,
    user_id: int,
    notification_type: str,
    title: str,
    payload: dict[str, object],
) -> Notification:
    """Create an in-app notification for a user.

    This is a low-level utility — it creates the database row but does
    NOT handle delivery (WebSocket push, email, etc.).  Real-time
    delivery happens via Redis pub/sub events published separately by
    the caller.

    Args:
        db: Active SQLAlchemy session.  The caller is responsible for
            committing the transaction.
        user_id: The ID of the user who should receive this
            notification.
        notification_type: A string categorizing the notification
            (e.g. ``"reply"``, ``"mention"``, ``"friend_request"``,
            ``"mod_action"``, ``"report"``).  The frontend uses this
            to pick the right icon and routing.
        title: Human-readable notification title shown in the UI
            (e.g. ``"alice replied to your thread"``).
        payload: A JSON-serializable dict with context data that the
            frontend needs for navigation.  Examples:

            - Reply: ``{"thread_id": 42, "post_id": 123}``
            - Friend request: ``{"from_user_id": 7, "request_id": 15}``
            - Moderation: ``{"action_type": "warn", "reason": "..."}``

    Returns:
        The newly created ``Notification`` object with a database-
        assigned ``id`` (via ``flush()``).

    Side effects:
        - Inserts a row into the ``notifications`` table (flushed but
          not committed).
        - Does NOT send WebSocket events — the caller handles that.
    """
    notification = Notification(
        user_id=user_id,
        notification_type=notification_type,
        title=title,
        payload=payload,
    )
    db.add(notification)
    # flush() assigns an ID without committing — the caller controls
    # the transaction boundary.
    db.flush()
    return notification

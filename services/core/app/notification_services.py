"""
Notification Service — Business Logic
=======================================

This module contains the service-layer logic for the in-app notification
system.  Notifications in PulseBoard are created as **side-effects** of user
actions (e.g., replying to a thread creates a notification for the thread
author).  This module handles *reading* and *managing* those notifications.

The notification lifecycle:

    1. **Creation**: ``create_notification()`` (in ``shared.services.notifications``)
       is called from various service modules (forum, chat, user, admin) whenever
       an event of interest occurs.  It inserts a ``Notification`` row with
       ``is_read=False``.

    2. **Delivery**: The Gateway's Redis-to-WebSocket bridge pushes a real-time
       event to the user's browser via the ``notifications:{user_id}`` channel.
       The frontend's ``useNotifications`` hook receives this and updates the UI.

    3. **Consumption**: The user views their notifications via ``GET /notifications``
       (this module's ``list_notifications``) and marks them as read via PATCH
       endpoints (``mark_notification_read``, ``mark_all_notifications_read``).

Key interview concepts:

  - **Ownership scoping**: Every query includes ``Notification.user_id == current_user.id``
    to ensure users can only access their own notifications.  This is an
    application-layer security measure (defense-in-depth on top of JWT auth).

  - **Aggregate queries with ``func.count``**: The ``unread_count`` uses
    SQLAlchemy's ``func.count()`` to compute the count in the database rather
    than loading all rows into Python and counting them.  This is O(1) memory
    vs O(n) for large notification lists.

  - **model_validate**: Pydantic v2's ``model_validate()`` creates a schema
    instance directly from an ORM model, using the ``from_attributes=True``
    config (previously called ``orm_mode`` in Pydantic v1).

  - **Re-export pattern**: ``create_notification`` is imported and re-exported
    (``noqa: F401``) so that other modules in the Core service can import it
    from this module as a convenience, keeping imports consistent within the
    service boundary.
"""

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.models.notification import Notification
from shared.models.user import User
from shared.schemas.notification import NotificationListResponse, NotificationResponse
from shared.services.notifications import create_notification  # noqa: F401 — re-export


def list_notifications(db: Session, current_user: User) -> NotificationListResponse:
    """Fetch all notifications for a user, ordered newest-first.

    This function performs two queries:
      1. Fetch all notification rows for the user (ordered by ``created_at DESC``).
      2. Count the number of unread notifications (``is_read=False``).

    The count query is separate (not computed from the fetched list) because
    in a future paginated implementation, the fetched list would only contain
    one page of results, but the unread count should reflect ALL notifications.

    Args:
        db: SQLAlchemy session.
        current_user: The authenticated user whose notifications to fetch.

    Returns:
        NotificationListResponse containing:
          - ``items``: List of NotificationResponse objects (newest first).
          - ``unread_count``: Total number of unread notifications (integer).
    """
    # Query 1: Fetch all notifications for this user, newest first.
    notifications = (
        db.execute(
            select(Notification)
            .where(Notification.user_id == current_user.id)
            .order_by(Notification.created_at.desc())
        )
        .scalars()
        .all()
    )

    # Query 2: Count unread notifications using a SQL COUNT aggregate.
    # This is more efficient than ``len([n for n in notifications if not n.is_read])``
    # because the counting happens in the database, not in Python.
    unread_count = db.execute(
        select(func.count(Notification.id)).where(
            Notification.user_id == current_user.id,
            Notification.is_read.is_(False),
        )
    ).scalar_one()

    return NotificationListResponse(
        # ``model_validate`` converts each SQLAlchemy ORM instance into a
        # Pydantic schema using the ``from_attributes=True`` config.
        items=[NotificationResponse.model_validate(item) for item in notifications],
        unread_count=unread_count,
    )


def mark_notification_read(
    db: Session, notification_id: int, current_user: User
) -> NotificationResponse:
    """Mark a single notification as read.

    The query filters by BOTH ``notification_id`` AND ``user_id`` to enforce
    ownership — a user cannot mark someone else's notification as read, even
    if they guess the ID.  This is a critical security pattern known as
    **row-level authorization**.

    Args:
        db: SQLAlchemy session.
        notification_id: The database ID of the notification to mark.
        current_user: The authenticated user (must own the notification).

    Returns:
        NotificationResponse: The updated notification with ``is_read=True``.

    Raises:
        HTTPException 404: Notification not found or does not belong to
            the current user (same error to prevent information leakage
            about other users' notification IDs).
    """
    # Combined lookup + ownership check in a single query.
    notification = db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == current_user.id,  # Ownership enforcement.
        )
    ).scalar_one_or_none()

    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found."
        )

    notification.is_read = True
    db.commit()
    db.refresh(notification)  # Reload to reflect the committed state.
    return NotificationResponse.model_validate(notification)


def mark_all_notifications_read(
    db: Session, current_user: User
) -> NotificationListResponse:
    """Mark ALL of a user's notifications as read and return the updated list.

    This iterates over all notifications in Python and sets ``is_read=True``
    on each one.  For a large number of notifications, a bulk UPDATE query
    would be more efficient:

        db.execute(
            update(Notification)
            .where(Notification.user_id == current_user.id)
            .values(is_read=True)
        )

    The current approach is simpler and works well for typical notification
    volumes (tens to low hundreds per user).

    After marking all as read, it delegates to ``list_notifications`` to
    build and return the full list (now with ``unread_count=0``), saving
    the frontend a separate API call.

    Args:
        db: SQLAlchemy session.
        current_user: The authenticated user.

    Returns:
        NotificationListResponse: Full notification list with all items
        marked as read.
    """
    # Load all notifications for the user (no status filter — marks ALL).
    notifications = (
        db.execute(select(Notification).where(Notification.user_id == current_user.id))
        .scalars()
        .all()
    )

    # Set is_read=True on each notification individually.
    for notification in notifications:
        notification.is_read = True

    db.commit()

    # Return the full list (reuses list_notifications for consistent formatting).
    return list_notifications(db, current_user)

"""
Notification Routes — Core Service
====================================

This module defines the HTTP endpoints for the in-app notification system.
Notifications are created as side-effects of other actions (new reply, friend
request, moderation action, etc.) and are consumed by the frontend's
``NotificationCenter`` component via these endpoints.

Endpoints:

    - ``GET  /``                    — List all notifications for the current user.
    - ``PATCH /{notification_id}/read`` — Mark a single notification as read.
    - ``PATCH /read-all``           — Mark ALL notifications as read.

Key interview concepts:

  - **PATCH (not PUT)**: We use PATCH because we're partially updating the
    notification (only the ``is_read`` field), not replacing the entire resource.

  - **Ownership enforcement**: Every query is scoped to
    ``Notification.user_id == current_user.id``, ensuring a user can only see
    and modify their own notifications.  This is a critical security pattern —
    without it, any authenticated user could read or dismiss another user's
    notifications by guessing IDs.

  - **Thin controller pattern**: Routes delegate immediately to service functions.
    The route functions contain zero business logic — they exist only to declare
    the HTTP method, path, response model, and dependencies.

  - **Real-time complement**: These REST endpoints are the *pull* side of
    notifications.  The *push* side is handled by WebSocket channels in the
    Gateway service (``notifications:*`` Redis pub/sub pattern), which deliver
    notifications instantly without polling.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from shared.core.database import get_db
from shared.core.auth_helpers import get_current_user
from shared.models.user import User
from shared.schemas.notification import NotificationListResponse, NotificationResponse
from app.notification_services import (
    list_notifications as list_notifications_service,
    mark_all_notifications_read,
    mark_notification_read,
)

router = APIRouter()


@router.get("", response_model=NotificationListResponse)
def list_notifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NotificationListResponse:
    """List all notifications for the authenticated user.

    Returns notifications in reverse chronological order (newest first)
    along with an ``unread_count`` integer.  The frontend uses the unread
    count to display a badge on the notification bell icon.

    The response is NOT paginated — it returns all notifications.  For a
    production system with heavy usage, you would add ``page``/``page_size``
    query parameters and limit the result set.

    Args:
        db: SQLAlchemy session (injected by FastAPI's DI).
        current_user: The authenticated user (from JWT via ``get_current_user``).

    Returns:
        NotificationListResponse: ``items`` list + ``unread_count``.
    """
    return list_notifications_service(db, current_user)


@router.patch("/{notification_id}/read", response_model=NotificationResponse)
def read_notification(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NotificationResponse:
    """Mark a single notification as read.

    Called when the user clicks on a notification in the dropdown.  The
    service layer verifies that the notification belongs to the current
    user (ownership check) before updating it.

    Args:
        notification_id: The database ID of the notification to mark as read.
        db: SQLAlchemy session.
        current_user: The authenticated user.

    Returns:
        NotificationResponse: The updated notification with ``is_read=True``.

    Raises:
        HTTPException 404: Notification not found or does not belong to
            the current user.
    """
    return mark_notification_read(db, notification_id, current_user)


@router.patch("/read-all", response_model=NotificationListResponse)
def read_all_notifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NotificationListResponse:
    """Mark ALL notifications as read for the current user.

    This is the "Mark all as read" button in the notification dropdown.
    After marking everything as read, it returns the full notification
    list (with ``unread_count=0``) so the frontend can update its state
    in a single round-trip.

    Args:
        db: SQLAlchemy session.
        current_user: The authenticated user.

    Returns:
        NotificationListResponse: Full notification list with all items
        marked as read and ``unread_count=0``.
    """
    return mark_all_notifications_read(db, current_user)

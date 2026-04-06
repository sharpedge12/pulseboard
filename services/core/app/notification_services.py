"""Notification service business logic."""

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.models.notification import Notification
from shared.models.user import User
from shared.schemas.notification import NotificationListResponse, NotificationResponse
from shared.services.notifications import create_notification  # noqa: F401 — re-export


def list_notifications(db: Session, current_user: User) -> NotificationListResponse:
    notifications = (
        db.execute(
            select(Notification)
            .where(Notification.user_id == current_user.id)
            .order_by(Notification.created_at.desc())
        )
        .scalars()
        .all()
    )
    unread_count = db.execute(
        select(func.count(Notification.id)).where(
            Notification.user_id == current_user.id,
            Notification.is_read.is_(False),
        )
    ).scalar_one()

    return NotificationListResponse(
        items=[NotificationResponse.model_validate(item) for item in notifications],
        unread_count=unread_count,
    )


def mark_notification_read(
    db: Session, notification_id: int, current_user: User
) -> NotificationResponse:
    notification = db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == current_user.id,
        )
    ).scalar_one_or_none()
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found."
        )

    notification.is_read = True
    db.commit()
    db.refresh(notification)
    return NotificationResponse.model_validate(notification)


def mark_all_notifications_read(
    db: Session, current_user: User
) -> NotificationListResponse:
    notifications = (
        db.execute(select(Notification).where(Notification.user_id == current_user.id))
        .scalars()
        .all()
    )
    for notification in notifications:
        notification.is_read = True
    db.commit()
    return list_notifications(db, current_user)

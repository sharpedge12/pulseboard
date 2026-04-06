"""Notification creation helper — used by user, forum, chat, moderation services."""

from sqlalchemy.orm import Session

from shared.models.notification import Notification


def create_notification(
    db: Session,
    user_id: int,
    notification_type: str,
    title: str,
    payload: dict[str, object],
) -> Notification:
    notification = Notification(
        user_id=user_id,
        notification_type=notification_type,
        title=title,
        payload=payload,
    )
    db.add(notification)
    db.flush()
    return notification

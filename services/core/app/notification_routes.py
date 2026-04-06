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
    return list_notifications_service(db, current_user)


@router.patch("/{notification_id}/read", response_model=NotificationResponse)
def read_notification(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NotificationResponse:
    return mark_notification_read(db, notification_id, current_user)


@router.patch("/read-all", response_model=NotificationListResponse)
def read_all_notifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NotificationListResponse:
    return mark_all_notifications_read(db, current_user)

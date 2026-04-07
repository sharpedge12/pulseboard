from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.core.database import get_db
from shared.core.auth_helpers import get_current_user
from shared.models.user import User, UserRole
from shared.models.vote import ContentReport
from shared.services import audit as audit_service
from shared.schemas.user import (
    FriendRequestListResponse,
    UserActionResponse,
    UserListItemResponse,
    UserMeResponse,
    UserPublicProfileResponse,
    UserReportRequest,
    UserUpdateRequest,
)
from app.user_services import (
    _is_online,
    list_friendships,
    respond_to_friend_request,
    send_friend_request as send_friend_request_service,
    serialize_public_user,
    update_current_user,
    upload_avatar,
)
from shared.services.notifications import create_notification

router = APIRouter()


@router.get("/me", response_model=UserMeResponse)
def read_current_user(current_user: User = Depends(get_current_user)) -> UserMeResponse:
    return UserMeResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        role=current_user.role.value,
        is_verified=current_user.is_verified,
        is_active=current_user.is_active,
        is_suspended=current_user.is_suspended,
        is_banned=current_user.is_banned,
        bio=current_user.bio,
        avatar_url=current_user.avatar_url,
        created_at=current_user.created_at,
        last_seen=current_user.last_seen,
    )


@router.patch("/me", response_model=UserMeResponse)
def patch_current_user(
    payload: UserUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserMeResponse:
    return update_current_user(db, current_user, payload)


@router.post("/me/avatar", response_model=UserMeResponse)
def upload_current_user_avatar(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserMeResponse:
    return upload_avatar(db, current_user, file)


@router.get("", response_model=list[UserListItemResponse])
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[UserListItemResponse]:
    users = db.query(User).order_by(User.username.asc()).all()
    return [
        UserListItemResponse(
            id=user.id,
            username=user.username,
            email=user.email,
            role=user.role.value,
            is_verified=user.is_verified,
            bio=user.bio,
            avatar_url=user.avatar_url,
            friendship_status=serialize_public_user(
                db, user, current_user
            ).friendship_status,
            created_at=user.created_at,
            last_seen=user.last_seen,
            is_online=_is_online(user),
        )
        for user in users
        if user.id != current_user.id
    ]


@router.get("/friends", response_model=FriendRequestListResponse)
def read_friendships(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FriendRequestListResponse:
    return list_friendships(db, current_user)


@router.get("/search", response_model=list[UserPublicProfileResponse])
def search_users(
    q: str = Query(min_length=1, max_length=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[UserPublicProfileResponse]:
    """Search users by username (case-insensitive partial match)."""
    pattern = f"%{q}%"
    users = (
        db.execute(
            select(User)
            .where(User.username.ilike(pattern))
            .order_by(User.username.asc())
            .limit(30)
        )
        .scalars()
        .all()
    )
    return [
        UserPublicProfileResponse(
            **serialize_public_user(db, user, current_user).model_dump()
        )
        for user in users
        if user.id != current_user.id
    ]


@router.post("/friends/{request_id}/accept", response_model=UserActionResponse)
def accept_friend_request(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserActionResponse:
    return UserActionResponse(
        message=respond_to_friend_request(db, request_id, current_user, True)
    )


@router.post("/friends/{request_id}/decline", response_model=UserActionResponse)
def decline_friend_request(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserActionResponse:
    return UserActionResponse(
        message=respond_to_friend_request(db, request_id, current_user, False)
    )


@router.post("/{user_id}/report", response_model=UserActionResponse)
def report_user(
    user_id: int,
    payload: UserReportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserActionResponse:
    reported_user = db.execute(
        select(User).where(User.id == user_id)
    ).scalar_one_or_none()
    if not reported_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )
    if reported_user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot report yourself.",
        )

    # Check for duplicate report
    existing = db.execute(
        select(ContentReport).where(
            ContentReport.reporter_id == current_user.id,
            ContentReport.entity_type == "user",
            ContentReport.entity_id == reported_user.id,
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already reported this user.",
        )

    # Create a ContentReport so it appears in the admin Reports panel
    report = ContentReport(
        reporter_id=current_user.id,
        entity_type="user",
        entity_id=reported_user.id,
        reason=payload.reason,
    )
    db.add(report)
    db.flush()

    audit_service.record(
        db,
        actor_id=current_user.id,
        action=audit_service.REPORT_CREATE,
        entity_type="report",
        entity_id=report.id,
        details={
            "entity_type": "user",
            "entity_id": reported_user.id,
            "reason": payload.reason,
        },
    )

    # Notify staff
    staff_users = (
        db.execute(
            select(User).where(User.role.in_([UserRole.ADMIN, UserRole.MODERATOR]))
        )
        .scalars()
        .all()
    )
    for staff_user in staff_users:
        if staff_user.id == current_user.id:
            continue
        create_notification(
            db,
            user_id=staff_user.id,
            notification_type="user_report",
            title=f"{current_user.username} reported {reported_user.username}",
            payload={
                "reported_user_id": reported_user.id,
                "reported_username": reported_user.username,
                "reporter_user_id": current_user.id,
                "reporter_username": current_user.username,
                "reason": payload.reason,
            },
        )
    db.commit()
    return UserActionResponse(message=f"Report submitted for {reported_user.username}.")


@router.post("/{user_id}/friend", response_model=UserActionResponse)
def create_friend_request(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserActionResponse:
    target_user = db.execute(
        select(User).where(User.id == user_id)
    ).scalar_one_or_none()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )
    if target_user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot add yourself.",
        )

    return UserActionResponse(
        message=send_friend_request_service(db, current_user, target_user)
    )


@router.get("/lookup/{username}", response_model=UserPublicProfileResponse)
def lookup_user_profile(
    username: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserPublicProfileResponse:
    user = db.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    return UserPublicProfileResponse(
        **serialize_public_user(db, user, current_user).model_dump()
    )


@router.get("/{user_id}", response_model=UserPublicProfileResponse)
def read_user_profile(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserPublicProfileResponse:
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    return UserPublicProfileResponse(
        **serialize_public_user(db, user, current_user).model_dump()
    )

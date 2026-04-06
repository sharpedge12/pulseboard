"""User service business logic — profile, friends, uploads."""

from datetime import datetime, timezone

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from shared.models.attachment import Attachment
from shared.models.friendship import FriendRequest, FriendRequestStatus
from shared.models.user import User
from shared.schemas.user import (
    FriendRequestListResponse,
    FriendRequestResponse,
    UserMeResponse,
    UserPublicProfileResponse,
    UserUpdateRequest,
)
from shared.schemas.upload import UploadResponse
from shared.services import audit as audit_service
from shared.services.notifications import create_notification
from shared.services.storage import remove_upload_file, save_upload_file


# ---------------------------------------------------------------------------
# Profile helpers
# ---------------------------------------------------------------------------


def _serialize_user(user: User) -> UserMeResponse:
    return UserMeResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role.value,
        is_verified=user.is_verified,
        is_active=user.is_active,
        is_suspended=user.is_suspended,
        is_banned=user.is_banned,
        bio=user.bio,
        avatar_url=user.avatar_url,
        created_at=user.created_at,
        last_seen=user.last_seen,
    )


def update_current_user(
    db: Session,
    current_user: User,
    payload: UserUpdateRequest,
) -> UserMeResponse:
    changes: dict[str, object] = {}

    if payload.username and payload.username != current_user.username:
        existing_user = db.execute(
            select(User).where(User.username == payload.username)
        ).scalar_one_or_none()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username is already taken.",
            )
        changes["username"] = {
            "old": current_user.username,
            "new": payload.username,
        }
        current_user.username = payload.username

    if payload.bio is not None:
        changes["bio_updated"] = True
        current_user.bio = payload.bio

    if changes:
        audit_service.record(
            db,
            actor_id=current_user.id,
            action=audit_service.USER_PROFILE_UPDATE,
            entity_type="user",
            entity_id=current_user.id,
            details=changes,
        )

    db.commit()
    db.refresh(current_user)
    return _serialize_user(current_user)


def upload_avatar(
    db: Session,
    current_user: User,
    file: UploadFile,
) -> UserMeResponse:
    upload_data = save_upload_file(file, "avatars")
    if current_user.avatar_url:
        previous_path = current_user.avatar_url.removeprefix("/uploads/")
        remove_upload_file(previous_path)

    current_user.avatar_url = upload_data["public_url"]
    db.add(
        Attachment(
            uploader_id=current_user.id,
            linked_entity_type="avatar",
            linked_entity_id=current_user.id,
            file_name=str(upload_data["file_name"]),
            file_type=str(upload_data["file_type"]),
            file_size=int(upload_data["file_size"]),
            storage_path=str(upload_data["storage_path"]),
        )
    )
    audit_service.record(
        db,
        actor_id=current_user.id,
        action=audit_service.USER_AVATAR_UPLOAD,
        entity_type="user",
        entity_id=current_user.id,
        details={
            "file_name": str(upload_data["file_name"]),
            "file_type": str(upload_data["file_type"]),
        },
    )
    db.commit()
    db.refresh(current_user)
    return _serialize_user(current_user)


def create_generic_upload(
    db: Session,
    current_user: User,
    file: UploadFile,
    linked_entity_type: str,
    linked_entity_id: int,
) -> UploadResponse:
    upload_data = save_upload_file(file, linked_entity_type)
    attachment = Attachment(
        uploader_id=current_user.id,
        linked_entity_type=linked_entity_type,
        linked_entity_id=linked_entity_id,
        file_name=str(upload_data["file_name"]),
        file_type=str(upload_data["file_type"]),
        file_size=int(upload_data["file_size"]),
        storage_path=str(upload_data["storage_path"]),
    )
    db.add(attachment)
    db.commit()
    db.refresh(attachment)
    return UploadResponse(
        id=attachment.id,
        file_name=attachment.file_name,
        file_type=attachment.file_type,
        file_size=attachment.file_size,
        storage_path=attachment.storage_path,
        public_url=str(upload_data["public_url"]),
        linked_entity_type=attachment.linked_entity_type,
        linked_entity_id=attachment.linked_entity_id,
        created_at=attachment.created_at,
    )


# ---------------------------------------------------------------------------
# Friends helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_friendship_status(db: Session, current_user: User, target_user: User) -> str:
    if current_user.id == target_user.id:
        return "self"

    request = db.execute(
        select(FriendRequest).where(
            or_(
                and_(
                    FriendRequest.requester_id == current_user.id,
                    FriendRequest.recipient_id == target_user.id,
                ),
                and_(
                    FriendRequest.requester_id == target_user.id,
                    FriendRequest.recipient_id == current_user.id,
                ),
            )
        )
    ).scalar_one_or_none()
    if not request:
        return "none"
    if request.status == FriendRequestStatus.ACCEPTED:
        return "friends"
    if request.status == FriendRequestStatus.PENDING:
        return (
            "incoming_pending"
            if request.recipient_id == current_user.id
            else "outgoing_pending"
        )
    return request.status.value


def _is_online(user: User) -> bool:
    """Check if user was seen within the last 5 minutes."""
    if not user.last_seen:
        return False
    return (datetime.now(timezone.utc) - user.last_seen).total_seconds() < 300


def serialize_public_user(
    db: Session, user: User, current_user: User | None = None
) -> UserPublicProfileResponse:
    friendship_status = "none"
    if current_user is not None:
        friendship_status = get_friendship_status(db, current_user, user)

    return UserPublicProfileResponse(
        id=user.id,
        username=user.username,
        role=user.role.value,
        is_verified=user.is_verified,
        bio=user.bio,
        avatar_url=user.avatar_url,
        friendship_status=friendship_status,
        created_at=user.created_at,
        last_seen=user.last_seen,
        is_online=_is_online(user),
    )


def send_friend_request(db: Session, current_user: User, target_user: User) -> str:
    if target_user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot add yourself.",
        )

    existing = db.execute(
        select(FriendRequest).where(
            or_(
                and_(
                    FriendRequest.requester_id == current_user.id,
                    FriendRequest.recipient_id == target_user.id,
                ),
                and_(
                    FriendRequest.requester_id == target_user.id,
                    FriendRequest.recipient_id == current_user.id,
                ),
            )
        )
    ).scalar_one_or_none()

    if existing:
        if existing.status == FriendRequestStatus.ACCEPTED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You are already friends.",
            )
        if existing.status == FriendRequestStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A friend request already exists.",
            )
        existing.requester_id = current_user.id
        existing.recipient_id = target_user.id
        existing.status = FriendRequestStatus.PENDING
        existing.responded_at = None
    else:
        db.add(
            FriendRequest(
                requester_id=current_user.id,
                recipient_id=target_user.id,
                status=FriendRequestStatus.PENDING,
            )
        )

    create_notification(
        db,
        user_id=target_user.id,
        notification_type="friend_request",
        title=f"{current_user.username} sent you a friend request",
        payload={
            "from_user_id": current_user.id,
            "from_username": current_user.username,
        },
    )
    audit_service.record(
        db,
        actor_id=current_user.id,
        action=audit_service.FRIEND_REQUEST_SEND,
        entity_type="friend_request",
        entity_id=target_user.id,
        details={
            "target_username": target_user.username,
        },
    )
    db.commit()
    return f"Friend request sent to {target_user.username}."


def respond_to_friend_request(
    db: Session,
    request_id: int,
    current_user: User,
    accept: bool,
) -> str:
    friend_request = db.execute(
        select(FriendRequest).where(FriendRequest.id == request_id)
    ).scalar_one_or_none()
    if not friend_request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Friend request not found.",
        )
    if friend_request.recipient_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot respond to this friend request.",
        )
    if friend_request.status != FriendRequestStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This friend request has already been handled.",
        )

    friend_request.status = (
        FriendRequestStatus.ACCEPTED if accept else FriendRequestStatus.DECLINED
    )
    friend_request.responded_at = _utcnow()
    audit_service.record(
        db,
        actor_id=current_user.id,
        action=(
            audit_service.FRIEND_REQUEST_ACCEPT
            if accept
            else audit_service.FRIEND_REQUEST_DECLINE
        ),
        entity_type="friend_request",
        entity_id=friend_request.id,
        details={
            "requester_id": friend_request.requester_id,
        },
    )
    create_notification(
        db,
        user_id=friend_request.requester_id,
        notification_type="friend_request_update",
        title=f"{current_user.username} {'accepted' if accept else 'declined'} your friend request",
        payload={
            "friend_request_id": friend_request.id,
            "accepted": accept,
            "user_id": current_user.id,
            "username": current_user.username,
        },
    )
    db.commit()
    return "Friend request accepted." if accept else "Friend request declined."


def list_friendships(db: Session, current_user: User) -> FriendRequestListResponse:
    incoming_requests = (
        db.execute(
            select(FriendRequest)
            .where(
                FriendRequest.recipient_id == current_user.id,
                FriendRequest.status == FriendRequestStatus.PENDING,
            )
            .order_by(FriendRequest.created_at.desc())
        )
        .scalars()
        .all()
    )
    outgoing_requests = (
        db.execute(
            select(FriendRequest)
            .where(
                FriendRequest.requester_id == current_user.id,
                FriendRequest.status == FriendRequestStatus.PENDING,
            )
            .order_by(FriendRequest.created_at.desc())
        )
        .scalars()
        .all()
    )
    accepted_requests = (
        db.execute(
            select(FriendRequest).where(
                FriendRequest.status == FriendRequestStatus.ACCEPTED,
                or_(
                    FriendRequest.requester_id == current_user.id,
                    FriendRequest.recipient_id == current_user.id,
                ),
            )
        )
        .scalars()
        .all()
    )

    def _request_response(
        friend_request: FriendRequest, counterpart: User
    ) -> FriendRequestResponse:
        return FriendRequestResponse(
            id=friend_request.id,
            status=friend_request.status.value,
            user=serialize_public_user(db, counterpart, current_user),
        )

    incoming = []
    for request in incoming_requests:
        counterpart = db.execute(
            select(User).where(User.id == request.requester_id)
        ).scalar_one()
        incoming.append(_request_response(request, counterpart))

    outgoing = []
    for request in outgoing_requests:
        counterpart = db.execute(
            select(User).where(User.id == request.recipient_id)
        ).scalar_one()
        outgoing.append(_request_response(request, counterpart))

    friends = []
    for request in accepted_requests:
        counterpart_id = (
            request.recipient_id
            if request.requester_id == current_user.id
            else request.requester_id
        )
        counterpart = db.execute(
            select(User).where(User.id == counterpart_id)
        ).scalar_one()
        friends.append(serialize_public_user(db, counterpart, current_user))

    return FriendRequestListResponse(
        incoming=incoming, outgoing=outgoing, friends=friends
    )

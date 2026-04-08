"""
User Service — Business Logic for Profiles, Friends, and Uploads
=================================================================

This module contains the **service layer** for user-related operations.  While
``user_routes.py`` handles HTTP concerns (status codes, request parsing), this
module handles the actual business logic:

  - **Profile management**: Serialize user data, update username/bio, upload avatars.
  - **Friendship lifecycle**: Send, accept, decline friend requests; list friendships.
  - **Online status**: Determine if a user is "online" based on ``last_seen``.
  - **File uploads**: Save files to disk and create ``Attachment`` records.

Key interview concepts:

  - **Serialization**: Converting an ORM model (SQLAlchemy ``User``) into a Pydantic
    response schema (``UserMeResponse``, ``UserPublicProfileResponse``).  This controls
    which fields are exposed to the API consumer and prevents leaking internal data.

  - **Friendship state machine**: A friend request goes through states:
    ``PENDING -> ACCEPTED`` or ``PENDING -> DECLINED``.  If declined, the requester
    can re-send (the existing record is recycled).  The state is checked from BOTH
    directions (A->B and B->A) since friendship is bidirectional.

  - **File upload flow**: ``save_upload_file`` handles the actual I/O (validation,
    disk write), returning metadata.  This module creates the ``Attachment`` ORM
    record and updates the user's ``avatar_url``.  Old avatars are deleted to
    prevent storage bloat.

  - **Audit logging**: Every state-changing action (profile update, avatar upload,
    friend request send/accept/decline) is recorded for compliance and debugging.

  - **Notification side-effects**: Friend request actions trigger in-app
    notifications to the other party via ``create_notification``.
"""

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
    """Convert a SQLAlchemy User model into a UserMeResponse Pydantic schema.

    This is the **private** serializer used internally after profile mutations
    (update, avatar upload).  It includes sensitive fields like ``email``,
    ``is_banned``, ``is_suspended`` that are only appropriate for the user
    themselves (not for public-facing endpoints).

    Why a separate function?
      - Avoids repeating the field mapping in every endpoint.
      - Provides a single place to add/remove fields if the schema changes.
      - Keeps the service functions focused on business logic, not serialization.

    Args:
        user: The SQLAlchemy User model instance (must be attached to a session
              so lazy-loaded attributes are available).

    Returns:
        UserMeResponse: Pydantic model safe to return as an HTTP response body.
    """
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
    """Update the authenticated user's profile (username and/or bio).

    This function implements a **partial update** pattern (PATCH semantics):
    only fields that are present and different from the current values are
    changed.  A ``changes`` dict tracks what was modified for audit logging.

    Username uniqueness is enforced at the application layer (not just the DB
    unique constraint) so we can return a friendly error message instead of
    a raw IntegrityError.

    Args:
        db: SQLAlchemy session.
        current_user: The authenticated User model (will be mutated in-place).
        payload: The update request containing optional ``username`` and ``bio``.

    Returns:
        UserMeResponse: The updated profile.

    Raises:
        HTTPException 400: If the requested username is already taken by
            another user.
    """
    # Track changes for audit logging (records what was changed and the
    # old/new values for forensic review).
    changes: dict[str, object] = {}

    if payload.username and payload.username != current_user.username:
        # Check uniqueness before updating — gives a clean error message
        # instead of a database IntegrityError.
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

    # ``bio`` uses ``is not None`` (not truthiness) so that an empty string
    # can be used to clear the bio.
    if payload.bio is not None:
        changes["bio_updated"] = True
        current_user.bio = payload.bio

    # Only create an audit log entry if something actually changed.
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
    db.refresh(current_user)  # Re-read from DB to pick up any server-side defaults.
    return _serialize_user(current_user)


def upload_avatar(
    db: Session,
    current_user: User,
    file: UploadFile,
) -> UserMeResponse:
    """Handle avatar image upload: save file, update user record, clean up old avatar.

    The upload flow:
      1. ``save_upload_file`` validates the file (MIME type, magic bytes,
         extension, size) and writes it to disk under ``uploads/avatars/``.
         Returns metadata including the public URL path.
      2. If the user already has an avatar, delete the old file from disk
         to prevent storage bloat (avatars are replaced, not accumulated).
      3. Update ``user.avatar_url`` to the new public URL.
      4. Create an ``Attachment`` record for tracking all uploaded files.
      5. Record an audit log entry for the upload event.

    Args:
        db: SQLAlchemy session.
        current_user: The authenticated user uploading the avatar.
        file: The uploaded file from the HTTP multipart form.

    Returns:
        UserMeResponse: The updated profile with the new ``avatar_url``.
    """
    # Step 1: Save the file to disk and get metadata (path, MIME type, size).
    upload_data = save_upload_file(file, "avatars")

    # Step 2: Delete the old avatar file if one exists.
    # ``removeprefix`` strips the ``/uploads/`` URL prefix to get the
    # relative filesystem path for deletion.
    if current_user.avatar_url:
        previous_path = current_user.avatar_url.removeprefix("/uploads/")
        remove_upload_file(previous_path)

    # Step 3: Update the user's avatar URL to point to the new file.
    current_user.avatar_url = upload_data["public_url"]

    # Step 4: Create an Attachment record for file tracking/management.
    # This allows admins to audit all uploads and link them to entities.
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

    # Step 5: Audit log for compliance.
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
    """Save an uploaded file and create an Attachment record for any entity type.

    Unlike ``upload_avatar`` which is specific to user avatars, this function
    handles generic file uploads that can be linked to threads, posts, messages,
    or draft entities.  It is called by the ``POST /api/v1/uploads`` endpoint.

    The ``linked_entity_type`` and ``linked_entity_id`` are stored in the
    Attachment record to associate the file with the content it belongs to
    (e.g., ``entity_type="post"``, ``entity_id=42``).

    Args:
        db: SQLAlchemy session.
        current_user: The authenticated user performing the upload.
        file: The uploaded file from the HTTP multipart form.
        linked_entity_type: The type of entity this file is attached to
            (e.g., ``"thread"``, ``"post"``, ``"message"``, ``"draft"``).
        linked_entity_id: The database ID of the entity this file belongs to.

    Returns:
        UploadResponse: Metadata about the uploaded file including its public URL.
    """
    # save_upload_file handles validation (MIME, magic bytes, extension, size)
    # and writes the file to disk under ``uploads/{linked_entity_type}/``.
    upload_data = save_upload_file(file, linked_entity_type)

    # Create the Attachment ORM record linking the file to its parent entity.
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
    db.refresh(attachment)  # Reload to get the DB-assigned ``id`` and ``created_at``.

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
# Online status helper
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime.

    Centralised here so all timestamp comparisons use the same clock,
    and to ensure we never create naive datetimes that would fail
    comparisons against timezone-aware values from the database.
    """
    return datetime.now(timezone.utc)


def _is_online(user: User) -> bool:
    """Determine if a user is currently "online" based on their last activity.

    A user is considered online if their ``last_seen`` timestamp is within
    the last 5 minutes (300 seconds).  The ``last_seen`` field is updated
    on every authenticated API request by the ``get_current_user`` dependency.

    This is a simple polling-based approach.  A more sophisticated system
    might use WebSocket heartbeats or Redis presence tracking.

    Args:
        user: The User model to check.

    Returns:
        True if the user was active within the last 5 minutes, False otherwise.
    """
    if not user.last_seen:
        return False
    return (datetime.now(timezone.utc) - user.last_seen).total_seconds() < 300


# ---------------------------------------------------------------------------
# Friends helpers
# ---------------------------------------------------------------------------


def get_friendship_status(db: Session, current_user: User, target_user: User) -> str:
    """Determine the friendship status between two users.

    The friendship relationship is stored as a single ``FriendRequest`` row
    between two users (not duplicated in both directions).  This function
    checks BOTH directions (A->B and B->A) in a single query using an OR
    clause, which is a common pattern for bidirectional relationships.

    Possible return values:
      - ``"self"``: The user is looking at their own profile.
      - ``"none"``: No friend request exists between the users.
      - ``"friends"``: The request was accepted (bidirectional friendship).
      - ``"incoming_pending"``: The target sent a request TO current_user.
      - ``"outgoing_pending"``: The current_user sent a request TO target.
      - ``"declined"``: The most recent request was declined.

    Args:
        db: SQLAlchemy session.
        current_user: The authenticated user (the "viewer").
        target_user: The user being viewed.

    Returns:
        A string representing the friendship status.
    """
    # Self-check: a user viewing their own profile.
    if current_user.id == target_user.id:
        return "self"

    # Query for a friend request in EITHER direction between the two users.
    # This OR pattern is essential because friendship is bidirectional:
    # if Alice sent Bob a request, it shows as "outgoing" for Alice and
    # "incoming" for Bob, but it's the SAME database row.
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
        # Distinguish between incoming and outgoing based on who is the
        # recipient (the person who needs to respond).
        return (
            "incoming_pending"
            if request.recipient_id == current_user.id
            else "outgoing_pending"
        )
    # Fall through for DECLINED or any other status.
    return request.status.value


def serialize_public_user(
    db: Session, user: User, current_user: User | None = None
) -> UserPublicProfileResponse:
    """Serialize a User model into a public profile response.

    Unlike ``_serialize_user`` (which includes sensitive fields for /me),
    this function returns only **publicly safe** information: no email,
    no account status flags.  It also computes the friendship status
    between the viewer and the target user.

    This is used for:
      - User search results (``@mention`` autocomplete).
      - Public profile pages (``/user/{username}``).
      - Friend list entries.
      - User listings on the "People" page.

    Args:
        db: SQLAlchemy session (needed for friendship status query).
        user: The user whose profile is being serialized.
        current_user: The viewer (optional; if None, friendship_status
            defaults to ``"none"``).

    Returns:
        UserPublicProfileResponse with public fields and friendship status.
    """
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
    """Send a friend request from current_user to target_user.

    This function implements a **state machine** for friend requests:

      1. No existing request -> Create a new PENDING request.
      2. Existing ACCEPTED  -> Reject (already friends).
      3. Existing PENDING   -> Reject (request already exists).
      4. Existing DECLINED  -> Recycle the row: update requester/recipient
         and reset to PENDING.  This allows re-sending after a decline
         without creating duplicate rows.

    The "recycle" pattern (case 4) is important: instead of deleting the
    declined request and creating a new one, we update the existing row.
    This preserves the audit trail and avoids orphaned foreign key references.

    Side effects:
      - Creates an in-app notification for the target user.
      - Records an audit log entry for the friend request.

    Args:
        db: SQLAlchemy session.
        current_user: The user sending the request.
        target_user: The user receiving the request.

    Returns:
        A success message string.

    Raises:
        HTTPException 400: Self-friending, already friends, or request
            already pending.
    """
    # Prevent self-friending (also checked in the route, but defense-in-depth).
    if target_user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot add yourself.",
        )

    # Check for an existing request in EITHER direction (bidirectional lookup).
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
        # DECLINED case: recycle the existing row by updating the requester,
        # recipient, and status.  This resets the request to PENDING while
        # keeping the same database row (preserves history).
        existing.requester_id = current_user.id
        existing.recipient_id = target_user.id
        existing.status = FriendRequestStatus.PENDING
        existing.responded_at = None  # Clear the previous response timestamp.
    else:
        # No existing request — create a new one.
        db.add(
            FriendRequest(
                requester_id=current_user.id,
                recipient_id=target_user.id,
                status=FriendRequestStatus.PENDING,
            )
        )

    # Notify the target user about the incoming friend request.
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

    # Audit log for compliance and debugging.
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
    """Accept or decline a pending friend request.

    This function enforces several authorization and state checks:
      1. The request must exist (404 if not).
      2. The current user must be the **recipient** (403 if not — only the
         recipient can respond; the requester cannot accept their own request).
      3. The request must be in PENDING status (400 if already handled —
         prevents double-accept or accept-after-decline race conditions).

    On acceptance, the status changes to ACCEPTED and both users become
    friends.  On decline, the status changes to DECLINED (the requester
    can re-send later via the "recycle" logic in ``send_friend_request``).

    Args:
        db: SQLAlchemy session.
        request_id: The database ID of the FriendRequest record.
        current_user: The authenticated user (must be the recipient).
        accept: True to accept, False to decline.

    Returns:
        A success message string indicating the action taken.

    Raises:
        HTTPException 403: Current user is not the recipient.
        HTTPException 404: Friend request not found.
        HTTPException 400: Request already handled (not PENDING).
    """
    # Step 1: Find the friend request by ID.
    friend_request = db.execute(
        select(FriendRequest).where(FriendRequest.id == request_id)
    ).scalar_one_or_none()
    if not friend_request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Friend request not found.",
        )

    # Step 2: Authorization — only the recipient can respond.
    if friend_request.recipient_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot respond to this friend request.",
        )

    # Step 3: State check — can only respond to PENDING requests.
    if friend_request.status != FriendRequestStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This friend request has already been handled.",
        )

    # Transition the state machine: PENDING -> ACCEPTED or PENDING -> DECLINED.
    friend_request.status = (
        FriendRequestStatus.ACCEPTED if accept else FriendRequestStatus.DECLINED
    )
    friend_request.responded_at = _utcnow()

    # Audit log records who responded and how.
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

    # Notify the original requester about the response.
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
    """List all friend requests and accepted friendships for a user.

    Returns three categorised lists:
      - **incoming**: Pending requests where current_user is the recipient
        (they need to accept/decline).
      - **outgoing**: Pending requests where current_user is the requester
        (waiting for the other person to respond).
      - **friends**: Accepted requests in either direction (full friendships).

    For each request, the "counterpart" user (the other person in the
    relationship) is loaded and serialised.  This requires N+1 queries
    (one per request to load the counterpart), which is acceptable for
    typical friend list sizes but would need optimisation (e.g., eager
    loading or a JOIN) for users with thousands of friends.

    Args:
        db: SQLAlchemy session.
        current_user: The authenticated user whose friendships to list.

    Returns:
        FriendRequestListResponse with ``incoming``, ``outgoing``, and
        ``friends`` lists.
    """
    # Query 1: Incoming pending requests (other people sent TO current_user).
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

    # Query 2: Outgoing pending requests (current_user sent TO others).
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

    # Query 3: Accepted friendships (current_user is on EITHER side).
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
        """Helper to build a FriendRequestResponse with the counterpart's profile."""
        return FriendRequestResponse(
            id=friend_request.id,
            status=friend_request.status.value,
            user=serialize_public_user(db, counterpart, current_user),
        )

    # Build the incoming list — counterpart is the requester (the person
    # who sent the request TO current_user).
    incoming = []
    for request in incoming_requests:
        counterpart = db.execute(
            select(User).where(User.id == request.requester_id)
        ).scalar_one()
        incoming.append(_request_response(request, counterpart))

    # Build the outgoing list — counterpart is the recipient (the person
    # current_user sent the request TO).
    outgoing = []
    for request in outgoing_requests:
        counterpart = db.execute(
            select(User).where(User.id == request.recipient_id)
        ).scalar_one()
        outgoing.append(_request_response(request, counterpart))

    # Build the friends list — counterpart is whichever user is NOT
    # the current_user in the relationship.
    friends = []
    for request in accepted_requests:
        # Determine the counterpart: if current_user is the requester,
        # the counterpart is the recipient, and vice versa.
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

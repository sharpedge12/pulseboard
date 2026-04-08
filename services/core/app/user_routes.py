"""
User Routes — Core Service
===========================

This module defines the HTTP endpoints for user profile management, friend
requests, user search, and user reporting.  It is the controller layer for
all ``/api/v1/users/`` endpoints.

Endpoint overview:

    Profile:
      - ``GET  /me``           — Return the authenticated user's full profile.
      - ``PATCH /me``          — Update username and/or bio.
      - ``POST /me/avatar``    — Upload a new avatar image.

    User Discovery:
      - ``GET  /``             — List all users (for "People" page).
      - ``GET  /search``       — Search users by username (autocomplete).
      - ``GET  /lookup/{username}`` — Get a user's public profile by username.
      - ``GET  /{user_id}``    — Get a user's public profile by ID.

    Friend Requests:
      - ``POST /{user_id}/friend``          — Send a friend request.
      - ``GET  /friends``                   — List incoming, outgoing, and accepted.
      - ``POST /friends/{id}/accept``       — Accept a pending request.
      - ``POST /friends/{id}/decline``      — Decline a pending request.

    Reporting:
      - ``POST /{user_id}/report``          — Report a user to moderators.

Key interview concepts:
  - **REST resource naming**: ``/users/me`` is a conventional alias for "the
    currently authenticated user", avoiding the need to put the user ID in
    the URL.
  - **PATCH vs PUT**: PATCH allows partial updates (only the fields included
    in the request body are changed).  PUT would require sending ALL fields.
  - **Route ordering**: FastAPI matches routes top-to-bottom.  Fixed paths
    like ``/me``, ``/friends``, ``/search``, ``/lookup/{username}`` must be
    defined BEFORE the wildcard ``/{user_id}`` to avoid ``"me"`` being
    interpreted as a user_id.
  - **Authorization**: Every endpoint requires ``Depends(get_current_user)``
    which extracts and validates the JWT from the Authorization header.
"""

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


# ---------------------------------------------------------------------------
# Profile endpoints
# ---------------------------------------------------------------------------


@router.get("/me", response_model=UserMeResponse)
def read_current_user(current_user: User = Depends(get_current_user)) -> UserMeResponse:
    """Return the full profile of the currently authenticated user.

    This is the first endpoint the frontend calls after login to populate
    the user's profile data in the React context (``AuthContext``).  It
    returns sensitive fields (email, account status flags) that are NOT
    exposed in public profile endpoints.

    The ``get_current_user`` dependency:
      1. Reads the ``Authorization: Bearer <token>`` header.
      2. Decodes and validates the JWT.
      3. Looks up the user by ID from the JWT's ``sub`` claim.
      4. Updates ``last_seen`` (used for online status indicators).

    Args:
        current_user: The authenticated User (injected by DI).

    Returns:
        UserMeResponse: Full profile including email, role, flags.
    """
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
    """Update the current user's profile (username and/or bio).

    Uses HTTP PATCH (not PUT) because it supports partial updates — the
    client only sends the fields they want to change.

    Args:
        payload: Fields to update (both optional).
        db: SQLAlchemy session.
        current_user: The authenticated user.

    Returns:
        UserMeResponse: The updated profile.

    Raises:
        HTTPException 400: If the new username is already taken.
    """
    return update_current_user(db, current_user, payload)


@router.post("/me/avatar", response_model=UserMeResponse)
def upload_current_user_avatar(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserMeResponse:
    """Upload a new avatar image for the current user.

    The ``File(...)`` dependency tells FastAPI to expect a multipart form
    upload (``Content-Type: multipart/form-data``).  The file is validated
    for type (MIME + magic bytes), size, and extension in the storage layer.

    If the user already has an avatar, the old file is deleted from disk
    before the new one is saved.

    Args:
        file: The uploaded image file.
        db: SQLAlchemy session.
        current_user: The authenticated user.

    Returns:
        UserMeResponse: The updated profile with the new ``avatar_url``.
    """
    return upload_avatar(db, current_user, file)


# ---------------------------------------------------------------------------
# User listing and search
# ---------------------------------------------------------------------------


@router.get("", response_model=list[UserListItemResponse])
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[UserListItemResponse]:
    """List all users for the "People" page.

    Returns every user except the currently authenticated user, sorted
    alphabetically.  Each result includes the friendship status between
    the current user and the listed user (none, pending, friends).

    This is an O(n) operation that queries friendship status for each user.
    For a large platform, this would need pagination and caching.

    Args:
        db: SQLAlchemy session.
        current_user: The authenticated user (excluded from results).

    Returns:
        List of UserListItemResponse with friendship status and online indicator.
    """
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
            # Compute the friendship status between current_user and this user.
            friendship_status=serialize_public_user(
                db, user, current_user
            ).friendship_status,
            created_at=user.created_at,
            last_seen=user.last_seen,
            is_online=_is_online(user),  # True if last_seen < 5 minutes ago.
        )
        for user in users
        if user.id != current_user.id  # Exclude self from the list.
    ]


@router.get("/friends", response_model=FriendRequestListResponse)
def read_friendships(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FriendRequestListResponse:
    """List all friend requests and friendships for the current user.

    Returns three lists:
      - ``incoming``: Pending requests FROM other users TO the current user.
      - ``outgoing``: Pending requests FROM the current user TO others.
      - ``friends``: Accepted friendships (bidirectional).

    Args:
        db: SQLAlchemy session.
        current_user: The authenticated user.

    Returns:
        FriendRequestListResponse with incoming, outgoing, and friends lists.
    """
    return list_friendships(db, current_user)


@router.get("/search", response_model=list[UserPublicProfileResponse])
def search_users(
    q: str = Query(min_length=1, max_length=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[UserPublicProfileResponse]:
    """Search users by username (case-insensitive partial match).

    Used by the ``@mention`` autocomplete in the MentionTextarea component.
    The query parameter ``q`` is matched with SQL ``ILIKE %q%`` for
    case-insensitive substring matching.

    Limited to 30 results to keep the response fast and the dropdown usable.

    Args:
        q: The search query (1-50 characters, validated by FastAPI).
        db: SQLAlchemy session.
        current_user: The authenticated user (excluded from results).

    Returns:
        List of matching UserPublicProfileResponse objects.
    """
    pattern = f"%{q}%"  # SQL ILIKE pattern for substring matching.
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
        if user.id != current_user.id  # Don't show the current user in search results.
    ]


# ---------------------------------------------------------------------------
# Friend request actions
# ---------------------------------------------------------------------------


@router.post("/friends/{request_id}/accept", response_model=UserActionResponse)
def accept_friend_request(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserActionResponse:
    """Accept a pending friend request.

    Only the **recipient** of a friend request can accept it.  The request
    must be in ``PENDING`` status.  On acceptance, the status changes to
    ``ACCEPTED`` and the requester is notified.

    Args:
        request_id: The ID of the FriendRequest record.
        db: SQLAlchemy session.
        current_user: The authenticated user (must be the recipient).

    Returns:
        UserActionResponse: Success message.

    Raises:
        HTTPException 403: Current user is not the recipient.
        HTTPException 404: Friend request not found.
    """
    return UserActionResponse(
        message=respond_to_friend_request(db, request_id, current_user, True)
    )


@router.post("/friends/{request_id}/decline", response_model=UserActionResponse)
def decline_friend_request(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserActionResponse:
    """Decline a pending friend request.

    Similar to accept, but changes the status to ``DECLINED``.  The
    requester is notified that their request was declined.

    Args:
        request_id: The ID of the FriendRequest record.
        db: SQLAlchemy session.
        current_user: The authenticated user (must be the recipient).

    Returns:
        UserActionResponse: Success message.
    """
    return UserActionResponse(
        message=respond_to_friend_request(db, request_id, current_user, False)
    )


# ---------------------------------------------------------------------------
# User reporting
# ---------------------------------------------------------------------------


@router.post("/{user_id}/report", response_model=UserActionResponse)
def report_user(
    user_id: int,
    payload: UserReportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserActionResponse:
    """Report a user to moderators for rule violations.

    Creates a ``ContentReport`` record (same table used for thread/post
    reports) and notifies all staff members (admins + moderators) via
    in-app notifications.

    Includes several safety checks:
      - Cannot report yourself.
      - Cannot submit duplicate reports for the same user.

    Args:
        user_id: The ID of the user being reported.
        payload: Contains the ``reason`` for the report.
        db: SQLAlchemy session.
        current_user: The authenticated user filing the report.

    Returns:
        UserActionResponse: Confirmation message.

    Raises:
        HTTPException 400: Self-reporting.
        HTTPException 404: Reported user not found.
        HTTPException 409: Duplicate report.
    """
    # Verify the reported user exists.
    reported_user = db.execute(
        select(User).where(User.id == user_id)
    ).scalar_one_or_none()
    if not reported_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    # Prevent self-reporting (nonsensical action).
    if reported_user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot report yourself.",
        )

    # Check for duplicate report — a user can only report another user once.
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

    # Create the report record.  The ``entity_type="user"`` distinguishes
    # this from thread/post reports in the admin dashboard.
    report = ContentReport(
        reporter_id=current_user.id,
        entity_type="user",
        entity_id=reported_user.id,
        reason=payload.reason,
    )
    db.add(report)
    db.flush()  # Get the report.id for the audit log.

    # Record the action in the audit log for compliance.
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

    # Notify all staff members (admins and moderators) about the report.
    staff_users = (
        db.execute(
            select(User).where(User.role.in_([UserRole.ADMIN, UserRole.MODERATOR]))
        )
        .scalars()
        .all()
    )
    for staff_user in staff_users:
        if staff_user.id == current_user.id:
            continue  # Don't notify the reporter if they happen to be staff.
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


# ---------------------------------------------------------------------------
# Friend request creation
# ---------------------------------------------------------------------------


@router.post("/{user_id}/friend", response_model=UserActionResponse)
def create_friend_request(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserActionResponse:
    """Send a friend request to another user.

    This endpoint handles the validation (user exists, not self), then
    delegates to the service layer for the friend request state machine
    (checking for existing requests, re-sending declined requests, etc.).

    Args:
        user_id: The ID of the user to send the request to.
        db: SQLAlchemy session.
        current_user: The authenticated user sending the request.

    Returns:
        UserActionResponse: Confirmation message.

    Raises:
        HTTPException 400: Self-friending or request already exists.
        HTTPException 404: Target user not found.
    """
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


# ---------------------------------------------------------------------------
# Public profile lookup
# ---------------------------------------------------------------------------


@router.get("/lookup/{username}", response_model=UserPublicProfileResponse)
def lookup_user_profile(
    username: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserPublicProfileResponse:
    """Get a user's public profile by username.

    Used when navigating to ``/user/{username}`` in the frontend.  Returns
    the public profile (no email, no account flags) plus the friendship
    status between the viewer and the target user.

    Args:
        username: The username to look up.
        db: SQLAlchemy session.
        current_user: The authenticated viewer.

    Returns:
        UserPublicProfileResponse with public fields + friendship status.

    Raises:
        HTTPException 404: User not found.
    """
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
    """Get a user's public profile by numeric ID.

    Similar to ``/lookup/{username}`` but uses the user's database ID.
    This is useful for internal navigation (e.g. clicking a user mention
    where we have the ID but not the username).

    NOTE: This route uses ``/{user_id}`` which is a wildcard path.  It MUST
    be defined AFTER all fixed paths (``/me``, ``/friends``, ``/search``,
    ``/lookup/{username}``) to avoid capturing those paths as ``user_id``.

    Args:
        user_id: The numeric user ID.
        db: SQLAlchemy session.
        current_user: The authenticated viewer.

    Returns:
        UserPublicProfileResponse with public fields + friendship status.

    Raises:
        HTTPException 404: User not found.
    """
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    return UserPublicProfileResponse(
        **serialize_public_user(db, user, current_user).model_dump()
    )

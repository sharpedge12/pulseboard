"""
Admin Services — Business Logic for Moderation and Administration.

This module implements the business logic behind every admin/moderation
endpoint defined in ``admin_routes.py``.  It enforces role-based access
control, manages user state (suspend/ban), handles content reports, issues
moderation actions (warn/suspend/ban), manages category moderator
assignments, and processes category requests.

Role hierarchy and permission model:
    PulseBoard uses a three-tier role system with a strict rank ordering:

        ``member`` (rank 1) < ``moderator`` (rank 2) < ``admin`` (rank 3)

    Key rules:
        - Users can only manage (suspend, ban, change role of) other users
          with a **strictly lower** rank.  This prevents moderators from
          suspending each other or admins, and prevents admins from
          demoting themselves.
        - Moderators are "scoped" to specific categories — they can only
          manage content in categories they are assigned to (via the
          ``category_moderators`` join table).  Admins have global scope.
        - Only admins can: ban/unban users, change user roles, approve/
          reject category requests, and assign category moderators.

    The ``ROLE_RANK`` dict and helper functions (``_ensure_staff``,
    ``_ensure_admin``, ``_can_manage_target``, ``_assert_manageable_target``)
    encapsulate this logic so it doesn't need to be repeated in every
    service function.

Key patterns for interviews:
    - **Guard-clause pattern**: Each function starts with permission checks
      that raise HTTP 403 early, keeping the happy path un-indented and
      readable.
    - **Audit trail**: Every state-changing operation records an entry in
      the ``audit_logs`` table for accountability and compliance.
    - **Notification dispatch**: Users are notified in-app (and via email)
      when moderation actions are taken against them.
    - **Idempotent operations**: Assigning a category moderator who is
      already assigned is a no-op (no error, no duplicate row).

Called from:
    ``app.admin_routes`` (HTTP layer).
"""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from shared.models.category import Category
from shared.models.post import Post
from shared.models.thread import Thread
from shared.models.user import User, UserRole
from shared.models.vote import (
    CategoryModerator,
    CategoryRequest,
    ContentReport,
    ModerationAction,
)
from shared.schemas.admin import (
    AdminReportResponse,
    AdminSummaryResponse,
    AdminThreadResponse,
    AdminUserResponse,
    CategoryRequestResponse,
    ModerationActionDetailResponse,
)
from shared.services.moderation import get_moderator_category_ids
from shared.services.notifications import create_notification
from shared.services.email import _send_moderation_email
from shared.services.audit import record as audit_record
from shared.services import audit as audit_actions


# ---------------------------------------------------------------------------
# Authorisation helpers
# ---------------------------------------------------------------------------

# Role rank mapping: higher number = more privileged.
# This dict drives all "can user A manage user B?" decisions.
ROLE_RANK = {
    UserRole.MEMBER: 1,
    UserRole.MODERATOR: 2,
    UserRole.ADMIN: 3,
}


def _ensure_staff(current_user: User) -> None:
    """
    Guard: raise HTTP 403 if the user is not at least a moderator.

    Used at the top of every admin/mod service function to reject
    regular members before any business logic runs.
    """
    if current_user.role not in {UserRole.ADMIN, UserRole.MODERATOR}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Staff access required.",
        )


def _ensure_admin(current_user: User) -> None:
    """
    Guard: raise HTTP 403 if the user is not an admin.

    Used for admin-only operations like banning users, changing roles,
    and reviewing category requests.
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )


def _can_manage_target(current_user: User, target_user: User) -> bool:
    """
    Check whether ``current_user`` has sufficient rank to manage
    ``target_user``.

    Rules:
        - You cannot manage yourself (returns False).
        - You can only manage users with a strictly lower role rank.

    This function returns a boolean rather than raising — it is used by
    the serialiser to compute UI flags (``can_suspend``, ``can_ban``,
    ``can_change_role``) as well as by the assertion helper below.
    """
    if current_user.id == target_user.id:
        return False
    return ROLE_RANK[current_user.role] > ROLE_RANK[target_user.role]


def _assert_manageable_target(current_user: User, target_user: User) -> None:
    """
    Guard: raise HTTP 403 if ``current_user`` cannot manage ``target_user``.

    Wraps ``_can_manage_target`` in an exception for use in service
    functions where failure should abort the request.
    """
    if not _can_manage_target(current_user, target_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot moderate users with the same or higher role.",
        )


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def _serialize_admin_user(user: User, current_user: User) -> AdminUserResponse:
    """
    Serialise a ``User`` ORM object into an ``AdminUserResponse`` for the
    admin panel.

    The response includes computed boolean fields that the frontend uses
    to show/hide action buttons:
        - ``can_suspend``     — True if the viewer outranks this user.
        - ``can_ban``         — True if the viewer is an admin AND outranks.
        - ``can_change_role`` — True if the viewer is an admin AND outranks.

    Args:
        user: The user being serialised.
        current_user: The authenticated staff member viewing the panel.
    """
    can_manage = _can_manage_target(current_user, user)
    return AdminUserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role.value,
        is_verified=user.is_verified,
        is_active=user.is_active,
        is_suspended=user.is_suspended,
        is_banned=user.is_banned,
        created_at=user.created_at,
        can_suspend=can_manage,
        can_ban=current_user.role == UserRole.ADMIN and can_manage,
        can_change_role=current_user.role == UserRole.ADMIN and can_manage,
    )


# ===========================================================================
# Dashboard summary
# ===========================================================================


def get_admin_summary(db: Session, current_user: User) -> AdminSummaryResponse:
    """
    Compute high-level statistics for the admin dashboard.

    For **admins**: all counts are global (all categories, all reports).
    For **moderators**: thread and report counts are scoped to only the
    categories they are assigned to moderate.  User counts are always
    global since user management is not category-scoped.

    Scoping logic:
        1. Call ``get_moderator_category_ids()`` to get the list of
           category IDs assigned to the current moderator.
        2. If ``None`` is returned → the user is an admin (no scoping).
        3. If an empty list → the moderator has no assignments (zero counts).
        4. Otherwise → filter queries by ``Thread.category_id IN (…)``.

    The report count requires extra care because reports can be on threads
    OR posts.  For post reports, we need a two-level subquery:
    ``posts → threads → category_id IN (…)``.

    Returns:
        ``AdminSummaryResponse`` with user, thread, and report statistics.
    """
    _ensure_staff(current_user)

    # Get the moderator's assigned category IDs (None for admins = global).
    category_ids = get_moderator_category_ids(db, current_user)

    # --- Thread counts (scoped for moderators) ---
    thread_q = select(func.count(Thread.id))
    locked_q = select(func.count(Thread.id)).where(Thread.is_locked.is_(True))
    pinned_q = select(func.count(Thread.id)).where(Thread.is_pinned.is_(True))

    if category_ids is not None:
        if len(category_ids) == 0:
            # Moderator with no assigned categories sees zero threads.
            thread_total = 0
            locked_threads = 0
            pinned_threads = 0
        else:
            thread_total = db.execute(
                thread_q.where(Thread.category_id.in_(category_ids))
            ).scalar_one()
            locked_threads = db.execute(
                locked_q.where(Thread.category_id.in_(category_ids))
            ).scalar_one()
            pinned_threads = db.execute(
                pinned_q.where(Thread.category_id.in_(category_ids))
            ).scalar_one()
    else:
        # Admin: global counts (no category filter).
        thread_total = db.execute(thread_q).scalar_one()
        locked_threads = db.execute(locked_q).scalar_one()
        pinned_threads = db.execute(pinned_q).scalar_one()

    # --- Pending reports (scoped for moderators) ---
    if category_ids is not None:
        if len(category_ids) == 0:
            pending = 0
        else:
            # Count pending THREAD reports in moderator's categories.
            thread_report_count = db.execute(
                select(func.count(ContentReport.id)).where(
                    ContentReport.status == "pending",
                    ContentReport.entity_type == "thread",
                    ContentReport.entity_id.in_(
                        select(Thread.id).where(Thread.category_id.in_(category_ids))
                    ),
                )
            ).scalar_one()

            # Count pending POST reports — requires joining through the
            # posts table to get the thread's category_id.
            post_report_count = db.execute(
                select(func.count(ContentReport.id)).where(
                    ContentReport.status == "pending",
                    ContentReport.entity_type == "post",
                    ContentReport.entity_id.in_(
                        select(Post.id).where(
                            Post.thread_id.in_(
                                select(Thread.id).where(
                                    Thread.category_id.in_(category_ids)
                                )
                            )
                        )
                    ),
                )
            ).scalar_one()

            pending = thread_report_count + post_report_count
    else:
        # Admin: count all pending reports globally.
        pending = db.execute(
            select(func.count(ContentReport.id)).where(
                ContentReport.status == "pending"
            )
        ).scalar_one()

    # --- User counts (always global — not category-scoped) ---
    return AdminSummaryResponse(
        users_total=db.execute(select(func.count(User.id))).scalar_one(),
        verified_users=db.execute(
            select(func.count(User.id)).where(User.is_verified.is_(True))
        ).scalar_one(),
        suspended_users=db.execute(
            select(func.count(User.id)).where(User.is_suspended.is_(True))
        ).scalar_one(),
        banned_users=db.execute(
            select(func.count(User.id)).where(User.is_banned.is_(True))
        ).scalar_one(),
        thread_total=thread_total,
        locked_threads=locked_threads,
        pinned_threads=pinned_threads,
        pending_reports=pending,
    )


# ===========================================================================
# User management
# ===========================================================================


def list_users(db: Session, current_user: User) -> list[AdminUserResponse]:
    """
    List all users for the admin panel, filtered by the viewer's role.

    - **Admins** see all users.
    - **Moderators** see only users with a lower role rank (i.e. regular
      members).  This prevents moderators from viewing other moderators'
      or admins' account details.

    Each user is serialised with computed action flags (can_suspend, etc.)
    so the frontend knows which buttons to enable.

    Returns:
        List of ``AdminUserResponse`` sorted newest first.
    """
    _ensure_staff(current_user)
    users = db.execute(select(User).order_by(User.created_at.desc())).scalars().all()

    # Moderators can only see users they outrank.
    visible_users = (
        users
        if current_user.role == UserRole.ADMIN
        else [
            user
            for user in users
            if ROLE_RANK[user.role] < ROLE_RANK[current_user.role]
        ]
    )
    return [_serialize_admin_user(user, current_user) for user in visible_users]


def update_user_role(db: Session, user_id: int, role: str, current_user: User) -> User:
    """
    Change a user's role.  **Admin-only**.

    Flow:
        1. Verify the current user is an admin.
        2. Parse and validate the new role string.
        3. Fetch the target user (HTTP 404 if not found).
        4. Assert the admin outranks the target (prevents self-demotion
           and same-rank changes).
        5. Update the role, record an audit log, commit.

    Args:
        db: Active database session.
        user_id: Target user's ID.
        role: New role string (``"admin"``, ``"moderator"``, ``"member"``).
        current_user: The admin performing the change.

    Returns:
        The updated ``User`` ORM object.
    """
    _ensure_admin(current_user)
    try:
        role_enum = UserRole(role.lower())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role."
        ) from exc

    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
        )

    _assert_manageable_target(current_user, user)

    user.role = role_enum
    audit_record(
        db,
        actor_id=current_user.id,
        action=audit_actions.USER_ROLE_CHANGE,
        entity_type="user",
        entity_id=user.id,
        details={"new_role": role, "username": user.username},
    )
    db.commit()
    db.refresh(user)
    return user


def set_user_suspension(
    db: Session, user_id: int, suspended: bool, current_user: User
) -> User:
    """
    Suspend or unsuspend a user.

    Suspended users can still log in and browse, but they are blocked
    from creating content (threads, posts, votes, chat messages) by the
    ``require_can_participate`` guard in the auth helpers.

    Args:
        db: Active database session.
        user_id: Target user's ID.
        suspended: ``True`` to suspend, ``False`` to unsuspend.
        current_user: The staff member performing the action.

    Returns:
        The updated ``User`` ORM object.

    Raises:
        HTTPException(403) if the current user is not staff or lacks rank.
        HTTPException(404) if the target user does not exist.
    """
    _ensure_staff(current_user)
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
        )

    _assert_manageable_target(current_user, user)

    user.is_suspended = suspended
    audit_record(
        db,
        actor_id=current_user.id,
        action=audit_actions.USER_SUSPEND
        if suspended
        else audit_actions.USER_UNSUSPEND,
        entity_type="user",
        entity_id=user.id,
        details={"username": user.username},
    )
    db.commit()
    db.refresh(user)
    return user


def set_user_ban(db: Session, user_id: int, banned: bool, current_user: User) -> User:
    """
    Ban or unban a user.  **Admin-only**.

    Banning is more severe than suspension:
        - ``is_banned = True`` — flags the account as banned.
        - ``is_active = False`` — prevents login entirely.

    Unbanning reverses both flags.

    Args:
        db: Active database session.
        user_id: Target user's ID.
        banned: ``True`` to ban, ``False`` to unban.
        current_user: The admin performing the action.

    Returns:
        The updated ``User`` ORM object.
    """
    _ensure_admin(current_user)
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
        )

    _assert_manageable_target(current_user, user)

    user.is_banned = banned
    user.is_active = not banned  # Banned users cannot log in.
    audit_record(
        db,
        actor_id=current_user.id,
        action=audit_actions.USER_BAN if banned else audit_actions.USER_UNBAN,
        entity_type="user",
        entity_id=user.id,
        details={"username": user.username},
    )
    db.commit()
    db.refresh(user)
    return user


# ===========================================================================
# Thread management
# ===========================================================================


def list_threads_for_moderation(
    db: Session, current_user: User
) -> list[AdminThreadResponse]:
    """
    List all threads for the moderation panel, scoped by role.

    - **Admins** see all threads globally.
    - **Moderators** see only threads in their assigned categories.
    - Moderators with **no** assigned categories see an empty list.

    Threads are sorted newest first.  Each item includes lock/pin status
    and the author/category metadata needed by the admin UI.

    Returns:
        List of ``AdminThreadResponse`` objects.
    """
    _ensure_staff(current_user)

    category_ids = get_moderator_category_ids(db, current_user)

    query = (
        select(Thread)
        .options(selectinload(Thread.author), selectinload(Thread.category))
        .order_by(Thread.created_at.desc())
    )

    if category_ids is not None and len(category_ids) > 0:
        # Moderator with assigned categories — filter by those categories.
        query = query.where(Thread.category_id.in_(category_ids))
    elif category_ids is not None:
        # Moderator with no assigned categories — return empty list.
        return []
    # else: admin — no filter, see all threads.

    threads = db.execute(query).scalars().all()

    return [
        AdminThreadResponse(
            id=thread.id,
            title=thread.title,
            category=thread.category.title,
            category_id=thread.category.id,
            author=thread.author.username,
            is_locked=thread.is_locked,
            is_pinned=thread.is_pinned,
            created_at=thread.created_at,
        )
        for thread in threads
    ]


def set_thread_lock(
    db: Session, thread_id: int, locked: bool, current_user: User
) -> Thread:
    """
    Lock or unlock a thread.

    Locking prevents new replies from being created (``create_post``
    checks ``thread.is_locked`` and returns HTTP 400 if True).

    Moderators can only lock/unlock threads in their assigned categories.
    Admins can lock/unlock any thread.

    Args:
        db: Active database session.
        thread_id: Thread to lock/unlock.
        locked: ``True`` to lock, ``False`` to unlock.
        current_user: The staff member performing the action.

    Returns:
        The updated ``Thread`` ORM object.
    """
    _ensure_staff(current_user)
    thread = db.execute(
        select(Thread).where(Thread.id == thread_id)
    ).scalar_one_or_none()
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found."
        )

    # Scoped moderation: moderators can only manage their assigned categories.
    category_ids = get_moderator_category_ids(db, current_user)
    if category_ids is not None and thread.category_id not in category_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not moderate this community.",
        )

    thread.is_locked = locked
    audit_record(
        db,
        actor_id=current_user.id,
        action=audit_actions.THREAD_LOCK if locked else audit_actions.THREAD_UNLOCK,
        entity_type="thread",
        entity_id=thread.id,
        details={"title": thread.title},
    )
    db.commit()
    db.refresh(thread)
    return thread


def set_thread_pin(
    db: Session, thread_id: int, pinned: bool, current_user: User
) -> Thread:
    """
    Pin or unpin a thread.

    Pinned threads sort to the top of the feed regardless of the current
    sort order (``list_threads`` uses ``Thread.is_pinned.desc()`` as the
    primary sort key).

    Same scoping rules as ``set_thread_lock``.

    Args:
        db: Active database session.
        thread_id: Thread to pin/unpin.
        pinned: ``True`` to pin, ``False`` to unpin.
        current_user: The staff member performing the action.

    Returns:
        The updated ``Thread`` ORM object.
    """
    _ensure_staff(current_user)
    thread = db.execute(
        select(Thread).where(Thread.id == thread_id)
    ).scalar_one_or_none()
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found."
        )

    category_ids = get_moderator_category_ids(db, current_user)
    if category_ids is not None and thread.category_id not in category_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not moderate this community.",
        )

    thread.is_pinned = pinned
    audit_record(
        db,
        actor_id=current_user.id,
        action=audit_actions.THREAD_PIN if pinned else audit_actions.THREAD_UNPIN,
        entity_type="thread",
        entity_id=thread.id,
        details={"title": thread.title},
    )
    db.commit()
    db.refresh(thread)
    return thread


# ===========================================================================
# Content reports
# ===========================================================================


def _resolve_report_content(
    db: Session, report: ContentReport
) -> tuple[str, str, int, str, int, str]:
    """
    Look up the reported content to extract display information for the
    admin report panel.

    Reports are polymorphic — they can reference threads, posts, or users
    (via ``entity_type`` + ``entity_id``).  This function resolves the
    reference and returns a standardised tuple of display values.

    Returns:
        Tuple of:
            - ``snippet`` — first 120 chars of the reported content.
            - ``author_username`` — who authored the reported content.
            - ``author_id`` — the author's user ID.
            - ``category_name`` — the category the content belongs to
              (empty string for user reports).
            - ``category_id`` — the category ID (0 for user reports).
            - ``thread_title`` — the parent thread's title (empty for
              user reports).

        Falls back to ``("", "[deleted]", 0, "", 0, "")`` if the
        referenced content has been deleted.
    """
    if report.entity_type == "thread":
        thread = db.execute(
            select(Thread)
            .options(selectinload(Thread.author), selectinload(Thread.category))
            .where(Thread.id == report.entity_id)
        ).scalar_one_or_none()
        if thread:
            snippet = thread.title[:120]
            return (
                snippet,
                thread.author.username,
                thread.author.id,
                thread.category.title,
                thread.category.id,
                thread.title,
            )
    elif report.entity_type == "post":
        post = db.execute(
            select(Post)
            .options(
                selectinload(Post.author),
                selectinload(Post.thread).selectinload(Thread.category),
            )
            .where(Post.id == report.entity_id)
        ).scalar_one_or_none()
        if post:
            snippet = post.body[:120]
            return (
                snippet,
                post.author.username,
                post.author.id,
                post.thread.category.title,
                post.thread.category.id,
                post.thread.title,
            )
    elif report.entity_type == "user":
        # User reports don't belong to a category.
        user = db.execute(
            select(User).where(User.id == report.entity_id)
        ).scalar_one_or_none()
        if user:
            snippet = f"User profile: @{user.username}"
            return (
                snippet,
                user.username,
                user.id,
                "",  # No category for user reports
                0,  # No category ID
                "",  # No thread title
            )
    # Fallback: the reported content has been deleted.
    return ("", "[deleted]", 0, "", 0, "")


def list_reports(
    db: Session, current_user: User, status_filter: str | None = None
) -> list[AdminReportResponse]:
    """
    List content reports visible to the current staff member.

    Visibility scoping:
        - **Admins** see all reports.
        - **Moderators** see reports for content in their assigned
          categories.  User-type reports (``entity_type="user"``) are
          visible to all staff regardless of category assignments.
        - Moderators with no assigned categories see no reports.

    Each report is enriched with:
        - A snippet of the reported content (for quick review).
        - The content author's username and ID.
        - The reporter's username.
        - The resolver's username (if already resolved).

    Args:
        db: Active database session.
        current_user: The staff member viewing reports.
        status_filter: Optional filter (``"pending"``, ``"resolved"``,
            ``"dismissed"``).

    Returns:
        List of ``AdminReportResponse`` objects sorted newest first.
    """
    _ensure_staff(current_user)

    query = select(ContentReport).order_by(ContentReport.created_at.desc())
    if status_filter:
        query = query.where(ContentReport.status == status_filter)

    reports = db.execute(query).scalars().all()

    category_ids = get_moderator_category_ids(db, current_user)

    # Moderator with no assigned categories sees no reports.
    if category_ids is not None and len(category_ids) == 0:
        return []

    results: list[AdminReportResponse] = []
    for report in reports:
        # Resolve the report's content to get display metadata.
        snippet, author, author_id, cat_name, cat_id, thread_title = (
            _resolve_report_content(db, report)
        )

        # Scoping: skip reports outside the moderator's assigned categories.
        # Exception: user reports (cat_id=0) are visible to all staff.
        if category_ids is not None and cat_id not in category_ids:
            if report.entity_type != "user":
                continue

        # Look up the reporter's username.
        reporter = db.execute(
            select(User).where(User.id == report.reporter_id)
        ).scalar_one_or_none()

        # Look up the resolver's username (if the report has been resolved).
        resolver_username = None
        if report.resolved_by:
            resolver = db.execute(
                select(User).where(User.id == report.resolved_by)
            ).scalar_one_or_none()
            resolver_username = resolver.username if resolver else None

        results.append(
            AdminReportResponse(
                id=report.id,
                reporter_id=report.reporter_id,
                reporter_username=reporter.username if reporter else "[deleted]",
                entity_type=report.entity_type,
                entity_id=report.entity_id,
                reason=report.reason,
                status=report.status,
                created_at=report.created_at,
                resolved_by=report.resolved_by,
                resolver_username=resolver_username,
                resolved_at=report.resolved_at,
                content_snippet=snippet,
                content_author=author,
                content_author_id=author_id,
                category_name=cat_name,
                category_id=cat_id,
                thread_title=thread_title,
            )
        )
    return results


def resolve_report(
    db: Session,
    report_id: int,
    new_status: str,
    current_user: User,
) -> ContentReport:
    """
    Mark a content report as resolved or dismissed.

    - **Resolved** — the moderator took action on the reported content.
    - **Dismissed** — the report was reviewed but deemed not actionable.

    Records who resolved the report and when (for the audit trail and
    admin UI).

    Args:
        db: Active database session.
        report_id: The report to resolve.
        new_status: ``"resolved"`` or ``"dismissed"``.
        current_user: The staff member resolving the report.

    Returns:
        The updated ``ContentReport`` ORM object.

    Raises:
        HTTPException(400) if the status is invalid.
        HTTPException(404) if the report does not exist.
    """
    _ensure_staff(current_user)

    if new_status not in ("resolved", "dismissed"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Status must be 'resolved' or 'dismissed'.",
        )

    report = db.execute(
        select(ContentReport).where(ContentReport.id == report_id)
    ).scalar_one_or_none()
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report not found."
        )

    report.status = new_status
    report.resolved_by = current_user.id
    report.resolved_at = datetime.now(timezone.utc)
    audit_record(
        db,
        actor_id=current_user.id,
        action=audit_actions.REPORT_RESOLVE,
        entity_type="report",
        entity_id=report.id,
        details={
            "new_status": new_status,
            "entity_type": report.entity_type,
            "entity_id": report.entity_id,
        },
    )
    db.commit()
    db.refresh(report)
    return report


# ===========================================================================
# Moderation actions (warn / suspend / ban)
# ===========================================================================


def create_moderation_action(
    db: Session,
    target_user_id: int,
    action_type: str,
    reason: str,
    current_user: User,
    duration_hours: int | None = None,
    report_id: int | None = None,
) -> ModerationActionDetailResponse:
    """
    Execute a formal moderation action against a user.

    This is a multi-step operation that:
        1. Validates the action type and target user.
        2. Verifies the current user outranks the target.
        3. Applies the account state change:
           - ``"warn"``    — no state change (just a notification).
           - ``"suspend"`` — sets ``is_suspended=True``, optionally with
             a ``suspended_until`` deadline.
           - ``"ban"``     — sets ``is_banned=True`` and ``is_active=False``
             (**admin-only**).
        4. Records a ``ModerationAction`` row in the database.
        5. Records an audit log entry.
        6. Auto-resolves the linked report if ``report_id`` is provided.
        7. Sends an in-app notification to the target user.
        8. Commits all changes.
        9. Sends a moderation email to the target user (after commit,
           non-blocking).

    Args:
        db: Active database session.
        target_user_id: The user being moderated.
        action_type: ``"warn"``, ``"suspend"``, or ``"ban"``.
        reason: Free-text explanation for the action.
        current_user: The staff member issuing the action.
        duration_hours: Optional suspension duration in hours.  If omitted,
            suspension is indefinite.
        report_id: Optional ID of a linked content report to auto-resolve.

    Returns:
        ``ModerationActionDetailResponse`` with the recorded action details.

    Raises:
        HTTPException(400) for invalid action types.
        HTTPException(403) if the user lacks permission (not staff, or
            trying to ban without admin role, or trying to moderate a
            higher-ranked user).
        HTTPException(404) if the target user does not exist.
    """
    _ensure_staff(current_user)

    if action_type not in ("warn", "suspend", "ban"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="action_type must be 'warn', 'suspend', or 'ban'.",
        )

    target = db.execute(
        select(User).where(User.id == target_user_id)
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target user not found.",
        )

    _assert_manageable_target(current_user, target)

    # --- Apply the account state change ---
    if action_type == "suspend":
        target.is_suspended = True
        if duration_hours:
            # Timed suspension: will be lifted after the specified duration.
            target.suspended_until = datetime.now(timezone.utc) + timedelta(
                hours=duration_hours
            )
        else:
            target.suspended_until = None  # Indefinite suspension
    elif action_type == "ban":
        # Only admins can ban.
        if current_user.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only admins can ban users.",
            )
        target.is_banned = True
        target.is_active = False  # Locked out of the platform

    # --- Record the moderation action ---
    action = ModerationAction(
        moderator_id=current_user.id,
        target_user_id=target_user_id,
        action_type=action_type,
        reason=reason,
        duration_hours=duration_hours,
        report_id=report_id,
    )
    db.add(action)

    # --- Audit log ---
    audit_record(
        db,
        actor_id=current_user.id,
        action=audit_actions.MOD_ACTION,
        entity_type="user",
        entity_id=target_user_id,
        details={
            "action_type": action_type,
            "reason": reason,
            "duration_hours": duration_hours,
            "report_id": report_id,
            "target_username": target.username,
        },
    )

    # --- Auto-resolve linked report (if provided) ---
    if report_id:
        report = db.execute(
            select(ContentReport).where(ContentReport.id == report_id)
        ).scalar_one_or_none()
        if report and report.status == "pending":
            report.status = "resolved"
            report.resolved_by = current_user.id
            report.resolved_at = datetime.now(timezone.utc)

    # --- In-app notification for the target user ---
    action_labels = {"warn": "Warning", "suspend": "Suspension", "ban": "Ban"}
    label = action_labels.get(action_type, action_type.capitalize())
    create_notification(
        db,
        user_id=target_user_id,
        notification_type="moderation_action",
        title=f"You have received a {label}",
        payload={
            "action_type": action_type,
            "reason": reason,
            "moderator_username": current_user.username,
        },
    )

    db.commit()
    db.refresh(action)

    # Send a moderation email AFTER commit (non-blocking, uses SMTP with
    # a 2-second timeout to prevent test/runtime hangs).
    _send_moderation_email(target, action_type, reason, current_user.username)

    return ModerationActionDetailResponse(
        id=action.id,
        moderator_username=current_user.username,
        target_username=target.username,
        action_type=action.action_type,
        reason=action.reason,
        duration_hours=action.duration_hours,
        report_id=action.report_id,
        created_at=action.created_at,
    )


# ===========================================================================
# Category moderator assignments
# ===========================================================================


def assign_category_moderator(
    db: Session, user_id: int, category_id: int, current_user: User
) -> None:
    """
    Assign a moderator to a specific category.  **Admin-only**.

    This creates a row in the ``category_moderators`` join table, which
    is queried by ``get_moderator_category_ids()`` to determine a
    moderator's scope.

    Idempotent — if the assignment already exists, this is a no-op
    (no error, no duplicate row).

    Args:
        db: Active database session.
        user_id: The moderator to assign.
        category_id: The category to assign them to.
        current_user: The admin performing the assignment.
    """
    _ensure_admin(current_user)
    # Check if the assignment already exists (idempotency).
    existing = db.execute(
        select(CategoryModerator).where(
            CategoryModerator.user_id == user_id,
            CategoryModerator.category_id == category_id,
        )
    ).scalar_one_or_none()
    if existing:
        return  # Already assigned — no-op.
    db.add(CategoryModerator(user_id=user_id, category_id=category_id))
    audit_record(
        db,
        actor_id=current_user.id,
        action=audit_actions.CATEGORY_MOD_ASSIGN,
        entity_type="category",
        entity_id=category_id,
        details={"user_id": user_id},
    )
    db.commit()


def remove_category_moderator(
    db: Session, user_id: int, category_id: int, current_user: User
) -> None:
    """
    Remove a moderator's assignment from a category.  **Admin-only**.

    After removal, the moderator can no longer manage content in this
    category (their ``get_moderator_category_ids()`` result will no
    longer include this category_id).

    Args:
        db: Active database session.
        user_id: The moderator to unassign.
        category_id: The category to remove them from.
        current_user: The admin performing the removal.
    """
    _ensure_admin(current_user)
    row = db.execute(
        select(CategoryModerator).where(
            CategoryModerator.user_id == user_id,
            CategoryModerator.category_id == category_id,
        )
    ).scalar_one_or_none()
    if row:
        db.delete(row)
        audit_record(
            db,
            actor_id=current_user.id,
            action=audit_actions.CATEGORY_MOD_REMOVE,
            entity_type="category",
            entity_id=category_id,
            details={"user_id": user_id},
        )
        db.commit()


# ===========================================================================
# Category requests (moderator proposes, admin reviews)
# ===========================================================================


def create_category_request(
    db: Session,
    title: str,
    slug: str,
    description: str,
    current_user: User,
) -> CategoryRequestResponse:
    """
    Submit a request to create a new community (category).

    This is the moderator-friendly alternative to direct category creation.
    The request enters a "pending" queue that admins review.

    Duplicate prevention:
        1. Check if a category with the same title or slug already exists
           (HTTP 400).
        2. Check if a pending request with the same title or slug is
           already in the queue (HTTP 400).

    Args:
        db: Active database session.
        title: Proposed category display name.
        slug: Proposed URL-safe identifier.
        description: Proposed category description.
        current_user: The staff member submitting the request.

    Returns:
        ``CategoryRequestResponse`` with the pending request details.
    """
    _ensure_staff(current_user)

    # Check for existing category with the same title or slug.
    existing = db.execute(
        select(Category).where((Category.title == title) | (Category.slug == slug))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A community with that title or slug already exists.",
        )

    # Check for a pending request with the same title or slug.
    pending = db.execute(
        select(CategoryRequest).where(
            CategoryRequest.status == "pending",
            (CategoryRequest.title == title) | (CategoryRequest.slug == slug),
        )
    ).scalar_one_or_none()
    if pending:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A pending request for that community already exists.",
        )

    req = CategoryRequest(
        requester_id=current_user.id,
        title=title,
        slug=slug,
        description=description,
    )
    db.add(req)
    db.flush()  # Get the auto-generated request ID for the audit log.
    audit_record(
        db,
        actor_id=current_user.id,
        action=audit_actions.CATEGORY_REQUEST_CREATE,
        entity_type="category_request",
        entity_id=req.id,
        details={"title": title, "slug": slug},
    )
    db.commit()
    db.refresh(req)

    return CategoryRequestResponse(
        id=req.id,
        requester_id=req.requester_id,
        requester_username=current_user.username,
        title=req.title,
        slug=req.slug,
        description=req.description,
        status=req.status,
        created_at=req.created_at,
    )


def list_category_requests(
    db: Session,
    current_user: User,
    status_filter: str | None = None,
) -> list[CategoryRequestResponse]:
    """
    List category requests with role-based visibility.

    - **Admins** see all requests (for review).
    - **Moderators** see only their own submissions (to track status).

    Args:
        db: Active database session.
        current_user: The staff member viewing requests.
        status_filter: Optional filter (``"pending"``, ``"approved"``,
            ``"rejected"``).

    Returns:
        List of ``CategoryRequestResponse`` objects sorted newest first.
    """
    _ensure_staff(current_user)

    query = select(CategoryRequest).order_by(CategoryRequest.created_at.desc())
    if status_filter:
        query = query.where(CategoryRequest.status == status_filter)

    # Moderators can only see their own requests.
    if current_user.role != UserRole.ADMIN:
        query = query.where(CategoryRequest.requester_id == current_user.id)

    requests = db.execute(query).scalars().all()

    results: list[CategoryRequestResponse] = []
    for req in requests:
        # Look up the requester's username for display.
        requester = db.execute(
            select(User).where(User.id == req.requester_id)
        ).scalar_one_or_none()

        # Look up the reviewer's username (if already reviewed).
        reviewer_username = None
        if req.reviewed_by:
            reviewer = db.execute(
                select(User).where(User.id == req.reviewed_by)
            ).scalar_one_or_none()
            reviewer_username = reviewer.username if reviewer else None

        results.append(
            CategoryRequestResponse(
                id=req.id,
                requester_id=req.requester_id,
                requester_username=(requester.username if requester else "[deleted]"),
                title=req.title,
                slug=req.slug,
                description=req.description,
                status=req.status,
                reviewed_by=req.reviewed_by,
                reviewer_username=reviewer_username,
                reviewed_at=req.reviewed_at,
                created_at=req.created_at,
            )
        )
    return results


def review_category_request(
    db: Session,
    request_id: int,
    new_status: str,
    current_user: User,
) -> CategoryRequestResponse:
    """
    Approve or reject a pending category request.  **Admin-only**.

    Approval flow:
        1. Verify the request is still pending (HTTP 400 if already reviewed).
        2. Double-check that no category with the same title/slug was created
           in the meantime (race condition guard).
        3. Create the new ``Category`` row.
        4. Auto-assign the requester as moderator of the new category
           (via ``CategoryModerator``).
        5. Notify the requester of the outcome.
        6. Record an audit log entry.
        7. Commit.

    Rejection flow:
        1. Mark the request as rejected.
        2. Notify the requester.
        3. Commit.

    Args:
        db: Active database session.
        request_id: The category request to review.
        new_status: ``"approved"`` or ``"rejected"``.
        current_user: The admin reviewing the request.

    Returns:
        ``CategoryRequestResponse`` with the updated request details.
    """
    _ensure_admin(current_user)

    if new_status not in ("approved", "rejected"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Status must be 'approved' or 'rejected'.",
        )

    req = db.execute(
        select(CategoryRequest).where(CategoryRequest.id == request_id)
    ).scalar_one_or_none()
    if not req:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Category request not found.",
        )

    # Guard: prevent re-reviewing an already-decided request.
    if req.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This request has already been reviewed.",
        )

    req.status = new_status
    req.reviewed_by = current_user.id
    req.reviewed_at = datetime.now(timezone.utc)

    if new_status == "approved":
        # Race condition guard: check that no category with the same
        # title/slug was created between the request submission and now.
        existing = db.execute(
            select(Category).where(
                (Category.title == req.title) | (Category.slug == req.slug)
            )
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A community with that title or slug was created in the meantime.",
            )

        # Create the new category.
        created_category = Category(
            title=req.title,
            slug=req.slug,
            description=req.description,
        )
        db.add(created_category)
        db.flush()  # Get the new category's ID.

        # Auto-assign the requester as moderator of the new category.
        db.add(
            CategoryModerator(
                user_id=req.requester_id,
                category_id=created_category.id,
            )
        )

    # Notify the requester of the review outcome.
    result_label = "approved" if new_status == "approved" else "rejected"
    audit_record(
        db,
        actor_id=current_user.id,
        action=audit_actions.CATEGORY_REQUEST_REVIEW,
        entity_type="category_request",
        entity_id=req.id,
        details={"status": new_status, "slug": req.slug},
    )
    create_notification(
        db,
        user_id=req.requester_id,
        notification_type="moderation_action",
        title=f"Your community request r/{req.slug} was {result_label}",
        payload={
            "action_type": "category_request_review",
            "slug": req.slug,
            "status": new_status,
        },
    )

    db.commit()
    db.refresh(req)

    requester = db.execute(
        select(User).where(User.id == req.requester_id)
    ).scalar_one_or_none()

    return CategoryRequestResponse(
        id=req.id,
        requester_id=req.requester_id,
        requester_username=requester.username if requester else "[deleted]",
        title=req.title,
        slug=req.slug,
        description=req.description,
        status=req.status,
        reviewed_by=req.reviewed_by,
        reviewer_username=current_user.username,
        reviewed_at=req.reviewed_at,
        created_at=req.created_at,
    )

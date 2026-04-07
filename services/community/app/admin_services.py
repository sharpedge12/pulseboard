"""Moderation service — business logic for admin/moderation operations."""

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

ROLE_RANK = {
    UserRole.MEMBER: 1,
    UserRole.MODERATOR: 2,
    UserRole.ADMIN: 3,
}


def _ensure_staff(current_user: User) -> None:
    if current_user.role not in {UserRole.ADMIN, UserRole.MODERATOR}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Staff access required.",
        )


def _ensure_admin(current_user: User) -> None:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )


def _can_manage_target(current_user: User, target_user: User) -> bool:
    if current_user.id == target_user.id:
        return False
    return ROLE_RANK[current_user.role] > ROLE_RANK[target_user.role]


def _assert_manageable_target(current_user: User, target_user: User) -> None:
    if not _can_manage_target(current_user, target_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot moderate users with the same or higher role.",
        )


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def _serialize_admin_user(user: User, current_user: User) -> AdminUserResponse:
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


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def get_admin_summary(db: Session, current_user: User) -> AdminSummaryResponse:
    """Return dashboard summary stats, scoped to the moderator's categories."""
    _ensure_staff(current_user)

    category_ids = get_moderator_category_ids(db, current_user)

    # --- Thread counts (scoped for moderators) ---
    thread_q = select(func.count(Thread.id))
    locked_q = select(func.count(Thread.id)).where(Thread.is_locked.is_(True))
    pinned_q = select(func.count(Thread.id)).where(Thread.is_pinned.is_(True))

    if category_ids is not None:
        if len(category_ids) == 0:
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
        thread_total = db.execute(thread_q).scalar_one()
        locked_threads = db.execute(locked_q).scalar_one()
        pinned_threads = db.execute(pinned_q).scalar_one()

    # --- Pending reports (scoped for moderators) ---
    if category_ids is not None:
        if len(category_ids) == 0:
            pending = 0
        else:
            # Count pending thread reports in moderator's categories
            thread_report_count = db.execute(
                select(func.count(ContentReport.id)).where(
                    ContentReport.status == "pending",
                    ContentReport.entity_type == "thread",
                    ContentReport.entity_id.in_(
                        select(Thread.id).where(Thread.category_id.in_(category_ids))
                    ),
                )
            ).scalar_one()

            # Count pending post reports in moderator's categories
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
        pending = db.execute(
            select(func.count(ContentReport.id)).where(
                ContentReport.status == "pending"
            )
        ).scalar_one()

    # --- User counts (global — not category-scoped) ---
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


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------


def list_users(db: Session, current_user: User) -> list[AdminUserResponse]:
    _ensure_staff(current_user)
    users = db.execute(select(User).order_by(User.created_at.desc())).scalars().all()
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
    _ensure_admin(current_user)
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
        )

    _assert_manageable_target(current_user, user)

    user.is_banned = banned
    user.is_active = not banned
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


# ---------------------------------------------------------------------------
# Thread management
# ---------------------------------------------------------------------------


def list_threads_for_moderation(
    db: Session, current_user: User
) -> list[AdminThreadResponse]:
    _ensure_staff(current_user)

    category_ids = get_moderator_category_ids(db, current_user)

    query = (
        select(Thread)
        .options(selectinload(Thread.author), selectinload(Thread.category))
        .order_by(Thread.created_at.desc())
    )

    if category_ids is not None and len(category_ids) > 0:
        query = query.where(Thread.category_id.in_(category_ids))
    elif category_ids is not None:
        # Moderator with no assigned categories should see nothing
        return []

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


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def _resolve_report_content(
    db: Session, report: ContentReport
) -> tuple[str, str, int, str, int, str]:
    """Return (snippet, author_username, author_id, category_name, category_id, thread_title)."""
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
        user = db.execute(
            select(User).where(User.id == report.entity_id)
        ).scalar_one_or_none()
        if user:
            snippet = f"User profile: @{user.username}"
            return (
                snippet,
                user.username,
                user.id,
                "",
                0,
                "",
            )
    return ("", "[deleted]", 0, "", 0, "")


def list_reports(
    db: Session, current_user: User, status_filter: str | None = None
) -> list[AdminReportResponse]:
    """List content reports visible to the current staff member."""
    _ensure_staff(current_user)

    query = select(ContentReport).order_by(ContentReport.created_at.desc())
    if status_filter:
        query = query.where(ContentReport.status == status_filter)

    reports = db.execute(query).scalars().all()

    category_ids = get_moderator_category_ids(db, current_user)

    # Moderator with no assigned categories should see no reports
    if category_ids is not None and len(category_ids) == 0:
        return []

    results: list[AdminReportResponse] = []
    for report in reports:
        snippet, author, author_id, cat_name, cat_id, thread_title = (
            _resolve_report_content(db, report)
        )

        if category_ids is not None and cat_id not in category_ids:
            # User reports (cat_id=0) are visible to all staff
            if report.entity_type != "user":
                continue

        reporter = db.execute(
            select(User).where(User.id == report.reporter_id)
        ).scalar_one_or_none()

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
    """Mark a report as resolved or dismissed."""
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


# ---------------------------------------------------------------------------
# Moderation actions (warn / suspend / ban)
# ---------------------------------------------------------------------------


def create_moderation_action(
    db: Session,
    target_user_id: int,
    action_type: str,
    reason: str,
    current_user: User,
    duration_hours: int | None = None,
    report_id: int | None = None,
) -> ModerationActionDetailResponse:
    """Execute a moderation action and record it."""
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

    # Apply the action
    if action_type == "suspend":
        target.is_suspended = True
        if duration_hours:
            target.suspended_until = datetime.now(timezone.utc) + timedelta(
                hours=duration_hours
            )
        else:
            target.suspended_until = None  # indefinite
    elif action_type == "ban":
        if current_user.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only admins can ban users.",
            )
        target.is_banned = True
        target.is_active = False

    # Record the action
    action = ModerationAction(
        moderator_id=current_user.id,
        target_user_id=target_user_id,
        action_type=action_type,
        reason=reason,
        duration_hours=duration_hours,
        report_id=report_id,
    )
    db.add(action)

    # Audit log entry
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

    # Auto-resolve the linked report if provided
    if report_id:
        report = db.execute(
            select(ContentReport).where(ContentReport.id == report_id)
        ).scalar_one_or_none()
        if report and report.status == "pending":
            report.status = "resolved"
            report.resolved_by = current_user.id
            report.resolved_at = datetime.now(timezone.utc)

    # Create in-app notification for the target user
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

    # Send moderation email to the target user (after commit, non-blocking)
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


# ---------------------------------------------------------------------------
# Category moderator assignments
# ---------------------------------------------------------------------------


def assign_category_moderator(
    db: Session, user_id: int, category_id: int, current_user: User
) -> None:
    """Admin assigns a moderator to a category."""
    _ensure_admin(current_user)
    existing = db.execute(
        select(CategoryModerator).where(
            CategoryModerator.user_id == user_id,
            CategoryModerator.category_id == category_id,
        )
    ).scalar_one_or_none()
    if existing:
        return
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
    """Admin removes a moderator from a category."""
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


# ---------------------------------------------------------------------------
# Category requests (moderator proposes, admin reviews)
# ---------------------------------------------------------------------------


def create_category_request(
    db: Session,
    title: str,
    slug: str,
    description: str,
    current_user: User,
) -> CategoryRequestResponse:
    """Moderator submits a request to create a new community."""
    _ensure_staff(current_user)

    existing = db.execute(
        select(Category).where((Category.title == title) | (Category.slug == slug))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A community with that title or slug already exists.",
        )

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
    db.flush()
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
    """List category requests. Admins see all; mods see their own."""
    _ensure_staff(current_user)

    query = select(CategoryRequest).order_by(CategoryRequest.created_at.desc())
    if status_filter:
        query = query.where(CategoryRequest.status == status_filter)

    if current_user.role != UserRole.ADMIN:
        query = query.where(CategoryRequest.requester_id == current_user.id)

    requests = db.execute(query).scalars().all()

    results: list[CategoryRequestResponse] = []
    for req in requests:
        requester = db.execute(
            select(User).where(User.id == req.requester_id)
        ).scalar_one_or_none()
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
    """Admin approves or rejects a category request."""
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

    if req.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This request has already been reviewed.",
        )

    req.status = new_status
    req.reviewed_by = current_user.id
    req.reviewed_at = datetime.now(timezone.utc)

    if new_status == "approved":
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
        created_category = Category(
            title=req.title,
            slug=req.slug,
            description=req.description,
        )
        db.add(created_category)
        db.flush()

        # Auto-assign the requester as moderator of the new category
        db.add(
            CategoryModerator(
                user_id=req.requester_id,
                category_id=created_category.id,
            )
        )

    # Notify the requester
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

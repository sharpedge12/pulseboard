"""
Admin Routes — HTTP API Endpoints for the Moderation Dashboard.

This module defines the ``/api/v1/admin`` router, which powers the admin
and moderator dashboard in the PulseBoard frontend.  It provides endpoints
for managing users, threads, content reports, moderation actions, category
moderator assignments, category requests, and audit logs.

Architecture notes:
    - **Thin routes, fat services**: Route handlers parse HTTP input and
      return responses, but all business logic (permission checks, state
      changes, notifications) lives in ``admin_services.py``.
    - **Role-based access**: Every endpoint requires authentication.  Most
      require at least moderator or admin role.  The service layer enforces
      fine-grained permission rules (e.g. moderators can only manage users
      with a lower role rank; only admins can ban users).
    - **Scoped moderation**: Moderators are assigned to specific categories.
      They can only see and manage content within those categories.  Admins
      have global access.
    - **Real-time updates**: When a category request is approved (creating
      a new category), the route broadcasts a ``category_created`` event
      on the ``global`` WebSocket channel so all clients update their
      sidebar in real-time.

Endpoint groups:
    1. **Summary** — ``GET /admin/summary`` — dashboard stats (user counts,
       thread counts, pending reports).
    2. **User management** — list users, change roles, suspend/unsuspend,
       ban/unban.
    3. **Thread management** — list threads, lock/unlock, pin/unpin.
    4. **Content reports** — list reports (with scoping), resolve/dismiss.
    5. **Moderation actions** — issue warn/suspend/ban against a user.
    6. **Category moderator assignments** — assign/remove moderators for
       specific categories (admin only).
    7. **Category requests** — moderators propose new categories; admins
       approve or reject.
    8. **Audit logs** — paginated, filterable activity log with role-based
       visibility.

Called from:
    The API gateway reverse-proxies ``/api/v1/admin/*`` to this service.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.core.database import get_db
from shared.core.auth_helpers import get_current_user
from shared.core.events import connection_manager, publish_event
from shared.models.category import Category
from shared.models.user import User, UserRole
from shared.schemas.admin import (
    AdminReportResponse,
    AdminSummaryResponse,
    AdminThreadResponse,
    AdminUserResponse,
    CategoryModeratorRequest,
    CategoryRequestCreate,
    CategoryRequestResponse,
    CategoryRequestReviewRequest,
    ModerationActionDetailResponse,
    ModerationActionRequest,
    ModerationActionResponse,
    PaginatedAuditLogResponse,
    ReportResolveRequest,
    RoleUpdateRequest,
)
from shared.schemas.category import CategoryResponse
from shared.services.moderation import get_moderator_category_ids
from shared.services.audit import list_audit_logs

from app.admin_services import (
    assign_category_moderator,
    create_category_request,
    create_moderation_action,
    get_admin_summary,
    list_category_requests,
    list_reports,
    list_threads_for_moderation,
    list_users,
    remove_category_moderator,
    resolve_report,
    review_category_request,
    set_thread_lock,
    set_thread_pin,
    set_user_ban,
    set_user_suspension,
    update_user_role,
)

router = APIRouter()


# ===========================================================================
# Summary
# ===========================================================================


@router.get("/summary", response_model=AdminSummaryResponse)
def admin_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AdminSummaryResponse:
    """
    GET /api/v1/admin/summary

    Return high-level dashboard statistics for the admin panel.

    Includes: total users, verified/suspended/banned counts, thread counts
    (total/locked/pinned), and pending report count.

    For moderators, thread and report counts are scoped to their assigned
    categories.  For admins, counts are global.

    Requires: staff role (moderator or admin).
    """
    return get_admin_summary(db, current_user)


# ===========================================================================
# User management
# ===========================================================================


@router.get("/users", response_model=list[AdminUserResponse])
def admin_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AdminUserResponse]:
    """
    GET /api/v1/admin/users

    List all users for the admin panel.

    Admins see all users.  Moderators only see users with a lower role
    rank (i.e. regular members).  Each user response includes boolean
    flags (``can_suspend``, ``can_ban``, ``can_change_role``) that tell
    the frontend which action buttons to enable.

    Requires: staff role (moderator or admin).
    """
    return list_users(db, current_user)


@router.patch("/users/{user_id}/role", response_model=AdminUserResponse)
def admin_update_user_role(
    user_id: int,
    payload: RoleUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AdminUserResponse:
    """
    PATCH /api/v1/admin/users/{user_id}/role

    Change a user's role (admin, moderator, or member).  **Admin-only**.

    The target user must have a strictly lower role rank than the current
    user (admins cannot demote themselves or other admins).

    Args:
        user_id: The user whose role should change.
        payload: ``RoleUpdateRequest`` with the new ``role`` string.
    """
    user = update_user_role(db, user_id, payload.role, current_user)
    # Re-fetch the full user list to get the updated serialised response
    # (with recalculated can_suspend/can_ban/can_change_role flags).
    users = list_users(db, current_user)
    return next(item for item in users if item.id == user.id)


@router.patch("/users/{user_id}/suspend", response_model=ModerationActionResponse)
def admin_suspend_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModerationActionResponse:
    """
    PATCH /api/v1/admin/users/{user_id}/suspend

    Suspend a user, preventing them from creating content.  Suspended
    users can still log in and read content, but ``require_can_participate``
    blocks them from posting, voting, or chatting.

    Requires: staff role.  Cannot suspend users of equal or higher rank.
    """
    user = set_user_suspension(db, user_id, True, current_user)
    return ModerationActionResponse(message=f"User {user.username} has been suspended.")


@router.patch("/users/{user_id}/unsuspend", response_model=ModerationActionResponse)
def admin_unsuspend_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModerationActionResponse:
    """
    PATCH /api/v1/admin/users/{user_id}/unsuspend

    Lift a user's suspension, restoring their ability to participate.

    Requires: staff role.
    """
    user = set_user_suspension(db, user_id, False, current_user)
    return ModerationActionResponse(
        message=f"User {user.username} has been unsuspended."
    )


@router.patch("/users/{user_id}/ban", response_model=ModerationActionResponse)
def admin_ban_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModerationActionResponse:
    """
    PATCH /api/v1/admin/users/{user_id}/ban

    Ban a user permanently.  This sets ``is_banned=True`` AND
    ``is_active=False``, effectively locking them out of the platform.

    **Admin-only** — moderators can suspend but cannot ban.
    """
    user = set_user_ban(db, user_id, True, current_user)
    return ModerationActionResponse(message=f"User {user.username} has been banned.")


@router.patch("/users/{user_id}/unban", response_model=ModerationActionResponse)
def admin_unban_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModerationActionResponse:
    """
    PATCH /api/v1/admin/users/{user_id}/unban

    Lift a user's ban, restoring their account access.

    **Admin-only**.
    """
    user = set_user_ban(db, user_id, False, current_user)
    return ModerationActionResponse(message=f"User {user.username} has been unbanned.")


# ===========================================================================
# Thread management
# ===========================================================================


@router.get("/threads", response_model=list[AdminThreadResponse])
def admin_threads(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AdminThreadResponse]:
    """
    GET /api/v1/admin/threads

    List all threads for the moderation panel.

    For moderators, results are scoped to their assigned categories.
    For admins, all threads are visible.  Threads are sorted newest first.

    Requires: staff role (moderator or admin).
    """
    return list_threads_for_moderation(db, current_user)


@router.patch("/threads/{thread_id}/lock", response_model=ModerationActionResponse)
def admin_lock_thread(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModerationActionResponse:
    """
    PATCH /api/v1/admin/threads/{thread_id}/lock

    Lock a thread, preventing new replies.  Existing content remains
    visible.  Useful for cooling down heated discussions or archiving
    resolved threads.

    Requires: staff role.  Moderators can only lock threads in their
    assigned categories.
    """
    thread = set_thread_lock(db, thread_id, True, current_user)
    return ModerationActionResponse(message=f"Thread '{thread.title}' locked.")


@router.patch("/threads/{thread_id}/unlock", response_model=ModerationActionResponse)
def admin_unlock_thread(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModerationActionResponse:
    """
    PATCH /api/v1/admin/threads/{thread_id}/unlock

    Unlock a previously locked thread, allowing new replies again.

    Requires: staff role.
    """
    thread = set_thread_lock(db, thread_id, False, current_user)
    return ModerationActionResponse(message=f"Thread '{thread.title}' unlocked.")


@router.patch("/threads/{thread_id}/pin", response_model=ModerationActionResponse)
def admin_pin_thread(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModerationActionResponse:
    """
    PATCH /api/v1/admin/threads/{thread_id}/pin

    Pin a thread to the top of the feed.  Pinned threads appear before
    all non-pinned threads regardless of the sort order (new/top/trending).

    Common uses: announcements, community guidelines, welcome threads.

    Requires: staff role.
    """
    thread = set_thread_pin(db, thread_id, True, current_user)
    return ModerationActionResponse(message=f"Thread '{thread.title}' pinned.")


@router.patch("/threads/{thread_id}/unpin", response_model=ModerationActionResponse)
def admin_unpin_thread(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModerationActionResponse:
    """
    PATCH /api/v1/admin/threads/{thread_id}/unpin

    Unpin a thread, returning it to its natural sort position.

    Requires: staff role.
    """
    thread = set_thread_pin(db, thread_id, False, current_user)
    return ModerationActionResponse(message=f"Thread '{thread.title}' unpinned.")


# ===========================================================================
# Content reports
# ===========================================================================


@router.get("/reports", response_model=list[AdminReportResponse])
def admin_list_reports(
    status_filter: str | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AdminReportResponse]:
    """
    GET /api/v1/admin/reports

    List content reports submitted by users.

    Admins see all reports.  Moderators see reports for content in their
    assigned categories (plus user-type reports, which are visible to all
    staff).

    The optional ``status`` query parameter filters by report status:
    ``"pending"``, ``"resolved"``, or ``"dismissed"``.

    Requires: staff role.
    """
    return list_reports(db, current_user, status_filter=status_filter)


@router.patch("/reports/{report_id}/resolve", response_model=ModerationActionResponse)
def admin_resolve_report(
    report_id: int,
    payload: ReportResolveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModerationActionResponse:
    """
    PATCH /api/v1/admin/reports/{report_id}/resolve

    Mark a report as ``"resolved"`` or ``"dismissed"``.

    - **Resolved** — the reported content was actioned (e.g. post removed,
      user warned).
    - **Dismissed** — the report was reviewed but no action was needed.

    Both statuses record who resolved the report and when (for the audit
    trail).

    Requires: staff role.
    """
    report = resolve_report(db, report_id, payload.status, current_user)
    return ModerationActionResponse(
        message=f"Report #{report.id} marked as {report.status}."
    )


# ===========================================================================
# Moderation actions (warn / suspend / ban from reports panel)
# ===========================================================================


@router.post(
    "/users/{user_id}/moderate",
    response_model=ModerationActionDetailResponse,
)
def admin_moderate_user(
    user_id: int,
    payload: ModerationActionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModerationActionDetailResponse:
    """
    POST /api/v1/admin/users/{user_id}/moderate

    Issue a formal moderation action against a user.

    Action types:
        - ``"warn"``    — send a warning notification (no account restriction).
        - ``"suspend"`` — suspend the user (optionally with a time-limited
          ``duration_hours``).
        - ``"ban"``     — permanently ban the user (**admin-only**).

    Can optionally be linked to a content report via ``report_id``.
    When linked, the report is automatically resolved.

    Side effects:
        - The target user receives an in-app notification.
        - A moderation email is sent to the target user.
        - An audit log entry is recorded.

    Requires: staff role.  Moderators can warn and suspend.  Only admins
    can ban.
    """
    return create_moderation_action(
        db,
        target_user_id=user_id,
        action_type=payload.action_type,
        reason=payload.reason,
        current_user=current_user,
        duration_hours=payload.duration_hours,
        report_id=payload.report_id,
    )


# ===========================================================================
# Category moderator assignments (admin only)
# ===========================================================================


@router.get("/category-moderators/{user_id}", response_model=list[int])
def admin_get_user_categories(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[int]:
    """
    GET /api/v1/admin/category-moderators/{user_id}

    Return the list of category IDs that a moderator is assigned to.

    Used by the admin panel to populate the category assignment checkboxes
    when editing a moderator's permissions.

    **Admin-only**.
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Admin only.",
        )
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(
            status_code=404,
            detail="User not found.",
        )
    ids = get_moderator_category_ids(db, target)
    return ids if ids is not None else []


@router.post("/category-moderators", response_model=ModerationActionResponse)
def admin_assign_category_moderator(
    payload: CategoryModeratorRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModerationActionResponse:
    """
    POST /api/v1/admin/category-moderators

    Assign a moderator to a specific category, granting them moderation
    powers (lock/pin/edit/delete) over content in that category.

    Idempotent — assigning a moderator who is already assigned is a no-op.

    **Admin-only**.
    """
    assign_category_moderator(db, payload.user_id, payload.category_id, current_user)
    return ModerationActionResponse(message="Category moderator assigned.")


@router.delete("/category-moderators", response_model=ModerationActionResponse)
def admin_remove_category_moderator(
    payload: CategoryModeratorRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModerationActionResponse:
    """
    DELETE /api/v1/admin/category-moderators

    Remove a moderator's assignment from a specific category.

    **Admin-only**.
    """
    remove_category_moderator(db, payload.user_id, payload.category_id, current_user)
    return ModerationActionResponse(message="Category moderator removed.")


# ===========================================================================
# Category requests (moderator proposes, admin reviews)
# ===========================================================================


@router.post(
    "/category-requests",
    response_model=CategoryRequestResponse,
    status_code=201,
)
def admin_create_category_request(
    payload: CategoryRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CategoryRequestResponse:
    """
    POST /api/v1/admin/category-requests

    Submit a request to create a new community (category).

    This is the moderator-friendly alternative to direct category creation
    (which is admin-only).  The request goes into a "pending" queue that
    admins can review.

    Duplicate checks: rejects requests if a category with the same title
    or slug already exists, or if a pending request with the same
    title/slug is already in the queue.

    Requires: staff role (moderator or admin).
    """
    return create_category_request(
        db,
        title=payload.title,
        slug=payload.slug,
        description=payload.description,
        current_user=current_user,
    )


@router.get("/category-requests", response_model=list[CategoryRequestResponse])
def admin_list_category_requests(
    status_filter: str | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CategoryRequestResponse]:
    """
    GET /api/v1/admin/category-requests

    List category requests.  Admins see all requests; moderators see only
    their own submissions.  Optional ``status`` filter.

    Requires: staff role.
    """
    return list_category_requests(db, current_user, status_filter=status_filter)


@router.patch(
    "/category-requests/{request_id}/review",
    response_model=CategoryRequestResponse,
)
async def admin_review_category_request(
    request_id: int,
    payload: CategoryRequestReviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CategoryRequestResponse:
    """
    PATCH /api/v1/admin/category-requests/{request_id}/review

    Approve or reject a pending category request.  **Admin-only**.

    If approved:
        1. A new ``Category`` row is created.
        2. The requester is auto-assigned as moderator of the new category.
        3. A ``category_created`` event is broadcast on the ``global``
           WebSocket channel so all clients' sidebar updates in real-time.
        4. The requester receives a notification.

    If rejected:
        The requester receives a notification informing them.
    """
    result = review_category_request(db, request_id, payload.status, current_user)

    # If approved, broadcast the new category to all connected clients so
    # the sidebar community list updates without a page refresh.
    if payload.status == "approved":
        cat = db.execute(
            select(Category).where(Category.slug == result.slug)
        ).scalar_one_or_none()
        if cat:
            cat_resp = CategoryResponse(
                id=cat.id,
                title=cat.title,
                slug=cat.slug,
                description=cat.description,
                thread_count=0,
            )
            event = jsonable_encoder(
                {
                    "event": "category_created",
                    "category": cat_resp.model_dump(),
                }
            )
            await connection_manager.broadcast("global", event)
            publish_event("global", event)

    return result


# ===========================================================================
# Audit logs
# ===========================================================================


@router.get("/audit-logs", response_model=PaginatedAuditLogResponse)
def admin_audit_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    action: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    actor_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PaginatedAuditLogResponse:
    """
    GET /api/v1/admin/audit-logs

    Return paginated audit logs with optional filters and role-based
    visibility.

    Visibility rules:
        - **Admin** — sees all audit log entries.
        - **Moderator** — sees their own actions plus actions by members.
        - **Member** — sees only their own actions.

    Query parameters:
        - ``page`` / ``page_size`` — pagination controls.
        - ``action`` — filter by action type (e.g. ``"thread_create"``).
        - ``entity_type`` — filter by entity type (e.g. ``"thread"``).
        - ``actor_id`` — filter by the user who performed the action.

    Returns:
        ``PaginatedAuditLogResponse`` with ``items``, ``total``, ``page``,
        ``page_size``, and ``total_pages``.
    """
    return list_audit_logs(
        db,
        current_user,
        page=page,
        page_size=page_size,
        action_filter=action,
        entity_type_filter=entity_type,
        actor_id_filter=actor_id,
    )

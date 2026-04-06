"""Moderation service — API routes (admin dashboard, reports, mod actions)."""

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


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


@router.get("/summary", response_model=AdminSummaryResponse)
def admin_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AdminSummaryResponse:
    return get_admin_summary(db, current_user)


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------


@router.get("/users", response_model=list[AdminUserResponse])
def admin_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AdminUserResponse]:
    return list_users(db, current_user)


@router.patch("/users/{user_id}/role", response_model=AdminUserResponse)
def admin_update_user_role(
    user_id: int,
    payload: RoleUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AdminUserResponse:
    user = update_user_role(db, user_id, payload.role, current_user)
    users = list_users(db, current_user)
    return next(item for item in users if item.id == user.id)


@router.patch("/users/{user_id}/suspend", response_model=ModerationActionResponse)
def admin_suspend_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModerationActionResponse:
    user = set_user_suspension(db, user_id, True, current_user)
    return ModerationActionResponse(message=f"User {user.username} has been suspended.")


@router.patch("/users/{user_id}/unsuspend", response_model=ModerationActionResponse)
def admin_unsuspend_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModerationActionResponse:
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
    user = set_user_ban(db, user_id, True, current_user)
    return ModerationActionResponse(message=f"User {user.username} has been banned.")


@router.patch("/users/{user_id}/unban", response_model=ModerationActionResponse)
def admin_unban_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModerationActionResponse:
    user = set_user_ban(db, user_id, False, current_user)
    return ModerationActionResponse(message=f"User {user.username} has been unbanned.")


# ---------------------------------------------------------------------------
# Thread management
# ---------------------------------------------------------------------------


@router.get("/threads", response_model=list[AdminThreadResponse])
def admin_threads(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AdminThreadResponse]:
    return list_threads_for_moderation(db, current_user)


@router.patch("/threads/{thread_id}/lock", response_model=ModerationActionResponse)
def admin_lock_thread(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModerationActionResponse:
    thread = set_thread_lock(db, thread_id, True, current_user)
    return ModerationActionResponse(message=f"Thread '{thread.title}' locked.")


@router.patch("/threads/{thread_id}/unlock", response_model=ModerationActionResponse)
def admin_unlock_thread(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModerationActionResponse:
    thread = set_thread_lock(db, thread_id, False, current_user)
    return ModerationActionResponse(message=f"Thread '{thread.title}' unlocked.")


@router.patch("/threads/{thread_id}/pin", response_model=ModerationActionResponse)
def admin_pin_thread(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModerationActionResponse:
    thread = set_thread_pin(db, thread_id, True, current_user)
    return ModerationActionResponse(message=f"Thread '{thread.title}' pinned.")


@router.patch("/threads/{thread_id}/unpin", response_model=ModerationActionResponse)
def admin_unpin_thread(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModerationActionResponse:
    thread = set_thread_pin(db, thread_id, False, current_user)
    return ModerationActionResponse(message=f"Thread '{thread.title}' unpinned.")


# ---------------------------------------------------------------------------
# Content reports
# ---------------------------------------------------------------------------


@router.get("/reports", response_model=list[AdminReportResponse])
def admin_list_reports(
    status_filter: str | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AdminReportResponse]:
    """List content reports. Admins see all; moderators see their categories."""
    return list_reports(db, current_user, status_filter=status_filter)


@router.patch("/reports/{report_id}/resolve", response_model=ModerationActionResponse)
def admin_resolve_report(
    report_id: int,
    payload: ReportResolveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModerationActionResponse:
    report = resolve_report(db, report_id, payload.status, current_user)
    return ModerationActionResponse(
        message=f"Report #{report.id} marked as {report.status}."
    )


# ---------------------------------------------------------------------------
# Moderation actions (warn / suspend / ban from reports panel)
# ---------------------------------------------------------------------------


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
    """Issue a warn, suspend, or ban against a user (optionally linked to a report)."""
    return create_moderation_action(
        db,
        target_user_id=user_id,
        action_type=payload.action_type,
        reason=payload.reason,
        current_user=current_user,
        duration_hours=payload.duration_hours,
        report_id=payload.report_id,
    )


# ---------------------------------------------------------------------------
# Category moderator assignments (admin only)
# ---------------------------------------------------------------------------


@router.get("/category-moderators/{user_id}", response_model=list[int])
def admin_get_user_categories(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[int]:
    """Return the list of category IDs assigned to a moderator."""
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
    assign_category_moderator(db, payload.user_id, payload.category_id, current_user)
    return ModerationActionResponse(message="Category moderator assigned.")


@router.delete("/category-moderators", response_model=ModerationActionResponse)
def admin_remove_category_moderator(
    payload: CategoryModeratorRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModerationActionResponse:
    remove_category_moderator(db, payload.user_id, payload.category_id, current_user)
    return ModerationActionResponse(message="Category moderator removed.")


# ---------------------------------------------------------------------------
# Category requests (moderator proposes, admin reviews)
# ---------------------------------------------------------------------------


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
    """Moderator submits a request to create a new community."""
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
    """List category requests. Admins see all; mods see their own."""
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
    """Admin approves or rejects a category request."""
    result = review_category_request(db, request_id, payload.status, current_user)

    # If approved, a new category was created — broadcast it
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


# ---------------------------------------------------------------------------
# Audit logs
# ---------------------------------------------------------------------------


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
    """Return paginated audit logs with role-based visibility."""
    return list_audit_logs(
        db,
        current_user,
        page=page,
        page_size=page_size,
        action_filter=action,
        entity_type_filter=entity_type,
        actor_id_filter=actor_id,
    )

from datetime import datetime

from pydantic import BaseModel


class AdminUserResponse(BaseModel):
    id: int
    username: str
    email: str
    role: str
    is_verified: bool
    is_active: bool
    is_suspended: bool
    is_banned: bool
    created_at: datetime
    can_suspend: bool = False
    can_ban: bool = False
    can_change_role: bool = False


class AdminThreadResponse(BaseModel):
    id: int
    title: str
    category: str
    category_id: int = 0
    author: str
    is_locked: bool
    is_pinned: bool
    created_at: datetime


class AdminSummaryResponse(BaseModel):
    users_total: int
    verified_users: int
    suspended_users: int
    banned_users: int
    thread_total: int
    locked_threads: int
    pinned_threads: int
    pending_reports: int = 0


class RoleUpdateRequest(BaseModel):
    role: str


class ModerationActionResponse(BaseModel):
    message: str


class AdminReportResponse(BaseModel):
    id: int
    reporter_id: int
    reporter_username: str
    entity_type: str
    entity_id: int
    reason: str
    status: str
    created_at: datetime
    resolved_by: int | None = None
    resolver_username: str | None = None
    resolved_at: datetime | None = None
    content_snippet: str = ""
    content_author: str = ""
    content_author_id: int = 0
    category_name: str = ""
    category_id: int = 0
    thread_title: str = ""


class ReportResolveRequest(BaseModel):
    status: str  # 'resolved' or 'dismissed'


class ModerationActionRequest(BaseModel):
    action_type: str  # 'warn', 'suspend', 'ban'
    reason: str
    duration_hours: int | None = None
    report_id: int | None = None


class ModerationActionDetailResponse(BaseModel):
    id: int
    moderator_username: str
    target_username: str
    action_type: str
    reason: str
    duration_hours: int | None = None
    report_id: int | None = None
    created_at: datetime


class CategoryModeratorRequest(BaseModel):
    user_id: int
    category_id: int


class CategoryRequestCreate(BaseModel):
    title: str
    slug: str
    description: str = ""


class CategoryRequestResponse(BaseModel):
    id: int
    requester_id: int
    requester_username: str = ""
    title: str
    slug: str
    description: str = ""
    status: str
    reviewed_by: int | None = None
    reviewer_username: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime


class CategoryRequestReviewRequest(BaseModel):
    status: str  # 'approved' or 'rejected'


# ---------------------------------------------------------------------------
# Audit logs
# ---------------------------------------------------------------------------


class AuditLogResponse(BaseModel):
    """Single audit log entry returned to the client."""

    id: int
    actor_id: int | None = None
    actor_username: str = ""
    action: str
    entity_type: str
    entity_id: int
    details: str = ""
    ip_address: str | None = None
    created_at: datetime


class PaginatedAuditLogResponse(BaseModel):
    """Paginated list of audit log entries."""

    items: list[AuditLogResponse]
    total: int
    page: int
    page_size: int
    total_pages: int

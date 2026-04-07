from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from shared.services.sanitize import sanitize_text


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
    role: str = Field(pattern=r"^(admin|moderator|member)$")


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
    status: str = Field(pattern=r"^(resolved|dismissed)$")


class ModerationActionRequest(BaseModel):
    action_type: str = Field(pattern=r"^(warn|suspend|ban)$")
    reason: str = Field(min_length=3, max_length=2000)
    duration_hours: int | None = Field(default=None, ge=1, le=8760)
    report_id: int | None = None

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, v: str) -> str:
        return sanitize_text(v)


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
    user_id: int = Field(ge=1)
    category_id: int = Field(ge=1)


class CategoryRequestCreate(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    slug: str = Field(min_length=3, max_length=120, pattern=r"^[a-z0-9-]+$")
    description: str = Field(default="", max_length=500)

    @field_validator("title")
    @classmethod
    def clean_title(cls, v: str) -> str:
        return sanitize_text(v)

    @field_validator("description")
    @classmethod
    def clean_description(cls, v: str) -> str:
        return sanitize_text(v)


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
    status: str = Field(pattern=r"^(approved|rejected)$")


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

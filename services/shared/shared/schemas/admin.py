"""
Admin, Moderation & Audit Log Schemas
=======================================

This module defines Pydantic models for the admin dashboard, moderation
actions, content reports, category moderation, community requests, and
audit logging.

**Interview Concept: Strict enum validation via regex patterns**

Several fields in this module use ``Field(pattern=r"^(value1|value2)$")``
instead of Python ``Enum`` types.  Why?

1. **Explicit whitelist** — The regex ``^(admin|moderator|member)$`` is
   a *closed set*.  Any value outside this set is rejected with a 422
   error.  This prevents privilege escalation attacks where a user
   sends ``{"role": "superadmin"}`` hoping the server blindly accepts it.

2. **No deserialization ambiguity** — Pydantic Enums can have issues
   with string vs value comparisons.  Regex patterns on string fields
   are unambiguous.

3. **Simpler JSON** — Clients send plain strings (``"admin"``) rather
   than needing to match an Enum's exact casing or naming convention.

**Interview Concept: Principle of Least Privilege in admin APIs**

Every admin action requires specific privileges:
- Only admins can change roles (``RoleUpdateRequest``).
- Only admins and moderators can issue moderation actions
  (``ModerationActionRequest``).
- Audit logs visibility is role-based: admins see everything, moderators
  see their own + member actions, members see only their own.

These schemas define the *shape* of the data; the *authorization checks*
happen in the service/route layer.

**Interview Concept: Audit logging**

``AuditLogResponse`` represents an immutable record of every significant
action in the system (user registration, role changes, content
moderation, etc.).  Audit logs are critical for:
- **Compliance** — Regulatory requirements (GDPR, SOX) often mandate
  action logging.
- **Forensics** — After a security incident, audit logs help
  reconstruct what happened.
- **Accountability** — Moderators know their actions are logged.
"""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from shared.services.sanitize import sanitize_text


class AdminUserResponse(BaseModel):
    """
    User representation for the admin user management table.

    Includes account status flags (``is_suspended``, ``is_banned``) and
    permission flags (``can_suspend``, ``can_ban``, ``can_change_role``)
    that are computed based on the requesting admin's role relative to
    the target user.

    **Why permission flags?**

    Instead of making the frontend compute "can this admin suspend this
    user?", the server pre-computes it.  This prevents the frontend from
    showing action buttons that would fail when clicked (better UX), and
    keeps authorization logic server-side where it belongs.
    """

    id: int
    username: str
    email: str
    role: str
    is_verified: bool
    is_active: bool
    is_suspended: bool
    is_banned: bool
    created_at: datetime
    can_suspend: bool = False  # Can the requesting admin suspend this user?
    can_ban: bool = False  # Can the requesting admin ban this user?
    can_change_role: bool = False  # Can the requesting admin change this user's role?


class AdminThreadResponse(BaseModel):
    """
    Thread representation for the admin thread management table.

    Includes moderation-specific fields like ``is_locked`` and
    ``is_pinned`` that admins can toggle.  ``category_id`` enables
    filtering threads by category in the admin UI.
    """

    id: int
    title: str
    category: str
    category_id: int = 0
    author: str
    is_locked: bool
    is_pinned: bool
    created_at: datetime


class AdminSummaryResponse(BaseModel):
    """
    Dashboard summary statistics for the admin overview panel.

    Provides aggregate counts so the admin can see the system health
    at a glance: total users, verification rate, moderation status,
    and pending reports requiring attention.

    ``pending_reports`` is highlighted in the admin UI to indicate
    work items that need moderator review.
    """

    users_total: int
    verified_users: int
    suspended_users: int
    banned_users: int
    thread_total: int
    locked_threads: int
    pinned_threads: int
    pending_reports: int = 0


class RoleUpdateRequest(BaseModel):
    """
    Schema for changing a user's role (PUT /api/v1/admin/users/{id}/role).

    **Interview Concept: Preventing privilege escalation**

    The regex ``^(admin|moderator|member)$`` is a strict whitelist.
    Without this, an attacker could send ``{"role": "superadmin"}`` or
    ``{"role": "root"}`` and — if the server naively accepted any string —
    gain unintended privileges.  By validating at the schema level, we
    ensure only valid roles reach the business logic.

    The ``^`` and ``$`` anchors are critical: without them, a value like
    ``"not_admin_lol"`` would match because it *contains* "admin".
    """

    role: str = Field(pattern=r"^(admin|moderator|member)$")


class ModerationActionResponse(BaseModel):
    """Generic response confirming a moderation action was performed."""

    message: str


class AdminReportResponse(BaseModel):
    """
    Detailed report representation for the admin moderation queue.

    Includes enriched context fields that help moderators make decisions
    without needing to navigate to the reported content:
    - ``content_snippet`` — Preview of the reported content (first ~200 chars).
    - ``content_author`` — Who created the reported content.
    - ``category_name`` / ``thread_title`` — Where the content lives.
    - ``reporter_username`` — Who filed the report.
    - ``resolver_username`` — Who resolved it (if resolved).

    This "pre-joined" approach reduces the number of clicks and API calls
    needed for a moderator to review a report.
    """

    id: int
    reporter_id: int
    reporter_username: str
    entity_type: str  # "thread", "post", or "user"
    entity_id: int
    reason: str
    status: str  # "pending", "resolved", or "dismissed"
    created_at: datetime
    resolved_by: int | None = None
    resolver_username: str | None = None
    resolved_at: datetime | None = None
    content_snippet: str = ""  # Preview of reported content
    content_author: str = ""  # Author of reported content
    content_author_id: int = 0
    category_name: str = ""  # Category where content was posted
    category_id: int = 0
    thread_title: str = ""  # Thread title (for post reports)


class ReportResolveRequest(BaseModel):
    """
    Schema for resolving a content report.

    Only two valid outcomes: ``"resolved"`` (action was taken against the
    content/user) or ``"dismissed"`` (report was reviewed but no action
    needed).  The strict regex prevents invalid statuses like "pending"
    (which is the initial state, not a resolution) or arbitrary strings.
    """

    status: str = Field(pattern=r"^(resolved|dismissed)$")


class ModerationActionRequest(BaseModel):
    """
    Schema for issuing a moderation action against a user.

    (POST /api/v1/admin/users/{id}/moderate)

    Fields:
    - ``action_type``: Exactly ``"warn"``, ``"suspend"``, or ``"ban"``.
      These map to escalating severity levels:
      * ``warn`` — Notify the user of a rule violation (no restrictions).
      * ``suspend`` — Temporarily restrict the user for ``duration_hours``.
      * ``ban`` — Permanently restrict the user.
    - ``reason``: Why the action was taken (3-2000 chars).  Sanitized
      because it may be displayed to the target user in a notification.
    - ``duration_hours``: Only relevant for suspensions.  ``ge=1``
      prevents zero-length suspensions; ``le=8760`` caps at 1 year
      (365 days * 24 hours) to prevent accidental permanent suspensions.
    - ``report_id``: Optional link to the content report that prompted
      this action.  Enables tracking which reports led to which actions.
    """

    # Strict whitelist: only these three action types are valid.
    # Prevents injection of arbitrary action types like "delete" or "promote".
    action_type: str = Field(pattern=r"^(warn|suspend|ban)$")
    # Reason must be substantive (min 3 chars) to prevent lazy moderation.
    reason: str = Field(min_length=3, max_length=2000)
    # Duration in hours; None for warnings and permanent bans.
    # ge=1: no zero-duration suspensions; le=8760: max 1 year.
    duration_hours: int | None = Field(default=None, ge=1, le=8760)
    report_id: int | None = None

    # -- XSS Prevention for moderation reason --
    # Moderation reasons are shown in notifications to the target user
    # and in the admin audit log.  Both are XSS attack surfaces.
    @field_validator("reason")
    @classmethod
    def clean_reason(cls, v: str) -> str:
        return sanitize_text(v)


class ModerationActionDetailResponse(BaseModel):
    """
    Detailed view of a past moderation action, used in the admin
    moderation history table.

    ``moderator_username`` and ``target_username`` are resolved from
    user IDs for human-readable display.
    """

    id: int
    moderator_username: str  # Who performed the action
    target_username: str  # Who was the target
    action_type: str  # "warn", "suspend", or "ban"
    reason: str
    duration_hours: int | None = None
    report_id: int | None = None  # Linked report (if any)
    created_at: datetime


class CategoryModeratorRequest(BaseModel):
    """
    Schema for assigning/removing a moderator to/from a category.

    Both ``user_id`` and ``category_id`` use ``ge=1`` to ensure valid
    positive IDs.  An ID of 0 or negative would be meaningless and
    could indicate a bug or manipulation attempt.
    """

    user_id: int = Field(ge=1)
    category_id: int = Field(ge=1)


class CategoryRequestCreate(BaseModel):
    """
    Schema for a regular user requesting a new community/category.

    Unlike ``CategoryCreateRequest`` (admin-only), this goes through an
    approval workflow: the request is stored with status "pending" and
    an admin reviews it.

    All fields are sanitized because they'll be displayed in the admin
    review queue.  The ``slug`` uses the same whitelist regex as the
    admin version (``^[a-z0-9-]+$``).
    """

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
    """
    Response schema for a community/category request.

    Includes the review status and (if reviewed) who reviewed it and when.
    The ``status`` field transitions through: "pending" → "approved" or
    "rejected".
    """

    id: int
    requester_id: int
    requester_username: str = ""
    title: str
    slug: str
    description: str = ""
    status: str  # "pending", "approved", or "rejected"
    reviewed_by: int | None = None
    reviewer_username: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime


class CategoryRequestReviewRequest(BaseModel):
    """
    Schema for an admin reviewing a community request.

    Only ``"approved"`` or ``"rejected"`` are valid.  ``"pending"`` is
    the initial state and cannot be set via this endpoint (you can't
    un-review a request).
    """

    status: str = Field(pattern=r"^(approved|rejected)$")


# ---------------------------------------------------------------------------
# Audit Logs
# ---------------------------------------------------------------------------
# Audit logs are immutable records of significant system actions.
# There are no "create" request schemas because audit logs are created
# internally by the service layer — users never directly create audit
# log entries.  Only response schemas are needed.
# ---------------------------------------------------------------------------


class AuditLogResponse(BaseModel):
    """
    Single audit log entry returned to the client.

    **Interview Concept: Immutable audit trail**

    Audit logs are append-only — once created, they cannot be updated
    or deleted (even by admins).  This guarantees the integrity of the
    audit trail for compliance and forensic purposes.

    Fields:
    - ``actor_id`` / ``actor_username`` — Who performed the action.
      ``None`` for system-generated events (e.g., automated cleanup).
    - ``action`` — What happened (e.g., "user.register", "thread.create",
      "admin.role_change", "mod.suspend").  Uses dot-notation for
      namespacing.
    - ``entity_type`` / ``entity_id`` — What was affected (e.g.,
      entity_type="user", entity_id=42).
    - ``details`` — Free-form JSON string with additional context.
    - ``ip_address`` — Client IP for security forensics.
    """

    id: int
    actor_id: int | None = None  # None for system-generated events
    actor_username: str = ""
    action: str  # Dot-notation action name
    entity_type: str  # "user", "thread", "post", etc.
    entity_id: int
    details: str = ""  # Additional context (JSON string)
    ip_address: str | None = None  # Client IP address
    created_at: datetime


class PaginatedAuditLogResponse(BaseModel):
    """
    Paginated list of audit log entries.

    Uses the same pagination pattern as ``PaginatedThreadsResponse``:
    offset-based with ``page``, ``page_size``, ``total``, and
    ``total_pages``.  The admin dashboard uses this to browse through
    potentially thousands of audit entries with filters for ``action``,
    ``entity_type``, and ``actor_id``.
    """

    items: list[AuditLogResponse]
    total: int
    page: int
    page_size: int
    total_pages: int

"""
Audit Log Service — Recording and Querying Audit Trail Entries
===============================================================

INTERVIEW CONTEXT:
    Audit logging is critical for **compliance** (SOC 2, GDPR, HIPAA),
    **security incident response** (who did what and when?), and
    **accountability** in any multi-user platform.  This module provides
    the shared infrastructure for all services to record audit entries.

USED BY:
    - **Core service**: auth events (register, login), profile updates,
      avatar uploads, friend requests
    - **Community service**: forum CRUD (threads, posts), moderation
      actions (warn, suspend, ban), admin operations (role changes,
      category management, report resolution), chat room creation

WHY IN THE SHARED LAYER?
    Both Core and Community services need to write audit log entries
    within the **same database transaction** as their primary operations.
    If the primary operation rolls back, the audit entry should roll back
    too — otherwise we'd have phantom audit records for things that never
    happened.  This is why ``record()`` does NOT call ``db.commit()``;
    the caller commits both the primary operation and the audit entry
    together.

ROLE-BASED VISIBILITY:
    The ``list_audit_logs()`` function enforces access control:
    - **Admin**: sees ALL audit log entries across the entire platform.
    - **Moderator**: sees their own actions + actions by regular members
      (but NOT other moderators' or admins' actions — separation of
      duties).
    - **Member**: sees only their own actions (transparency without
      exposing other users' activity).

    This is a common pattern in enterprise systems called "row-level
    security" — the same table, different views depending on who's
    asking.

ACTION CONSTANTS:
    We define 29 string constants (e.g. ``THREAD_CREATE``, ``USER_BAN``)
    instead of using raw strings throughout the codebase.  Benefits:
    - Typo prevention (IDE autocomplete + import errors catch mistakes)
    - Easy auditing of "what actions do we log?" (all in one place)
    - Frontend can use these same strings for filter dropdowns
"""

import json
import logging
import math
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.models.audit_log import AuditLog
from shared.models.user import User, UserRole
from shared.schemas.admin import AuditLogResponse, PaginatedAuditLogResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Action constants — use these when calling ``record()``.
#
# INTERVIEW NOTE: Using constants instead of raw strings provides:
#   1. Compile-time (import-time) error detection for typos
#   2. IDE autocomplete support
#   3. A single source of truth for all auditable actions
#   4. Easy discovery — "what do we audit?" → look here
#
# Grouped by domain area for readability (29 total).
# ---------------------------------------------------------------------------

# Thread actions — logged by Community service forum routes
THREAD_CREATE = "thread_create"
THREAD_UPDATE = "thread_update"
THREAD_DELETE = "thread_delete"
THREAD_LOCK = "thread_lock"  # Prevents new replies
THREAD_UNLOCK = "thread_unlock"
THREAD_PIN = "thread_pin"  # Sticky at top of category
THREAD_UNPIN = "thread_unpin"

# Post actions — logged by Community service forum routes
POST_CREATE = "post_create"
POST_UPDATE = "post_update"
POST_DELETE = "post_delete"

# User / auth actions — logged by Core service auth routes
USER_REGISTER = "user_register"  # New account creation
USER_LOGIN = "user_login"  # Successful authentication
USER_ROLE_CHANGE = "user_role_change"  # Admin promotes/demotes a user
USER_SUSPEND = "user_suspend"  # Temporary account lockout
USER_UNSUSPEND = "user_unsuspend"
USER_BAN = "user_ban"  # Permanent account lockout
USER_UNBAN = "user_unban"

# Moderation actions — logged by Community service admin routes
MOD_ACTION = "mod_action"  # Generic moderation action
REPORT_CREATE = "report_create"  # User files a content report
REPORT_RESOLVE = "report_resolve"  # Staff resolves/dismisses a report

# Category / community management — logged by Community service admin routes
CATEGORY_CREATE = "category_create"
CATEGORY_REQUEST_CREATE = "category_request_create"  # User requests new category
CATEGORY_REQUEST_REVIEW = "category_request_review"  # Admin approves/rejects
CATEGORY_MOD_ASSIGN = "category_mod_assign"  # Assign moderator to category
CATEGORY_MOD_REMOVE = "category_mod_remove"

# User profile — logged by Core service user routes
USER_PROFILE_UPDATE = "user_profile_update"
USER_AVATAR_UPLOAD = "user_avatar_upload"

# Friends — logged by Core service friend routes
FRIEND_REQUEST_SEND = "friend_request_send"
FRIEND_REQUEST_ACCEPT = "friend_request_accept"
FRIEND_REQUEST_DECLINE = "friend_request_decline"

# Chat — logged by Community service chat routes
CHAT_ROOM_CREATE = "chat_room_create"
CHAT_MESSAGE_SEND = "chat_message_send"


def _encode_details(details: Any) -> str:
    """Normalise *details* to a JSON string for storage.

    INTERVIEW NOTE:
        The ``details`` column stores arbitrary context about an action
        (e.g. "old_role: member, new_role: admin").  We accept dicts,
        strings, or any JSON-serializable value and normalise them all
        to a JSON string.  The ``default=str`` fallback handles types
        like ``datetime`` that aren't natively JSON-serializable.

    Args:
        details: Arbitrary data — dict, string, None, or any
            JSON-serializable value.

    Returns:
        A JSON string representation, or an empty string if None.
    """
    if details is None:
        return ""
    if isinstance(details, str):
        return details
    try:
        return json.dumps(details, default=str)
    except (TypeError, ValueError):
        return str(details)


# ---------------------------------------------------------------------------
# Record — creates a single audit log entry
# ---------------------------------------------------------------------------


def record(
    db: Session,
    *,
    actor_id: int | None,
    action: str,
    entity_type: str,
    entity_id: int,
    details: Any = None,
    ip_address: str | None = None,
) -> AuditLog:
    """Write a single audit log entry into the database.

    INTERVIEW NOTE — TRANSACTION SAFETY:
        This function uses ``db.add()`` but intentionally does NOT call
        ``db.commit()``.  The caller is responsible for committing.
        This design ensures the audit entry lives in the **same
        transaction** as the primary operation:

        .. code-block:: python

            # In a route handler:
            thread = create_thread(db, ...)
            audit.record(db, actor_id=user.id, action=THREAD_CREATE, ...)
            db.commit()  # Both thread + audit entry commit atomically

        If the thread creation fails and rolls back, the audit entry
        rolls back too — no phantom records.

    Args:
        db: Active SQLAlchemy session (caller is responsible for commit).
        actor_id: ``User.id`` of the person who performed the action.
            Can be None for system-initiated actions.
        action: One of the action constants defined above (e.g.
            ``THREAD_CREATE``, ``USER_BAN``).
        entity_type: The kind of entity acted upon — ``'thread'``,
            ``'post'``, ``'user'``, ``'category'``, ``'chat_room'``, etc.
        entity_id: Primary key of the entity that was acted upon.
        details: Optional dict/string with extra context (e.g.
            ``{"old_role": "member", "new_role": "admin"}``).
        ip_address: Optional IP address of the actor (useful for
            security investigations).

    Returns:
        The newly created ``AuditLog`` row (added to session but
        not yet committed).
    """
    entry = AuditLog(
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=_encode_details(details),
        ip_address=ip_address,
    )
    db.add(entry)
    # We intentionally do NOT commit here — the caller should commit after
    # the primary operation so the audit entry is in the same transaction.
    return entry


# ---------------------------------------------------------------------------
# Query — paginated listing with role-based access control
# ---------------------------------------------------------------------------


def list_audit_logs(
    db: Session,
    current_user: User,
    *,
    page: int = 1,
    page_size: int = 25,
    action_filter: str | None = None,
    entity_type_filter: str | None = None,
    actor_id_filter: int | None = None,
) -> PaginatedAuditLogResponse:
    """Return paginated audit logs respecting role-based visibility.

    INTERVIEW NOTE — ROW-LEVEL SECURITY:
        This implements application-level row-level security.  The same
        ``audit_logs`` table is queried, but different users see
        different subsets of rows:

        - **Admin** (``UserRole.ADMIN``): No WHERE filter — sees
          everything.  Admins need full visibility for incident response.
        - **Moderator** (``UserRole.MODERATOR``): Sees their own
          actions + actions performed by regular members.  Cannot see
          other moderators' or admins' actions (separation of duties).
        - **Member** (default): Sees only their own actions.  Provides
          transparency ("here's what you've done") without exposing
          other users' activity.

        This pattern is common in enterprise SaaS applications and is
        sometimes implemented at the database level (PostgreSQL RLS
        policies) instead of the application level.

    Args:
        db: Active SQLAlchemy session.
        current_user: The authenticated user making the request.  Their
            ``role`` determines visibility scope.
        page: 1-indexed page number for pagination.
        page_size: Number of entries per page (default 25).
        action_filter: Optional — filter to a specific action type
            (e.g. ``"thread_create"``).
        entity_type_filter: Optional — filter to a specific entity type
            (e.g. ``"user"``).
        actor_id_filter: Optional — filter to a specific actor's
            actions.

    Returns:
        ``PaginatedAuditLogResponse`` containing items, total count,
        current page, page size, and total pages.

    Side effects:
        Read-only — no database mutations.
    """
    base_q = select(AuditLog)
    count_q = select(func.count(AuditLog.id))

    # --- Role-based scoping ---
    # Apply WHERE clauses based on the current user's role.
    if current_user.role == UserRole.ADMIN:
        pass  # Admin sees all — no filter needed
    elif current_user.role == UserRole.MODERATOR:
        # Moderator visibility: own actions + member actions.
        # Uses a subquery to find all member user IDs, then ORs with
        # the moderator's own ID.
        member_ids_subq = select(User.id).where(User.role == UserRole.MEMBER)
        mod_filter = (AuditLog.actor_id == current_user.id) | (
            AuditLog.actor_id.in_(member_ids_subq)
        )
        base_q = base_q.where(mod_filter)
        count_q = count_q.where(mod_filter)
    else:
        # Members see only their own actions
        base_q = base_q.where(AuditLog.actor_id == current_user.id)
        count_q = count_q.where(AuditLog.actor_id == current_user.id)

    # --- Optional filters (applied on top of role scoping) ---
    if action_filter:
        base_q = base_q.where(AuditLog.action == action_filter)
        count_q = count_q.where(AuditLog.action == action_filter)
    if entity_type_filter:
        base_q = base_q.where(AuditLog.entity_type == entity_type_filter)
        count_q = count_q.where(AuditLog.entity_type == entity_type_filter)
    if actor_id_filter is not None:
        base_q = base_q.where(AuditLog.actor_id == actor_id_filter)
        count_q = count_q.where(AuditLog.actor_id == actor_id_filter)

    # Execute count query first for pagination metadata
    total: int = db.execute(count_q).scalar_one()
    total_pages = max(1, math.ceil(total / page_size))

    # Fetch the page of results, newest first
    logs = (
        db.execute(
            base_q.order_by(AuditLog.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )

    # Batch-load actor usernames to avoid N+1 queries.
    # Instead of lazy-loading user.username for each log entry (N queries),
    # we collect all unique actor IDs and fetch them in a single query.
    actor_ids = {log.actor_id for log in logs if log.actor_id is not None}
    actor_map: dict[int, str] = {}
    if actor_ids:
        actors = db.execute(
            select(User.id, User.username).where(User.id.in_(actor_ids))
        ).all()
        actor_map = {row.id: row.username for row in actors}

    # Build response DTOs
    items = [
        AuditLogResponse(
            id=log.id,
            actor_id=log.actor_id,
            actor_username=actor_map.get(log.actor_id, "") if log.actor_id else "",
            action=log.action,
            entity_type=log.entity_type,
            entity_id=log.entity_id,
            details=log.details,
            ip_address=log.ip_address,
            created_at=log.created_at,
        )
        for log in logs
    ]

    return PaginatedAuditLogResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )

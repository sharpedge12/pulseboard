"""Audit log service — recording and querying audit trail entries.

This module lives in the shared library so any service (Core, Community)
can record audit entries.  The query helpers enforce role-based visibility:
  * Admins see all logs.
  * Moderators see moderator-level + member-level actions.
  * Members see only their own actions.
"""

import json
import logging
import math
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.models.audit_log import AuditLog
from shared.models.user import User, UserRole
from shared.schemas.admin import AuditLogResponse, PaginatedAuditLogResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Action constants — use these when calling ``record()``.
# ---------------------------------------------------------------------------

# Thread actions
THREAD_CREATE = "thread_create"
THREAD_UPDATE = "thread_update"
THREAD_DELETE = "thread_delete"
THREAD_LOCK = "thread_lock"
THREAD_UNLOCK = "thread_unlock"
THREAD_PIN = "thread_pin"
THREAD_UNPIN = "thread_unpin"

# Post actions
POST_CREATE = "post_create"
POST_UPDATE = "post_update"
POST_DELETE = "post_delete"

# User / auth actions
USER_REGISTER = "user_register"
USER_LOGIN = "user_login"
USER_ROLE_CHANGE = "user_role_change"
USER_SUSPEND = "user_suspend"
USER_UNSUSPEND = "user_unsuspend"
USER_BAN = "user_ban"
USER_UNBAN = "user_unban"

# Moderation
MOD_ACTION = "mod_action"
REPORT_CREATE = "report_create"
REPORT_RESOLVE = "report_resolve"

# Category / community
CATEGORY_CREATE = "category_create"
CATEGORY_REQUEST_CREATE = "category_request_create"
CATEGORY_REQUEST_REVIEW = "category_request_review"
CATEGORY_MOD_ASSIGN = "category_mod_assign"
CATEGORY_MOD_REMOVE = "category_mod_remove"

# User profile
USER_PROFILE_UPDATE = "user_profile_update"
USER_AVATAR_UPLOAD = "user_avatar_upload"

# Friends
FRIEND_REQUEST_SEND = "friend_request_send"
FRIEND_REQUEST_ACCEPT = "friend_request_accept"
FRIEND_REQUEST_DECLINE = "friend_request_decline"

# Chat
CHAT_ROOM_CREATE = "chat_room_create"
CHAT_MESSAGE_SEND = "chat_message_send"


def _encode_details(details: Any) -> str:
    """Normalise *details* to a JSON string."""
    if details is None:
        return ""
    if isinstance(details, str):
        return details
    try:
        return json.dumps(details, default=str)
    except (TypeError, ValueError):
        return str(details)


# ---------------------------------------------------------------------------
# Record
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
    """Write a single audit log entry.

    Args:
        db: Active SQLAlchemy session (caller is responsible for commit).
        actor_id: ``User.id`` of the person who performed the action.
        action: One of the action constants defined above.
        entity_type: The kind of entity acted upon (``'thread'``, ``'user'``, …).
        entity_id: Primary key of the entity.
        details: Optional dict/string with extra context.
        ip_address: Optional IP of the actor.

    Returns:
        The newly created ``AuditLog`` row (not yet committed).
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
# Query
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

    Visibility rules:
      * **Admin**: sees everything.
      * **Moderator**: sees own actions + actions by members.
      * **Member**: sees only own actions.
    """
    base_q = select(AuditLog)
    count_q = select(func.count(AuditLog.id))

    # Role-based scoping
    if current_user.role == UserRole.ADMIN:
        pass  # no filter
    elif current_user.role == UserRole.MODERATOR:
        # Moderator sees own actions and actions by members
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

    # Optional filters
    if action_filter:
        base_q = base_q.where(AuditLog.action == action_filter)
        count_q = count_q.where(AuditLog.action == action_filter)
    if entity_type_filter:
        base_q = base_q.where(AuditLog.entity_type == entity_type_filter)
        count_q = count_q.where(AuditLog.entity_type == entity_type_filter)
    if actor_id_filter is not None:
        base_q = base_q.where(AuditLog.actor_id == actor_id_filter)
        count_q = count_q.where(AuditLog.actor_id == actor_id_filter)

    total: int = db.execute(count_q).scalar_one()
    total_pages = max(1, math.ceil(total / page_size))

    logs = (
        db.execute(
            base_q.order_by(AuditLog.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )

    # Batch-load actor usernames
    actor_ids = {log.actor_id for log in logs if log.actor_id is not None}
    actor_map: dict[int, str] = {}
    if actor_ids:
        actors = db.execute(
            select(User.id, User.username).where(User.id.in_(actor_ids))
        ).all()
        actor_map = {row.id: row.username for row in actors}

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

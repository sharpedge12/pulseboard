"""
Audit Log Model — Compliance and Accountability Trail
=======================================================

Database table defined here:
    - "audit_logs" -> AuditLog (immutable record of significant actions)

WHAT IS AN AUDIT LOG?
    An audit log (also called an "audit trail") is an immutable, append-only
    record of every significant action performed in the system. It answers
    the fundamental security/compliance questions:
      - WHO did it?        (actor_id)
      - WHAT did they do?  (action)
      - WHAT was affected? (entity_type + entity_id)
      - WHEN did it happen? (created_at from TimestampMixin)
      - WHERE were they?   (ip_address)
      - WHY / HOW?         (details)

    This is a legal and compliance requirement in many industries (healthcare,
    finance, government). Even for a forum, audit logs are essential for:
      1. Moderator accountability — "who banned this user and why?"
      2. Debugging — "what actions led to this broken state?"
      3. Security investigation — "what did the compromised account do?"

WHY IMMUTABLE / APPEND-ONLY?
    Audit logs should NEVER be updated or deleted (in production). If someone
    can modify the audit trail, it loses its value as evidence. In PulseBoard,
    audit log rows are only ever INSERTed, never UPDATed or DELETEd.

    In production systems, you'd enforce this with:
      - Database-level: REVOKE UPDATE, DELETE ON audit_logs FROM app_user;
      - Application-level: No update/delete endpoints or service methods.
      - Infrastructure-level: Ship logs to an external, tamper-proof store
        (e.g., AWS CloudTrail, Splunk, ELK stack).

THE ACTOR/ACTION/ENTITY PATTERN:
    This is a standard audit log schema used across the industry:
      - actor_id:    The user who performed the action (FK to users).
      - action:      A short, machine-readable identifier like "thread_create",
                     "user_ban", "role_change". Used for filtering and grouping.
      - entity_type: The type of thing acted upon ("thread", "post", "user").
      - entity_id:   The PK of the entity acted upon.
      - details:     Free-form context (JSON or text).
      - ip_address:  The actor's IP address at the time.

    This design is similar to the polymorphic entity pattern in Vote/Reaction
    (entity_type + entity_id), but for a completely different purpose: recording
    history rather than user interactions.

ROLE-BASED VISIBILITY:
    Not everyone can see all audit logs:
      - admin:     Sees ALL audit log entries.
      - moderator: Sees their own actions + member actions.
      - member:    Sees only their own actions.
    This is enforced at the API layer (list_audit_logs service function).

ACTIONS TRACKED (29 action constants in services/shared/shared/services/audit.py):
    Auth: user_register, user_login
    Forum: thread_create, thread_update, thread_delete, post_create, post_update,
           post_delete
    Admin: role_change, user_suspend, user_ban, thread_lock, thread_pin,
           thread_unlock, thread_unpin, report_resolve, report_dismiss,
           mod_action_create, category_create, category_update, category_delete,
           category_request_approve, category_request_reject
    Chat: chat_room_create
    User: profile_update, avatar_upload, friend_request_send, friend_request_accept,
          friend_request_decline
"""

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.core.database import Base
from shared.models.base import TimestampMixin


class AuditLog(TimestampMixin, Base):
    """
    Immutable record of a significant action performed in the system.

    Database table: "audit_logs"

    Each row captures one discrete action (e.g., "admin promoted user 42 to
    moderator from IP 192.168.1.1 at 2024-01-15 14:30:00").

    Relationships:
        - actor: The user who performed the action (many-to-one, nullable).
                 Nullable because some actions are performed by the SYSTEM
                 (e.g., automated cleanup jobs, seed scripts) without a
                 human actor.

    DESIGN NOTES:
        - entity_id is a plain Integer (not a foreign key) because it can
          reference rows in ANY table (threads, posts, users, etc.). This is
          the same polymorphic pattern used in Vote and ContentReport.
        - details is a Text field (not JSON) for maximum flexibility. Some
          entries store JSON strings, others store plain text descriptions.
          The application layer parses it as needed.
        - ip_address uses String(45) to accommodate both IPv4 ("192.168.1.1",
          max 15 chars) and IPv6 ("2001:0db8:85a3:0000:0000:8a2e:0370:7334",
          max 45 chars).

    INTERVIEW TIP:
        If asked "how would you scale this?", consider:
          1. Partitioning: partition by created_at (monthly) so old logs can
             be archived or moved to cold storage.
          2. Indexing: composite index on (entity_type, entity_id) for fast
             lookups like "show all actions on thread 42".
          3. Externalization: ship logs to a dedicated logging service
             (ELK, Datadog) for search, alerting, and retention policies.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)

    # The user who performed the action. Nullable for system-initiated actions
    # (e.g., automated cleanup, seed scripts). SET NULL on user deletion: the
    # audit record survives even if the actor's account is later deleted. This
    # is critical — you must NEVER lose audit history when a user is removed.
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Machine-readable action identifier, e.g., "thread_create", "user_ban".
    # String(60) accommodates descriptive action names. NOT an enum because
    # new action types are added frequently as features are built — an enum
    # would require a database migration for each new action.
    action: Mapped[str] = mapped_column(String(60))

    # The type of entity acted upon: "thread", "post", "user", "category", etc.
    # Combined with entity_id, this uniquely identifies the target of the action.
    entity_type: Mapped[str] = mapped_column(String(30))

    # The primary key of the entity acted upon. NOT a foreign key because it
    # could reference any table (polymorphic). The actual referenced row might
    # also be deleted by the time the audit log is reviewed — that's fine,
    # the audit record still documents what happened.
    entity_id: Mapped[int] = mapped_column(Integer)

    # Free-form details providing context. Examples:
    #   - "Role changed from member to moderator"
    #   - '{"old_title": "...", "new_title": "..."}'
    #   - "Reason: spam in multiple threads"
    # Defaults to empty string (not NULL) so code can safely concatenate/check
    # without None guards.
    details: Mapped[str] = mapped_column(Text, default="")

    # The IP address of the actor at the time of the action. Useful for:
    #   1. Security investigation (detect account compromise from unusual IPs)
    #   2. Regulatory compliance (some jurisdictions require IP logging)
    # Nullable because background jobs and system actions don't have an IP.
    # String(45) fits both IPv4 (max 15 chars) and IPv6 (max 45 chars).
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    # Relationship to the User who performed the action. foreign_keys is
    # explicitly specified because there's only one FK in this table, but
    # it's good practice for clarity (and required if more FKs were added later).
    actor = relationship("User", foreign_keys=[actor_id])

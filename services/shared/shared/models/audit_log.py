"""Audit log model for tracking all significant user and system actions."""

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.core.database import Base
from shared.models.base import TimestampMixin


class AuditLog(TimestampMixin, Base):
    """Records significant actions performed by users, moderators, and admins.

    Attributes:
        actor_id: The user who performed the action (nullable for system actions).
        action: Short action identifier, e.g. 'thread_create', 'user_ban'.
        entity_type: The type of entity acted upon, e.g. 'thread', 'post', 'user'.
        entity_id: The primary key of the entity acted upon.
        details: Free-form text with extra context (JSON-encoded or plain).
        ip_address: Optional IP address of the actor at time of action.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[int] = mapped_column(Integer)
    details: Mapped[str] = mapped_column(Text, default="")
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    actor = relationship("User", foreign_keys=[actor_id])

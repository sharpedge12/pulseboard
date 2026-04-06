from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.core.database import Base
from shared.models.base import TimestampMixin


class Vote(TimestampMixin, Base):
    """Up/down vote on a thread or post. Value is +1 or -1."""

    __tablename__ = "votes"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "entity_type", "entity_id", name="uq_vote_user_entity"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    entity_type: Mapped[str] = mapped_column(String(10))  # 'thread' or 'post'
    entity_id: Mapped[int] = mapped_column(Integer)
    value: Mapped[int] = mapped_column(SmallInteger)  # +1 or -1


class Reaction(TimestampMixin, Base):
    """Emoji reaction on a thread or post."""

    __tablename__ = "reactions"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "entity_type",
            "entity_id",
            "emoji",
            name="uq_reaction_user_entity_emoji",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    entity_type: Mapped[str] = mapped_column(String(10))  # 'thread' or 'post'
    entity_id: Mapped[int] = mapped_column(Integer)
    emoji: Mapped[str] = mapped_column(String(32))


class ContentReport(TimestampMixin, Base):
    """Report a thread or post for moderation review."""

    __tablename__ = "content_reports"
    __table_args__ = (
        UniqueConstraint(
            "reporter_id",
            "entity_type",
            "entity_id",
            name="uq_report_user_entity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    entity_type: Mapped[str] = mapped_column(String(10))  # 'thread' or 'post'
    entity_id: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending/resolved/dismissed
    resolved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    reporter = relationship("User", foreign_keys=[reporter_id])
    resolver = relationship("User", foreign_keys=[resolved_by])


class ModerationAction(TimestampMixin, Base):
    """Records moderation actions (warn, suspend, ban) taken against users."""

    __tablename__ = "moderation_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    moderator_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    target_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    action_type: Mapped[str] = mapped_column(String(20))  # warn/suspend/ban
    reason: Mapped[str] = mapped_column(Text)
    duration_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    report_id: Mapped[int | None] = mapped_column(
        ForeignKey("content_reports.id", ondelete="SET NULL"), nullable=True
    )

    moderator = relationship("User", foreign_keys=[moderator_id])
    target_user = relationship("User", foreign_keys=[target_user_id])
    report = relationship("ContentReport")


class CategoryModerator(TimestampMixin, Base):
    """Junction table scoping moderators to specific categories."""

    __tablename__ = "category_moderators"
    __table_args__ = (
        UniqueConstraint("user_id", "category_id", name="uq_category_moderator"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE")
    )

    user = relationship("User")
    category = relationship("Category")


class CategoryRequest(TimestampMixin, Base):
    """Request from a moderator to create a new category/community."""

    __tablename__ = "category_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    requester_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending / approved / rejected
    reviewed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    requester = relationship("User", foreign_keys=[requester_id])
    reviewer = relationship("User", foreign_keys=[reviewed_by])

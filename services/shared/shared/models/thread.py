from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.core.database import Base
from shared.models.base import TimestampMixin


class Thread(TimestampMixin, Base):
    __tablename__ = "threads"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE")
    )
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(255), index=True)
    body: Mapped[str] = mapped_column(Text)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)

    category = relationship("Category", back_populates="threads")
    author = relationship("User", back_populates="threads")
    posts = relationship("Post", back_populates="thread", cascade="all, delete-orphan")
    subscriptions = relationship(
        "ThreadSubscription", back_populates="thread", cascade="all, delete-orphan"
    )
    tags = relationship("Tag", secondary="thread_tags", back_populates="threads")


class ThreadSubscription(Base):
    __tablename__ = "thread_subscriptions"
    __table_args__ = (
        UniqueConstraint("thread_id", "user_id", name="uq_thread_subscription"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    thread = relationship("Thread", back_populates="subscriptions")
    user = relationship("User", back_populates="thread_subscriptions")

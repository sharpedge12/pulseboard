from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.core.database import Base
from shared.models.base import TimestampMixin


class Tag(TimestampMixin, Base):
    """A tag that can be applied to threads."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True, index=True)

    threads = relationship("Thread", secondary="thread_tags", back_populates="tags")


class ThreadTag(Base):
    """Junction table linking threads to tags."""

    __tablename__ = "thread_tags"
    __table_args__ = (UniqueConstraint("thread_id", "tag_id", name="uq_thread_tag"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"))
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"))

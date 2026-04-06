from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.core.database import Base
from shared.models.base import TimestampMixin


class Post(TimestampMixin, Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"))
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    parent_post_id: Mapped[int | None] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"), nullable=True
    )
    body: Mapped[str] = mapped_column(Text)

    thread = relationship("Thread", back_populates="posts")
    author = relationship("User", back_populates="posts")
    parent_post = relationship("Post", remote_side=[id], back_populates="replies")
    replies = relationship(
        "Post", back_populates="parent_post", cascade="all, delete-orphan"
    )

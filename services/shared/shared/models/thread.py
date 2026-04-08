"""
Thread & Thread Subscription Models
=====================================

Database tables defined here:
    - "threads"              -> Thread (a discussion topic / top-level post)
    - "thread_subscriptions" -> ThreadSubscription (users following a thread)

FORUM DATA MODEL:
    The discussion hierarchy is: Category -> Thread -> Post (replies).
      - A Category is a community/subforum (like a subreddit).
      - A Thread is a discussion topic within a category (like a Reddit post).
      - Posts are replies within a thread (like Reddit comments).

    This file defines the middle layer: Thread. Each thread belongs to exactly
    one category (many-to-one) and has zero or more posts (one-to-many).

THREAD SUBSCRIPTIONS:
    When a user creates a thread or replies to one, they're automatically
    subscribed. Subscribers receive notifications when new replies are posted.
    The subscription table is a many-to-many junction between users and threads.
"""

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.core.database import Base
from shared.models.base import TimestampMixin


class Thread(TimestampMixin, Base):
    """
    A discussion thread — the main unit of content in PulseBoard.

    Database table: "threads"

    A thread is analogous to a Reddit post or a forum topic. It has a title,
    body, belongs to a category, and is authored by a user. Users reply to
    threads by creating Post objects.

    Relationships:
        - category:      The category this thread belongs to (many-to-one)
        - author:        The user who created this thread (many-to-one)
        - posts:         All replies/comments in this thread (one-to-many)
        - subscriptions: Users subscribed to this thread for notifications
        - tags:          Tags applied to this thread (many-to-many via thread_tags)

    MODERATION FLAGS:
        is_locked: Prevents new replies. Used when a discussion becomes toxic
                   or off-topic. The thread remains visible but read-only.
        is_pinned: "Stickies" the thread to the top of its category. Used for
                   announcements, rules, FAQs. Multiple threads can be pinned.
    """

    __tablename__ = "threads"

    id: Mapped[int] = mapped_column(primary_key=True)

    # ---- Foreign Keys -------------------------------------------------------
    # WHAT IS A FOREIGN KEY?
    #   A foreign key is a column that references the primary key of another
    #   table. It enforces REFERENTIAL INTEGRITY — you can't create a thread
    #   in a category that doesn't exist, and you can't create a thread by a
    #   user that doesn't exist.
    #
    # WHAT IS ondelete="CASCADE"?
    #   CASCADE means: if the referenced row is deleted, delete this row too.
    #   If a category is deleted, all threads in it are automatically deleted.
    #   If a user is deleted, all their threads are deleted. This is handled
    #   by the DATABASE ENGINE, not Python — it's a constraint in the DDL.
    #
    #   Alternative ondelete behaviors:
    #     - SET NULL: set the FK column to NULL (useful for optional FKs)
    #     - RESTRICT: prevent the parent row from being deleted
    #     - NO ACTION: similar to RESTRICT but checked at transaction end

    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE")
    )
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # ---- Content Fields -----------------------------------------------------
    # Title is indexed for search functionality (LIKE '%query%' or full-text).
    # String(255) is a practical limit — titles longer than 255 chars are unusual
    # and would break UI layouts.
    title: Mapped[str] = mapped_column(String(255), index=True)

    # Body uses Text type (unlimited length) because thread bodies can contain
    # long, detailed content with formatting, code blocks, etc.
    body: Mapped[str] = mapped_column(Text)

    # ---- Moderation Flags ---------------------------------------------------
    # Both default to False — threads start unlocked and unpinned.
    # These are toggled by moderators/admins via the admin dashboard.
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)

    # ---- ORM Relationships --------------------------------------------------
    # back_populates creates a bidirectional link. For example:
    #   thread.category returns the Category object
    #   category.threads returns all Thread objects in that category
    # SQLAlchemy handles the SQL JOINs under the hood.
    category = relationship("Category", back_populates="threads")
    author = relationship("User", back_populates="threads")

    # cascade="all, delete-orphan": when a thread is deleted, all its posts are
    # deleted too. "delete-orphan" means a Post that is removed from
    # thread.posts (unlinked in Python) is also deleted from the database.
    posts = relationship("Post", back_populates="thread", cascade="all, delete-orphan")
    subscriptions = relationship(
        "ThreadSubscription", back_populates="thread", cascade="all, delete-orphan"
    )

    # Many-to-many with Tag via the "thread_tags" junction table.
    # secondary="thread_tags" tells SQLAlchemy to use the ThreadTag table as
    # the intermediary. You can then do thread.tags to get all Tags, or
    # tag.threads to get all Threads with that tag.
    tags = relationship("Tag", secondary="thread_tags", back_populates="threads")


class ThreadSubscription(Base):
    """
    Junction table tracking which users are subscribed to which threads.

    Database table: "thread_subscriptions"

    WHAT IS A JUNCTION TABLE?
        A junction table (also called a "bridge table" or "association table")
        implements a many-to-many relationship in a relational database. A user
        can subscribe to many threads, and a thread can have many subscribers.
        This can't be represented with a single foreign key — you need a
        separate table with two foreign keys.

        Each row means: "User X is subscribed to Thread Y".

    WHY THE UNIQUE CONSTRAINT?
        UniqueConstraint("thread_id", "user_id") prevents a user from
        subscribing to the same thread twice. Without this, the application
        would need to check for duplicates before every insert, which is
        error-prone and race-condition-susceptible. The database constraint
        is the ultimate safety net — a duplicate INSERT will fail with a
        database error.

    WHY NO TimestampMixin?
        This table doesn't inherit TimestampMixin because we don't need to
        track when a subscription was created or updated. It's a simple
        boolean relationship: either the subscription exists or it doesn't.

    Relationships:
        - thread: The thread being subscribed to
        - user:   The subscriber
    """

    __tablename__ = "thread_subscriptions"

    # __table_args__ is SQLAlchemy's way of adding table-level constraints.
    # The UniqueConstraint is expressed as a composite unique index on the
    # (thread_id, user_id) pair. Named constraints (name="uq_thread_subscription")
    # are important for migrations — Alembic and ALTER TABLE need to reference
    # constraints by name to drop or modify them.
    __table_args__ = (
        UniqueConstraint("thread_id", "user_id", name="uq_thread_subscription"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Both FKs use CASCADE: if the thread or user is deleted, the subscription
    # is automatically cleaned up.
    thread_id: Mapped[int] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    thread = relationship("Thread", back_populates="subscriptions")
    user = relationship("User", back_populates="thread_subscriptions")

"""
Tag & ThreadTag Models — Many-to-Many Tagging System
======================================================

Database tables defined here:
    - "tags"        -> Tag (a label like "python", "docker", "beginner")
    - "thread_tags" -> ThreadTag (junction table linking threads to tags)

TAGGING CONCEPT:
    Tags are labels that users apply to threads to categorize and aid discovery.
    Unlike categories (which are hierarchical and admin-controlled), tags are
    flat and user-driven. A thread can have multiple tags, and a tag can be
    applied to multiple threads.

    Examples:
        Thread: "How to deploy FastAPI with Docker"
          Tags: [python, fastapi, docker, deployment]

        Thread: "React hooks best practices"
          Tags: [react, javascript, frontend]

MANY-TO-MANY RELATIONSHIP PATTERN:
    In relational databases, a many-to-many relationship CANNOT be represented
    with a single foreign key (a FK only handles one-to-many). You need a
    JUNCTION TABLE (also called an "association table" or "bridge table") that
    sits between the two tables:

        tags  <-->>  thread_tags  <<-->  threads

    Each row in thread_tags represents one tag-thread pair. To find all tags
    for a thread, you JOIN through thread_tags:
        SELECT t.name FROM tags t
        JOIN thread_tags tt ON t.id = tt.tag_id
        WHERE tt.thread_id = 42;

    SQLAlchemy abstracts this with secondary="thread_tags" on the relationship
    definition, so in Python you just access thread.tags directly.

ALTERNATIVE APPROACHES (for interview discussion):
    1. JSON array on Thread: tags = ["python", "docker"]. Simple but loses
       referential integrity, indexing, and JOIN capabilities.
    2. PostgreSQL ARRAY type: Similar to JSON but with array operators. Still
       lacks FK constraints and efficient indexing of individual elements.
    3. Denormalized tag string: tags = "python,docker,fastapi". Even worse —
       searching requires LIKE patterns and parsing.
    The junction table approach is the correct relational design.
"""

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.core.database import Base
from shared.models.base import TimestampMixin


class Tag(TimestampMixin, Base):
    """
    A tag/label that can be applied to threads for categorization and discovery.

    Database table: "tags"

    Tags are global (not scoped to a category) and reusable across all threads.
    The unique constraint on `name` ensures each tag exists exactly once — when
    a user types "python" on a new thread, we look up the existing Tag row
    (or create one if it doesn't exist), rather than creating duplicates.

    Relationships:
        - threads: All threads that have this tag (many-to-many via thread_tags).
                   Accessed via tag.threads in Python.

    DESIGN DECISIONS:
        - name is unique + indexed: unique prevents duplicate tags like "Python"
          and "python" (application layer normalizes to lowercase before insert).
          Indexed for fast lookups when the user types a tag name.
        - String(60): tag names should be concise. 60 chars accommodates
          hyphenated compound tags like "object-oriented-programming".
    """

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)

    # The tag's display name, e.g., "python", "fastapi", "beginner".
    # Unique prevents duplicates; index enables fast autocomplete/search.
    name: Mapped[str] = mapped_column(String(60), unique=True, index=True)

    # Many-to-many relationship with Thread via the thread_tags junction table.
    # secondary="thread_tags" tells SQLAlchemy to use the ThreadTag table as
    # the intermediary. back_populates="tags" links this to Thread.tags.
    # SQLAlchemy generates the JOIN SQL automatically when you access tag.threads.
    threads = relationship("Thread", secondary="thread_tags", back_populates="tags")


class ThreadTag(Base):
    """
    Junction table implementing the many-to-many relationship between
    threads and tags.

    Database table: "thread_tags"

    Each row means: "Thread X has Tag Y". The composite unique constraint on
    (thread_id, tag_id) prevents applying the same tag to the same thread twice.

    WHY NO TimestampMixin?
        This is a pure association table — it only stores the relationship
        between a thread and a tag. There's no meaningful "created_at" or
        "updated_at" for this association. Some designs do track when a tag
        was applied, but PulseBoard doesn't need that.

    WHY A SEPARATE MODEL (not just a SQLAlchemy Table)?
        SQLAlchemy offers two ways to define junction tables:
          1. A Table object: thread_tags = Table("thread_tags", Base.metadata, ...)
          2. A full model class (what we use here): class ThreadTag(Base): ...

        Using a model class is preferred when:
          - You might want to add extra columns later (e.g., "tagged_by" user ID)
          - You want to query the junction table directly (e.g., count how many
            threads use each tag)
          - You want ORM features like mapped_column for type safety

    Relationships:
        - None defined explicitly. Navigation happens through Tag.threads and
          Thread.tags (which use secondary="thread_tags" to go through this table).
    """

    __tablename__ = "thread_tags"

    # Composite unique constraint: each (thread, tag) pair can appear at most once.
    # Named for migration tool compatibility.
    __table_args__ = (UniqueConstraint("thread_id", "tag_id", name="uq_thread_tag"),)

    id: Mapped[int] = mapped_column(primary_key=True)

    # FK to threads table. CASCADE: if a thread is deleted, its tag associations
    # are removed (but the Tag row itself survives — it might be used by other
    # threads).
    thread_id: Mapped[int] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"))

    # FK to tags table. CASCADE: if a tag is deleted, all its thread associations
    # are removed (but the Thread rows survive — they just lose that tag).
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"))

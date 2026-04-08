"""
Category Model — Forum Communities / Sub-forums
=================================================

Database table defined here:
    - "categories" -> Category (a forum section/community, like a subreddit)

WHAT IS A CATEGORY?
    A category is a topic area that groups related threads. Examples:
      - "General Discussion"
      - "Backend Engineering"
      - "Help and Support"

    In Reddit terms, a category is like a subreddit. In traditional forum
    software, it's called a "board" or "sub-forum". The hierarchy is:
        Category (1) -->> Thread (many) -->> Post (many)

    Categories are created by admins (or via approved CategoryRequests from
    moderators). Each category has:
      - A human-readable title ("Backend Engineering")
      - A URL-safe slug ("backend-engineering")
      - An optional description

SLUG CONCEPT (important for interviews):
    A "slug" is a URL-friendly version of a title. Instead of:
        /categories/5              (opaque, meaningless to users)
        /categories/Backend%20Eng  (ugly, has encoded spaces)
    We use:
        /categories/backend-engineering  (clean, SEO-friendly, readable)

    Slugs are generated from titles by lowercasing, replacing spaces with
    hyphens, and removing special characters. They must be UNIQUE because
    they're used as identifiers in URLs.

    The slug is stored as a separate column (not computed on-the-fly) because:
      1. It's used in WHERE clauses — needs to be indexed for fast lookups.
      2. The slug generation algorithm might change, but existing URLs must
         continue to work (so we persist the slug at creation time).
"""

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.core.database import Base
from shared.models.base import TimestampMixin


class Category(TimestampMixin, Base):
    """
    A forum category/community — the top-level organizational unit for threads.

    Database table: "categories"

    Relationships:
        - threads: All threads in this category (one-to-many).
                   Accessed via category.threads in Python.

    DESIGN NOTES:
        - title and slug are both UNIQUE + INDEXED. Unique prevents duplicate
          categories; indexed enables O(log n) lookups for both human-readable
          names and URL slugs.
        - description is optional (nullable) because a category's purpose might
          be self-evident from the title.
        - Inherits created_at and updated_at from TimestampMixin.

    INTERVIEW: ONE-TO-MANY RELATIONSHIP
        The relationship between Category and Thread is ONE-TO-MANY:
          - ONE category has MANY threads.
          - Each thread belongs to exactly ONE category.
        This is implemented via the category_id foreign key on the threads
        table (defined in thread.py). The FK is on the "many" side (threads),
        not the "one" side (categories). This is a fundamental rule of
        relational database design.
    """

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)

    # The display name shown in the UI. unique=True prevents duplicate
    # categories (you don't want two "General Discussion" categories).
    # index=True enables fast lookups and sorting.
    # String(120) balances flexibility with sanity — 120 chars is plenty for
    # a category name.
    title: Mapped[str] = mapped_column(String(120), unique=True, index=True)

    # The URL-safe slug, e.g., "backend-engineering". See module docstring
    # for why slugs exist. Both unique and indexed for the same reasons as title.
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)

    # Optional longer description displayed in the category's sidebar/header.
    # Text type because descriptions can be multi-paragraph. Nullable because
    # not every category needs a description.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # One-to-many relationship: one category has many threads.
    # back_populates="category" means Thread.category navigates in the
    # reverse direction (from a thread to its category).
    threads = relationship("Thread", back_populates="category")

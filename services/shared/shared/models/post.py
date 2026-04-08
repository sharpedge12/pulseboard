"""
Post Model — Threaded/Nested Comments (Reddit-Style Comment Trees)
===================================================================

Database table defined here:
    - "posts" -> Post (a reply/comment within a thread)

THE COMMENT TREE PROBLEM:
    In a flat comment system (like early YouTube), all comments are at the same
    level. But platforms like Reddit, Hacker News, and PulseBoard support NESTED
    comments — you can reply to a reply, creating a tree structure:

        Thread: "How do I learn Python?"
        ├── Post A: "Start with the official tutorial"
        │   ├── Post B: "I second this — it's great"
        │   └── Post C: "Also check out Automate the Boring Stuff"
        │       └── Post D: "That book changed my career"
        └── Post E: "Try building a small project"

    To model this tree in a relational database, we use a SELF-REFERENTIAL
    FOREIGN KEY: each Post has an optional parent_post_id that points to
    another Post in the SAME table. This is called the "adjacency list" pattern.

ADJACENCY LIST PATTERN:
    - If parent_post_id is NULL → this is a top-level reply to the thread.
    - If parent_post_id is set → this is a reply to another post.

    Pros:
      + Simple schema — just one nullable FK column.
      + Easy to add new replies (just INSERT with the parent's ID).
      + Works with any depth of nesting.

    Cons:
      - Fetching an entire subtree requires recursive queries (or multiple
        queries). In PostgreSQL, you'd use a recursive CTE:
            WITH RECURSIVE tree AS (
                SELECT * FROM posts WHERE thread_id = 42 AND parent_post_id IS NULL
                UNION ALL
                SELECT p.* FROM posts p JOIN tree t ON p.parent_post_id = t.id
            )
      - For PulseBoard, we load all posts for a thread in one query and build
        the tree in Python, which is simpler and works fine for threads with
        < 10,000 posts.

    Alternatives (for interview discussion):
      - Materialized Path: store the full path as a string, e.g. "/1/3/7".
        Fast subtree queries (WHERE path LIKE '/1/3/%') but path updates
        when moving nodes are expensive.
      - Nested Sets: store left/right boundary numbers. Very fast reads but
        expensive writes (inserting a node renumbers all siblings).
      - Closure Table: a separate table storing all ancestor-descendant pairs.
        Fast for both reads and writes but uses more storage.

    The adjacency list is the right choice here because:
      1. We rarely need to query deep subtrees — we load all posts per thread.
      2. Inserts are O(1) — just set the parent_post_id.
      3. The schema is trivially simple to understand and maintain.
"""

from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.core.database import Base
from shared.models.base import TimestampMixin


class Post(TimestampMixin, Base):
    """
    A reply/comment within a thread, supporting arbitrary nesting depth.

    Database table: "posts"

    Each post belongs to exactly one thread and optionally has a parent post.
    Together, the posts in a thread form a tree (forest, technically — multiple
    root-level replies).

    Relationships:
        - thread:      The thread this post belongs to (many-to-one)
        - author:      The user who wrote this post (many-to-one)
        - parent_post: The post this is a reply to, if any (self-referential)
        - replies:     Direct child posts replying to this post (one-to-many)

    SELF-REFERENTIAL RELATIONSHIP EXPLAINED:
        The parent_post and replies relationships form a BIDIRECTIONAL
        self-referential link:
          - post.parent_post navigates UP the tree (child -> parent)
          - post.replies navigates DOWN the tree (parent -> children)
        This allows traversing the comment tree in either direction from
        any node.
    """

    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Every post belongs to a thread. CASCADE ensures that when a thread is
    # deleted, all its posts are deleted too. This is essential for data
    # consistency — orphaned posts (posts with no thread) would be unreachable.
    thread_id: Mapped[int] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"))

    # The user who wrote this post. CASCADE means deleting a user deletes
    # their posts. An alternative design would use SET NULL to preserve posts
    # from deleted users (showing "[deleted]" like Reddit does), but that
    # would require making author_id nullable.
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # ---- THE SELF-REFERENTIAL FOREIGN KEY -----------------------------------
    # This is the key column that enables nested comments. It points to another
    # row in THIS SAME TABLE ("posts.id").
    #
    # nullable=True: A NULL parent_post_id means this is a TOP-LEVEL reply
    # (directly to the thread, not to another post). This is the "root node"
    # of a comment subtree.
    #
    # ondelete="CASCADE": If a parent post is deleted, ALL its replies (and
    # their replies, recursively) are deleted too. This is called a "cascading
    # delete" and prevents orphaned subtrees. On Reddit, deleting a comment
    # shows "[deleted]" but keeps the subtree — PulseBoard takes the simpler
    # approach of removing the entire subtree.
    parent_post_id: Mapped[int | None] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"), nullable=True
    )

    # The actual content of the reply. Text type (unlimited length) allows
    # detailed responses with code blocks, links, etc.
    body: Mapped[str] = mapped_column(Text)

    # ---- ORM Relationships --------------------------------------------------
    thread = relationship("Thread", back_populates="posts")
    author = relationship("User", back_populates="posts")

    # SELF-REFERENTIAL RELATIONSHIP — this is the tricky part:
    #
    # parent_post: navigates from a reply UP to its parent.
    #   - remote_side=[id] tells SQLAlchemy: "the 'remote' side of this FK is
    #     the `id` column". This disambiguates the self-join — without it,
    #     SQLAlchemy wouldn't know which direction the FK points.
    #   - back_populates="replies" links this to the inverse relationship.
    parent_post = relationship("Post", remote_side=[id], back_populates="replies")

    # replies: navigates from a post DOWN to all direct child replies.
    #   - cascade="all, delete-orphan" ensures that deleting a post also deletes
    #     all its direct replies. Combined with the database-level CASCADE on
    #     the FK, this means deleting a post removes the ENTIRE subtree.
    #   - back_populates="parent_post" links this to the inverse relationship.
    #
    # INTERVIEW NOTE — HOW TO BUILD THE TREE IN PYTHON:
    #   1. Fetch all posts for a thread in one query: posts = db.query(Post)
    #      .filter(Post.thread_id == thread_id).all()
    #   2. Build a dict: posts_by_id = {p.id: p for p in posts}
    #   3. Group by parent: for each post, append it to
    #      posts_by_id[post.parent_post_id].replies
    #   4. Root posts are those where parent_post_id is None.
    #   This is O(n) time and O(n) space — much better than N+1 queries.
    replies = relationship(
        "Post", back_populates="parent_post", cascade="all, delete-orphan"
    )

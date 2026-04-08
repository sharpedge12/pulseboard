"""
Forum Search — Full-Text Search Across Threads and Posts.

This module implements the ``GET /api/v1/search`` endpoint's backend logic.
It searches threads and posts using SQL ``ILIKE`` (case-insensitive LIKE)
queries, which is simple and works well for moderate dataset sizes without
requiring an external search engine like Elasticsearch.

How the search works:
    1. The user's query string is escaped (``%`` and ``_`` are neutralised)
       and wrapped with ``%…%`` wildcards for substring matching.
    2. Threads are searched across title, body, author username, category
       title, and tag names.
    3. Posts are searched across body, author username, parent thread title,
       and category title.
    4. Results from both entity types are merged into a single list and
       returned to the frontend.

Performance note:
    ``ILIKE '%term%'`` cannot use a standard B-tree index, so it performs a
    sequential scan.  For a large-scale production system you would add
    PostgreSQL ``pg_trgm`` trigram indexes or switch to a dedicated search
    service.  For PulseBoard's scale this is perfectly adequate.

Filters:
    - ``category`` — restrict results to threads/posts in a specific category
      (matched by slug).
    - ``author`` — restrict results to a specific author (matched by username).
    - ``content_type`` — ``"thread"`` or ``"post"`` to search only one type.
    - ``tag`` — restrict thread results to those tagged with a specific tag.

Called from:
    ``app.forum_routes.search_router`` (``GET /api/v1/search``).
"""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from shared.models.category import Category
from shared.models.post import Post
from shared.models.tag import Tag, ThreadTag
from shared.models.thread import Thread
from shared.models.user import User
from shared.schemas.search import SearchResponse, SearchResultItem


def search_forum(
    db: Session,
    query: str,
    category: str | None = None,
    author: str | None = None,
    content_type: str | None = None,
    tag: str | None = None,
) -> SearchResponse:
    """
    Search threads and/or posts for content matching the given query string.

    Args:
        db: SQLAlchemy database session.
        query: The user's raw search string (may contain spaces, special
            characters, etc.).
        category: Optional category slug to restrict results.
        author: Optional username to restrict results to a single author.
        content_type: ``"thread"``, ``"post"``, or ``None`` (search both).
        tag: Optional tag name to filter thread results.

    Returns:
        A ``SearchResponse`` containing the original query, the total number
        of results, and a list of ``SearchResultItem`` objects (each
        annotated with ``result_type`` so the frontend knows how to render
        and link them).

    Interview note — SQL injection safety:
        The search pattern is built via ``ILIKE`` with parameter binding
        (SQLAlchemy handles escaping).  The ``%`` and ``_`` wildcards inside
        the *user's* input are additionally escaped to prevent the user from
        crafting LIKE patterns that match unintended rows.
    """
    cleaned_query = query.strip()
    if not cleaned_query:
        # Early return: no point running queries for an empty string.
        return SearchResponse(query=query, total=0, results=[])

    results: list[SearchResultItem] = []

    # --- Escape SQL LIKE wildcards in the user's input ---
    # Without this, a user typing "100%" would match any string containing
    # "100" followed by anything, not the literal "100%".
    escaped = (
        cleaned_query.replace("\\", "\\\\")  # Escape backslash first
        .replace("%", "\\%")  # Then escape the % wildcard
        .replace("_", "\\_")  # Then escape the _ wildcard
    )
    # Wrap with wildcards for substring matching: "foo" → "%foo%"
    search_pattern = f"%{escaped}%"

    # ---------------------------------------------------------------------------
    # Search threads
    # ---------------------------------------------------------------------------
    if content_type in {None, "thread"}:
        # Build a query that JOINs threads with their author, category, and
        # tags so we can search across all of those fields simultaneously.
        thread_query = (
            select(Thread)
            .join(Thread.author)
            .join(Thread.category)
            # LEFT JOIN tags — a thread may have zero tags, and we still
            # want it to appear if the title/body matches.
            .outerjoin(ThreadTag, ThreadTag.thread_id == Thread.id)
            .outerjoin(Tag, Tag.id == ThreadTag.tag_id)
            # Eager-load related objects to avoid N+1 queries when we
            # access thread.author.username and thread.category.title below.
            .options(selectinload(Thread.author), selectinload(Thread.category))
            .where(
                # OR across multiple columns: a match in ANY column counts.
                or_(
                    Thread.title.ilike(search_pattern),
                    Thread.body.ilike(search_pattern),
                    User.username.ilike(search_pattern),
                    Category.title.ilike(search_pattern),
                    Tag.name.ilike(search_pattern),
                )
            )
            .order_by(Thread.created_at.desc())  # Newest first
        )

        # Apply optional filters to narrow the search scope.
        if category:
            thread_query = thread_query.where(Category.slug == category)
        if author:
            thread_query = thread_query.where(User.username == author)
        if tag:
            thread_query = thread_query.where(Tag.name == tag)

        # ``.unique()`` is required because the outer join on tags can
        # duplicate thread rows (one per tag).  SQLAlchemy's ``unique()``
        # deduplicates by primary key.
        threads = db.execute(thread_query).scalars().unique().all()
        for thread in threads:
            results.append(
                SearchResultItem(
                    result_type="thread",
                    id=thread.id,
                    title=thread.title,
                    snippet=thread.body[:180],  # First 180 chars as preview
                    category=thread.category.title,
                    author=thread.author.username,
                    thread_id=thread.id,  # Used by frontend to build the link
                )
            )

    # ---------------------------------------------------------------------------
    # Search posts (replies / comments)
    # ---------------------------------------------------------------------------
    if content_type in {None, "post"}:
        # Posts are searched across body text, author, parent thread title,
        # and category title.  We join through Post → Thread → Category.
        post_query = (
            select(Post)
            .join(Post.author)
            .join(Post.thread)
            .join(Thread.category)
            .options(
                selectinload(Post.author),
                selectinload(Post.thread).selectinload(Thread.category),
            )
            .where(
                or_(
                    Post.body.ilike(search_pattern),
                    User.username.ilike(search_pattern),
                    Thread.title.ilike(search_pattern),
                    Category.title.ilike(search_pattern),
                )
            )
            .order_by(Post.created_at.desc())
        )

        if category:
            post_query = post_query.where(Category.slug == category)
        if author:
            post_query = post_query.where(User.username == author)

        posts = db.execute(post_query).scalars().unique().all()
        for post in posts:
            results.append(
                SearchResultItem(
                    result_type="post",
                    id=post.id,
                    # Posts don't have their own title, so we derive one
                    # from the parent thread's title for display purposes.
                    title=f"Reply in {post.thread.title}",
                    snippet=post.body[:180],
                    category=post.thread.category.title,
                    author=post.author.username,
                    thread_id=post.thread.id,  # Link leads to the thread
                )
            )

    return SearchResponse(query=query, total=len(results), results=results)

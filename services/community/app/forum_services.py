"""
Forum Service Business Logic — Threads, Posts, and Categories.

This is the **core business-logic layer** for the Community service.  It sits
between the thin HTTP route handlers (``forum_routes.py``) and the database
models (``shared.models``).  Every operation that touches threads, posts, or
categories passes through here.

Architecture — "thin routes, fat services":
    The route layer handles HTTP concerns (status codes, request parsing,
    real-time event broadcasting).  This module handles *business rules*:
    permission checks, data validation, notification dispatch, audit logging,
    and AI bot triggers.  Keeping logic here makes it testable without an
    HTTP server and reusable across multiple entry points (REST, WebSocket,
    CLI, etc.).

Key functions:
    - ``create_thread``  — validates category, creates thread + tags +
      subscription, triggers @pulse bot if mentioned, records audit log.
    - ``list_threads``   — paginated listing with sort/filter/time-range,
      bulk-fetches vote scores and reactions to avoid N+1 queries.
    - ``get_thread_detail`` — fetches a single thread with its entire nested
      comment tree (built via ``_build_post_tree``).
    - ``create_post``    — creates a reply, notifies author/parent/subscribers/
      mentions, triggers @pulse bot.
    - ``update_thread`` / ``update_post`` — permission-gated editing with
      owner / moderator / admin hierarchy.
    - ``delete_thread`` / ``delete_post`` — same permission model as updates.

Permission model (used across update/delete operations):
    1. **Owner** — the user who created the thread/post can always edit/delete.
    2. **Moderator** — can edit/delete content within their assigned categories,
       but NOT content authored by admins.
    3. **Admin** — can edit/delete any content globally.

Database patterns:
    - ``db.flush()`` — sends the INSERT to the DB to get an auto-generated ID,
      but does NOT commit the transaction.  Useful when you need the ID for
      follow-up inserts (e.g. audit log, tag associations) before committing
      the whole batch atomically.
    - ``db.commit()`` — finalises the transaction.  All flushed operations
      become permanent.
    - ``selectinload(…)`` — SQLAlchemy eager-loading strategy that runs a
      second SELECT to load related objects, avoiding lazy-load N+1 queries.

Called from:
    ``app.forum_routes`` (HTTP layer).
"""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from shared.models.category import Category
from shared.models.post import Post
from shared.models.thread import Thread, ThreadSubscription
from shared.models.user import User, UserRole
from shared.schemas.category import CategoryCreateRequest, CategoryResponse
from shared.schemas.post import PostAuthorResponse, PostResponse
from shared.schemas.thread import (
    ThreadAuthorResponse,
    ThreadCategoryResponse,
    ThreadCreateRequest,
    ThreadDetailResponse,
    ThreadListItemResponse,
    ThreadUpdateRequest,
    PaginatedThreadsResponse,
)
from shared.schemas.tag import TagResponse
from shared.services.notifications import create_notification
from shared.services.mentions import create_mention_notifications
from shared.services.attachments import assign_attachments_to_entity, list_attachments
from shared.services.moderation import get_moderator_category_ids
from shared.services.bot import (
    schedule_forum_bot_reply,
    should_invoke_bot,
)
from shared.services.audit import record as audit_record
from shared.services import audit as audit_actions
from app.forum_votes import (
    get_reaction_counts,
    get_reaction_counts_bulk,
    get_vote_scores_bulk,
    _get_vote_score,
)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------
# These small functions convert ORM model instances into Pydantic response
# schemas.  They are "private" (prefixed with ``_``) because they are only
# used within this module.  Keeping serialisation in dedicated helpers
# avoids duplicating the same field-mapping code in every service function.
# ---------------------------------------------------------------------------


def _post_author(user: User) -> PostAuthorResponse:
    """Convert a ``User`` ORM object into the lightweight author schema
    embedded inside every ``PostResponse``."""
    return PostAuthorResponse(
        id=user.id,
        username=user.username,
        role=user.role.value,
        avatar_url=user.avatar_url,
    )


def _thread_author(user: User) -> ThreadAuthorResponse:
    """Convert a ``User`` ORM object into the lightweight author schema
    embedded inside every ``ThreadListItemResponse``."""
    return ThreadAuthorResponse(
        id=user.id,
        username=user.username,
        role=user.role.value,
        avatar_url=user.avatar_url,
    )


def _thread_category(category: Category) -> ThreadCategoryResponse:
    """Convert a ``Category`` ORM object into the compact category schema
    embedded inside thread responses (just id, title, and slug)."""
    return ThreadCategoryResponse(
        id=category.id, title=category.title, slug=category.slug
    )


# ---------------------------------------------------------------------------
# Post tree builder
# ---------------------------------------------------------------------------


def _build_post_tree(
    posts: list[Post],
    attachment_map: dict[int, list],
    vote_scores: dict[int, int] | None = None,
    reaction_map: dict[int, list] | None = None,
) -> list[PostResponse]:
    """
    Convert a flat list of ``Post`` ORM objects into a nested comment tree.

    This is one of the most interview-relevant algorithms in the codebase.
    Reddit-style threaded comments are stored *flat* in the database (each
    post has a nullable ``parent_post_id`` foreign key pointing to its parent
    comment).  The frontend, however, needs a *nested* JSON structure where
    each post contains a ``replies`` array of child posts, recursively.

    Algorithm (two-pass, O(n)):
        **Pass 1** — Build a lookup dict mapping ``post.id → PostResponse``.
        **Pass 2** — Iterate again; for each post that has a ``parent_post_id``,
            append it to the parent's ``replies`` list.  Posts with no parent
            (or whose parent is missing) become root-level comments.

    Why not use recursion?
        A recursive SQL query (CTE) or recursive Python function would also
        work, but the two-pass approach is simpler, handles arbitrarily deep
        nesting without risking Python's recursion limit, and runs in O(n)
        time regardless of tree depth.

    Args:
        posts: Flat list of Post ORM objects (all posts in one thread).
        attachment_map: Dict mapping ``post_id → list[AttachmentResponse]``.
        vote_scores: Dict mapping ``post_id → aggregate_vote_score``.
        reaction_map: Dict mapping ``post_id → list[ReactionCountResponse]``.

    Returns:
        List of root-level ``PostResponse`` objects, each with a nested
        ``replies`` tree.  Sorted chronologically (oldest first).
    """
    vote_scores = vote_scores or {}
    reaction_map = reaction_map or {}

    # Lookup dict: post_id → serialised PostResponse node.
    post_map: dict[int, PostResponse] = {}
    roots: list[PostResponse] = []

    # Sort by creation time so the tree reads chronologically.
    ordered_posts = sorted(posts, key=lambda post: (post.created_at, post.id))

    # --- Pass 1: Create a PostResponse node for every post ---
    for post in ordered_posts:
        post_map[post.id] = PostResponse(
            id=post.id,
            thread_id=post.thread_id,
            parent_post_id=post.parent_post_id,
            body=post.body,
            created_at=post.created_at,
            updated_at=post.updated_at,
            vote_score=vote_scores.get(post.id, 0),
            reactions=reaction_map.get(post.id, []),
            author=_post_author(post.author),
            attachments=attachment_map.get(post.id, []),
            replies=[],  # Will be populated in Pass 2
        )

    # --- Pass 2: Link children to parents ---
    for post in ordered_posts:
        node = post_map[post.id]
        if post.parent_post_id and post.parent_post_id in post_map:
            # This post is a reply to another post → nest it.
            post_map[post.parent_post_id].replies.append(node)
        else:
            # Top-level comment (no parent or parent was deleted).
            roots.append(node)

    return roots


# ---------------------------------------------------------------------------
# Thread serialisation
# ---------------------------------------------------------------------------


def _serialize_thread(
    thread: Thread,
    attachment_map: dict[int, list] | None = None,
    vote_score: int = 0,
    user_vote: int = 0,
    reactions: list | None = None,
    reply_count: int | None = None,
) -> ThreadListItemResponse:
    """
    Serialise a ``Thread`` ORM object into a ``ThreadListItemResponse``
    Pydantic schema suitable for the thread listing feed.

    This helper is used in two contexts:
        1. ``list_threads`` — feed items on the homepage.
        2. ``get_thread_detail`` — the base fields are unpacked and extended
           with the nested ``posts`` tree.

    Args:
        thread: The Thread ORM object (with ``author``, ``category``, and
            ``tags`` eager-loaded).
        attachment_map: Dict mapping ``thread_id → list[AttachmentResponse]``.
        vote_score: Pre-computed aggregate vote score for this thread.
        user_vote: The current user's vote (+1, -1, or 0).  Not always
            available (e.g. for unauthenticated listing requests).
        reactions: Pre-computed list of ``ReactionCountResponse`` objects.
        reply_count: Pre-computed reply count.  If ``None``, falls back to
            ``len(thread.posts)`` (which requires posts to be loaded).

    Returns:
        A fully populated ``ThreadListItemResponse``.
    """
    # Convert the thread's Tag ORM objects into lightweight TagResponse schemas.
    tags = [
        TagResponse(id=tag.id, name=tag.name) for tag in getattr(thread, "tags", [])
    ]
    return ThreadListItemResponse(
        id=thread.id,
        title=thread.title,
        body=thread.body,
        is_locked=thread.is_locked,
        is_pinned=thread.is_pinned,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
        reply_count=reply_count if reply_count is not None else len(thread.posts),
        vote_score=vote_score,
        user_vote=user_vote,
        reactions=reactions or [],
        author=_thread_author(thread.author),
        category=_thread_category(thread.category),
        attachments=(attachment_map or {}).get(thread.id, []),
        tags=tags,
    )


# ===========================================================================
# Category operations
# ===========================================================================


def list_categories(db: Session) -> list[CategoryResponse]:
    """
    Fetch all forum categories with their thread counts.

    Uses a LEFT OUTER JOIN + GROUP BY to count threads per category in a
    single SQL query (avoids N+1).  Categories with zero threads still
    appear (thanks to the outer join).

    Returns:
        List of ``CategoryResponse`` objects sorted alphabetically by title.
    """
    rows = db.execute(
        select(Category, func.count(Thread.id))
        .outerjoin(Thread, Thread.category_id == Category.id)
        .group_by(Category.id)
        .order_by(Category.title.asc())
    ).all()
    return [
        CategoryResponse(
            id=category.id,
            title=category.title,
            slug=category.slug,
            description=category.description,
            thread_count=thread_count,
        )
        for category, thread_count in rows
    ]


def create_category(
    db: Session, payload: CategoryCreateRequest, current_user: User
) -> CategoryResponse:
    """
    Create a new forum category (community).  **Admin-only**.

    Flow:
        1. Verify the current user is an admin (HTTP 403 otherwise).
        2. Check for duplicate title or slug (HTTP 400 if collision).
        3. Insert the ``Category`` row.
        4. Record an audit log entry (``CATEGORY_CREATE``).
        5. Commit and return the serialised response.

    Moderators who want a new category should use the category request
    workflow (``POST /admin/category-requests``) instead.

    Args:
        db: Active database session.
        payload: ``CategoryCreateRequest`` with title, slug, description.
        current_user: The authenticated user making the request.

    Returns:
        ``CategoryResponse`` with the new category's details (thread_count=0).

    Raises:
        HTTPException(403) if the user is not an admin.
        HTTPException(400) if the title or slug already exists.
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can create communities. Moderators can request a new community from the dashboard.",
        )

    # Check for existing category with the same title OR slug.
    # Using OR here catches both exact-title and exact-slug collisions.
    existing_category = db.execute(
        select(Category).where(
            (Category.title == payload.title) | (Category.slug == payload.slug)
        )
    ).scalar_one_or_none()
    if existing_category:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Category title or slug already exists.",
        )

    category = Category(
        title=payload.title,
        slug=payload.slug,
        description=payload.description,
    )
    db.add(category)
    # flush() sends the INSERT to the DB to get the auto-generated
    # category.id, but does NOT commit.  We need the ID for the audit log.
    db.flush()
    audit_record(
        db,
        actor_id=current_user.id,
        action=audit_actions.CATEGORY_CREATE,
        entity_type="category",
        entity_id=category.id,
        details={"title": payload.title, "slug": payload.slug},
    )
    db.commit()
    db.refresh(category)
    return CategoryResponse(
        id=category.id,
        title=category.title,
        slug=category.slug,
        description=category.description,
        thread_count=0,
    )


# ===========================================================================
# Thread listing (paginated)
# ===========================================================================


def list_threads(
    db: Session,
    category_slug: str | None = None,
    sort: str = "new",
    time_range: str = "all",
    page: int = 1,
    page_size: int = 10,
    tag: str | None = None,
) -> PaginatedThreadsResponse:
    """
    List threads with pagination, sorting, filtering, and bulk-fetched
    engagement data (vote scores, reactions, attachments).

    This function is the backbone of the homepage feed.  It demonstrates
    several important backend engineering patterns:

    1. **Dynamic query building** — filters and joins are conditionally
       applied based on which query parameters the user provided.
    2. **Correlated subquery for reply count** — instead of loading all
       posts into Python to count them, we use a SQL scalar subquery
       ``SELECT COUNT(*) FROM posts WHERE posts.thread_id = threads.id``
       that the DB engine evaluates per row.
    3. **Bulk data fetching** — after fetching the page of threads, we
       batch-fetch vote scores, reaction counts, and attachments for all
       threads on the page in three queries (rather than 3 × N queries).
    4. **Ceiling division for total_pages** —
       ``(total + page_size - 1) // page_size`` computes the ceiling of
       ``total / page_size`` without floating-point arithmetic.

    Sorting modes:
        - ``"new"``      — newest threads first (default).
        - ``"top"``      — most replies first.
        - ``"trending"`` — approximate trending score:
          ``reply_count / (age_in_hours + 1)``.  The ``+1`` prevents
          division by zero for brand-new threads.

    Pinned threads always sort to the top regardless of sort mode.

    Args:
        db: Active database session.
        category_slug: Optional category slug to filter by.
        sort: ``"new"``, ``"top"``, or ``"trending"``.
        time_range: ``"all"``, ``"hour"``, ``"day"``, ``"week"``, ``"month"``,
            ``"year"``.
        page: 1-indexed page number.
        page_size: Number of threads per page (1–100).
        tag: Optional tag name to filter by.

    Returns:
        ``PaginatedThreadsResponse`` with ``items``, ``total``, ``page``,
        ``page_size``, and ``total_pages``.
    """
    # -----------------------------------------------------------------------
    # Step 1: Build dynamic filter conditions
    # -----------------------------------------------------------------------
    # We collect filter expressions in a list and apply them to both the
    # count query and the data query so they always agree on the total.
    filters = []
    join_category = False
    join_tag = False

    if category_slug:
        join_category = True
        filters.append(Category.slug == category_slug)

    if tag:
        from shared.models.tag import Tag, ThreadTag

        join_tag = True
        filters.append(Tag.name == tag)

    # Time range filter — restrict threads to those created within the
    # specified window (e.g. last 24 hours, last week).
    if time_range and time_range != "all":
        delta_map = {
            "hour": timedelta(hours=1),
            "day": timedelta(days=1),
            "week": timedelta(weeks=1),
            "month": timedelta(days=30),
            "year": timedelta(days=365),
        }
        delta = delta_map.get(time_range)
        if delta:
            cutoff = datetime.now(timezone.utc) - delta
            filters.append(Thread.created_at >= cutoff)

    # -----------------------------------------------------------------------
    # Step 2: Count total matching threads (for pagination metadata)
    # -----------------------------------------------------------------------
    # We use COUNT(DISTINCT thread.id) because the tag join can produce
    # duplicate rows (one thread × multiple tags).
    count_query = select(func.count(func.distinct(Thread.id)))
    if join_category:
        count_query = count_query.join(Thread.category)
    if join_tag:
        from shared.models.tag import Tag, ThreadTag

        count_query = count_query.join(
            ThreadTag, ThreadTag.thread_id == Thread.id
        ).join(Tag, Tag.id == ThreadTag.tag_id)
    for f in filters:
        count_query = count_query.where(f)

    total = db.execute(count_query).scalar_one()

    # Ceiling division to compute total_pages.
    total_pages = max(1, (total + page_size - 1) // page_size)
    # Clamp page to valid range so out-of-bounds page numbers don't error.
    page = max(1, min(page, total_pages))

    # -----------------------------------------------------------------------
    # Step 3: Correlated subquery for reply_count
    # -----------------------------------------------------------------------
    # This subquery counts posts per thread at the SQL level, so we never
    # need to load all Post objects just to count them.
    reply_count_sq = (
        select(func.count(Post.id))
        .where(Post.thread_id == Thread.id)
        .correlate(Thread)
        .scalar_subquery()
        .label("reply_count")
    )

    # -----------------------------------------------------------------------
    # Step 4: Main data query with eager-loading and pagination
    # -----------------------------------------------------------------------
    query = select(Thread, reply_count_sq).options(
        selectinload(Thread.author),  # Avoid N+1 on thread.author
        selectinload(Thread.category),  # Avoid N+1 on thread.category
        selectinload(Thread.tags),  # Avoid N+1 on thread.tags
    )
    if join_category:
        query = query.join(Thread.category)
    else:
        # Still need the join for ordering but won't filter
        pass
    if join_tag:
        from shared.models.tag import Tag, ThreadTag

        query = query.join(ThreadTag, ThreadTag.thread_id == Thread.id).join(
            Tag, Tag.id == ThreadTag.tag_id
        )
    for f in filters:
        query = query.where(f)

    # -----------------------------------------------------------------------
    # Step 5: Apply sort order
    # -----------------------------------------------------------------------
    # Pinned threads always float to the top (``is_pinned DESC`` so True > False).
    if sort == "top":
        query = query.order_by(
            Thread.is_pinned.desc(),
            reply_count_sq.desc(),
            Thread.created_at.desc(),
        )
    elif sort == "trending":
        # Trending score ≈ reply_count / (age_in_hours + 1)
        # Uses SQL ``EXTRACT(EPOCH FROM now() - created_at)`` to compute
        # the thread's age in seconds, then converts to hours.
        age_seconds = func.extract(
            "epoch",
            func.now() - Thread.created_at,
        )
        trending_score = reply_count_sq / (age_seconds / 3600 + 1)
        query = query.order_by(
            Thread.is_pinned.desc(),
            trending_score.desc(),
            Thread.created_at.desc(),
        )
    else:
        # Default: newest first
        query = query.order_by(
            Thread.is_pinned.desc(),
            Thread.created_at.desc(),
        )

    # SQL-level OFFSET/LIMIT pagination.
    offset = (page - 1) * page_size
    query = query.limit(page_size).offset(offset)

    # ``.unique()`` deduplicates rows that were duplicated by JOINs
    # (e.g. a thread with multiple tags).
    rows = db.execute(query).unique().all()

    # Unpack the query result tuples: each row is (Thread, reply_count).
    threads_with_counts: list[tuple[Thread, int]] = [
        (row[0], row[1] or 0) for row in rows
    ]

    # -----------------------------------------------------------------------
    # Step 6: Bulk-fetch engagement data to avoid N+1 queries
    # -----------------------------------------------------------------------
    thread_ids = [t.id for t, _ in threads_with_counts]

    # Three queries instead of 3 × len(thread_ids) queries:
    attachment_map = list_attachments(db, "thread", thread_ids)
    vote_scores = get_vote_scores_bulk(db, "thread", thread_ids)
    reaction_map = get_reaction_counts_bulk(db, "thread", thread_ids)

    # -----------------------------------------------------------------------
    # Step 7: Serialise and return the paginated response
    # -----------------------------------------------------------------------
    items = [
        _serialize_thread(
            thread,
            attachment_map,
            vote_score=vote_scores.get(thread.id, 0),
            reactions=reaction_map.get(thread.id, []),
            reply_count=rc,
        )
        for thread, rc in threads_with_counts
    ]

    return PaginatedThreadsResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# ===========================================================================
# Thread detail (single thread + nested post tree)
# ===========================================================================


def get_thread_detail(db: Session, thread_id: int) -> ThreadDetailResponse:
    """
    Fetch a single thread with its complete nested comment tree.

    This is the main data source for the thread page in the frontend.
    It uses ``selectinload`` to eagerly load the thread's posts (and their
    authors), then delegates to ``_build_post_tree`` to arrange them into
    a nested tree structure.

    Data fetched:
        - Thread metadata (title, body, timestamps, lock/pin status).
        - Thread author and category (for the header).
        - All posts with their authors (for the comment section).
        - Tags (for the tag badges).
        - Vote scores and reactions for the thread AND all its posts.
        - Attachments for the thread AND all its posts.

    Args:
        db: Active database session.
        thread_id: Primary key of the thread to fetch.

    Returns:
        ``ThreadDetailResponse`` — extends ``ThreadListItemResponse`` with
        a nested ``posts`` tree.

    Raises:
        HTTPException(404) if the thread does not exist.
    """
    thread = db.execute(
        select(Thread)
        .where(Thread.id == thread_id)
        .options(
            selectinload(Thread.author),
            selectinload(Thread.category),
            # Nested eager-load: load posts, then each post's author.
            selectinload(Thread.posts).selectinload(Post.author),
            selectinload(Thread.tags),
        )
    ).scalar_one_or_none()
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found."
        )

    # Fetch attachments for the thread itself and for all its posts.
    thread_attachments = list_attachments(db, "thread", [thread.id])
    post_attachments = list_attachments(db, "post", [post.id for post in thread.posts])

    # Fetch engagement data (votes + reactions) for the thread and all posts.
    thread_vote_score = _get_vote_score(db, "thread", thread.id)
    thread_reactions = get_reaction_counts(db, "thread", thread.id)

    post_ids = [post.id for post in thread.posts]
    post_vote_scores = get_vote_scores_bulk(db, "post", post_ids)
    post_reaction_map = get_reaction_counts_bulk(db, "post", post_ids)

    return ThreadDetailResponse(
        # Unpack the base thread fields from _serialize_thread.
        **_serialize_thread(
            thread,
            thread_attachments,
            vote_score=thread_vote_score,
            reactions=thread_reactions,
        ).model_dump(),
        # Build the nested comment tree from the flat posts list.
        posts=_build_post_tree(
            thread.posts,
            post_attachments,
            post_vote_scores,
            post_reaction_map,
        ),
    )


# ===========================================================================
# Thread creation
# ===========================================================================


def create_thread(
    db: Session, payload: ThreadCreateRequest, current_user: User
) -> ThreadDetailResponse:
    """
    Create a new discussion thread in a category.

    This function orchestrates several sub-operations within a single
    database transaction:

    1. **Category validation** — confirm the target category exists.
    2. **Thread insertion** — create the ``Thread`` row.
    3. **Tag management** — for each tag name in the payload:
       - Look up an existing ``Tag`` row by name.
       - If not found, create a new ``Tag``.
       - Associate the tag with the thread (via the ``thread_tags``
         many-to-many join table).
    4. **Attachment linking** — reassign any uploaded files from "draft"
       status to be linked to this thread.
    5. **Auto-subscription** — the author is automatically subscribed to
       their own thread so they receive notifications for replies.
    6. **Audit logging** — record a ``THREAD_CREATE`` entry.
    7. **Commit** — all of the above is committed atomically.
    8. **Bot trigger** — if the thread body contains ``@pulse``, schedule
       an AI bot reply in a background thread.  This happens AFTER commit
       so the bot's background thread can read the committed data.

    Args:
        db: Active database session.
        payload: ``ThreadCreateRequest`` with title, body, category_id,
            optional tag_names and attachment_ids.
        current_user: The authenticated user creating the thread.

    Returns:
        ``ThreadDetailResponse`` for the newly created thread.

    Raises:
        HTTPException(404) if the specified category does not exist.
    """
    # Validate that the target category exists.
    category = db.execute(
        select(Category).where(Category.id == payload.category_id)
    ).scalar_one_or_none()
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Category not found."
        )

    # Create the thread row.
    thread = Thread(
        category_id=payload.category_id,
        author_id=current_user.id,
        title=payload.title,
        body=payload.body,
    )
    db.add(thread)
    # flush() to get the auto-generated thread.id (needed for tag + attachment linking).
    db.flush()

    # --- Tag management: get-or-create tags and link them to the thread ---
    if payload.tag_names:
        from shared.models.tag import Tag

        for tag_name in payload.tag_names:
            tag_name = tag_name.strip().lower()
            if not tag_name:
                continue
            # Try to find an existing tag with this name.
            tag = db.execute(
                select(Tag).where(Tag.name == tag_name)
            ).scalar_one_or_none()
            if not tag:
                # Tag doesn't exist yet — create it.
                tag = Tag(name=tag_name)
                db.add(tag)
                db.flush()  # Get the tag's auto-generated ID.
            # Link the tag to the thread via the many-to-many relationship.
            thread.tags.append(tag)

    # Reassign uploaded draft attachments to this thread.
    assign_attachments_to_entity(
        db,
        current_user,
        payload.attachment_ids,
        "thread",
        thread.id,
    )

    # Auto-subscribe the author to their own thread for reply notifications.
    db.add(ThreadSubscription(thread_id=thread.id, user_id=current_user.id))

    # Record an audit trail entry for accountability.
    audit_record(
        db,
        actor_id=current_user.id,
        action=audit_actions.THREAD_CREATE,
        entity_type="thread",
        entity_id=thread.id,
        details={"title": payload.title, "category_id": payload.category_id},
    )
    db.commit()

    # --- AI bot trigger (AFTER commit) ---
    # The bot runs in a daemon background thread with its own DB session.
    # We must commit first so the bot can read the thread from the database.
    if payload.body and should_invoke_bot(payload.body):
        schedule_forum_bot_reply(
            thread_id=thread.id,
            thread_title=payload.title,
            thread_body=payload.body,
            parent_post_id=None,
            user_message=payload.body,
            poster_user_id=current_user.id,
        )

    # Return the full thread detail (re-fetches from DB with eager loading).
    return get_thread_detail(db, thread.id)


# ===========================================================================
# Thread update
# ===========================================================================


def update_thread(
    db: Session,
    thread_id: int,
    payload: ThreadUpdateRequest,
    current_user: User,
) -> ThreadDetailResponse:
    """
    Edit a thread's title and body.

    Permission hierarchy (checked in order):
        1. Thread owner — always allowed.
        2. Moderator — allowed unless:
           a. The thread was authored by an admin (moderators cannot
              override admin content).
           b. The thread's category is outside the moderator's assigned
              categories (scoped moderation).
        3. Admin — always allowed.

    If a non-owner edits the thread, a notification is sent to the
    original author so they know their content was modified.

    Args:
        db: Active database session.
        thread_id: Primary key of the thread to update.
        payload: ``ThreadUpdateRequest`` with new title and body.
        current_user: The authenticated user performing the edit.

    Returns:
        Updated ``ThreadDetailResponse``.

    Raises:
        HTTPException(404) if the thread does not exist.
        HTTPException(403) if the user lacks permission.
    """
    thread = db.execute(
        select(Thread)
        .where(Thread.id == thread_id)
        .options(selectinload(Thread.author))
    ).scalar_one_or_none()
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found."
        )

    # --- Permission checks ---
    is_owner = thread.author_id == current_user.id
    if not is_owner and current_user.role not in {
        UserRole.ADMIN,
        UserRole.MODERATOR,
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not allowed to edit this thread.",
        )

    # Moderators cannot edit content authored by admins.
    if (
        not is_owner
        and current_user.role == UserRole.MODERATOR
        and thread.author
        and thread.author.role == UserRole.ADMIN
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Moderators cannot edit admin threads.",
        )

    # Moderators can only edit threads in their assigned categories.
    if not is_owner and current_user.role == UserRole.MODERATOR:
        allowed_ids = get_moderator_category_ids(db, current_user)
        if allowed_ids is not None and thread.category_id not in allowed_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Thread is outside your assigned communities.",
            )

    # Apply the edits.
    thread.title = payload.title
    thread.body = payload.body
    audit_record(
        db,
        actor_id=current_user.id,
        action=audit_actions.THREAD_UPDATE,
        entity_type="thread",
        entity_id=thread.id,
        details={"title": payload.title},
    )
    db.commit()

    # Notify the original author if a moderator or admin edited their thread.
    if not is_owner:
        create_notification(
            db,
            user_id=thread.author_id,
            notification_type="post_edited",
            title=f"{current_user.username} edited your thread",
            payload={"thread_id": thread.id},
        )
        db.commit()  # Second commit to persist the notification.

    return get_thread_detail(db, thread.id)


# ===========================================================================
# Post creation
# ===========================================================================


def create_post(
    db: Session,
    thread_id: int,
    body: str,
    current_user: User,
    parent_post_id: int | None = None,
    attachment_ids: list[int] | None = None,
) -> tuple[PostResponse, list[int]]:
    """
    Create a new post (reply) in a thread and dispatch notifications.

    This is one of the most complex functions in the codebase because it
    orchestrates many side effects:

    1. **Thread validation** — confirm the thread exists and is not locked.
    2. **Parent post validation** — if ``parent_post_id`` is given, confirm
       the parent post exists and belongs to this thread.
    3. **Post insertion** — create the ``Post`` row.
    4. **Attachment linking** — reassign draft attachments to this post.
    5. **Bot detection** — check if ``@pulse`` is mentioned (before commit).
    6. **Audit logging** — record a ``POST_CREATE`` entry.
    7. **Commit** — persist everything atomically.
    8. **Notification dispatch** — notify up to four groups of users:
       a. Thread author (if not the current user).
       b. Parent post author (if replying to a specific comment).
       c. @mentioned users (parsed from the post body).
       d. Thread subscribers (users who explicitly subscribed).
       Duplicate recipients are tracked via a ``set`` to avoid sending
       multiple notifications to the same user.
    9. **Second commit** — persist the notification records.
    10. **Bot trigger** — if @pulse was detected, schedule the AI reply
        in a background thread (after commit).

    Args:
        db: Active database session.
        thread_id: Thread to post in.
        body: The post's text content.
        current_user: The authenticated user creating the post.
        parent_post_id: Optional ID of the post being replied to (for
            nested comments).
        attachment_ids: Optional list of pre-uploaded attachment IDs to
            link to this post.

    Returns:
        Tuple of:
            - ``PostResponse`` — the serialised new post.
            - ``list[int]`` — user IDs that should receive real-time
              notification events (used by the route layer for WebSocket
              broadcasting).

    Raises:
        HTTPException(404) if thread or parent post not found.
        HTTPException(400) if the thread is locked.
    """
    # --- Thread validation ---
    thread = db.execute(
        select(Thread).where(Thread.id == thread_id)
    ).scalar_one_or_none()
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found."
        )
    if thread.is_locked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Thread is locked."
        )

    # --- Parent post validation (for nested replies) ---
    parent_post = None
    if parent_post_id is not None:
        parent_post = db.execute(
            select(Post).where(Post.id == parent_post_id, Post.thread_id == thread_id)
        ).scalar_one_or_none()
        if not parent_post:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Parent post not found."
            )

    # --- Insert the post ---
    post = Post(
        thread_id=thread_id,
        author_id=current_user.id,
        parent_post_id=parent_post_id,
        body=body,
    )
    db.add(post)
    db.flush()  # Get the auto-generated post.id for attachment linking.

    # Link any pre-uploaded attachments to this post.
    assign_attachments_to_entity(
        db,
        current_user,
        attachment_ids or [],
        "post",
        post.id,
    )

    # Check for @pulse mention BEFORE commit (just a string check).
    _invoke_bot_for_post = should_invoke_bot(body)

    audit_record(
        db,
        actor_id=current_user.id,
        action=audit_actions.POST_CREATE,
        entity_type="post",
        entity_id=post.id,
        details={"thread_id": thread_id},
    )
    db.commit()

    # Re-fetch the post with eager-loaded relationships for serialisation.
    created_post = db.execute(
        select(Post)
        .where(Post.id == post.id)
        .options(selectinload(Post.author), selectinload(Post.replies))
    ).scalar_one()

    # --- Notification dispatch ---
    # Track recipients in a set to avoid sending duplicate notifications.
    recipient_ids: set[int] = set()

    # Notify the thread author (unless the current user IS the author).
    if thread.author_id != current_user.id:
        recipient_ids.add(thread.author_id)
        create_notification(
            db,
            user_id=thread.author_id,
            notification_type="reply",
            title=f"{current_user.username} replied to your thread",
            payload={"thread_id": thread.id, "post_id": created_post.id},
        )

    # Notify the parent post's author (if this is a nested reply).
    if parent_post and parent_post.author_id != current_user.id:
        recipient_ids.add(parent_post.author_id)
        create_notification(
            db,
            user_id=parent_post.author_id,
            notification_type="mention_reply",
            title=f"{current_user.username} replied to your comment",
            payload={"thread_id": thread.id, "post_id": created_post.id},
        )

    # Notify @mentioned users (parsed from the post body).
    for user_id in create_mention_notifications(
        db,
        body,
        current_user,
        notification_type="mention",
        title_template="{actor} mentioned you in a discussion",
        payload_factory=lambda _user: {
            "thread_id": thread.id,
            "post_id": created_post.id,
        },
    ):
        recipient_ids.add(user_id)

    # Notify thread subscribers (users who opted in to follow this thread).
    subscribers = (
        db.execute(
            select(ThreadSubscription).where(ThreadSubscription.thread_id == thread.id)
        )
        .scalars()
        .all()
    )
    for subscription in subscribers:
        if (
            subscription.user_id not in recipient_ids
            and subscription.user_id != current_user.id
        ):
            recipient_ids.add(subscription.user_id)
            create_notification(
                db,
                user_id=subscription.user_id,
                notification_type="followed_thread",
                title="New activity in a thread you follow",
                payload={"thread_id": thread.id, "post_id": created_post.id},
            )

    db.commit()  # Persist all notification records.

    # --- AI bot trigger (AFTER commit) ---
    if _invoke_bot_for_post:
        schedule_forum_bot_reply(
            thread_id=thread_id,
            thread_title=thread.title,
            thread_body=thread.body or "",
            parent_post_id=post.id,
            user_message=body,
            poster_user_id=current_user.id,
        )

    return PostResponse(
        id=created_post.id,
        thread_id=created_post.thread_id,
        parent_post_id=created_post.parent_post_id,
        body=created_post.body,
        created_at=created_post.created_at,
        updated_at=created_post.updated_at,
        author=_post_author(created_post.author),
        attachments=list_attachments(db, "post", [created_post.id]).get(
            created_post.id, []
        ),
        replies=[],
    ), list(recipient_ids)


# ===========================================================================
# Post update
# ===========================================================================


def update_post(
    db: Session,
    post_id: int,
    body: str,
    current_user: User,
) -> PostResponse:
    """
    Edit a post's body text.

    Uses the same three-tier permission model as ``update_thread``:
        1. Owner — always allowed.
        2. Moderator — allowed unless the post was authored by an admin
           or is in a category outside the moderator's assignments.
        3. Admin — always allowed.

    If a non-owner edits the post, the original author is notified.

    Args:
        db: Active database session.
        post_id: Primary key of the post to edit.
        body: The new post body text.
        current_user: The authenticated user performing the edit.

    Returns:
        Updated ``PostResponse``.

    Raises:
        HTTPException(404) if the post does not exist.
        HTTPException(403) if the user lacks permission.
    """
    post = db.execute(
        select(Post)
        .where(Post.id == post_id)
        .options(selectinload(Post.author), selectinload(Post.thread))
    ).scalar_one_or_none()
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Post not found."
        )

    # --- Permission checks (same hierarchy as update_thread) ---
    is_owner = post.author_id == current_user.id
    if not is_owner and current_user.role not in {
        UserRole.ADMIN,
        UserRole.MODERATOR,
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not allowed to edit this post.",
        )

    if (
        not is_owner
        and current_user.role == UserRole.MODERATOR
        and post.author
        and post.author.role == UserRole.ADMIN
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Moderators cannot edit admin posts.",
        )

    # Scoped moderation: check if the post's thread is in an allowed category.
    if not is_owner and current_user.role == UserRole.MODERATOR:
        allowed_ids = get_moderator_category_ids(db, current_user)
        if (
            allowed_ids is not None
            and post.thread
            and post.thread.category_id not in allowed_ids
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Post is outside your assigned communities.",
            )

    # Apply the edit.
    post.body = body
    audit_record(
        db,
        actor_id=current_user.id,
        action=audit_actions.POST_UPDATE,
        entity_type="post",
        entity_id=post.id,
        details={"thread_id": post.thread_id},
    )
    db.commit()
    db.refresh(post)

    # Notify the original author if edited by someone else.
    if not is_owner:
        create_notification(
            db,
            user_id=post.author_id,
            notification_type="post_edited",
            title=f"{current_user.username} edited your post",
            payload={"thread_id": post.thread_id, "post_id": post.id},
        )
        db.commit()

    return PostResponse(
        id=post.id,
        thread_id=post.thread_id,
        parent_post_id=post.parent_post_id,
        body=post.body,
        created_at=post.created_at,
        updated_at=post.updated_at,
        author=_post_author(post.author),
        attachments=list_attachments(db, "post", [post.id]).get(post.id, []),
        replies=[],
    )


# ===========================================================================
# Get single post by ID
# ===========================================================================


def get_post_by_id(db: Session, post_id: int) -> PostResponse:
    """
    Fetch a single post by its primary key, including its direct replies.

    This is used by:
        - ``GET /api/v1/posts/{post_id}`` — the single-post endpoint.
        - Vote/reaction routes — to look up the post's parent thread for
          WebSocket channel broadcasting.

    Args:
        db: Active database session.
        post_id: Primary key of the post.

    Returns:
        ``PostResponse`` with vote score, reactions, attachments, and nested
        replies (one level deep).

    Raises:
        HTTPException(404) if the post does not exist.
    """
    post = db.execute(
        select(Post)
        .where(Post.id == post_id)
        .options(
            selectinload(Post.author),
            # Eager-load direct replies AND their authors for the reply tree.
            selectinload(Post.replies).selectinload(Post.author),
        )
    ).scalar_one_or_none()
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Post not found."
        )

    # Bulk-fetch vote scores and reactions for this post's direct replies.
    reply_ids = [reply.id for reply in post.replies]
    reply_vote_scores = get_vote_scores_bulk(db, "post", reply_ids)
    reply_reaction_map = get_reaction_counts_bulk(db, "post", reply_ids)

    return PostResponse(
        id=post.id,
        thread_id=post.thread_id,
        parent_post_id=post.parent_post_id,
        body=post.body,
        created_at=post.created_at,
        updated_at=post.updated_at,
        vote_score=_get_vote_score(db, "post", post.id),
        reactions=get_reaction_counts(db, "post", post.id),
        author=_post_author(post.author),
        attachments=list_attachments(db, "post", [post.id]).get(post.id, []),
        replies=_build_post_tree(
            post.replies,
            list_attachments(db, "post", [reply.id for reply in post.replies]),
            reply_vote_scores,
            reply_reaction_map,
        ),
    )


# ===========================================================================
# Thread subscription
# ===========================================================================


def subscribe_to_thread(db: Session, thread_id: int, current_user: User) -> None:
    """
    Subscribe the current user to a thread for reply notifications.

    Idempotent — if the user is already subscribed, this is a no-op (no
    error, no duplicate row).

    Note: Thread authors are auto-subscribed at thread creation time.
    Other users can subscribe manually via ``POST /threads/{id}/subscribe``.

    Args:
        db: Active database session.
        thread_id: Thread to subscribe to.
        current_user: The user subscribing.

    Raises:
        HTTPException(404) if the thread does not exist.
    """
    thread = db.execute(
        select(Thread).where(Thread.id == thread_id)
    ).scalar_one_or_none()
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found."
        )

    # Idempotency check — don't insert a duplicate subscription row.
    existing = db.execute(
        select(ThreadSubscription).where(
            ThreadSubscription.thread_id == thread_id,
            ThreadSubscription.user_id == current_user.id,
        )
    ).scalar_one_or_none()
    if not existing:
        db.add(ThreadSubscription(thread_id=thread_id, user_id=current_user.id))
        db.commit()


# ===========================================================================
# Thread deletion
# ===========================================================================


def delete_thread(db: Session, thread_id: int, current_user: User) -> None:
    """
    Delete a thread and all its associated posts (cascade).

    Same three-tier permission model as update:
        1. Owner — always allowed.
        2. Moderator — scoped to assigned categories; cannot delete admin
           content.
        3. Admin — always allowed.

    An audit log entry is recorded before the actual deletion so the
    record captures the thread's title and category while they still exist.

    Args:
        db: Active database session.
        thread_id: Primary key of the thread to delete.
        current_user: The authenticated user requesting deletion.

    Raises:
        HTTPException(404) if the thread does not exist.
        HTTPException(403) if the user lacks permission.
    """
    thread = db.execute(
        select(Thread)
        .where(Thread.id == thread_id)
        .options(selectinload(Thread.author))
    ).scalar_one_or_none()
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found."
        )
    is_owner = thread.author_id == current_user.id
    if not is_owner and current_user.role not in {
        UserRole.ADMIN,
        UserRole.MODERATOR,
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not allowed to delete this thread.",
        )

    if (
        not is_owner
        and current_user.role == UserRole.MODERATOR
        and thread.author
        and thread.author.role == UserRole.ADMIN
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Moderators cannot delete admin threads.",
        )

    if not is_owner and current_user.role == UserRole.MODERATOR:
        allowed_ids = get_moderator_category_ids(db, current_user)
        if allowed_ids is not None and thread.category_id not in allowed_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Thread is outside your assigned communities.",
            )

    # Record the audit log BEFORE deletion (so we can capture the title).
    audit_record(
        db,
        actor_id=current_user.id,
        action=audit_actions.THREAD_DELETE,
        entity_type="thread",
        entity_id=thread.id,
        details={"title": thread.title, "category_id": thread.category_id},
    )
    db.delete(thread)
    db.commit()


# ===========================================================================
# Post deletion
# ===========================================================================


def delete_post(db: Session, post_id: int, current_user: User) -> None:
    """
    Delete a single post (reply).

    Same three-tier permission model as ``delete_thread``.

    Args:
        db: Active database session.
        post_id: Primary key of the post to delete.
        current_user: The authenticated user requesting deletion.

    Raises:
        HTTPException(404) if the post does not exist.
        HTTPException(403) if the user lacks permission.
    """
    post = db.execute(
        select(Post)
        .where(Post.id == post_id)
        .options(selectinload(Post.author), selectinload(Post.thread))
    ).scalar_one_or_none()
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Post not found."
        )
    is_owner = post.author_id == current_user.id
    if not is_owner and current_user.role not in {
        UserRole.ADMIN,
        UserRole.MODERATOR,
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not allowed to delete this post.",
        )

    if (
        not is_owner
        and current_user.role == UserRole.MODERATOR
        and post.author
        and post.author.role == UserRole.ADMIN
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Moderators cannot delete admin posts.",
        )

    if not is_owner and current_user.role == UserRole.MODERATOR:
        allowed_ids = get_moderator_category_ids(db, current_user)
        if (
            allowed_ids is not None
            and post.thread
            and post.thread.category_id not in allowed_ids
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Post is outside your assigned communities.",
            )

    audit_record(
        db,
        actor_id=current_user.id,
        action=audit_actions.POST_DELETE,
        entity_type="post",
        entity_id=post.id,
        details={"thread_id": post.thread_id},
    )
    db.delete(post)
    db.commit()

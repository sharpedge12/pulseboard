"""
Forum Routes — HTTP API Endpoints for Categories, Threads, Posts, and Search.

This module defines four APIRouter instances, each responsible for a logical
group of forum endpoints.  The routers are mounted by ``app.main`` under the
``/api/v1/`` prefix:

    - ``category_router`` → ``/api/v1/categories``
    - ``thread_router``   → ``/api/v1/threads``
    - ``post_router``     → ``/api/v1/posts``
    - ``search_router``   → ``/api/v1/search``

Architecture notes:
    - **Thin routes, fat services**: Route handlers validate input (via
      Pydantic schemas) and handle HTTP concerns (status codes, real-time
      event broadcasting), but delegate all business logic to
      ``forum_services``, ``forum_votes``, and ``forum_search``.
    - **Real-time broadcasting**: After mutating state (create post, cast
      vote, etc.), the route publishes an event both locally (via the
      in-process ``ConnectionManager``) and to Redis pub/sub (via
      ``publish_event``).  The gateway's Redis-to-WebSocket bridge picks
      up Redis messages and fans them out to all connected browsers.
    - **Auth patterns**: Public endpoints (list, get, search) use no auth
      dependency.  Write endpoints inject ``get_current_user`` and call
      ``require_can_participate`` to block suspended/banned users.

HTTP method conventions used in this API:
    - GET    → Read / list (no side effects)
    - POST   → Create a new resource
    - PATCH  → Partial update of an existing resource
    - DELETE → Remove a resource
"""

from fastapi import APIRouter, Depends, Query, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from shared.core.database import get_db
from shared.core.auth_helpers import get_current_user, require_can_participate
from shared.core.events import connection_manager, publish_event
from shared.models.user import User
from shared.schemas.category import CategoryCreateRequest, CategoryResponse
from shared.schemas.post import PostCreateRequest, PostResponse, PostUpdateRequest
from shared.schemas.thread import (
    ThreadCreateRequest,
    ThreadDetailResponse,
    ThreadUpdateRequest,
    PaginatedThreadsResponse,
)
from shared.schemas.vote import (
    ContentReportRequest,
    ContentReportResponse,
    ReactionCountResponse,
    ReactionRequest,
    VoterResponse,
    VoteRequest,
    VoteResponse,
)
from app.forum_services import (
    create_category,
    create_post,
    create_thread,
    delete_post,
    delete_thread,
    get_post_by_id,
    get_thread_detail,
    list_categories as list_categories_service,
    list_threads as list_threads_service,
    subscribe_to_thread,
    update_post,
    update_thread,
)
from app.forum_votes import (
    cast_vote,
    get_voters,
    remove_vote,
    report_content,
    toggle_reaction,
)
from app.forum_search import search_forum
from shared.schemas.search import SearchResponse

# ===========================================================================
# Categories
# ===========================================================================

category_router = APIRouter()


@category_router.get("", response_model=list[CategoryResponse])
def list_categories(db: Session = Depends(get_db)) -> list[CategoryResponse]:
    """
    GET /api/v1/categories

    List all forum categories (communities) with their thread counts.
    No authentication required — categories are public.

    Returns:
        JSON array of ``CategoryResponse`` objects sorted alphabetically.
    """
    return list_categories_service(db)


@category_router.post(
    "", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED
)
async def create_category_endpoint(
    payload: CategoryCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CategoryResponse:
    """
    POST /api/v1/categories

    Create a new forum category.  **Admin-only** — moderators should use
    the category request workflow instead.

    After creation, a ``category_created`` event is broadcast to all
    connected clients on the ``global`` WebSocket channel so the sidebar
    updates in real-time.

    Args:
        payload: ``CategoryCreateRequest`` with title, slug, and description.

    Returns:
        The created ``CategoryResponse`` (HTTP 201).
    """
    category = create_category(db, payload, current_user)

    # Broadcast the new category to all connected WebSocket clients so
    # the sidebar community list updates without a page refresh.
    event = jsonable_encoder(
        {
            "event": "category_created",
            "category": category.model_dump(),
        }
    )
    await connection_manager.broadcast("global", event)
    publish_event("global", event)  # Also push to Redis for other gateway instances.

    return category


# ===========================================================================
# Threads
# ===========================================================================

thread_router = APIRouter()


@thread_router.get("", response_model=PaginatedThreadsResponse)
def list_threads(
    category: str | None = Query(default=None),
    sort: str = Query(default="new", pattern="^(new|top|trending)$"),
    time_range: str = Query(default="all", pattern="^(all|hour|day|week|month|year)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    tag: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> PaginatedThreadsResponse:
    """
    GET /api/v1/threads

    List threads with pagination, sorting, and filtering.  Public endpoint
    (no auth required) — this powers the main feed on the homepage.

    Query parameters:
        - ``category`` — filter by category slug (e.g. ``?category=general``).
        - ``sort`` — ``new`` (default), ``top`` (most replies), or
          ``trending`` (replies / age).
        - ``time_range`` — ``all``, ``hour``, ``day``, ``week``, ``month``,
          ``year``.
        - ``page`` / ``page_size`` — cursor-free pagination.
        - ``tag`` — filter threads by tag name.

    Returns:
        ``PaginatedThreadsResponse`` with ``items``, ``total``, ``page``,
        ``page_size``, and ``total_pages``.
    """
    return list_threads_service(
        db,
        category,
        sort=sort,
        time_range=time_range,
        page=page,
        page_size=page_size,
        tag=tag,
    )


@thread_router.post(
    "", response_model=ThreadDetailResponse, status_code=status.HTTP_201_CREATED
)
def create_thread_endpoint(
    payload: ThreadCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ThreadDetailResponse:
    """
    POST /api/v1/threads

    Create a new discussion thread.  Requires authentication and an active
    (non-suspended, non-banned) account.

    The service layer handles:
        - Validating the category exists.
        - Creating/linking tags.
        - Auto-subscribing the author to the thread.
        - Detecting ``@pulse`` mentions to trigger the AI bot.
        - Recording an audit log entry.

    Args:
        payload: ``ThreadCreateRequest`` with title, body, category_id,
            optional tag_names and attachment_ids.

    Returns:
        Full ``ThreadDetailResponse`` (HTTP 201).
    """
    # require_can_participate raises 403 if user is suspended or banned.
    require_can_participate(current_user)
    return create_thread(db, payload, current_user)


@thread_router.get("/{thread_id}", response_model=ThreadDetailResponse)
def get_thread(thread_id: int, db: Session = Depends(get_db)) -> ThreadDetailResponse:
    """
    GET /api/v1/threads/{thread_id}

    Retrieve a single thread with all its posts arranged in a nested
    comment tree.  Public endpoint — no auth required.

    Returns:
        ``ThreadDetailResponse`` including the thread metadata, vote score,
        reactions, and a recursive ``posts`` tree.
    """
    return get_thread_detail(db, thread_id)


@thread_router.patch("/{thread_id}", response_model=ThreadDetailResponse)
def update_thread_endpoint(
    thread_id: int,
    payload: ThreadUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ThreadDetailResponse:
    """
    PATCH /api/v1/threads/{thread_id}

    Edit a thread's title and body.  Allowed for:
        - The thread author (owner).
        - Moderators (for threads in their assigned categories).
        - Admins (any thread).

    If edited by a non-owner, a notification is sent to the original author.
    """
    return update_thread(db, thread_id, payload, current_user)


@thread_router.delete("/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_thread_endpoint(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """
    DELETE /api/v1/threads/{thread_id}

    Delete a thread and all its posts (cascade).  Same permission model
    as update: owner, moderator (scoped), or admin.

    Returns:
        HTTP 204 No Content on success.
    """
    delete_thread(db, thread_id, current_user)


@thread_router.get("/{thread_id}/posts", response_model=list[PostResponse])
def list_thread_posts(
    thread_id: int, db: Session = Depends(get_db)
) -> list[PostResponse]:
    """
    GET /api/v1/threads/{thread_id}/posts

    List all posts (replies) for a thread as a flat-then-nested tree.
    This is a convenience endpoint — the same data is also available
    inside the ``posts`` field of ``GET /threads/{id}``.
    """
    return get_thread_detail(db, thread_id).posts


@thread_router.post(
    "/{thread_id}/posts",
    response_model=PostResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_thread_post(
    thread_id: int,
    payload: PostCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PostResponse:
    """
    POST /api/v1/threads/{thread_id}/posts

    Create a new reply in a thread.  Requires authentication and an active
    account.  Locked threads reject new replies (HTTP 400).

    Nested replies are supported via ``parent_post_id`` — if provided, the
    new post is a reply *to that specific post*, creating a comment tree.

    After creation:
        1. A ``post_created`` event is broadcast on the thread's WebSocket
           channel for live updates.
        2. Notifications are sent to: the thread author, the parent post
           author (if replying to a specific comment), any ``@mentioned``
           users, and all thread subscribers.

    Args:
        payload: ``PostCreateRequest`` with body, optional parent_post_id,
            and optional attachment_ids.

    Returns:
        The created ``PostResponse`` (HTTP 201).
    """
    require_can_participate(current_user)

    # create_post returns both the serialised post AND a list of user IDs
    # that should receive real-time notifications.
    post, recipient_ids = create_post(
        db,
        thread_id,
        payload.body,
        current_user,
        payload.parent_post_id,
        payload.attachment_ids,
    )

    # --- Real-time broadcasting ---
    # 1. Broadcast the new post to everyone viewing this thread.
    thread_event = jsonable_encoder(
        {
            "event": "post_created",
            "thread_id": thread_id,
            "post": post.model_dump(),
        }
    )
    await connection_manager.broadcast(f"thread:{thread_id}", thread_event)
    publish_event(f"thread:{thread_id}", thread_event)

    # 2. Send individual notification events to each recipient's personal
    #    notification channel (used by the notification bell in the navbar).
    for recipient_id in recipient_ids:
        notification_event = jsonable_encoder(
            {
                "event": "notification_created",
                "notification_type": "reply",
                "thread_id": thread_id,
                "title": f"{current_user.username} added a new reply",
            }
        )
        await connection_manager.broadcast(
            f"notifications:{recipient_id}", notification_event
        )
        publish_event(f"notifications:{recipient_id}", notification_event)

    return post


@thread_router.post("/{thread_id}/subscribe", status_code=status.HTTP_204_NO_CONTENT)
def subscribe_thread(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """
    POST /api/v1/threads/{thread_id}/subscribe

    Subscribe to a thread to receive notifications when new replies are
    posted.  Idempotent — subscribing to an already-subscribed thread is
    a no-op.

    Note: Thread authors are auto-subscribed when they create a thread.
    """
    subscribe_to_thread(db, thread_id, current_user)


# --- Votes ---


@thread_router.post("/{thread_id}/vote", response_model=VoteResponse)
async def vote_on_thread(
    thread_id: int,
    payload: VoteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VoteResponse:
    """
    POST /api/v1/threads/{thread_id}/vote

    Upvote or downvote a thread.  ``value`` must be ``1`` or ``-1``.
    Voting the same direction twice removes the vote (toggle).  Voting
    the opposite direction flips the vote.

    Broadcasts a ``vote_updated`` event so other users see the score
    change in real-time.
    """
    result = cast_vote(db, current_user.id, "thread", thread_id, payload.value)

    # Broadcast updated score to everyone viewing this thread.
    vote_event = jsonable_encoder(
        {
            "event": "vote_updated",
            "entity_type": "thread",
            "entity_id": thread_id,
            "vote_score": result.vote_score,
        }
    )
    await connection_manager.broadcast(f"thread:{thread_id}", vote_event)
    publish_event(f"thread:{thread_id}", vote_event)
    return result


@thread_router.delete("/{thread_id}/vote", response_model=VoteResponse)
async def unvote_thread(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VoteResponse:
    """
    DELETE /api/v1/threads/{thread_id}/vote

    Remove the current user's vote on a thread.  Unlike POST (which
    toggles), this unconditionally clears the vote.
    """
    result = remove_vote(db, current_user.id, "thread", thread_id)
    vote_event = jsonable_encoder(
        {
            "event": "vote_updated",
            "entity_type": "thread",
            "entity_id": thread_id,
            "vote_score": result.vote_score,
        }
    )
    await connection_manager.broadcast(f"thread:{thread_id}", vote_event)
    publish_event(f"thread:{thread_id}", vote_event)
    return result


# --- Reactions ---


@thread_router.post("/{thread_id}/react", response_model=list[ReactionCountResponse])
async def react_to_thread(
    thread_id: int,
    payload: ReactionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ReactionCountResponse]:
    """
    POST /api/v1/threads/{thread_id}/react

    Toggle an emoji reaction on a thread.  If the user already reacted
    with the same emoji, it is removed; otherwise it is added.

    Returns the complete updated reaction counts for this thread.
    """
    result = toggle_reaction(db, current_user.id, "thread", thread_id, payload.emoji)

    # Broadcast so all viewers see the updated reaction badges.
    reaction_event = jsonable_encoder(
        {
            "event": "reaction_updated",
            "entity_type": "thread",
            "entity_id": thread_id,
            "reactions": [{"emoji": r.emoji, "count": r.count} for r in result],
        }
    )
    await connection_manager.broadcast(f"thread:{thread_id}", reaction_event)
    publish_event(f"thread:{thread_id}", reaction_event)
    return result


# --- Report ---


@thread_router.post(
    "/{thread_id}/report",
    response_model=ContentReportResponse,
    status_code=status.HTTP_201_CREATED,
)
def report_thread(
    thread_id: int,
    payload: ContentReportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ContentReportResponse:
    """
    POST /api/v1/threads/{thread_id}/report

    Report a thread for moderator review.  Each user can only report a
    given thread once (duplicate reports return HTTP 409).

    Returns:
        ``ContentReportResponse`` (HTTP 201).
    """
    return report_content(db, current_user.id, "thread", thread_id, payload.reason)


# --- Voters ---


@thread_router.get("/{thread_id}/voters", response_model=list[VoterResponse])
def list_thread_voters(
    thread_id: int,
    db: Session = Depends(get_db),
) -> list[VoterResponse]:
    """
    GET /api/v1/threads/{thread_id}/voters

    List all users who voted on this thread, including their vote
    direction (+1 or -1) and avatars.  Used by the "who voted?" popover
    in the frontend.
    """
    return get_voters(db, "thread", thread_id)


# ===========================================================================
# Posts
# ===========================================================================

post_router = APIRouter()


@post_router.get("/{post_id}", response_model=PostResponse)
def get_post(post_id: int, db: Session = Depends(get_db)) -> PostResponse:
    """
    GET /api/v1/posts/{post_id}

    Retrieve a single post by ID, including its direct replies.  Public
    endpoint — no auth required.
    """
    return get_post_by_id(db, post_id)


@post_router.patch("/{post_id}", response_model=PostResponse)
def update_post_endpoint(
    post_id: int,
    payload: PostUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PostResponse:
    """
    PATCH /api/v1/posts/{post_id}

    Edit a post's body.  Allowed for: the post author, moderators (scoped
    to their categories), and admins.

    If edited by a non-owner, a notification is sent to the original author.
    """
    return update_post(db, post_id, payload.body, current_user)


@post_router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_post_endpoint(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """
    DELETE /api/v1/posts/{post_id}

    Delete a post.  Same permission model as update.

    Returns:
        HTTP 204 No Content on success.
    """
    delete_post(db, post_id, current_user)


# --- Votes ---


@post_router.post("/{post_id}/vote", response_model=VoteResponse)
async def vote_on_post(
    post_id: int,
    payload: VoteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VoteResponse:
    """
    POST /api/v1/posts/{post_id}/vote

    Upvote or downvote a post (reply).  Same toggle semantics as thread
    voting.  The ``vote_updated`` event is broadcast on the parent thread's
    WebSocket channel (since post votes are displayed on the thread page).
    """
    result = cast_vote(db, current_user.id, "post", post_id, payload.value)

    # Look up the post's parent thread to broadcast on the correct channel.
    post = get_post_by_id(db, post_id)
    vote_event = jsonable_encoder(
        {
            "event": "vote_updated",
            "entity_type": "post",
            "entity_id": post_id,
            "vote_score": result.vote_score,
        }
    )
    await connection_manager.broadcast(f"thread:{post.thread_id}", vote_event)
    publish_event(f"thread:{post.thread_id}", vote_event)
    return result


@post_router.delete("/{post_id}/vote", response_model=VoteResponse)
async def unvote_post(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VoteResponse:
    """
    DELETE /api/v1/posts/{post_id}/vote

    Remove the current user's vote on a post.
    """
    result = remove_vote(db, current_user.id, "post", post_id)
    post = get_post_by_id(db, post_id)
    vote_event = jsonable_encoder(
        {
            "event": "vote_updated",
            "entity_type": "post",
            "entity_id": post_id,
            "vote_score": result.vote_score,
        }
    )
    await connection_manager.broadcast(f"thread:{post.thread_id}", vote_event)
    publish_event(f"thread:{post.thread_id}", vote_event)
    return result


# --- Reactions ---


@post_router.post("/{post_id}/react", response_model=list[ReactionCountResponse])
async def react_to_post(
    post_id: int,
    payload: ReactionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ReactionCountResponse]:
    """
    POST /api/v1/posts/{post_id}/react

    Toggle an emoji reaction on a post.  Same toggle semantics as thread
    reactions.  Event is broadcast on the parent thread's channel.
    """
    result = toggle_reaction(db, current_user.id, "post", post_id, payload.emoji)
    post = get_post_by_id(db, post_id)
    reaction_event = jsonable_encoder(
        {
            "event": "reaction_updated",
            "entity_type": "post",
            "entity_id": post_id,
            "reactions": [{"emoji": r.emoji, "count": r.count} for r in result],
        }
    )
    await connection_manager.broadcast(f"thread:{post.thread_id}", reaction_event)
    publish_event(f"thread:{post.thread_id}", reaction_event)
    return result


# --- Voters ---


@post_router.get("/{post_id}/voters", response_model=list[VoterResponse])
def list_post_voters(
    post_id: int,
    db: Session = Depends(get_db),
) -> list[VoterResponse]:
    """
    GET /api/v1/posts/{post_id}/voters

    List all users who voted on this post, with their vote direction.
    """
    return get_voters(db, "post", post_id)


# --- Report ---


@post_router.post(
    "/{post_id}/report",
    response_model=ContentReportResponse,
    status_code=status.HTTP_201_CREATED,
)
def report_post(
    post_id: int,
    payload: ContentReportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ContentReportResponse:
    """
    POST /api/v1/posts/{post_id}/report

    Report a post for moderator review.  Duplicate reports from the same
    user are rejected (HTTP 409).
    """
    return report_content(db, current_user.id, "post", post_id, payload.reason)


# ===========================================================================
# Search
# ===========================================================================

search_router = APIRouter()


@search_router.get("", response_model=SearchResponse)
def search_content(
    q: str = Query(default="", max_length=200),
    category: str | None = Query(default=None, max_length=120),
    author: str | None = Query(default=None, max_length=50),
    content_type: str | None = Query(
        default=None, alias="type", pattern="^(thread|post)$"
    ),
    tag: str | None = Query(default=None, max_length=60),
    db: Session = Depends(get_db),
) -> SearchResponse:
    """
    GET /api/v1/search

    Search across threads and posts using case-insensitive substring
    matching (SQL ILIKE).  Public endpoint — no auth required.

    Query parameters:
        - ``q`` — search term (max 200 chars).
        - ``category`` — optional category slug filter.
        - ``author`` — optional author username filter.
        - ``type`` — ``"thread"`` or ``"post"`` (omit for both).
        - ``tag`` — optional tag name filter (threads only).

    Returns:
        ``SearchResponse`` with matched results and total count.
    """
    return search_forum(db, q, category, author, content_type, tag=tag)

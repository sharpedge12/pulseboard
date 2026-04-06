"""Forum service routes — categories, threads, posts, search."""

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

# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

category_router = APIRouter()


@category_router.get("", response_model=list[CategoryResponse])
def list_categories(db: Session = Depends(get_db)) -> list[CategoryResponse]:
    return list_categories_service(db)


@category_router.post(
    "", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED
)
async def create_category_endpoint(
    payload: CategoryCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CategoryResponse:
    category = create_category(db, payload, current_user)

    event = jsonable_encoder(
        {
            "event": "category_created",
            "category": category.model_dump(),
        }
    )
    await connection_manager.broadcast("global", event)
    publish_event("global", event)

    return category


# ---------------------------------------------------------------------------
# Threads
# ---------------------------------------------------------------------------

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
    require_can_participate(current_user)
    return create_thread(db, payload, current_user)


@thread_router.get("/{thread_id}", response_model=ThreadDetailResponse)
def get_thread(thread_id: int, db: Session = Depends(get_db)) -> ThreadDetailResponse:
    return get_thread_detail(db, thread_id)


@thread_router.patch("/{thread_id}", response_model=ThreadDetailResponse)
def update_thread_endpoint(
    thread_id: int,
    payload: ThreadUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ThreadDetailResponse:
    return update_thread(db, thread_id, payload, current_user)


@thread_router.delete("/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_thread_endpoint(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    delete_thread(db, thread_id, current_user)


@thread_router.get("/{thread_id}/posts", response_model=list[PostResponse])
def list_thread_posts(
    thread_id: int, db: Session = Depends(get_db)
) -> list[PostResponse]:
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
    require_can_participate(current_user)
    post, recipient_ids = create_post(
        db,
        thread_id,
        payload.body,
        current_user,
        payload.parent_post_id,
        payload.attachment_ids,
    )

    thread_event = jsonable_encoder(
        {
            "event": "post_created",
            "thread_id": thread_id,
            "post": post.model_dump(),
        }
    )
    await connection_manager.broadcast(f"thread:{thread_id}", thread_event)
    publish_event(f"thread:{thread_id}", thread_event)

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
    subscribe_to_thread(db, thread_id, current_user)


# --- Votes ---


@thread_router.post("/{thread_id}/vote", response_model=VoteResponse)
async def vote_on_thread(
    thread_id: int,
    payload: VoteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VoteResponse:
    result = cast_vote(db, current_user.id, "thread", thread_id, payload.value)
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
    result = toggle_reaction(db, current_user.id, "thread", thread_id, payload.emoji)
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
    return report_content(db, current_user.id, "thread", thread_id, payload.reason)


# --- Voters ---


@thread_router.get("/{thread_id}/voters", response_model=list[VoterResponse])
def list_thread_voters(
    thread_id: int,
    db: Session = Depends(get_db),
) -> list[VoterResponse]:
    return get_voters(db, "thread", thread_id)


# ---------------------------------------------------------------------------
# Posts
# ---------------------------------------------------------------------------

post_router = APIRouter()


@post_router.get("/{post_id}", response_model=PostResponse)
def get_post(post_id: int, db: Session = Depends(get_db)) -> PostResponse:
    return get_post_by_id(db, post_id)


@post_router.patch("/{post_id}", response_model=PostResponse)
def update_post_endpoint(
    post_id: int,
    payload: PostUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PostResponse:
    return update_post(db, post_id, payload.body, current_user)


@post_router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_post_endpoint(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    delete_post(db, post_id, current_user)


# --- Votes ---


@post_router.post("/{post_id}/vote", response_model=VoteResponse)
async def vote_on_post(
    post_id: int,
    payload: VoteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VoteResponse:
    result = cast_vote(db, current_user.id, "post", post_id, payload.value)
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
    return report_content(db, current_user.id, "post", post_id, payload.reason)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

search_router = APIRouter()


@search_router.get("", response_model=SearchResponse)
def search_content(
    q: str = Query(default=""),
    category: str | None = Query(default=None),
    author: str | None = Query(default=None),
    content_type: str | None = Query(default=None, alias="type"),
    tag: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> SearchResponse:
    return search_forum(db, q, category, author, content_type, tag=tag)

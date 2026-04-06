"""Forum service business logic — threads, posts, categories."""

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


def _post_author(user: User) -> PostAuthorResponse:
    return PostAuthorResponse(
        id=user.id,
        username=user.username,
        role=user.role.value,
        avatar_url=user.avatar_url,
    )


def _thread_author(user: User) -> ThreadAuthorResponse:
    return ThreadAuthorResponse(
        id=user.id,
        username=user.username,
        role=user.role.value,
        avatar_url=user.avatar_url,
    )


def _thread_category(category: Category) -> ThreadCategoryResponse:
    return ThreadCategoryResponse(
        id=category.id, title=category.title, slug=category.slug
    )


def _build_post_tree(
    posts: list[Post],
    attachment_map: dict[int, list],
    vote_scores: dict[int, int] | None = None,
    reaction_map: dict[int, list] | None = None,
) -> list[PostResponse]:
    vote_scores = vote_scores or {}
    reaction_map = reaction_map or {}
    post_map: dict[int, PostResponse] = {}
    roots: list[PostResponse] = []

    ordered_posts = sorted(posts, key=lambda post: (post.created_at, post.id))
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
            replies=[],
        )

    for post in ordered_posts:
        node = post_map[post.id]
        if post.parent_post_id and post.parent_post_id in post_map:
            post_map[post.parent_post_id].replies.append(node)
        else:
            roots.append(node)

    return roots


def _serialize_thread(
    thread: Thread,
    attachment_map: dict[int, list] | None = None,
    vote_score: int = 0,
    user_vote: int = 0,
    reactions: list | None = None,
    reply_count: int | None = None,
) -> ThreadListItemResponse:
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


def list_categories(db: Session) -> list[CategoryResponse]:
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
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can create communities. Moderators can request a new community from the dashboard.",
        )

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


def list_threads(
    db: Session,
    category_slug: str | None = None,
    sort: str = "new",
    time_range: str = "all",
    page: int = 1,
    page_size: int = 10,
    tag: str | None = None,
) -> PaginatedThreadsResponse:
    # Build a base filter that both the count query and the data query share.
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

    # --- Count total matching threads (no eager-loading needed) ---
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
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))

    # --- Subquery for reply_count to avoid loading all posts ---
    reply_count_sq = (
        select(func.count(Post.id))
        .where(Post.thread_id == Thread.id)
        .correlate(Thread)
        .scalar_subquery()
        .label("reply_count")
    )

    # --- Data query with SQL-level pagination ---
    query = select(Thread, reply_count_sq).options(
        selectinload(Thread.author),
        selectinload(Thread.category),
        selectinload(Thread.tags),
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

    # --- Ordering ---
    if sort == "top":
        query = query.order_by(
            Thread.is_pinned.desc(),
            reply_count_sq.desc(),
            Thread.created_at.desc(),
        )
    elif sort == "trending":
        # Approximate trending score: reply_count / (age_in_hours + 1)
        # SQLAlchemy expression for hours since creation
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

    offset = (page - 1) * page_size
    query = query.limit(page_size).offset(offset)

    rows = db.execute(query).unique().all()

    threads_with_counts: list[tuple[Thread, int]] = [
        (row[0], row[1] or 0) for row in rows
    ]

    thread_ids = [t.id for t, _ in threads_with_counts]

    attachment_map = list_attachments(db, "thread", thread_ids)
    vote_scores = get_vote_scores_bulk(db, "thread", thread_ids)
    reaction_map = get_reaction_counts_bulk(db, "thread", thread_ids)

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


def get_thread_detail(db: Session, thread_id: int) -> ThreadDetailResponse:
    thread = db.execute(
        select(Thread)
        .where(Thread.id == thread_id)
        .options(
            selectinload(Thread.author),
            selectinload(Thread.category),
            selectinload(Thread.posts).selectinload(Post.author),
            selectinload(Thread.tags),
        )
    ).scalar_one_or_none()
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found."
        )

    thread_attachments = list_attachments(db, "thread", [thread.id])
    post_attachments = list_attachments(db, "post", [post.id for post in thread.posts])

    thread_vote_score = _get_vote_score(db, "thread", thread.id)
    thread_reactions = get_reaction_counts(db, "thread", thread.id)

    post_ids = [post.id for post in thread.posts]
    post_vote_scores = get_vote_scores_bulk(db, "post", post_ids)
    post_reaction_map = get_reaction_counts_bulk(db, "post", post_ids)

    return ThreadDetailResponse(
        **_serialize_thread(
            thread,
            thread_attachments,
            vote_score=thread_vote_score,
            reactions=thread_reactions,
        ).model_dump(),
        posts=_build_post_tree(
            thread.posts,
            post_attachments,
            post_vote_scores,
            post_reaction_map,
        ),
    )


def create_thread(
    db: Session, payload: ThreadCreateRequest, current_user: User
) -> ThreadDetailResponse:
    category = db.execute(
        select(Category).where(Category.id == payload.category_id)
    ).scalar_one_or_none()
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Category not found."
        )

    thread = Thread(
        category_id=payload.category_id,
        author_id=current_user.id,
        title=payload.title,
        body=payload.body,
    )
    db.add(thread)
    db.flush()

    if payload.tag_names:
        from shared.models.tag import Tag

        for tag_name in payload.tag_names:
            tag_name = tag_name.strip().lower()
            if not tag_name:
                continue
            tag = db.execute(
                select(Tag).where(Tag.name == tag_name)
            ).scalar_one_or_none()
            if not tag:
                tag = Tag(name=tag_name)
                db.add(tag)
                db.flush()
            thread.tags.append(tag)

    assign_attachments_to_entity(
        db,
        current_user,
        payload.attachment_ids,
        "thread",
        thread.id,
    )
    db.add(ThreadSubscription(thread_id=thread.id, user_id=current_user.id))
    audit_record(
        db,
        actor_id=current_user.id,
        action=audit_actions.THREAD_CREATE,
        entity_type="thread",
        entity_id=thread.id,
        details={"title": payload.title, "category_id": payload.category_id},
    )
    db.commit()
    return get_thread_detail(db, thread.id)


def update_thread(
    db: Session,
    thread_id: int,
    payload: ThreadUpdateRequest,
    current_user: User,
) -> ThreadDetailResponse:
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
            detail="Not allowed to edit this thread.",
        )

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

    if not is_owner and current_user.role == UserRole.MODERATOR:
        allowed_ids = get_moderator_category_ids(db, current_user)
        if allowed_ids is not None and thread.category_id not in allowed_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Thread is outside your assigned communities.",
            )

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

    if not is_owner:
        create_notification(
            db,
            user_id=thread.author_id,
            notification_type="post_edited",
            title=f"{current_user.username} edited your thread",
            payload={"thread_id": thread.id},
        )

    return get_thread_detail(db, thread.id)


def create_post(
    db: Session,
    thread_id: int,
    body: str,
    current_user: User,
    parent_post_id: int | None = None,
    attachment_ids: list[int] | None = None,
) -> tuple[PostResponse, list[int]]:
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

    parent_post = None
    if parent_post_id is not None:
        parent_post = db.execute(
            select(Post).where(Post.id == parent_post_id, Post.thread_id == thread_id)
        ).scalar_one_or_none()
        if not parent_post:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Parent post not found."
            )

    post = Post(
        thread_id=thread_id,
        author_id=current_user.id,
        parent_post_id=parent_post_id,
        body=body,
    )
    db.add(post)
    db.flush()
    assign_attachments_to_entity(
        db,
        current_user,
        attachment_ids or [],
        "post",
        post.id,
    )
    if should_invoke_bot(body):
        # Bot reply is generated asynchronously in a background thread.
        # The user's post is committed immediately; the bot reply will
        # appear via WebSocket once ready.
        schedule_forum_bot_reply(
            thread_id=thread_id,
            thread_title=thread.title,
            thread_body=thread.body,
            parent_post_id=post.id,
            user_message=body,
            poster_user_id=current_user.id,
        )
    audit_record(
        db,
        actor_id=current_user.id,
        action=audit_actions.POST_CREATE,
        entity_type="post",
        entity_id=post.id,
        details={"thread_id": thread_id},
    )
    db.commit()
    created_post = db.execute(
        select(Post)
        .where(Post.id == post.id)
        .options(selectinload(Post.author), selectinload(Post.replies))
    ).scalar_one()
    recipient_ids: set[int] = set()
    if thread.author_id != current_user.id:
        recipient_ids.add(thread.author_id)
        create_notification(
            db,
            user_id=thread.author_id,
            notification_type="reply",
            title=f"{current_user.username} replied to your thread",
            payload={"thread_id": thread.id, "post_id": created_post.id},
        )

    if parent_post and parent_post.author_id != current_user.id:
        recipient_ids.add(parent_post.author_id)
        create_notification(
            db,
            user_id=parent_post.author_id,
            notification_type="mention_reply",
            title=f"{current_user.username} replied to your comment",
            payload={"thread_id": thread.id, "post_id": created_post.id},
        )

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

    db.commit()
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


def update_post(
    db: Session,
    post_id: int,
    body: str,
    current_user: User,
) -> PostResponse:
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

    if not is_owner:
        create_notification(
            db,
            user_id=post.author_id,
            notification_type="post_edited",
            title=f"{current_user.username} edited your post",
            payload={"thread_id": post.thread_id, "post_id": post.id},
        )

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


def get_post_by_id(db: Session, post_id: int) -> PostResponse:
    post = db.execute(
        select(Post)
        .where(Post.id == post_id)
        .options(
            selectinload(Post.author),
            selectinload(Post.replies).selectinload(Post.author),
        )
    ).scalar_one_or_none()
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Post not found."
        )

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


def subscribe_to_thread(db: Session, thread_id: int, current_user: User) -> None:
    thread = db.execute(
        select(Thread).where(Thread.id == thread_id)
    ).scalar_one_or_none()
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found."
        )

    existing = db.execute(
        select(ThreadSubscription).where(
            ThreadSubscription.thread_id == thread_id,
            ThreadSubscription.user_id == current_user.id,
        )
    ).scalar_one_or_none()
    if not existing:
        db.add(ThreadSubscription(thread_id=thread_id, user_id=current_user.id))
        db.commit()


def delete_thread(db: Session, thread_id: int, current_user: User) -> None:
    """Delete a thread. Owners, moderators, and admins may delete."""
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


def delete_post(db: Session, post_id: int, current_user: User) -> None:
    """Delete a post. Owners, moderators, and admins may delete."""
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

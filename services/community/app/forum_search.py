"""Forum search logic."""

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
    cleaned_query = query.strip()
    if not cleaned_query:
        return SearchResponse(query=query, total=0, results=[])

    results: list[SearchResultItem] = []
    # Escape SQL LIKE wildcards to prevent pattern injection
    escaped = (
        cleaned_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    search_pattern = f"%{escaped}%"

    if content_type in {None, "thread"}:
        thread_query = (
            select(Thread)
            .join(Thread.author)
            .join(Thread.category)
            .outerjoin(ThreadTag, ThreadTag.thread_id == Thread.id)
            .outerjoin(Tag, Tag.id == ThreadTag.tag_id)
            .options(selectinload(Thread.author), selectinload(Thread.category))
            .where(
                or_(
                    Thread.title.ilike(search_pattern),
                    Thread.body.ilike(search_pattern),
                    User.username.ilike(search_pattern),
                    Category.title.ilike(search_pattern),
                    Tag.name.ilike(search_pattern),
                )
            )
            .order_by(Thread.created_at.desc())
        )
        if category:
            thread_query = thread_query.where(Category.slug == category)
        if author:
            thread_query = thread_query.where(User.username == author)
        if tag:
            thread_query = thread_query.where(Tag.name == tag)

        threads = db.execute(thread_query).scalars().unique().all()
        for thread in threads:
            results.append(
                SearchResultItem(
                    result_type="thread",
                    id=thread.id,
                    title=thread.title,
                    snippet=thread.body[:180],
                    category=thread.category.title,
                    author=thread.author.username,
                    thread_id=thread.id,
                )
            )

    if content_type in {None, "post"}:
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
                    title=f"Reply in {post.thread.title}",
                    snippet=post.body[:180],
                    category=post.thread.category.title,
                    author=post.author.username,
                    thread_id=post.thread.id,
                )
            )

    return SearchResponse(query=query, total=len(results), results=results)

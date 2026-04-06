"""Forum tests for the microservice architecture."""

from sqlalchemy import select

from shared.models.category import Category
from shared.models.user import User, UserRole
from tests.conftest import register_verified_user


def _register_and_login(client, email: str, username: str) -> dict[str, str]:
    session = register_verified_user(client, email, username)
    return {"Authorization": f"Bearer {session['access_token']}"}


def test_admin_can_create_category(client, db_session) -> None:
    headers = _register_and_login(client, "admin@example.com", "adminuser")
    admin = db_session.execute(
        select(User).where(User.email == "admin@example.com")
    ).scalar_one()
    admin.role = UserRole.ADMIN
    db_session.commit()

    response = client.post(
        "/api/v1/categories",
        headers=headers,
        json={
            "title": "Announcements",
            "slug": "announcements",
            "description": "Important updates",
        },
    )

    assert response.status_code == 201
    assert response.json()["slug"] == "announcements"


def test_member_cannot_create_category(client) -> None:
    headers = _register_and_login(client, "member@example.com", "memberuser")

    response = client.post(
        "/api/v1/categories",
        headers=headers,
        json={
            "title": "Secret",
            "slug": "secret",
            "description": "Nope",
        },
    )

    assert response.status_code == 403


def test_moderator_cannot_directly_create_category(client, db_session) -> None:
    """Moderators must request community creation; direct creation is admin-only."""
    headers = _register_and_login(client, "mod@example.com", "moduser")
    moderator = db_session.execute(
        select(User).where(User.email == "mod@example.com")
    ).scalar_one()
    moderator.role = UserRole.MODERATOR
    db_session.commit()

    response = client.post(
        "/api/v1/categories",
        headers=headers,
        json={
            "title": "Community Launch",
            "slug": "community-launch",
            "description": "New community created by a moderator",
        },
    )

    assert response.status_code == 403


def test_moderator_can_request_category(client, db_session) -> None:
    """Moderators can submit a category creation request."""
    headers = _register_and_login(client, "mod2@example.com", "moduser2")
    moderator = db_session.execute(
        select(User).where(User.email == "mod2@example.com")
    ).scalar_one()
    moderator.role = UserRole.MODERATOR
    db_session.commit()

    response = client.post(
        "/api/v1/admin/category-requests",
        headers=headers,
        json={
            "title": "Community Launch",
            "slug": "community-launch",
            "description": "New community requested by a moderator",
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Community Launch"
    assert data["status"] == "pending"


def test_thread_and_nested_post_flow(client, db_session) -> None:
    headers = _register_and_login(client, "forum@example.com", "forumuser")
    category = db_session.execute(
        select(Category).where(Category.slug == "general")
    ).scalar_one()

    thread_response = client.post(
        "/api/v1/threads",
        headers=headers,
        json={
            "category_id": category.id,
            "title": "How should Redis caching work?",
            "body": "I want to discuss cache invalidation strategies.",
        },
    )
    assert thread_response.status_code == 201
    thread_id = thread_response.json()["id"]

    root_post = client.post(
        f"/api/v1/threads/{thread_id}/posts",
        headers=headers,
        json={"body": "Start with category and thread list caching."},
    )
    assert root_post.status_code == 201
    root_post_id = root_post.json()["id"]

    reply_post = client.post(
        f"/api/v1/threads/{thread_id}/posts",
        headers=headers,
        json={
            "body": "Use Redis keys scoped by thread id.",
            "parent_post_id": root_post_id,
        },
    )
    assert reply_post.status_code == 201

    detail_response = client.get(f"/api/v1/threads/{thread_id}")
    assert detail_response.status_code == 200
    detail_body = detail_response.json()
    assert detail_body["title"] == "How should Redis caching work?"
    assert detail_body["reply_count"] == 2
    assert len(detail_body["posts"]) == 1
    assert len(detail_body["posts"][0]["replies"]) == 1


def test_search_returns_thread_and_post_matches(client, db_session) -> None:
    headers = _register_and_login(client, "search@example.com", "searchuser")
    category = db_session.execute(
        select(Category).where(Category.slug == "backend")
    ).scalar_one()

    thread_response = client.post(
        "/api/v1/threads",
        headers=headers,
        json={
            "category_id": category.id,
            "title": "FastAPI search indexing",
            "body": "Thread body about search ranking and indexing.",
        },
    )
    thread_id = thread_response.json()["id"]

    client.post(
        f"/api/v1/threads/{thread_id}/posts",
        headers=headers,
        json={"body": "Post content also talks about search filters."},
    )

    response = client.get("/api/v1/search", params={"q": "search"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 2
    result_types = {item["result_type"] for item in body["results"]}
    assert "thread" in result_types
    assert "post" in result_types


def test_default_categories_are_available(client) -> None:
    response = client.get("/api/v1/categories")
    assert response.status_code == 200
    assert len(response.json()) >= 1

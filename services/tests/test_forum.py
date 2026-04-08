"""
Forum tests for the PulseBoard microservice architecture.

INTERVIEW CONCEPTS:
    These 7 tests validate the core forum functionality and its
    role-based access control (RBAC):

    1. **Admin can create category** — admin-only privilege
    2. **Member cannot create category** — RBAC enforcement
    3. **Moderator cannot create category directly** — separation of powers
    4. **Moderator can request category** — approval workflow
    5. **Thread and nested post flow** — the core forum data model
    6. **Search returns threads and posts** — full-text search
    7. **Default categories available** — seed data verification

    ROLE-BASED ACCESS CONTROL (RBAC):
    PulseBoard has 3 roles with different permissions:
    - ADMIN: Can do everything (create categories, manage users, etc.)
    - MODERATOR: Can moderate content, request new categories (needs admin approval)
    - MEMBER: Can create threads, posts, vote, react (but no admin features)

    This is the Principle of Least Privilege — each role has only the
    permissions it needs. Moderators can't create categories directly
    because category creation affects the entire platform structure.

    TESTING STRATEGY:
    Tests use a helper ``_register_and_login`` to set up authenticated users.
    For admin/moderator tests, the user's role is modified directly in the
    database after registration (since there's no "promote" API endpoint
    in the test flow).
"""

from sqlalchemy import select

from shared.models.category import Category
from shared.models.user import User, UserRole
from tests.conftest import register_verified_user


def _register_and_login(client, email: str, username: str) -> dict[str, str]:
    """Register a verified user and return an Authorization header dict.

    This helper combines registration, verification, and login into one call,
    then formats the access token as an Authorization header ready to pass
    to subsequent API calls.

    Returns:
        A dict like ``{"Authorization": "Bearer eyJhbG..."}`` that can be
        passed directly as the ``headers`` argument to client methods.
    """
    session = register_verified_user(client, email, username)
    return {"Authorization": f"Bearer {session['access_token']}"}


def test_admin_can_create_category(client, db_session) -> None:
    """TEST 1: Admin users should be able to create new forum categories.

    What this validates:
    - POST /api/v1/categories returns 201 for admin users
    - The created category has the correct slug
    - Category creation is an admin-only operation

    INTERVIEW NOTE:
        The test manually sets the user's role to ADMIN in the database.
        In production, an existing admin would promote a user via the
        admin dashboard. Here, we bypass that to test the authorization
        check in isolation.
    """
    # Register a user and promote them to admin
    headers = _register_and_login(client, "admin@example.com", "adminuser")
    admin = db_session.execute(
        select(User).where(User.email == "admin@example.com")
    ).scalar_one()
    admin.role = UserRole.ADMIN
    db_session.commit()

    # Create a new category
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
    """TEST 2: Regular members should NOT be able to create categories.

    What this validates:
    - POST /api/v1/categories returns 403 Forbidden for member-role users
    - The endpoint checks the user's role before allowing the operation

    INTERVIEW NOTE:
        This is a NEGATIVE test — it verifies that the system correctly
        DENIES access. Negative tests are crucial for security; without
        them, you might accidentally remove an authorization check and
        never notice until a real user exploits it.
    """
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

    # 403 Forbidden — the user is authenticated but lacks permission
    assert response.status_code == 403


def test_moderator_cannot_directly_create_category(client, db_session) -> None:
    """TEST 3: Moderators must request community creation; direct creation is admin-only.

    What this validates:
    - Even moderators get 403 when trying to POST /api/v1/categories
    - Category creation requires ADMIN role, not just MODERATOR

    INTERVIEW NOTE:
        This enforces "separation of powers" — moderators can moderate
        content within existing categories, but they can't unilaterally
        create new top-level categories. They must submit a request that
        an admin reviews and approves. This prevents category sprawl.
    """
    headers = _register_and_login(client, "mod@example.com", "moduser")
    # Promote to moderator (not admin)
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

    # Still 403 — moderators can't create categories directly
    assert response.status_code == 403


def test_moderator_can_request_category(client, db_session) -> None:
    """TEST 4: Moderators can submit a category creation request for admin review.

    What this validates:
    - POST /api/v1/admin/category-requests returns 201 for moderators
    - The request is created with status "pending" (awaiting admin review)
    - The request contains the correct title

    INTERVIEW NOTE:
        This is an "approval workflow" pattern common in enterprise software.
        Instead of granting moderators direct creation power, they submit
        requests that admins can approve or reject. This balances autonomy
        (moderators can propose ideas) with control (admins make final decisions).
    """
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
    assert data["status"] == "pending"  # Awaiting admin review


def test_thread_and_nested_post_flow(client, db_session) -> None:
    """TEST 5: Create a thread, add a reply, add a nested reply, verify the structure.

    What this validates:
    - Thread creation (POST /api/v1/threads) returns 201
    - Post creation (POST /api/v1/threads/{id}/posts) returns 201
    - Nested replies via ``parent_post_id`` create a tree structure
    - Thread detail includes the correct reply count (2)
    - Posts are nested correctly: 1 top-level post with 1 reply

    INTERVIEW NOTE on nested comments:
        This is the Reddit-style comment threading model. Each post can
        have a ``parent_post_id`` that points to another post in the same
        thread. This creates a tree structure:

        Thread: "How should Redis caching work?"
        ├── Post 1: "Start with category and thread list caching."
        │   └── Reply: "Use Redis keys scoped by thread id."

        The API returns this as nested JSON:
        ``posts[0].replies[0]`` = the nested reply

        ``reply_count`` is 2 (total posts, both top-level and nested).
        ``len(posts)`` is 1 (only top-level posts at the root level).
    """
    headers = _register_and_login(client, "forum@example.com", "forumuser")
    # Look up the "general" category created by the setup_database fixture
    category = db_session.execute(
        select(Category).where(Category.slug == "general")
    ).scalar_one()

    # Create a thread in the General Discussion category
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

    # Create a top-level reply (root post — no parent_post_id)
    root_post = client.post(
        f"/api/v1/threads/{thread_id}/posts",
        headers=headers,
        json={"body": "Start with category and thread list caching."},
    )
    assert root_post.status_code == 201
    root_post_id = root_post.json()["id"]

    # Create a nested reply (reply to the root post)
    reply_post = client.post(
        f"/api/v1/threads/{thread_id}/posts",
        headers=headers,
        json={
            "body": "Use Redis keys scoped by thread id.",
            "parent_post_id": root_post_id,  # Nesting: this reply is a child of root_post
        },
    )
    assert reply_post.status_code == 201

    # Fetch the thread detail and verify the nested structure
    detail_response = client.get(f"/api/v1/threads/{thread_id}")
    assert detail_response.status_code == 200
    detail_body = detail_response.json()

    assert detail_body["title"] == "How should Redis caching work?"
    assert detail_body["reply_count"] == 2  # Total: root post + nested reply
    assert len(detail_body["posts"]) == 1  # Only 1 top-level post
    assert len(detail_body["posts"][0]["replies"]) == 1  # 1 nested reply under it


def test_search_returns_thread_and_post_matches(client, db_session) -> None:
    """TEST 6: Search should return both thread and post matches.

    What this validates:
    - GET /api/v1/search?q=search returns results from both threads and posts
    - The search finds matches in thread titles, thread bodies, and post bodies
    - Results include a ``result_type`` field ("thread" or "post")
    - At least 2 results are returned (one thread, one post)

    INTERVIEW NOTE:
        Full-text search is a critical feature for any content platform.
        This test verifies that search indexes both threads and posts,
        and that the results distinguish between the two types. In
        production, this might use PostgreSQL's full-text search (tsvector)
        or an external service like Elasticsearch.
    """
    headers = _register_and_login(client, "search@example.com", "searchuser")
    category = db_session.execute(
        select(Category).where(Category.slug == "backend")
    ).scalar_one()

    # Create a thread with "search" in the title and body
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

    # Create a post with "search" in the body
    client.post(
        f"/api/v1/threads/{thread_id}/posts",
        headers=headers,
        json={"body": "Post content also talks about search filters."},
    )

    # Search for "search" — should match both the thread and the post
    response = client.get("/api/v1/search", params={"q": "search"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 2  # At least the thread + post we created
    result_types = {item["result_type"] for item in body["results"]}
    assert "thread" in result_types  # Thread title/body matched
    assert "post" in result_types  # Post body matched


def test_default_categories_are_available(client) -> None:
    """TEST 7: The default seeded categories should be available via the API.

    What this validates:
    - GET /api/v1/categories returns 200
    - At least 1 category exists (the setup_database fixture seeds 4)

    INTERVIEW NOTE:
        This is a "smoke test" — it verifies the most basic functionality
        works (categories endpoint is reachable and returns data). If this
        fails, it usually means the test database setup is broken.
    """
    response = client.get("/api/v1/categories")
    assert response.status_code == 200
    assert len(response.json()) >= 1

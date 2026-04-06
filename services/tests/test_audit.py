"""Audit log tests for the microservice architecture.

Validates that:
  1. Actions are recorded in the audit_logs table.
  2. The /admin/audit-logs endpoint returns paginated results.
  3. Role-based visibility is enforced (admin sees all, member sees own).
  4. Query filters (action, entity_type) work correctly.
"""

from sqlalchemy import select

from shared.models.audit_log import AuditLog
from shared.models.category import Category
from shared.models.user import User, UserRole
from tests.conftest import register_verified_user


def _register_and_login(client, email: str, username: str) -> dict[str, str]:
    session = register_verified_user(client, email, username)
    return {"Authorization": f"Bearer {session['access_token']}"}


def test_thread_creation_creates_audit_log(client, db_session) -> None:
    """Creating a thread should produce a thread_create audit entry."""
    headers = _register_and_login(client, "audit1@example.com", "audituser1")
    category = db_session.execute(
        select(Category).where(Category.slug == "general")
    ).scalar_one()

    response = client.post(
        "/api/v1/threads",
        headers=headers,
        json={
            "category_id": category.id,
            "title": "Audit test thread",
            "body": "Testing that audit logs are created.",
        },
    )
    assert response.status_code == 201
    thread_id = response.json()["id"]

    logs = (
        db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "thread_create",
                AuditLog.entity_id == thread_id,
            )
        )
        .scalars()
        .all()
    )
    assert len(logs) == 1
    assert logs[0].entity_type == "thread"


def test_post_creation_creates_audit_log(client, db_session) -> None:
    """Creating a post should produce a post_create audit entry."""
    headers = _register_and_login(client, "audit2@example.com", "audituser2")
    category = db_session.execute(
        select(Category).where(Category.slug == "general")
    ).scalar_one()

    thread_resp = client.post(
        "/api/v1/threads",
        headers=headers,
        json={
            "category_id": category.id,
            "title": "Thread for post audit",
            "body": "Body.",
        },
    )
    thread_id = thread_resp.json()["id"]

    post_resp = client.post(
        f"/api/v1/threads/{thread_id}/posts",
        headers=headers,
        json={"body": "Audited reply."},
    )
    assert post_resp.status_code == 201
    post_id = post_resp.json()["id"]

    logs = (
        db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "post_create",
                AuditLog.entity_id == post_id,
            )
        )
        .scalars()
        .all()
    )
    assert len(logs) == 1
    assert logs[0].entity_type == "post"


def test_register_creates_audit_log(client, db_session) -> None:
    """User registration should produce a user_register audit entry."""
    client.post(
        "/api/v1/auth/register",
        json={
            "email": "auditreg@example.com",
            "username": "auditreguser",
            "password": "supersecret",
        },
    )

    user = db_session.execute(
        select(User).where(User.email == "auditreg@example.com")
    ).scalar_one()

    logs = (
        db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "user_register",
                AuditLog.entity_id == user.id,
            )
        )
        .scalars()
        .all()
    )
    assert len(logs) == 1
    assert logs[0].entity_type == "user"
    assert logs[0].actor_id == user.id


def test_login_creates_audit_log(client, db_session) -> None:
    """Successful login should produce a user_login audit entry."""
    _register_and_login(client, "auditlogin@example.com", "auditloginuser")

    user = db_session.execute(
        select(User).where(User.email == "auditlogin@example.com")
    ).scalar_one()

    logs = (
        db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "user_login",
                AuditLog.actor_id == user.id,
            )
        )
        .scalars()
        .all()
    )
    assert len(logs) == 1
    assert logs[0].entity_type == "user"


def test_admin_sees_all_audit_logs(client, db_session) -> None:
    """Admin should see audit logs from all users via the endpoint."""
    # Create two users — one member, one admin
    member_headers = _register_and_login(
        client, "auditmember@example.com", "auditmember"
    )
    admin_headers = _register_and_login(client, "auditadmin@example.com", "auditadmin")
    admin_user = db_session.execute(
        select(User).where(User.email == "auditadmin@example.com")
    ).scalar_one()
    admin_user.role = UserRole.ADMIN
    db_session.commit()

    # Member creates a thread (generates audit entries)
    category = db_session.execute(
        select(Category).where(Category.slug == "general")
    ).scalar_one()
    client.post(
        "/api/v1/threads",
        headers=member_headers,
        json={
            "category_id": category.id,
            "title": "Member thread",
            "body": "Member body.",
        },
    )

    # Admin queries audit logs
    resp = client.get("/api/v1/admin/audit-logs", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] > 0
    assert len(data["items"]) > 0
    # Admin should see the member's thread_create entry
    actions = [item["action"] for item in data["items"]]
    assert "thread_create" in actions


def test_member_sees_only_own_audit_logs(client, db_session) -> None:
    """Members should only see their own audit logs."""
    # Create two members
    member1_headers = _register_and_login(client, "member1@example.com", "member1user")
    member2_headers = _register_and_login(client, "member2@example.com", "member2user")
    member1 = db_session.execute(
        select(User).where(User.email == "member1@example.com")
    ).scalar_one()

    # Both create threads
    category = db_session.execute(
        select(Category).where(Category.slug == "general")
    ).scalar_one()
    client.post(
        "/api/v1/threads",
        headers=member1_headers,
        json={
            "category_id": category.id,
            "title": "Member1 thread",
            "body": "Body.",
        },
    )
    client.post(
        "/api/v1/threads",
        headers=member2_headers,
        json={
            "category_id": category.id,
            "title": "Member2 thread",
            "body": "Body.",
        },
    )

    # Member1 queries audit logs — should only see own entries
    resp = client.get("/api/v1/admin/audit-logs", headers=member1_headers)
    assert resp.status_code == 200
    data = resp.json()
    for item in data["items"]:
        assert item["actor_id"] == member1.id


def test_audit_log_action_filter(client, db_session) -> None:
    """Filtering by action should return only matching entries."""
    headers = _register_and_login(client, "auditfilter@example.com", "auditfilteruser")
    # Register + login already produced user_register and user_login entries.
    # Create a thread to also produce thread_create.
    category = db_session.execute(
        select(Category).where(Category.slug == "general")
    ).scalar_one()
    client.post(
        "/api/v1/threads",
        headers=headers,
        json={
            "category_id": category.id,
            "title": "Filter test thread",
            "body": "Body.",
        },
    )

    # Filter by thread_create
    resp = client.get(
        "/api/v1/admin/audit-logs",
        headers=headers,
        params={"action": "thread_create"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    for item in data["items"]:
        assert item["action"] == "thread_create"


def test_audit_log_entity_type_filter(client, db_session) -> None:
    """Filtering by entity_type should return only matching entries."""
    headers = _register_and_login(client, "auditentity@example.com", "auditentityuser")
    category = db_session.execute(
        select(Category).where(Category.slug == "general")
    ).scalar_one()
    client.post(
        "/api/v1/threads",
        headers=headers,
        json={
            "category_id": category.id,
            "title": "Entity filter test",
            "body": "Body.",
        },
    )

    # Filter by entity_type=thread
    resp = client.get(
        "/api/v1/admin/audit-logs",
        headers=headers,
        params={"entity_type": "thread"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    for item in data["items"]:
        assert item["entity_type"] == "thread"


def test_audit_log_pagination(client, db_session) -> None:
    """Audit logs endpoint should respect page_size and return pagination metadata."""
    headers = _register_and_login(client, "auditpage@example.com", "auditpageuser")
    category = db_session.execute(
        select(Category).where(Category.slug == "general")
    ).scalar_one()
    # Create 3 threads to generate multiple audit entries
    for i in range(3):
        client.post(
            "/api/v1/threads",
            headers=headers,
            json={
                "category_id": category.id,
                "title": f"Pagination thread {i}",
                "body": "Body.",
            },
        )

    # Request page_size=2 — should get at most 2 items per page
    resp = client.get(
        "/api/v1/admin/audit-logs",
        headers=headers,
        params={"page_size": 2, "page": 1},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) <= 2
    assert data["page"] == 1
    assert data["page_size"] == 2
    # Total should be > 2 (register + login + 3 thread_create = 5+ entries)
    assert data["total"] >= 5
    assert data["total_pages"] >= 3


def test_category_creation_creates_audit_log(client, db_session) -> None:
    """Admin creating a category should produce a category_create audit entry."""
    headers = _register_and_login(client, "cataudit@example.com", "cataudituser")
    admin_user = db_session.execute(
        select(User).where(User.email == "cataudit@example.com")
    ).scalar_one()
    admin_user.role = UserRole.ADMIN
    db_session.commit()

    resp = client.post(
        "/api/v1/categories",
        headers=headers,
        json={
            "title": "Audit Category",
            "slug": "audit-category",
            "description": "Test.",
        },
    )
    assert resp.status_code == 201

    logs = (
        db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "category_create",
                AuditLog.actor_id == admin_user.id,
            )
        )
        .scalars()
        .all()
    )
    assert len(logs) == 1
    assert logs[0].entity_type == "category"

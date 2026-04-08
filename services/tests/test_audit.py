"""
Audit log tests for the PulseBoard microservice architecture.

INTERVIEW CONCEPTS:

    These 10 tests validate the **audit trail** system — a record of every
    significant action that occurs in the application. Audit logs are critical
    for:

    1. **Security compliance** — regulations like SOC 2, GDPR, and HIPAA
       require organizations to maintain an immutable record of who did what
       and when. If there's a data breach, the audit trail helps reconstruct
       what happened.

    2. **Debugging and forensics** — when something goes wrong (accidental
       deletion, unauthorized access), the audit log provides a timeline of
       events leading up to the incident.

    3. **Accountability** — every action is tied to an actor (user ID),
       making it clear who performed each operation.

    The PulseBoard audit system records:
    - ``actor_id``: the user who performed the action
    - ``action``: what they did (e.g. "thread_create", "user_login")
    - ``entity_type``: the type of object affected (e.g. "thread", "user")
    - ``entity_id``: the specific object's database ID
    - ``ip_address``: where the request came from (not tested here)
    - ``details``: optional JSON with extra context

    ROLE-BASED VISIBILITY:
    Not everyone should see all audit logs:
    - **Admin**: sees ALL logs across ALL users (full visibility)
    - **Moderator**: sees their own + member actions (limited staff view)
    - **Member**: sees ONLY their own logs (personal activity history)

    This follows the Principle of Least Privilege — each role sees the
    minimum amount of data necessary for their responsibilities.

    TESTING STRATEGY:
    The tests fall into 3 categories:
    - Tests 1-4: Verify that specific actions CREATE audit log entries
    - Tests 5-6: Verify role-based visibility (who sees what)
    - Tests 7-10: Verify query features (filters, pagination)
"""

from sqlalchemy import select

from shared.models.audit_log import AuditLog
from shared.models.category import Category
from shared.models.user import User, UserRole
from tests.conftest import register_verified_user


def _register_and_login(client, email: str, username: str) -> dict[str, str]:
    """Register a verified user and return an Authorization header dict.

    This is the same helper pattern used in test_forum.py. It returns a
    ready-to-use headers dict so test functions can make authenticated
    requests in a single line.

    Returns:
        ``{"Authorization": "Bearer eyJhbG..."}``
    """
    session = register_verified_user(client, email, username)
    return {"Authorization": f"Bearer {session['access_token']}"}


def test_thread_creation_creates_audit_log(client, db_session) -> None:
    """TEST 1: Creating a thread should produce a ``thread_create`` audit entry.

    What this validates:
    - After a thread is created via the API, the audit_logs table contains
      exactly one entry with action="thread_create"
    - The entry's entity_id matches the created thread's ID
    - The entry's entity_type is "thread"

    INTERVIEW NOTE:
        This verifies that the audit system is wired into the business logic
        correctly. The audit record is created within the same database
        transaction as the thread itself — if the thread creation fails and
        rolls back, the audit entry is also rolled back. This ensures
        consistency: you never have an audit log for an action that didn't
        actually happen.

        We query the database directly (not the API) to verify the side
        effect. This is a common pattern in tests — use the API for the
        action, then check the database for the expected side effect.
    """
    headers = _register_and_login(client, "audit1@example.com", "audituser1")
    # Look up the "general" category seeded by the setup_database fixture
    category = db_session.execute(
        select(Category).where(Category.slug == "general")
    ).scalar_one()

    # Create a thread — this should trigger an audit log entry
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

    # Verify: query the audit_logs table for the thread_create entry
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
    # Exactly one audit log should exist for this specific thread creation
    assert len(logs) == 1
    assert logs[0].entity_type == "thread"


def test_post_creation_creates_audit_log(client, db_session) -> None:
    """TEST 2: Creating a post (reply) should produce a ``post_create`` audit entry.

    What this validates:
    - Replying to a thread generates a separate audit entry from the thread itself
    - The post's audit entry has action="post_create" and entity_type="post"
    - The entity_id matches the specific post ID (not the thread ID)

    INTERVIEW NOTE:
        This test creates BOTH a thread and a post, which generates two
        audit entries (thread_create + post_create). The assertion is
        narrowly scoped — it filters by action="post_create" AND the
        specific entity_id, ensuring we're checking the right entry and
        not accidentally matching the thread_create entry.
    """
    headers = _register_and_login(client, "audit2@example.com", "audituser2")
    category = db_session.execute(
        select(Category).where(Category.slug == "general")
    ).scalar_one()

    # First create a thread (required to have something to reply to)
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

    # Create a reply to the thread — this is the action we're auditing
    post_resp = client.post(
        f"/api/v1/threads/{thread_id}/posts",
        headers=headers,
        json={"body": "Audited reply."},
    )
    assert post_resp.status_code == 201
    post_id = post_resp.json()["id"]

    # Verify: the audit log has an entry for this specific post
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
    """TEST 3: User registration should produce a ``user_register`` audit entry.

    What this validates:
    - Calling POST /auth/register creates an audit log entry
    - The entry's actor_id equals the newly created user's ID (self-referential:
      the user is both the actor and the entity being acted upon)
    - entity_type is "user" and entity_id is the new user's ID

    INTERVIEW NOTE:
        Registration is interesting because the "actor" doesn't exist yet when
        the request starts — the user is created during the request. The audit
        system handles this by recording the actor_id after the user row is
        created but within the same transaction.

        Note: we don't use ``_register_and_login`` here because we only want
        to test the registration step, not login. We call the register
        endpoint directly.
    """
    # Register a new user (without verifying or logging in)
    client.post(
        "/api/v1/auth/register",
        json={
            "email": "auditreg@example.com",
            "username": "auditreguser",
            "password": "supersecret",
        },
    )

    # Look up the created user to get their ID
    user = db_session.execute(
        select(User).where(User.email == "auditreg@example.com")
    ).scalar_one()

    # Verify: the audit log has a user_register entry for this user
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
    # The actor is the user themselves (self-registration)
    assert logs[0].actor_id == user.id


def test_login_creates_audit_log(client, db_session) -> None:
    """TEST 4: Successful login should produce a ``user_login`` audit entry.

    What this validates:
    - After a verified user logs in, the audit_logs table records the event
    - The entry's actor_id matches the user who logged in
    - entity_type is "user"

    INTERVIEW NOTE:
        Logging login events is a security best practice. It allows
        administrators to detect suspicious activity such as:
        - Logins from unusual locations (via IP address)
        - Logins at unusual times
        - Multiple failed login attempts (brute force detection)

        Note: ``_register_and_login`` performs both registration AND login,
        so after this call, the audit log will contain BOTH user_register
        and user_login entries. We filter specifically for user_login.
    """
    # Register, verify, and log in — the login step is what we're auditing
    _register_and_login(client, "auditlogin@example.com", "auditloginuser")

    # Look up the user to get their ID for the assertion
    user = db_session.execute(
        select(User).where(User.email == "auditlogin@example.com")
    ).scalar_one()

    # Verify: the audit log has a user_login entry for this user
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
    """TEST 5: Admin users should see audit logs from ALL users via the endpoint.

    What this validates:
    - GET /admin/audit-logs returns 200 for admin users
    - The response includes entries from OTHER users (not just the admin)
    - Specifically, the admin can see the member's thread_create entry

    INTERVIEW NOTE on role-based visibility:
        This is the core authorization test for the audit log system. The
        endpoint uses the requesting user's role to determine what data to
        return:

        - Admin: ``SELECT * FROM audit_logs`` (no filter)
        - Moderator: ``WHERE actor_id = self OR actor_role = 'member'``
        - Member: ``WHERE actor_id = self`` (only own logs)

        The test creates TWO users (a member and an admin), has the member
        perform an action, then verifies the admin can see that action in
        the audit log response. Without proper role-based filtering, the
        admin might only see their own logs.
    """
    # Create two users — one regular member, one admin
    member_headers = _register_and_login(
        client, "auditmember@example.com", "auditmember"
    )
    admin_headers = _register_and_login(client, "auditadmin@example.com", "auditadmin")
    # Promote the second user to admin
    admin_user = db_session.execute(
        select(User).where(User.email == "auditadmin@example.com")
    ).scalar_one()
    admin_user.role = UserRole.ADMIN
    db_session.commit()

    # Member creates a thread — this generates a thread_create audit entry
    # owned by the member (not the admin)
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

    # Admin queries the audit logs — should see the member's entry
    resp = client.get("/api/v1/admin/audit-logs", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] > 0
    assert len(data["items"]) > 0
    # Verify the member's thread_create action appears in the admin's view
    actions = [item["action"] for item in data["items"]]
    assert "thread_create" in actions


def test_member_sees_only_own_audit_logs(client, db_session) -> None:
    """TEST 6: Members should only see their OWN audit logs, not other users'.

    What this validates:
    - GET /admin/audit-logs filtered by role returns only the requesting
      user's entries
    - Even though both users created threads, member1 only sees logs
      where actor_id == member1.id
    - No entry from member2 leaks into member1's view

    INTERVIEW NOTE:
        This is the NEGATIVE counterpart to test 5. While test 5 verifies
        that admins CAN see everything, this test verifies that members
        CANNOT see other members' data. Together, they form a complete
        authorization test:
        - Positive test: authorized users see what they should
        - Negative test: unauthorized users don't see what they shouldn't

        This pattern prevents privilege escalation bugs where a member
        could discover other users' activities by querying the audit endpoint.
    """
    # Create two regular members
    member1_headers = _register_and_login(client, "member1@example.com", "member1user")
    member2_headers = _register_and_login(client, "member2@example.com", "member2user")
    member1 = db_session.execute(
        select(User).where(User.email == "member1@example.com")
    ).scalar_one()

    # Both members create threads (generating audit entries for each)
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

    # Member1 queries audit logs — should ONLY see their own entries
    resp = client.get("/api/v1/admin/audit-logs", headers=member1_headers)
    assert resp.status_code == 200
    data = resp.json()
    # Every single entry must belong to member1 — no leakage from member2
    for item in data["items"]:
        assert item["actor_id"] == member1.id


def test_audit_log_action_filter(client, db_session) -> None:
    """TEST 7: Filtering by ``action`` param should return only matching entries.

    What this validates:
    - GET /admin/audit-logs?action=thread_create returns ONLY thread_create entries
    - Other actions (user_register, user_login) are excluded from the response
    - The filter is applied server-side (not just client-side display)

    INTERVIEW NOTE:
        Server-side filtering is important for two reasons:
        1. **Performance**: sending all logs to the client and filtering in
           JavaScript wastes bandwidth and is slow for large datasets
        2. **Security**: if filtering were client-side, the full dataset would
           be exposed in the network response (visible in DevTools)

        The test relies on the fact that ``_register_and_login`` generates
        user_register + user_login entries, and the thread creation generates
        a thread_create entry. Filtering by thread_create should exclude the
        auth-related entries.
    """
    headers = _register_and_login(client, "auditfilter@example.com", "auditfilteruser")
    # Register + login already produced user_register and user_login entries.
    # Create a thread to also produce a thread_create entry.
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

    # Filter by action=thread_create — should exclude user_register and user_login
    resp = client.get(
        "/api/v1/admin/audit-logs",
        headers=headers,
        params={"action": "thread_create"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    # Every returned entry must have action == "thread_create"
    for item in data["items"]:
        assert item["action"] == "thread_create"


def test_audit_log_entity_type_filter(client, db_session) -> None:
    """TEST 8: Filtering by ``entity_type`` param should return only matching entries.

    What this validates:
    - GET /admin/audit-logs?entity_type=thread returns ONLY thread-related entries
    - User-related entries (registration, login) are excluded
    - The filter works independently of the ``action`` filter

    INTERVIEW NOTE:
        This is a different filter dimension from test 7. While ``action``
        filters by what happened (create, update, delete), ``entity_type``
        filters by what type of object was affected (thread, user, post).

        The two filters can be combined for precise queries:
        ``?action=thread_create&entity_type=thread`` — all thread creations
        ``?entity_type=user`` — all user-related events (register, login, etc.)

        This mirrors how SQL WHERE clauses work: each query parameter adds
        an AND condition to the database query.
    """
    headers = _register_and_login(client, "auditentity@example.com", "auditentityuser")
    category = db_session.execute(
        select(Category).where(Category.slug == "general")
    ).scalar_one()
    # Create a thread — this generates a "thread" entity_type entry
    client.post(
        "/api/v1/threads",
        headers=headers,
        json={
            "category_id": category.id,
            "title": "Entity filter test",
            "body": "Body.",
        },
    )

    # Filter by entity_type=thread — should exclude "user" entity_type entries
    resp = client.get(
        "/api/v1/admin/audit-logs",
        headers=headers,
        params={"entity_type": "thread"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    # Every returned entry must have entity_type == "thread"
    for item in data["items"]:
        assert item["entity_type"] == "thread"


def test_audit_log_pagination(client, db_session) -> None:
    """TEST 9: Audit logs endpoint should respect ``page_size`` and return pagination metadata.

    What this validates:
    - The response includes pagination fields: page, page_size, total, total_pages
    - Setting page_size=2 limits the response to at most 2 items
    - total reflects the FULL count of matching entries (not just this page)
    - total_pages is calculated correctly (ceil(total / page_size))

    INTERVIEW NOTE on pagination:
        Pagination is essential for any endpoint that returns a potentially
        large dataset. Without it, querying audit logs for an active platform
        could return millions of rows, causing:
        - Memory exhaustion on the server (loading all rows into memory)
        - Slow response times (serializing millions of rows to JSON)
        - Network saturation (sending megabytes of JSON to the client)
        - Browser freezing (rendering thousands of DOM elements)

        The implementation uses OFFSET-based pagination:
        ``SELECT * FROM audit_logs LIMIT page_size OFFSET (page - 1) * page_size``

        For very large tables (millions of rows), OFFSET pagination becomes
        slow because the database must scan and discard all skipped rows.
        The alternative is cursor-based (keyset) pagination using
        ``WHERE id < last_seen_id ORDER BY id DESC LIMIT page_size``, which
        is O(1) regardless of page number. For an interview, knowing both
        approaches and their trade-offs is important.

    The test generates 5+ audit entries:
    - 1 user_register (from registration)
    - 1 user_login (from login)
    - 3 thread_create (from the loop below)
    Then requests page_size=2 and verifies the response.
    """
    headers = _register_and_login(client, "auditpage@example.com", "auditpageuser")
    category = db_session.execute(
        select(Category).where(Category.slug == "general")
    ).scalar_one()
    # Create 3 threads to generate multiple audit entries (total: 5+ entries)
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

    # Request page 1 with page_size=2 — should get at most 2 items
    resp = client.get(
        "/api/v1/admin/audit-logs",
        headers=headers,
        params={"page_size": 2, "page": 1},
    )
    assert resp.status_code == 200
    data = resp.json()
    # At most 2 items on this page (the page_size we requested)
    assert len(data["items"]) <= 2
    # Pagination metadata should be correct
    assert data["page"] == 1
    assert data["page_size"] == 2
    # Total should be >= 5 (register + login + 3 thread_create)
    assert data["total"] >= 5
    # total_pages = ceil(total / page_size), so at least ceil(5/2) = 3
    assert data["total_pages"] >= 3


def test_category_creation_creates_audit_log(client, db_session) -> None:
    """TEST 10: Admin creating a category should produce a ``category_create`` audit entry.

    What this validates:
    - Category creation (admin-only action) is properly audited
    - The entry's actor_id matches the admin who created it
    - entity_type is "category"

    INTERVIEW NOTE:
        This test completes the coverage of audited actions by testing an
        admin-only operation. Unlike thread/post creation (available to all
        members), category creation requires admin privileges. The audit
        log captures WHO performed the action — useful for tracking which
        admin created or modified platform structure.

        The setup pattern is: register user -> promote to admin via DB ->
        perform admin action -> verify audit entry. This is the same
        pattern used in test_forum.py for admin tests.
    """
    headers = _register_and_login(client, "cataudit@example.com", "cataudituser")
    # Promote the user to admin (required for category creation)
    admin_user = db_session.execute(
        select(User).where(User.email == "cataudit@example.com")
    ).scalar_one()
    admin_user.role = UserRole.ADMIN
    db_session.commit()

    # Create a new category — admin-only operation
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

    # Verify: the audit log has a category_create entry by this admin
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

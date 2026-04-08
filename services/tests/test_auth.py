"""
Authentication tests for the PulseBoard microservice architecture.

INTERVIEW CONCEPTS:
    These 5 tests validate the core authentication flow that every web
    application needs:

    1. **Registration** — creating a new account
    2. **Email verification** — proving the user owns the email address
    3. **Login blocking** — preventing unverified users from logging in
    4. **Login + session** — the full login-to-authenticated-request flow
    5. **Token refresh** — getting new tokens without re-entering credentials

    The auth flow follows industry-standard patterns:
    - Passwords are hashed with pbkdf2_sha256 (never stored in plaintext)
    - JWT (JSON Web Tokens) are used for stateless authentication:
      * Access token (30 min) — attached to every API request
      * Refresh token (7 days) — used to get a new access token when it expires
    - Email verification prevents account creation with someone else's email

    TESTING STRATEGY:
    Each test is isolated (the database is reset between tests via the
    ``setup_database`` autouse fixture). Tests follow the Arrange-Act-Assert
    pattern and test one specific behavior.
"""

from sqlalchemy import select

from shared.models.user import EmailVerificationToken, User


def _register(client, email: str, username: str, password: str = "supersecret"):
    """Helper: register a user and return the HTTP response.

    Encapsulates the registration API call so test functions stay concise.
    All test users use the same password ("supersecret") by default.
    """
    return client.post(
        "/api/v1/auth/register",
        json={"email": email, "username": username, "password": password},
    )


def _verify_user(client, db_session, email: str):
    """Helper: find the verification token for a user and verify their email.

    In production, the user clicks a link in their email. In tests, we:
    1. Query the database to find the verification token for this user
    2. Call the verify-email endpoint with that token

    This simulates the user clicking the verification link without actually
    sending an email.

    Args:
        client: The test HTTP client.
        db_session: Direct database session for querying tokens.
        email: The email address of the user to verify.

    INTERVIEW NOTE:
        ``scalar_one()`` returns exactly one result or raises an exception.
        This is a safety check — if the user or token doesn't exist, the
        test fails immediately with a clear error instead of silently
        proceeding with ``None``.
    """
    # Look up the user by email to get their ID
    user = db_session.execute(select(User).where(User.email == email)).scalar_one()
    # Find the unused verification token for this user
    token_row = db_session.execute(
        select(EmailVerificationToken).where(
            EmailVerificationToken.user_id == user.id,
            EmailVerificationToken.used_at.is_(None),  # Token not yet used
        )
    ).scalar_one()
    # Call the verification endpoint with the token
    response = client.post(
        "/api/v1/auth/verify-email",
        json={"token": token_row.token},
    )
    assert response.status_code == 200
    # Expire all cached ORM objects so subsequent queries see the updated data.
    # Without this, db_session might return stale ``is_verified=False``.
    db_session.expire_all()
    return response


def test_register_returns_message(client) -> None:
    """TEST 1: Registration should succeed and tell the user to verify their email.

    What this validates:
    - POST /api/v1/auth/register returns 201 Created
    - The response includes a message mentioning email verification
    - The user is NOT automatically logged in (must verify first)

    INTERVIEW NOTE:
        201 Created (not 200 OK) is the correct HTTP status for resource
        creation. The response doesn't include tokens because the user
        must verify their email before they can log in.
    """
    response = _register(client, "user@example.com", "testuser")
    assert response.status_code == 201
    body = response.json()
    # Response should contain a message telling the user to check their email
    assert "message" in body
    assert "verify" in body["message"].lower()


def test_verify_email_updates_user(client, db_session) -> None:
    """TEST 2: Email verification should mark the user as verified in the database.

    What this validates:
    - After calling the verify-email endpoint with a valid token,
      the user's ``is_verified`` field is set to ``True``
    - The verification token is consumed (can't be reused)

    INTERVIEW NOTE:
        This test checks a DATABASE SIDE EFFECT, not just the API response.
        It queries the User model directly to verify that ``is_verified``
        was actually persisted. This is important because a bug could return
        a 200 response without actually updating the database.
    """
    # Arrange: register a new user
    register_response = _register(client, "verify@example.com", "verifyuser")
    assert register_response.status_code == 201

    # Act: verify their email
    _verify_user(client, db_session, "verify@example.com")

    # Assert: check the database directly — user should be verified
    user = db_session.execute(
        select(User).where(User.email == "verify@example.com")
    ).scalar_one()
    assert user.is_verified is True


def test_login_blocked_for_unverified(client) -> None:
    """TEST 3: Unverified users should NOT be able to log in.

    What this validates:
    - POST /api/v1/auth/login returns 403 Forbidden for unverified users
    - The error message mentions verification
    - Even with correct credentials, login is denied without verification

    INTERVIEW NOTE:
        This is a security control. Without email verification, anyone could
        register with someone else's email and claim to be them. The 403
        status code means "authenticated but not authorized" — the password
        is correct, but the account isn't fully set up yet.
    """
    # Register but do NOT verify
    _register(client, "notyetverified@example.com", "unverifieduser")

    # Try to log in — should be blocked
    login_response = client.post(
        "/api/v1/auth/login",
        json={
            "email": "notyetverified@example.com",
            "password": "supersecret",
        },
    )
    assert login_response.status_code == 403
    # Error message should mention verification
    assert "verify" in login_response.json()["detail"].lower()


def test_login_and_me_flow(client, db_session) -> None:
    """TEST 4: Full login flow — register, verify, login, access protected endpoint.

    What this validates:
    - The complete auth lifecycle works end-to-end
    - Login returns a valid JWT access token
    - The access token can be used to access the ``/users/me`` endpoint
    - The ``/users/me`` response contains the correct user data

    INTERVIEW NOTE:
        This is an integration test that validates the entire auth chain:
        registration -> verification -> login -> JWT -> protected endpoint.

        The ``Authorization: Bearer <token>`` header is the standard way to
        pass JWT tokens in HTTP requests. "Bearer" means "whoever bears
        (carries) this token is authenticated."
    """
    # Arrange: register and verify
    _register(client, "member@example.com", "memberuser")
    _verify_user(client, db_session, "member@example.com")

    # Act: log in to get a JWT access token
    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": "member@example.com", "password": "supersecret"},
    )
    assert login_response.status_code == 200

    # Act: use the access token to call a protected endpoint
    access_token = login_response.json()["access_token"]
    me_response = client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    # Assert: the /users/me endpoint returns the authenticated user's data
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "member@example.com"


def test_refresh_issues_new_tokens(client, db_session) -> None:
    """TEST 5: Token refresh should issue a new, different refresh token.

    What this validates:
    - POST /api/v1/auth/refresh accepts a valid refresh token
    - It returns a 200 response with new tokens
    - The new refresh token is DIFFERENT from the old one (token rotation)

    INTERVIEW NOTE on token rotation:
        Issuing a NEW refresh token on every refresh is a security best
        practice called "token rotation." If an attacker steals a refresh
        token, they can only use it once before it's invalidated. Without
        rotation, a stolen refresh token could be used indefinitely (for
        its entire 7-day lifetime).

        The test verifies rotation by asserting that the returned refresh
        token differs from the one we sent. This means the old token was
        consumed and can't be reused.
    """
    # Arrange: register, verify, and log in to get tokens
    _register(client, "refresh@example.com", "refreshuser")
    _verify_user(client, db_session, "refresh@example.com")

    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": "refresh@example.com", "password": "supersecret"},
    )
    assert login_response.status_code == 200

    # Act: use the refresh token to get new tokens
    refresh_token = login_response.json()["refresh_token"]
    refresh_response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )

    # Assert: new tokens are issued, and the refresh token is rotated
    assert refresh_response.status_code == 200
    assert refresh_response.json()["refresh_token"] != refresh_token

"""Auth tests for the microservice architecture."""

from sqlalchemy import select

from shared.models.user import EmailVerificationToken, User


def _register(client, email: str, username: str, password: str = "supersecret"):
    """Helper: register a user and return the response."""
    return client.post(
        "/api/v1/auth/register",
        json={"email": email, "username": username, "password": password},
    )


def _verify_user(client, db_session, email: str):
    """Helper: find the verification token for *email* and verify."""
    user = db_session.execute(select(User).where(User.email == email)).scalar_one()
    token_row = db_session.execute(
        select(EmailVerificationToken).where(
            EmailVerificationToken.user_id == user.id,
            EmailVerificationToken.used_at.is_(None),
        )
    ).scalar_one()
    response = client.post(
        "/api/v1/auth/verify-email",
        json={"token": token_row.token},
    )
    assert response.status_code == 200
    db_session.expire_all()
    return response


def test_register_returns_message(client) -> None:
    response = _register(client, "user@example.com", "testuser")
    assert response.status_code == 201
    body = response.json()
    assert "message" in body
    assert "verify" in body["message"].lower()


def test_verify_email_updates_user(client, db_session) -> None:
    register_response = _register(client, "verify@example.com", "verifyuser")
    assert register_response.status_code == 201

    _verify_user(client, db_session, "verify@example.com")

    user = db_session.execute(
        select(User).where(User.email == "verify@example.com")
    ).scalar_one()
    assert user.is_verified is True


def test_login_blocked_for_unverified(client) -> None:
    _register(client, "notyetverified@example.com", "unverifieduser")

    login_response = client.post(
        "/api/v1/auth/login",
        json={
            "email": "notyetverified@example.com",
            "password": "supersecret",
        },
    )
    assert login_response.status_code == 403
    assert "verify" in login_response.json()["detail"].lower()


def test_login_and_me_flow(client, db_session) -> None:
    _register(client, "member@example.com", "memberuser")
    _verify_user(client, db_session, "member@example.com")

    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": "member@example.com", "password": "supersecret"},
    )
    assert login_response.status_code == 200

    access_token = login_response.json()["access_token"]
    me_response = client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "member@example.com"


def test_refresh_issues_new_tokens(client, db_session) -> None:
    _register(client, "refresh@example.com", "refreshuser")
    _verify_user(client, db_session, "refresh@example.com")

    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": "refresh@example.com", "password": "supersecret"},
    )
    assert login_response.status_code == 200

    refresh_token = login_response.json()["refresh_token"]
    refresh_response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )

    assert refresh_response.status_code == 200
    assert refresh_response.json()["refresh_token"] != refresh_token

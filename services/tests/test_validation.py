"""End-to-end validation tests for Features 13 & 14.

Tests that thread creation, post creation, avatar upload, and file upload
work correctly with the new input sanitization and file validation in place.
"""

import io

from services.tests.conftest import (
    TestingSessionLocal,
    app,
    register_verified_user,
)
from fastapi.testclient import TestClient
from shared.models.user import User


def _make_admin(email: str) -> None:
    db = TestingSessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        user.role = "admin"
        db.commit()
    finally:
        db.close()


def test_thread_creation_preserves_special_chars(client: TestClient):
    """Thread titles/bodies with &, <, >, quotes should NOT be mangled."""
    auth = register_verified_user(client, "alice@test.com", "alice")
    _make_admin("alice@test.com")
    token = auth["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.post(
        "/api/v1/threads",
        json={
            "category_id": 1,
            "title": "Python & FastAPI: if (x < 10) tips",
            "body": "What do you think about Python & FastAPI? Is x < 10 valid? Check vector<int> too! @alice thoughts?",
        },
        headers=headers,
    )
    assert resp.status_code == 201, f"Thread creation failed: {resp.json()}"
    data = resp.json()
    assert "&amp;" not in data["title"], f"Title double-escaped: {data['title']}"
    assert "&lt;" not in data["title"], f"Title < escaped: {data['title']}"
    assert "&" in data["title"], "Ampersand missing from title"
    assert "<" in data["title"], "Less-than missing from title"
    assert "@alice" in data["body"], "Mention stripped from body"


def test_thread_creation_strips_xss(client: TestClient):
    """XSS payloads in thread title/body should be stripped."""
    auth = register_verified_user(client, "bob@test.com", "bob")
    _make_admin("bob@test.com")
    token = auth["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.post(
        "/api/v1/threads",
        json={
            "category_id": 1,
            "title": "Hello <script>alert(1)</script> World",
            "body": 'Check this <iframe src="evil.com"></iframe> out!',
        },
        headers=headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "<script>" not in data["title"]
    assert "<iframe" not in data["body"]
    assert "Hello" in data["title"]
    assert "out!" in data["body"]


def test_post_creation_preserves_quotes(client: TestClient):
    """Post bodies with quotes and comparison operators should be preserved."""
    auth = register_verified_user(client, "carol@test.com", "carol")
    _make_admin("carol@test.com")
    token = auth["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create thread first
    client.post(
        "/api/v1/threads",
        json={"category_id": 1, "title": "Test thread", "body": "Test body content"},
        headers=headers,
    )

    resp = client.post(
        "/api/v1/threads/1/posts",
        json={
            "body": 'Tom said "this is great" and x > 5 is correct.',
        },
        headers=headers,
    )
    assert resp.status_code == 201, f"Post creation failed: {resp.json()}"
    data = resp.json()
    assert "&quot;" not in data["body"], f"Quotes escaped: {data['body']}"
    assert '"' in data["body"], "Quotes missing"
    assert ">" in data["body"], "Greater-than missing"


def test_avatar_upload_jpeg(client: TestClient):
    """JPEG avatar upload should work."""
    auth = register_verified_user(client, "dave@test.com", "dave")
    token = auth["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    resp = client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("avatar.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")},
        headers=headers,
    )
    assert resp.status_code == 200, f"Avatar upload failed: {resp.json()}"
    data = resp.json()
    assert data.get("avatar_url"), "No avatar_url returned"


def test_avatar_upload_gif(client: TestClient):
    """GIF avatar upload should work."""
    auth = register_verified_user(client, "eve@test.com", "eve")
    token = auth["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    gif_bytes = b"GIF89a" + b"\x00" * 100
    resp = client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("avatar.gif", io.BytesIO(gif_bytes), "image/gif")},
        headers=headers,
    )
    assert resp.status_code == 200, f"GIF avatar upload failed: {resp.json()}"


def test_upload_rejects_bad_file_type(client: TestClient):
    """Executable uploads should be rejected."""
    auth = register_verified_user(client, "frank@test.com", "frank")
    token = auth["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.post(
        "/api/v1/uploads",
        data={"linked_entity_type": "draft", "linked_entity_id": "0"},
        files={
            "file": (
                "hack.exe",
                io.BytesIO(b"MZ" + b"\x00" * 100),
                "application/x-msdownload",
            )
        },
        headers=headers,
    )
    assert resp.status_code == 400


def test_upload_rejects_mismatched_magic_bytes(client: TestClient):
    """File with JPEG extension/MIME but GIF content should be rejected."""
    auth = register_verified_user(client, "grace@test.com", "grace")
    token = auth["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.post(
        "/api/v1/uploads",
        data={"linked_entity_type": "draft", "linked_entity_id": "0"},
        files={
            "file": ("image.jpg", io.BytesIO(b"GIF89a" + b"\x00" * 100), "image/jpeg")
        },
        headers=headers,
    )
    assert resp.status_code == 400
    assert "does not match" in resp.json()["detail"]


def test_upload_rejects_invalid_entity_type(client: TestClient):
    """Upload with invalid linked_entity_type should be rejected."""
    auth = register_verified_user(client, "heidi@test.com", "heidi")
    token = auth["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    resp = client.post(
        "/api/v1/uploads",
        data={"linked_entity_type": "hacked", "linked_entity_id": "0"},
        files={"file": ("image.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "Invalid linked_entity_type" in resp.json()["detail"]


def test_vote_rejects_zero(client: TestClient):
    """Vote value 0 should be rejected."""
    auth = register_verified_user(client, "ivan@test.com", "ivan")
    _make_admin("ivan@test.com")
    token = auth["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create a thread to vote on
    client.post(
        "/api/v1/threads",
        json={"category_id": 1, "title": "Vote test thread", "body": "Testing votes"},
        headers=headers,
    )

    resp = client.post(
        "/api/v1/threads/1/vote",
        json={"value": 0},
        headers=headers,
    )
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"

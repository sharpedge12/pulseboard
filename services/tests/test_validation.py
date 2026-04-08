"""
End-to-end validation and security tests for PulseBoard (Features 13 & 14).

INTERVIEW CONCEPTS:

    These 9 tests validate two critical security features:

    **Feature 13 — File Upload Hardening + GIF Support**:
    - Magic-byte validation: the server reads the first few bytes of every
      uploaded file and verifies they match the declared MIME type. This
      prevents MIME-type spoofing (e.g. uploading a .exe disguised as .jpg).
    - File extension whitelist: only known-safe extensions are accepted.
    - Entity type whitelist: the ``linked_entity_type`` parameter is
      validated against a set of allowed values (draft, thread, post, etc.).

    **Feature 14 — Input Validation & XSS Sanitization**:
    - HTML tag stripping: ``<script>``, ``<iframe>``, and other dangerous
      tags are removed from user input before storage.
    - Special character preservation: legitimate characters like ``&``, ``<``,
      ``>``, and ``"`` in normal text are NOT escaped or mangled. This is
      important for technical forums where users discuss code.
    - Vote value validation: the vote schema rejects ``value=0`` (only +1
      and -1 are meaningful).

    WHY BOTH XSS STRIPPING AND CHARACTER PRESERVATION MATTER:

    A naive XSS filter might HTML-encode ALL angle brackets, turning
    ``x < 10`` into ``x &lt; 10``. This breaks code discussions on a
    technical forum. PulseBoard's sanitizer is smarter: it strips
    complete HTML *tags* (``<script>...</script>``) but preserves bare
    ``<`` and ``>`` characters that aren't part of tags.

    TESTING STRATEGY:
    Tests 1-3: Input sanitization (XSS removal + character preservation)
    Tests 4-5: Valid file uploads (JPEG and GIF avatar)
    Tests 6-8: File upload rejection (bad type, mismatched magic bytes, invalid entity)
    Test 9: Schema validation (vote value=0 rejected)

    Each test follows the Arrange-Act-Assert pattern:
    - Arrange: register a user, prepare test data
    - Act: make the API call with crafted input
    - Assert: verify the response matches expectations

    MAGIC BYTES (File Signatures):
    The first few bytes of a file identify its true format, regardless of
    the file extension or declared MIME type. Common signatures:
    - JPEG: ``FF D8 FF`` (first 3 bytes)
    - PNG:  ``89 50 4E 47`` (the ASCII string ".PNG" with a high bit)
    - GIF:  ``GIF89a`` or ``GIF87a`` (6 ASCII bytes)
    - PDF:  ``%PDF`` (4 ASCII bytes)
    - EXE:  ``MZ`` (2 ASCII bytes — the Mark Zbikowski signature)

    By checking magic bytes, the server can detect when a user renames
    ``malware.exe`` to ``image.jpg`` — the file extension says JPEG, but
    the magic bytes say EXE. This is a defense-in-depth measure on top
    of MIME type checking.
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
    """Promote a user to admin by directly updating the database.

    This bypasses the admin promotion API (which itself requires admin
    privileges) and directly sets the user's role column. Used in tests
    where we need admin access but the test isn't about role management.

    INTERVIEW NOTE:
        This is a common test pattern — manipulate the database directly
        to set up preconditions. It's faster and simpler than exercising
        the full API chain to reach the desired state.
    """
    db = TestingSessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        user.role = "admin"
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Input sanitization tests (XSS removal + character preservation)
# ---------------------------------------------------------------------------


def test_thread_creation_preserves_special_chars(client: TestClient):
    """TEST 1: Legitimate special characters (&, <, >, quotes) must NOT be mangled.

    What this validates:
    - Ampersands (``&``) in titles like "Python & FastAPI" are preserved
    - Angle brackets (``<``, ``>``) in code-like text (``x < 10``,
      ``vector<int>``) are preserved
    - ``@mentions`` are preserved in the body
    - Characters are NOT double-escaped (``&`` should NOT become ``&amp;``)

    INTERVIEW NOTE on double-escaping:
        A common bug in sanitization is "double escaping." If the sanitizer
        converts ``&`` to ``&amp;`` on save, and then the frontend does it
        again on display, the user sees ``&amp;amp;`` instead of ``&``.

        The assertion ``"&amp;" not in data["title"]`` catches this bug.
        The sanitizer should strip dangerous HTML tags but leave plain-text
        characters alone.

    Why this matters for a technical forum:
        Users on PulseBoard discuss code. If the sanitizer escapes ``<``
        to ``&lt;``, a post about "if x < 10" renders as "if x &lt; 10"
        — making code discussions unreadable. The sanitizer must be smart
        enough to distinguish between ``<script>`` (dangerous HTML tag)
        and ``x < 10`` (harmless comparison operator).
    """
    auth = register_verified_user(client, "alice@test.com", "alice")
    _make_admin("alice@test.com")
    token = auth["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create a thread with special characters that should be preserved
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

    # Verify: characters are preserved, NOT HTML-escaped
    assert "&amp;" not in data["title"], f"Title double-escaped: {data['title']}"
    assert "&lt;" not in data["title"], f"Title < escaped: {data['title']}"
    assert "&" in data["title"], "Ampersand missing from title"
    assert "<" in data["title"], "Less-than missing from title"
    # @mentions should survive sanitization
    assert "@alice" in data["body"], "Mention stripped from body"


def test_thread_creation_strips_xss(client: TestClient):
    """TEST 2: XSS (Cross-Site Scripting) payloads must be stripped from input.

    What this validates:
    - ``<script>alert(1)</script>`` tags are removed from thread titles
    - ``<iframe src="evil.com">`` tags are removed from thread bodies
    - The surrounding text ("Hello", "World", "out!") is preserved
    - The sanitizer removes tags but doesn't destroy the entire input

    INTERVIEW NOTE on XSS:
        Cross-Site Scripting (XSS) is a top-10 OWASP vulnerability.
        It occurs when user input containing JavaScript is stored in the
        database and later rendered in another user's browser without
        escaping. The injected script runs with the victim's session,
        allowing the attacker to:
        - Steal session cookies (``document.cookie``)
        - Redirect to phishing sites
        - Modify the page content (defacement)
        - Make API calls as the victim

        Defense in depth:
        1. **Server-side sanitization** (tested here) — strip dangerous
           tags before storing in the database
        2. **Content-Security-Policy header** — prevents inline scripts
           even if sanitization has a bug
        3. **Frontend escaping** — React auto-escapes JSX output by default

        The ``<iframe>`` test is equally important — iframes can embed
        malicious pages that look like the real site (clickjacking) or
        load attacker-controlled content.
    """
    auth = register_verified_user(client, "bob@test.com", "bob")
    _make_admin("bob@test.com")
    token = auth["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create a thread with XSS payloads in both title and body
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

    # Dangerous tags must be stripped
    assert "<script>" not in data["title"]
    assert "<iframe" not in data["body"]
    # Legitimate surrounding text must be preserved
    assert "Hello" in data["title"]
    assert "out!" in data["body"]


def test_post_creation_preserves_quotes(client: TestClient):
    """TEST 3: Post bodies with quotes and comparison operators should be preserved.

    What this validates:
    - Double quotes (``"``) in text like 'Tom said "this is great"' are NOT
      escaped to ``&quot;``
    - Greater-than (``>``) in expressions like ``x > 5`` is preserved
    - The sanitizer doesn't over-sanitize normal punctuation

    INTERVIEW NOTE:
        This is the "post" counterpart to test 1 (which tests threads).
        Posts go through the same sanitization pipeline but via a different
        API endpoint (``POST /threads/{id}/posts``). Testing both ensures
        the sanitizer is applied consistently across all content types.

        ``&quot;`` is the HTML entity for a double quote. If the sanitizer
        encodes quotes, it breaks normal English text and makes code
        discussions with string literals unreadable.
    """
    auth = register_verified_user(client, "carol@test.com", "carol")
    _make_admin("carol@test.com")
    token = auth["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create a thread first (posts must belong to a thread)
    client.post(
        "/api/v1/threads",
        json={"category_id": 1, "title": "Test thread", "body": "Test body content"},
        headers=headers,
    )

    # Create a post with quotes and comparison operators
    resp = client.post(
        "/api/v1/threads/1/posts",
        json={
            "body": 'Tom said "this is great" and x > 5 is correct.',
        },
        headers=headers,
    )
    assert resp.status_code == 201, f"Post creation failed: {resp.json()}"
    data = resp.json()

    # Verify: quotes and angle brackets are preserved, NOT HTML-encoded
    assert "&quot;" not in data["body"], f"Quotes escaped: {data['body']}"
    assert '"' in data["body"], "Quotes missing"
    assert ">" in data["body"], "Greater-than missing"


# ---------------------------------------------------------------------------
# File upload tests — valid uploads
# ---------------------------------------------------------------------------


def test_avatar_upload_jpeg(client: TestClient):
    """TEST 4: JPEG avatar upload should be accepted by the server.

    What this validates:
    - POST /users/me/avatar accepts a JPEG file
    - The response includes an ``avatar_url`` pointing to the stored file
    - Magic byte validation passes for valid JPEG content

    INTERVIEW NOTE on magic bytes:
        The test constructs a minimal JPEG by starting with ``FF D8 FF E0``
        — the JPEG file signature (Start of Image marker + JFIF APP0 marker).
        The remaining bytes (``\\x00 * 100``) are padding. This isn't a valid
        *image* (it would fail to render), but it passes the magic byte check,
        which only inspects the first few bytes.

        In a real JPEG, the remaining bytes would contain the image data
        (DCT coefficients, Huffman tables, etc.). The server doesn't decode
        the full image — it only validates the file signature to prevent
        MIME-type spoofing.

        The ``files`` parameter in the test client creates a multipart/form-data
        request, which is how browsers send file uploads. The tuple format is:
        ``(filename, file_object, content_type)``.
    """
    auth = register_verified_user(client, "dave@test.com", "dave")
    token = auth["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Construct a minimal JPEG: magic bytes FF D8 FF E0 + padding
    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    resp = client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("avatar.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")},
        headers=headers,
    )
    assert resp.status_code == 200, f"Avatar upload failed: {resp.json()}"
    data = resp.json()
    # The response should include a URL where the avatar can be accessed
    assert data.get("avatar_url"), "No avatar_url returned"


def test_avatar_upload_gif(client: TestClient):
    """TEST 5: GIF avatar upload should be accepted (animated avatar support).

    What this validates:
    - POST /users/me/avatar accepts GIF files
    - The GIF magic bytes ``GIF89a`` pass validation
    - GIF support works end-to-end (important for animated avatars)

    INTERVIEW NOTE:
        ``GIF89a`` is the literal ASCII string that begins every GIF89a file
        (the most common GIF version, supporting animation and transparency).
        The older format ``GIF87a`` is also valid but doesn't support
        animation. The server accepts both.

        GIF support was added specifically because users requested animated
        avatars — a feature popular on platforms like Discord and GitHub.
    """
    auth = register_verified_user(client, "eve@test.com", "eve")
    token = auth["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Construct a minimal GIF: magic bytes "GIF89a" + padding
    gif_bytes = b"GIF89a" + b"\x00" * 100
    resp = client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("avatar.gif", io.BytesIO(gif_bytes), "image/gif")},
        headers=headers,
    )
    assert resp.status_code == 200, f"GIF avatar upload failed: {resp.json()}"


# ---------------------------------------------------------------------------
# File upload tests — rejection cases (negative / security tests)
# ---------------------------------------------------------------------------


def test_upload_rejects_bad_file_type(client: TestClient):
    """TEST 6: Executable files (.exe) should be rejected by the upload endpoint.

    What this validates:
    - POST /uploads returns 400 Bad Request for disallowed MIME types
    - ``application/x-msdownload`` (the MIME type for .exe files) is blocked
    - The server doesn't rely solely on the file extension — it checks the
      Content-Type header declared by the client

    INTERVIEW NOTE on defense in depth:
        This test validates the first layer of file upload security:
        MIME type checking. The server maintains an allowlist of permitted
        MIME types (``image/jpeg``, ``image/png``, ``image/gif``, etc.).
        Any type not on the list is rejected.

        However, MIME types are client-declared and easily spoofable (the
        attacker can set any Content-Type they want). That's why the server
        also checks magic bytes (tested in test 7) — a second, independent
        validation that's much harder to fool.

        ``MZ`` (``\\x4d\\x5a``) is the magic byte signature for Windows
        executables (PE format). It stands for "Mark Zbikowski," the
        Microsoft engineer who designed the format.
    """
    auth = register_verified_user(client, "frank@test.com", "frank")
    token = auth["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Attempt to upload an executable file
    resp = client.post(
        "/api/v1/uploads",
        data={"linked_entity_type": "draft", "linked_entity_id": "0"},
        files={
            "file": (
                "hack.exe",
                io.BytesIO(b"MZ" + b"\x00" * 100),  # MZ = Windows EXE magic bytes
                "application/x-msdownload",  # MIME type for .exe files
            )
        },
        headers=headers,
    )
    # 400 Bad Request — file type not in the allowlist
    assert resp.status_code == 400


def test_upload_rejects_mismatched_magic_bytes(client: TestClient):
    """TEST 7: File with JPEG extension/MIME but GIF magic bytes should be rejected.

    What this validates:
    - The server reads the actual file content (magic bytes) and compares
      them against the declared MIME type
    - A file claiming to be ``image/jpeg`` but containing ``GIF89a`` bytes
      is caught and rejected with a 400 error
    - The error message includes "does not match" to help debugging

    INTERVIEW NOTE on MIME-type spoofing:
        This is the most important upload security test. An attacker could:
        1. Take a malicious file (HTML with JavaScript, SVG with embedded JS)
        2. Rename it to ``image.jpg``
        3. Set the Content-Type to ``image/jpeg``
        4. Upload it — if the server only checks the extension and MIME type,
           it would accept the file

        When another user views the "image," their browser might execute
        the embedded JavaScript (depending on the Content-Type the server
        serves it with). Magic byte validation prevents this because the
        file's actual content doesn't match the claimed type.

        In this test, we send GIF content (``GIF89a``) with a JPEG MIME
        type (``image/jpeg``). The server's magic byte checker sees that
        the first bytes match GIF, not JPEG, and rejects the upload.

        This is also called "content sniffing" or "file type verification."
    """
    auth = register_verified_user(client, "grace@test.com", "grace")
    token = auth["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Upload a file with JPEG MIME type but GIF magic bytes — mismatch!
    resp = client.post(
        "/api/v1/uploads",
        data={"linked_entity_type": "draft", "linked_entity_id": "0"},
        files={
            "file": ("image.jpg", io.BytesIO(b"GIF89a" + b"\x00" * 100), "image/jpeg")
        },
        headers=headers,
    )
    # 400 Bad Request — magic bytes don't match the declared MIME type
    assert resp.status_code == 400
    assert "does not match" in resp.json()["detail"]


def test_upload_rejects_invalid_entity_type(client: TestClient):
    """TEST 8: Upload with an invalid ``linked_entity_type`` should be rejected.

    What this validates:
    - The ``linked_entity_type`` parameter is validated against a whitelist
      of allowed values (draft, thread, post, message, avatars)
    - Arbitrary strings like "hacked" are rejected with 400
    - The error message includes "Invalid linked_entity_type"

    INTERVIEW NOTE:
        ``linked_entity_type`` tells the server what the upload is for
        (a thread attachment, a chat message file, an avatar, etc.). Without
        validation, an attacker could inject arbitrary strings that might
        cause issues downstream (path traversal, SQL injection, or just
        unexpected behavior in business logic).

        This is an example of "allowlist validation" — instead of trying to
        block known-bad values (blocklist), we define exactly which values
        are permitted and reject everything else. Allowlists are more secure
        because they protect against unknown attack vectors.
    """
    auth = register_verified_user(client, "heidi@test.com", "heidi")
    token = auth["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Upload a valid JPEG but with an invalid entity type
    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    resp = client.post(
        "/api/v1/uploads",
        data={"linked_entity_type": "hacked", "linked_entity_id": "0"},
        files={"file": ("image.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")},
        headers=headers,
    )
    # 400 Bad Request — "hacked" is not in the allowed entity types
    assert resp.status_code == 400
    assert "Invalid linked_entity_type" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Schema validation test
# ---------------------------------------------------------------------------


def test_vote_rejects_zero(client: TestClient):
    """TEST 9: Vote with value=0 should be rejected by Pydantic schema validation.

    What this validates:
    - POST /threads/{id}/vote with ``{"value": 0}`` returns 422
    - The VoteRequest schema's ``field_validator`` catches invalid values
    - Only +1 (upvote) and -1 (downvote) are accepted

    INTERVIEW NOTE on Pydantic validation:
        Pydantic's ``field_validator`` runs BEFORE the request reaches the
        route handler. If validation fails, FastAPI automatically returns
        a 422 Unprocessable Entity response with details about what went wrong.

        Why reject 0? The vote schema allows ``ge=-1, le=1`` (range -1 to +1),
        which technically includes 0. But a vote of 0 is semantically
        meaningless — it's neither upvote nor downvote. The custom
        ``field_validator`` adds a business rule on top of the range check:
        ``if value == 0: raise ValueError("Vote value cannot be 0")``.

        This is a good example of the difference between structural
        validation (is it an integer in range?) and business validation
        (does this value make sense in our domain?). Pydantic handles both.

        422 Unprocessable Entity means "the request body is syntactically
        valid JSON, but the values don't pass validation." Compare with:
        - 400 Bad Request: malformed request (invalid JSON, missing fields)
        - 422 Unprocessable Entity: well-formed but semantically invalid
    """
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

    # Attempt to vote with value=0 — should be rejected
    resp = client.post(
        "/api/v1/threads/1/vote",
        json={"value": 0},
        headers=headers,
    )
    # 422 Unprocessable Entity — Pydantic validation rejected the value
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"

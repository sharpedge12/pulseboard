# Design Patterns, Principles & Clean Code in PulseBoard

> This document explains every design pattern, SOLID principle, and clean code
> practice used in the project — with exact file paths and code examples so you
> can point to them during a code review or interview.

---

## Table of Contents

1. [Design Patterns](#1-design-patterns)
   - [1.1 Dependency Injection](#11-dependency-injection)
   - [1.2 Repository / Service Layer Pattern](#12-repository--service-layer-pattern)
   - [1.3 Singleton Pattern](#13-singleton-pattern)
   - [1.4 Observer Pattern (Pub/Sub)](#14-observer-pattern-pubsub)
   - [1.5 Factory Pattern](#15-factory-pattern)
   - [1.6 Strategy Pattern](#16-strategy-pattern)
   - [1.7 Middleware Chain (Chain of Responsibility)](#17-middleware-chain-chain-of-responsibility)
   - [1.8 Decorator Pattern](#18-decorator-pattern)
   - [1.9 Proxy Pattern](#19-proxy-pattern)
   - [1.10 Builder Pattern](#110-builder-pattern)
   - [1.11 Unit of Work Pattern](#111-unit-of-work-pattern)
   - [1.12 Template Method Pattern](#112-template-method-pattern)
2. [SOLID Principles](#2-solid-principles)
3. [Clean Code Practices](#3-clean-code-practices)
4. [Frontend Patterns (React)](#4-frontend-patterns-react)

---

## 1. Design Patterns

### 1.1 Dependency Injection

**What it is:** Instead of a function creating its own dependencies (DB connection,
current user), they are *injected* from the outside. This makes code testable and
loosely coupled.

**Where we use it:** FastAPI's `Depends()` system — the core of our entire backend.

**File:** `services/shared/shared/core/auth_helpers.py`

```python
# The dependency chain: extract token -> decode JWT -> lookup user
def get_current_user(
    token: str = Depends(oauth2_scheme),   # Injected: Bearer token from header
    db: Session = Depends(get_db),         # Injected: database session
) -> User:
    payload = safe_decode_token(token)
    user = db.execute(select(User).where(User.id == int(payload["sub"]))).scalar_one_or_none()
    return user
```

**File:** `services/shared/shared/core/database.py`

```python
# Generator-based dependency — FastAPI calls next() to get the session,
# and the finally block runs after the request completes (auto-cleanup)
def get_db() -> Generator[Session, None, None]:
    db: Session = SessionLocal()
    try:
        yield db       # <-- injected into route handlers
    finally:
        db.close()     # <-- always runs, even on exceptions
```

**How routes use it:**

```python
# File: services/community/app/forum_routes.py
@router.post("/")
def create_thread(
    data: ThreadCreateRequest,                               # Injected: validated request body
    current_user: User = Depends(get_current_user),          # Injected: authenticated user
    db: Session = Depends(get_db),                           # Injected: DB session
):
    require_can_participate(current_user)                     # Permission check
    return forum_services.create_thread(db, data, current_user)
```

**Why it matters:**
- Routes don't know *how* to create a DB session or decode a JWT — they just declare what they need
- In tests, we can swap `get_db` to return a SQLite session instead of PostgreSQL
- Each dependency is independently testable

---

### 1.2 Repository / Service Layer Pattern

**What it is:** Business logic lives in *service* functions (not in route handlers).
Routes are thin controllers that only handle HTTP concerns (status codes, headers).
Services handle the actual logic (queries, validation, side effects).

**Structure in every service:**

```
routes.py      ->  "What HTTP endpoint does this map to?"
services.py    ->  "What is the business logic?"
models/        ->  "What does the data look like in the database?"
schemas/       ->  "What does the data look like in the API?"
```

**Example — Thread creation:**

**File:** `services/community/app/forum_routes.py` (thin controller)

```python
@router.post("/", response_model=ThreadDetailResponse, status_code=201)
def create_thread(data: ThreadCreateRequest, current_user=Depends(get_current_user), db=Depends(get_db)):
    require_can_participate(current_user)
    return forum_services.create_thread(db, data, current_user)   # Delegates to service
```

**File:** `services/community/app/forum_services.py` (fat service — 913 lines)

```python
def create_thread(db: Session, data: ThreadCreateRequest, author: User) -> dict:
    # 1. Create thread row
    # 2. Assign tags (get-or-create)
    # 3. Link draft attachments
    # 4. Detect @mentions, create notifications
    # 5. Trigger @pulse bot if mentioned
    # 6. Record audit log
    # 7. Publish Redis event for real-time
    # 8. Serialize and return
```

**Why it matters:**
- Routes stay under 10 lines — easy to scan for "what endpoints exist?"
- Services are independently testable (pass a DB session, get a result)
- Business logic changes don't require touching HTTP layer

---

### 1.3 Singleton Pattern

**What it is:** Ensure only one instance of a resource exists in the entire process.
Prevents resource leaks (too many DB connections, too many Redis connections).

**Example 1 — Settings (with `@lru_cache`)**

**File:** `services/shared/shared/core/config.py`

```python
@lru_cache          # Python decorator that caches the return value forever
def get_settings() -> Settings:
    return Settings()

settings = get_settings()   # Module-level — created once when first imported
```

Every file that does `from shared.core.config import settings` gets the *same* object.
`@lru_cache` ensures `Settings()` is constructed exactly once.

**Example 2 — Redis client (lazy singleton)**

**File:** `services/shared/shared/core/redis.py`

```python
_redis_client: redis.Redis | None = None    # Module-level, starts as None

def get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:                # First call creates it
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client                     # All subsequent calls reuse it
```

**Example 3 — WebSocket ConnectionManager**

**File:** `services/shared/shared/core/events.py`

```python
connection_manager = ConnectionManager()    # Module-level singleton
# All WebSocket endpoints share this one manager so they can broadcast to each other
```

**Why it matters:**
- One DB engine = one connection pool (not one pool per request)
- One Redis client = one TCP connection reused across all pub/sub calls
- One ConnectionManager = all WebSocket channels visible to the broadcast system

---

### 1.4 Observer Pattern (Pub/Sub)

**What it is:** Publishers emit events without knowing who is listening.
Subscribers react to events without knowing who sent them. Decouples producers
from consumers.

**How we implement it:** Redis pub/sub as the event bus.

**Publisher side (any service):**

**File:** `services/shared/shared/core/events.py`

```python
def publish_event(channel: str, payload: dict) -> None:
    """Publish event to Redis — picked up by Gateway's bridge."""
    try:
        client = get_redis_client()
        client.publish(channel, json.dumps(jsonable_encoder(payload)))
    except RedisError:
        logger.warning("Redis publish skipped")   # Graceful degradation
```

**Called from business logic:**

```python
# File: services/community/app/forum_services.py (after creating a post)
publish_event(f"thread:{thread.id}", {
    "type": "post_created",
    "post": serialized_post,
})
```

**Subscriber side (Gateway):**

**File:** `services/gateway/app/main.py`

```python
# Background task subscribes to Redis patterns
pubsub.psubscribe("thread:*", "chat:room:*", "notifications:*", "global")

# When a message arrives, broadcast to all connected WebSocket clients
message = pubsub.get_message()
if message:
    channel = _redis_channel_to_ws_channel(message["channel"])
    await connection_manager.broadcast(channel, json.loads(message["data"]))
```

**WebSocket ConnectionManager (the observer registry):**

```python
class ConnectionManager:
    def __init__(self):
        self.connections: dict[str, list[WebSocket]] = defaultdict(list)

    async def connect(self, channel: str, websocket: WebSocket):
        await websocket.accept()
        self.connections[channel].append(websocket)    # Register observer

    async def broadcast(self, channel: str, message: dict):
        dead = []
        for ws in list(self.connections[channel]):     # Notify all observers
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(channel, ws)               # Clean up dead observers
```

**The full flow:**

```
Alice posts reply
  -> forum_services.create_post()
  -> publish_event("thread:5", {type: "post_created", ...})
  -> Redis PUBLISH "thread:5"
  -> Gateway's background task receives it
  -> connection_manager.broadcast("thread:5", data)
  -> Bob's WebSocket receives JSON
  -> useThreadLiveUpdates hook fires onPostCreated
  -> React re-renders with new post
```

**Why it matters:**
- Community service doesn't know about WebSockets (separation of concerns)
- Gateway doesn't know about forum logic
- Adding a new subscriber (e.g., analytics) requires zero changes to publishers

---

### 1.5 Factory Pattern

**What it is:** A function that creates and returns objects, hiding the
construction complexity.

**Example 1 — JWT Token Factory:**

**File:** `services/shared/shared/core/security.py`

```python
def create_token(subject: str, expires_delta: timedelta, token_type: str = "access",
                 extra_claims: dict | None = None) -> str:
    """Factory that builds JWT tokens with standard claims."""
    payload = {"sub": subject, "exp": datetime.now(UTC) + expires_delta, "type": token_type}
    if extra_claims:
        payload.update(extra_claims)
    payload["iat"] = int(datetime.now(UTC).timestamp())
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)

# Specialized factories that use the generic one:
def create_access_token(subject: str) -> str:
    return create_token(subject, timedelta(minutes=30))

def create_refresh_token(subject: str, token_id: str) -> str:
    return create_token(subject, timedelta(days=7), token_type="refresh",
                        extra_claims={"token_id": token_id})
```

**Example 2 — Session Factory:**

**File:** `services/shared/shared/core/database.py`

```python
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
# SessionLocal() creates a new session each time — factory pattern
```

**Example 3 — Notification Factory:**

**File:** `services/shared/shared/services/notifications.py`

```python
def create_notification(db, user_id, notification_type, title, payload=None):
    """Factory that creates Notification rows with consistent structure."""
    notif = Notification(user_id=user_id, notification_type=notification_type,
                         title=title, payload=payload or {})
    db.add(notif)
    db.flush()
    return notif
```

---

### 1.6 Strategy Pattern

**What it is:** Define a family of algorithms, encapsulate each one, and make
them interchangeable at runtime.

**Where we use it:** AI bot web search with fallback chain.

**File:** `services/shared/shared/services/bot.py`

```python
def _web_search(query: str) -> str:
    """Strategy pattern — try Tavily first, fall back to DuckDuckGo."""
    result = _tavily_search(query)       # Strategy 1: Tavily API
    if result:
        return result
    result = _ddg_search(query)          # Strategy 2: DuckDuckGo fallback
    if result:
        return result
    return ""                            # Strategy 3: No results

def _tavily_search(query: str) -> str:
    """Tavily search strategy — uses API key, returns formatted results."""
    ...

def _ddg_search(query: str) -> str:
    """DuckDuckGo search strategy — no API key needed, instant answers."""
    ...
```

**Also used in:** Password hashing scheme migration.

**File:** `services/shared/shared/core/security.py`

```python
password_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
# CryptContext is a strategy pattern — it can verify hashes from ANY scheme
# in the list. If we add "argon2" later, old pbkdf2 hashes still verify,
# and new passwords automatically use the first scheme in the list.
```

---

### 1.7 Middleware Chain (Chain of Responsibility)

**What it is:** A request passes through a series of handlers (middleware). Each
handler can process the request, modify it, or pass it to the next handler.

**How we use it:** Every request passes through 3 middleware layers before reaching
the route handler.

**File:** `services/core/app/main.py`

```python
# Middleware is applied in REVERSE order — last added runs first
app.add_middleware(SecurityHeadersMiddleware)              # Layer 3: Add security headers
app.add_middleware(RateLimitMiddleware, rate_limit=20,     # Layer 2: Rate limit auth endpoints
                   paths=["/api/v1/auth/"])
app.add_middleware(CORSMiddleware, allow_origins=...,      # Layer 1: CORS checks
                   allow_credentials=True)

# Request flow:
# Client -> CORS -> RateLimit -> SecurityHeaders -> Route Handler
# Response flow:
# Route Handler -> SecurityHeaders -> RateLimit -> CORS -> Client
```

**File:** `services/shared/shared/core/rate_limit.py`

```python
class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if not self._should_limit(request.url.path):
            return await call_next(request)        # Pass to next handler
        count = self._clean_and_count(client_ip)
        if count >= self.rate_limit:
            return JSONResponse(status_code=429)   # Short-circuit the chain
        self._requests[client_ip].append(time.monotonic())
        return await call_next(request)            # Pass to next handler
```

---

### 1.8 Decorator Pattern

**What it is:** Add behavior to functions without modifying them, using wrappers.

**Where we use it:** Pydantic `@field_validator` on every user-input field.

**File:** `services/shared/shared/schemas/thread.py`

```python
class ThreadCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    body: str = Field(..., min_length=1, max_length=40000)

    @field_validator("title")          # Decorator: wraps the title field
    @classmethod
    def sanitize_title(cls, v: str) -> str:
        return sanitize_text(v)        # Strips XSS before it reaches the DB

    @field_validator("body")           # Same decorator on body field
    @classmethod
    def sanitize_body(cls, v: str) -> str:
        return sanitize_text(v)
```

**Also used:** FastAPI route decorators (`@router.get`, `@router.post`), Python's
`@lru_cache`, `@property`, `@computed_field`.

---

### 1.9 Proxy Pattern

**What it is:** An intermediary that controls access to another object or service.

**Where we use it:** The API Gateway is a reverse proxy.

**File:** `services/gateway/app/main.py`

```python
async def _proxy(request: Request, target_base: str) -> Response:
    """Forward the incoming request to the target backend service."""
    url = f"{target_base}{request.url.path}"
    if request.url.query:
        url += f"?{request.url.query}"
    # Copy all headers (including Authorization) to the backend
    headers = dict(request.headers)
    body = await request.body()
    async with httpx.AsyncClient() as client:
        resp = await client.request(request.method, url, headers=headers, content=body)
    return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))

# Route mapping — proxy decides WHERE to forward based on path prefix
@app.api_route("/api/v1/auth/{path:path}", methods=["GET","POST","PUT","PATCH","DELETE"])
async def proxy_auth(request: Request):
    return await _proxy(request, settings.core_service_url)      # -> Core :8001

@app.api_route("/api/v1/threads/{path:path}", methods=["GET","POST","PUT","PATCH","DELETE"])
async def proxy_threads(request: Request):
    return await _proxy(request, settings.community_service_url) # -> Community :8002
```

**Why it matters:**
- Frontend only knows about one URL (`localhost:8000`) — doesn't know Core/Community exist
- Gateway can add rate limiting, logging, auth checks before forwarding
- Backend services can be moved/scaled without changing the frontend

---

### 1.10 Builder Pattern

**What it is:** Construct complex objects step by step.

**Where we use it:** Building the AI bot's context and prompt.

**File:** `services/shared/shared/services/bot.py`

```python
def build_bot_reply(thread_title, thread_body, posts, participants, mentioned_user):
    """Build the complete prompt step by step."""
    # Step 1: Build system prompt with personality
    system_prompt = "You are Pulse, a helpful AI assistant..."

    # Step 2: Build thread context
    context = build_thread_context(thread_title, thread_body, posts)

    # Step 3: Build participant list
    participant_info = [_format_user_profile(p) for p in participants]

    # Step 4: Assemble messages array for the API call
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Thread: {context}\n\nReply to: {mentioned_user}"},
    ]

    # Step 5: Call Groq API
    response = _call_groq(messages)

    # Step 6: Post-process (strip citations)
    return _strip_citations(response)
```

---

### 1.11 Unit of Work Pattern

**What it is:** Track all changes made during a business transaction and commit
them as a single unit — either all succeed or all roll back.

**Where we use it:** SQLAlchemy sessions in every service function.

**File:** `services/community/app/forum_services.py`

```python
def create_thread(db: Session, data: ThreadCreateRequest, author: User) -> dict:
    # All these operations happen in ONE transaction:
    thread = Thread(title=data.title, body=data.body, ...)
    db.add(thread)
    db.flush()                 # Get thread.id without committing

    # Add tags
    for tag_name in data.tag_names:
        tag = db.query(Tag).filter(Tag.name == tag_name).first()
        if not tag:
            tag = Tag(name=tag_name)
            db.add(tag)
            db.flush()
        db.add(ThreadTag(thread_id=thread.id, tag_id=tag.id))

    # Add notifications
    create_notification(db, mentioned_user.id, "mention", ...)

    # Record audit log
    audit.record(db, actor_id=author.id, action="thread_created", ...)

    db.commit()                # ALL changes committed atomically
    # If any step fails, the entire transaction rolls back — no partial data
```

**Key distinction:** `db.flush()` sends SQL to the DB (gets auto-generated IDs)
but does NOT commit. `db.commit()` makes it permanent. This is how we create a
thread, get its ID, then use that ID for tags and notifications — all in one
atomic transaction.

---

### 1.12 Template Method Pattern

**What it is:** Define the skeleton of an algorithm in a base, let subclasses
override specific steps.

**Where we use it:** The audit log `record()` function provides a template that
all services call with different action types.

**File:** `services/shared/shared/services/audit.py`

```python
def record(db, actor_id, action, entity_type, entity_id, details=None, ip_address=None):
    """Template for creating audit entries — same structure, different content."""
    log = AuditLog(
        actor_id=actor_id,
        action=action,              # "thread_created", "user_banned", "role_changed", ...
        entity_type=entity_type,    # "thread", "user", "post", ...
        entity_id=entity_id,
        details=details or {},
        ip_address=ip_address,
    )
    db.add(log)
    db.flush()
```

**Called with different "steps" across the codebase:**

```python
# In auth_services.py:
audit.record(db, user.id, REGISTER, "user", user.id)

# In forum_services.py:
audit.record(db, author.id, THREAD_CREATED, "thread", thread.id,
             details={"title": thread.title})

# In admin_services.py:
audit.record(db, admin.id, USER_ROLE_CHANGED, "user", target.id,
             details={"old_role": old_role, "new_role": new_role})
```

---

## 2. SOLID Principles

### S — Single Responsibility Principle

> "A class/module should have only one reason to change."

| File | Responsibility | It does NOT do... |
|------|---------------|-------------------|
| `auth_routes.py` | HTTP routing for auth | Business logic |
| `auth_services.py` | Auth business logic | HTTP handling, email sending |
| `auth_email.py` | Email sending | Auth logic |
| `auth_oauth.py` | OAuth2 flows | Password auth |
| `sanitize.py` | XSS sanitization only | DB queries, HTTP |
| `storage.py` | File I/O + validation only | Business logic |
| `rate_limit.py` | Request counting only | Auth, DB access |

**Frontend too:**

| File | Responsibility |
|------|---------------|
| `AuthContext.jsx` | Auth state management only |
| `api.js` | HTTP requests only |
| `timeUtils.js` | Time formatting only |
| `useNotifications.js` | Notification WebSocket only |

---

### O — Open/Closed Principle

> "Open for extension, closed for modification."

**Password hashing — add new algorithm without changing existing code:**

```python
# File: services/shared/shared/core/security.py
password_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

# To add argon2 later, just change the list — no code changes:
# password_context = CryptContext(schemes=["argon2", "pbkdf2_sha256"], deprecated="auto")
# Old pbkdf2 hashes still verify. New passwords auto-use argon2.
```

**Middleware — add new middleware without touching existing ones:**

```python
# Just add a line — existing middleware untouched:
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware, ...)
app.add_middleware(CORSMiddleware, ...)
# To add a logging middleware: app.add_middleware(RequestLoggingMiddleware)
```

**Notification types — add new types without changing the model:**

```python
# File: services/shared/shared/models/notification.py
class Notification(Base):
    notification_type = Column(String(50))  # "reply", "mention", "friend_request", ...
    payload = Column(JSON)                  # Flexible — any data shape

# Adding a new notification type (e.g., "badge_earned") requires:
# 1. Call create_notification(db, user_id, "badge_earned", "You earned a badge!", {...})
# That's it. No model changes, no migration.
```

---

### L — Liskov Substitution Principle

> "Subtypes should be substitutable for their base types."

**TimestampMixin — any model using it gets created_at/updated_at:**

```python
# File: services/shared/shared/models/base.py
class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

# All models that use this mixin can be used wherever timestamps are expected:
class Thread(TimestampMixin, Base): ...
class Post(TimestampMixin, Base): ...
class User(TimestampMixin, Base): ...
```

**Pydantic schema inheritance:**

```python
# File: services/shared/shared/schemas/thread.py
class ThreadUpdateRequest(BaseModel):
    title: str | None = None
    body: str | None = None
# Can be used anywhere a BaseModel is expected — validation works the same way
```

---

### I — Interface Segregation Principle

> "Clients should not be forced to depend on interfaces they don't use."

**Different response schemas for different consumers:**

```python
# File: services/shared/shared/schemas/user.py

# For the user themselves — includes private data
class UserMeResponse(BaseModel):
    id: int
    email: str          # Private — only you see your own email
    username: str
    role: str
    bio: str | None
    avatar_url: str | None
    is_verified: bool

# For public profile views — no private data
class UserPublicProfileResponse(BaseModel):
    id: int
    username: str       # No email!
    role: str
    bio: str | None
    avatar_url: str | None
    is_online: bool

# For user lists — minimal data
class UserListItemResponse(BaseModel):
    id: int
    username: str
    avatar_url: str | None
    is_online: bool
```

Each API consumer gets only the data it needs — no over-exposure.

---

### D — Dependency Inversion Principle

> "Depend on abstractions, not concretions."

**Routes depend on abstract dependencies, not concrete implementations:**

```python
# File: services/community/app/forum_routes.py
@router.post("/")
def create_thread(
    data: ThreadCreateRequest,
    current_user: User = Depends(get_current_user),   # Abstract: "give me the current user"
    db: Session = Depends(get_db),                    # Abstract: "give me a DB session"
):
    ...

# In production: get_db() returns a PostgreSQL session
# In tests: get_db() is overridden to return a SQLite session
# The route code is IDENTICAL in both cases
```

**File:** `services/tests/conftest.py`

```python
# Override the dependency for tests:
def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db    # Swap implementation
```

---

## 3. Clean Code Practices

### 3.1 Meaningful Names

```python
# Bad:
def proc(d, u):
    t = T(a=u.id, b=d.title)

# What we actually write:
def create_thread(db: Session, data: ThreadCreateRequest, author: User) -> dict:
    thread = Thread(author_id=author.id, title=data.title, body=data.body)
```

All names are self-documenting: `get_current_user`, `require_can_participate`,
`sanitize_text`, `publish_event`, `list_audit_logs`, `cast_vote`.

### 3.2 Small Functions (Single Purpose)

Every function does ONE thing. Large operations are broken into helpers:

```python
# File: services/community/app/forum_services.py
# Instead of one 200-line create_thread(), we have:
def create_thread(...)        # Orchestrator (calls the below)
def _build_post_tree(...)     # Builds nested comment tree
def _serialize_thread(...)    # Converts ORM model to dict
def _thread_author(...)       # Fetches thread author with error handling
def _thread_category(...)     # Fetches category with error handling
```

### 3.3 Guard Clauses (Early Returns)

Avoid deep nesting by returning early on error conditions:

```python
# File: services/shared/shared/core/auth_helpers.py
def get_current_user(token, db):
    payload = safe_decode_token(token)
    if not payload or payload.get("type") != "access":     # Guard 1
        raise HTTPException(401, "Could not validate credentials.")

    subject = payload.get("sub")
    if not subject:                                         # Guard 2
        raise HTTPException(401, "Could not validate credentials.")

    user = db.execute(select(User).where(User.id == int(subject))).scalar_one_or_none()
    if not user or user.is_banned or not user.is_active:   # Guard 3
        raise HTTPException(401, "Could not validate credentials.")

    # Happy path — all checks passed
    user.last_seen = datetime.now(timezone.utc)
    db.commit()
    return user
```

### 3.4 No Magic Numbers

```python
# Bad:
if len(files) > 20:
    raise ValueError()

# What we do:
# File: services/shared/shared/schemas/thread.py
attachment_ids: list[int] = Field(default_factory=list, max_length=20)   # Named constraint

# File: services/shared/shared/core/config.py
access_token_expire_minutes: int = 30       # Named setting
refresh_token_expire_days: int = 7          # Named setting
max_upload_size_mb: int = 25                # Named setting
```

### 3.5 Consistent Error Handling

Every error returns a proper HTTP status code with a meaningful message:

```python
# 400 — Bad Request (validation error)
raise HTTPException(400, "Invalid vote value")

# 401 — Unauthorized (not logged in / bad token)
raise HTTPException(401, "Could not validate credentials.")

# 403 — Forbidden (logged in but not allowed)
raise HTTPException(403, "You do not have permission to perform this action.")

# 404 — Not Found
raise HTTPException(404, "Thread not found.")

# 409 — Conflict (duplicate)
raise HTTPException(409, "Friend request already sent.")

# 429 — Too Many Requests (rate limit)
JSONResponse(status_code=429, content={"detail": "Too many requests."})
```

No bare `except:` anywhere in the codebase. Every catch is specific:

```python
except ProgrammingError as exc:     # Specific: SQL error
except RedisError:                  # Specific: Redis connection issue
except JWTError:                    # Specific: Token decode failure
except smtplib.SMTPException:       # Specific: Email sending failure
```

### 3.6 DRY (Don't Repeat Yourself)

Shared library eliminates duplication across services:

| Shared utility | Used by | Instead of duplicating in... |
|---------------|---------|------------------------------|
| `sanitize_text()` | All 12 schema files | Every service having its own sanitizer |
| `get_current_user()` | All route files | Every service re-implementing JWT decode |
| `create_notification()` | Core + Community | Both services writing notification code |
| `publish_event()` | Forum + Chat + Admin | Every feature re-implementing Redis pub |
| `audit.record()` | Auth + Forum + Admin + Chat | Every action writing audit code |

### 3.7 Docstrings on Every Function

Every function has a Google-style docstring:

```python
def cast_vote(db: Session, user: User, entity_type: str, entity_id: int, value: int) -> dict:
    """Create, flip, or remove a vote on a thread or post.

    Uses an upsert pattern:
    - If no existing vote: create new vote
    - If existing vote with same value: remove (toggle off)
    - If existing vote with different value: flip (+1 <-> -1)

    Args:
        db: Database session.
        user: The authenticated user casting the vote.
        entity_type: Either "thread" or "post".
        entity_id: ID of the thread or post being voted on.
        value: +1 (upvote) or -1 (downvote).

    Returns:
        Dict with new_score (int) and user_vote (int or None).
    """
```

### 3.8 Type Annotations Everywhere

```python
# Every function signature is fully typed:
def list_threads(
    db: Session,
    category_slug: str | None = None,
    sort: str = "newest",
    time_range: str | None = None,
    page: int = 1,
    page_size: int = 20,
    tag: str | None = None,
) -> dict[str, object]:
```

---

## 4. Frontend Patterns (React)

### 4.1 Context Provider Pattern

```jsx
// File: frontend/src/context/AuthContext.jsx
const AuthContext = createContext(null);

export function AuthProvider({ children }) {
    const [session, setSession] = useState(null);
    const [profile, setProfile] = useState(null);

    const login = useCallback(async (email, password) => { ... }, []);
    const logout = useCallback(() => { ... }, []);

    const value = useMemo(() => ({
        session, profile, login, logout
    }), [session, profile, login, logout]);

    return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);
```

### 4.2 Custom Hook Pattern (Separation of Concerns)

```jsx
// File: frontend/src/hooks/useThreadLiveUpdates.js
// Data fetching + WebSocket logic separated from UI
function useThreadLiveUpdates(threadId, { onPostCreated, onVoteUpdated }) {
    useEffect(() => {
        const ws = new WebSocket(`${WS_BASE_URL}/ws/thread/${threadId}`);
        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            if (data.type === "post_created") onPostCreated(data);
        };
        return () => ws.close();    // Cleanup on unmount or threadId change
    }, [threadId]);
}

// Used in ThreadPage.jsx — UI only cares about callbacks:
useThreadLiveUpdates(threadId, {
    onPostCreated: (data) => setPosts(prev => [...prev, data.post]),
    onVoteUpdated: (data) => updateVoteInTree(data),
});
```

### 4.3 Protected Route Pattern (HOC / Layout Route)

```jsx
// File: frontend/src/components/ProtectedRoute.jsx
function ProtectedRoute({ requiredRole }) {
    const { session, profile } = useAuth();

    if (!session) return <Navigate to="/login" replace />;

    if (requiredRole === "staff" && !["admin","moderator"].includes(profile?.role))
        return <Navigate to="/" replace />;

    return <Outlet />;   // Render child routes
}

// Used in App.jsx:
<Route element={<ProtectedRoute />}>
    <Route path="/dashboard" element={<DashboardPage />} />
    <Route path="/chat" element={<ChatPage />} />
</Route>
<Route element={<ProtectedRoute requiredRole="staff" />}>
    <Route path="/admin" element={<AdminPage />} />
</Route>
```

### 4.4 Controlled Component Pattern

```jsx
// File: frontend/src/pages/HomePage.jsx
const [title, setTitle] = useState("");
const [body, setBody] = useState("");
const [categoryId, setCategoryId] = useState("");

<input value={title} onChange={(e) => setTitle(e.target.value)} />
<textarea value={body} onChange={(e) => setBody(e.target.value)} />
<select value={categoryId} onChange={(e) => setCategoryId(e.target.value)}>
```

React state is the single source of truth. The DOM input always reflects state.

### 4.5 URL-as-State Pattern

```jsx
// File: frontend/src/pages/HomePage.jsx
const [searchParams, setSearchParams] = useSearchParams();
const page = parseInt(searchParams.get("page")) || 1;
const community = searchParams.get("community") || "";

// Changing page updates the URL — shareable, bookmarkable, back-button works
const goToPage = (p) => {
    const params = new URLSearchParams(searchParams);
    if (p === 1) params.delete("page");
    else params.set("page", p);
    setSearchParams(params);
};
```

---

*Every pattern listed here has inline comments in the actual source code explaining
it. Open the referenced file and search for the comment to see it in full context.*

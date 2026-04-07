# Low-Level Design (LLD)

## 1. Overview

This document describes the internal structure, module layout, data flow, and implementation details for each of PulseBoard's 3 backend services after the consolidation from 7 services (see [ADR-0001](adr/0001-consolidate-microservices.md)).

---

## 2. Directory Structure

```
services/
├── shared/                          # Shared pip-installable library
│   ├── pyproject.toml
│   └── shared/
│       ├── __init__.py
│       ├── core/
│       │   ├── auth_helpers.py      # get_current_user(), JWT validation, last_seen update
│       │   ├── config.py            # Settings (Pydantic BaseSettings, all env vars)
│       │   ├── database.py          # Engine, SessionLocal, Base, get_db, init_db, _run_migrations
│       │   ├── events.py            # publish_event(), ConnectionManager
│       │   ├── logging.py           # configure_logging()
│       │   ├── redis.py             # get_redis_client()
│       │   └── security.py          # create_access_token(), create_refresh_token(), safe_decode_token()
│       ├── models/                  # All 24 SQLAlchemy ORM models
│       │   ├── __init__.py          # Imports all models to ensure metadata registration
│       │   ├── base.py              # TimestampMixin
│       │   ├── user.py              # User, RefreshToken, EmailVerificationToken, PasswordResetToken
│       │   ├── category.py          # Category
│       │   ├── thread.py            # Thread, ThreadSubscription
│       │   ├── post.py              # Post
│       │   ├── tag.py               # Tag, ThreadTag
│       │   ├── vote.py              # Vote, Reaction, ContentReport, ModerationAction, CategoryModerator, CategoryRequest
│       │   ├── chat.py              # ChatRoom, ChatRoomMember
│       │   ├── notification.py      # Notification
│       │   ├── friendship.py        # FriendRequest
│       │   ├── oauth_account.py     # OAuthAccount
│       │   ├── attachment.py        # Attachment
│       │   └── audit_log.py         # AuditLog
│       ├── schemas/                 # All Pydantic request/response schemas
│       │   ├── auth.py, user.py, category.py, thread.py, post.py, tag.py
│       │   ├── vote.py, chat.py, notification.py, upload.py, search.py, admin.py
│       └── services/               # Cross-cutting service helpers
│           ├── audit.py             # record(), list_audit_logs(), action constants
│           ├── bot.py               # AI bot (Groq + Tavily + DuckDuckGo)
│           ├── email.py             # SMTP email dispatch
│           ├── mentions.py          # @mention parsing and notification
│           ├── notifications.py     # create_notification()
│           ├── moderation.py        # send_moderation_notification()
│           ├── attachments.py       # Attachment linking helpers
│           └── storage.py           # File upload/storage helpers
│
├── gateway/                         # API Gateway (port 8000)
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── __init__.py
│       └── main.py                  # Reverse proxy, WS hub, Redis bridge, static files
│
├── core/                            # Core Service (port 8001)
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── __init__.py
│       ├── main.py                  # FastAPI app, mounts all core routers
│       ├── auth_routes.py           # /api/v1/auth/* (10 routes)
│       ├── auth_services.py         # Auth business logic
│       ├── auth_email.py            # Verification + reset email templates
│       ├── auth_oauth.py            # Google + GitHub OAuth handlers
│       ├── user_routes.py           # /api/v1/users/* (12 routes)
│       ├── user_services.py         # User/friends business logic
│       ├── upload_routes.py         # /api/v1/uploads/* (2 routes)
│       └── notification_routes.py   # /api/v1/notifications/* (3 routes)
│       └── notification_services.py # Notification business logic
│
├── community/                       # Community Service (port 8002)
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── __init__.py
│       ├── main.py                  # FastAPI app, mounts all community routers
│       ├── forum_routes.py          # /api/v1/categories/*, /threads/*, /posts/*, /search/* (24 routes)
│       ├── forum_services.py        # Thread/post/category business logic
│       ├── forum_search.py          # Full-text search logic
│       ├── forum_votes.py           # Vote + reaction logic
│       ├── forum_seed.py            # Default category seeding
│       ├── admin_routes.py          # /api/v1/admin/* (21 routes)
│       ├── admin_services.py        # Moderation business logic
│       ├── chat_routes.py           # /api/v1/chat/* (7 routes)
│       └── chat_services.py         # Chat room + message business logic
│
└── tests/                           # Integration tests
    ├── conftest.py                  # Composite app builder (SQLite)
    ├── test_auth.py                 # 5 auth tests
    ├── test_forum.py                # 7 forum tests
    └── test_audit.py                # 10 audit tests
```

---

## 3. Core Service — Internal Design

### 3.1 Module Responsibilities

| Module | Lines (approx.) | Responsibility |
|--------|-----------------|----------------|
| `main.py` | 65 | App factory, lifespan, CORS, mount 4 routers + StaticFiles for uploads |
| `auth_routes.py` | 123 | 10 auth endpoints (register, login, refresh, OAuth, verification, password reset) |
| `auth_services.py` | 270 | Registration, login validation, token management, email verification, password reset |
| `auth_email.py` | 151 | HTML email templates, SMTP dispatch (smtplib, timeout=2) |
| `auth_oauth.py` | 215 | Google OpenID Connect + GitHub OAuth2 flows |
| `user_routes.py` | 259 | 12 user endpoints (profile CRUD, friends, search, reports) |
| `user_services.py` | 432 | Profile updates, friend request logic, avatar uploads, user serialization, online status, audit logging |
| `upload_routes.py` | 36 | 2 upload endpoints (limits, create) |
| `notification_routes.py` | 39 | 3 notification endpoints (list, mark read, mark all read) |
| `notification_services.py` | 67 | Notification query/update logic |

### 3.2 Router Mounting

```python
# core/app/main.py
app.include_router(auth_router,         prefix="/api/v1/auth",          tags=["auth"])
app.include_router(user_router,         prefix="/api/v1/users",         tags=["users"])
app.include_router(upload_router,       prefix="/api/v1/uploads",       tags=["uploads"])
app.include_router(notification_router, prefix="/api/v1/notifications", tags=["notifications"])
app.mount("/uploads", StaticFiles(directory=upload_dir), name="uploads")
```

### 3.3 Auth Flow — Sequence

```
POST /api/v1/auth/register
  → auth_services.register_user()
    → Hash password (pbkdf2_sha256)
    → INSERT users (is_verified=False)
    → INSERT email_verification_tokens
    → auth_email._send_verification_email() (SMTP)
    → Return {"message": "Account created..."}

POST /api/v1/auth/login
  → auth_services.authenticate_user()
    → SELECT user WHERE email=...
    → Verify password hash
    → Check is_verified, is_active, is_banned
    → CREATE access_token (JWT, 30 min)
    → CREATE refresh_token (JWT, 7 days)
    → INSERT refresh_tokens
    → Return {access_token, refresh_token}
```

### 3.4 User Profile — Data Flow

```
GET /api/v1/users/me
  → get_current_user() [shared auth helper]
    → Decode JWT, load User from DB
    → UPDATE users SET last_seen=now()
  → user_services._serialize_user(user)
    → Compute is_online (last_seen within 5 min)
    → Return UserMeResponse

PATCH /api/v1/users/me
  → get_current_user()
  → user_services.update_profile(db, user, updates)
    → UPDATE users SET bio/username/avatar_url
    → Return updated UserMeResponse
```

### 3.5 Notification — Data Flow

```
# Created by other services via shared helper:
shared.services.notifications.create_notification(db, user_id, type, title, payload)
  → INSERT notifications
  → publish_event(f"notifications:{user_id}", notification_data)

# Read by Core service:
GET /api/v1/notifications
  → notification_services.list_notifications(db, user_id)
    → SELECT notifications WHERE user_id=... ORDER BY created_at DESC

PATCH /api/v1/notifications/read-all
  → notification_services.mark_all_read(db, user_id)
    → UPDATE notifications SET is_read=True WHERE user_id=... AND is_read=False
```

---

## 4. Community Service — Internal Design

### 4.1 Module Responsibilities

| Module | Lines (approx.) | Responsibility |
|--------|-----------------|----------------|
| `main.py` | 55 | App factory, lifespan, CORS, mount 6 routers, optional category seeding |
| `forum_routes.py` | 456 | 24 endpoints across 4 sub-routers (categories, threads, posts, search) |
| `forum_services.py` | 794 | Thread/post CRUD, pagination, category management, subscriptions |
| `forum_search.py` | 108 | Full-text search across threads and posts |
| `forum_votes.py` | 278 | Vote casting/removal, reaction toggling, voter listing |
| `forum_seed.py` | 45 | Default category seeding on startup |
| `admin_routes.py` | 358 | 21 moderation endpoints |
| `admin_services.py` | 777 | User management, thread moderation, reports, category requests |
| `chat_routes.py` | 140 | 7 chat endpoints |
| `chat_services.py` | 375 | Chat room + message business logic |

### 4.2 Router Mounting

```python
# community/app/main.py
app.include_router(category_router, prefix="/api/v1/categories", tags=["categories"])
app.include_router(thread_router,   prefix="/api/v1/threads",    tags=["threads"])
app.include_router(post_router,     prefix="/api/v1/posts",      tags=["posts"])
app.include_router(search_router,   prefix="/api/v1/search",     tags=["search"])
app.include_router(admin_router,    prefix="/api/v1/admin",      tags=["admin"])
app.include_router(chat_router,     prefix="/api/v1/chat",       tags=["chat"])
```

### 4.3 Thread Listing with Pagination — Detailed Flow

```
GET /api/v1/threads?category=general&sort=top&time_range=week&page=2&page_size=20

  → forum_routes.list_threads_route()
    → Validate query params:
        category: str (slug)          → filter by category.slug
        sort: "new" | "top" | "trending"
        time_range: "all" | "year" | "month" | "week" | "day" | "hour"
        page: int >= 1 (default 1)
        page_size: int 1..100 (default 20)
        tag: str (optional)

  → forum_services.list_threads(db, category, sort, time_range, page, page_size, tag)
    → Build base query: SELECT threads JOIN categories JOIN users
    → Apply filters:
        IF category: WHERE categories.slug = :category
        IF tag: JOIN thread_tags JOIN tags WHERE tags.name = :tag
        IF time_range != "all": WHERE threads.created_at >= cutoff_date
    → Apply sorting:
        "new"      → ORDER BY threads.created_at DESC
        "top"      → ORDER BY vote_score DESC (subquery: SUM(votes.value))
        "trending" → ORDER BY (vote_score / age_hours^1.5) DESC
    → Count total: SELECT COUNT(*)
    → Paginate: OFFSET (page-1)*page_size LIMIT page_size
    → For each thread: attach author, category, tags, vote_count, reaction_counts, post_count
    → Return PaginatedThreadsResponse:
        {
          "items": [...],        # list of ThreadResponse
          "total": 142,          # total matching threads
          "page": 2,             # current page
          "page_size": 20,       # items per page
          "total_pages": 8       # ceil(total / page_size)
        }
```

### 4.4 Moderation — Data Flow

```
# Staff locks a thread:
PATCH /api/v1/admin/threads/{thread_id}/lock
  → admin_services._ensure_staff(user)
  → admin_services._get_manageable_thread(db, user, thread_id)
    → Check thread exists
    → If moderator: verify thread.category_id in assigned categories
    → If moderator: verify thread.author.role < user.role
  → UPDATE threads SET is_locked=True
  → Return {"detail": "Thread locked."}

# Staff resolves a report:
PATCH /api/v1/admin/reports/{report_id}/resolve
  → admin_services._ensure_staff(user)
  → SELECT content_report WHERE id=...
  → UPDATE content_reports SET status="resolved", resolved_by=user.id, resolved_at=now()
  → Return updated report

# Admin approves category request:
PATCH /api/v1/admin/category-requests/{request_id}/review
  → admin_services._ensure_admin(user)
  → SELECT category_request WHERE id=...
  → IF status=="approved":
      INSERT categories (title, slug, description from request)
      INSERT category_moderators (requester_id, new_category_id)
      publish_event("global", {event: "category_created", category})
  → UPDATE category_requests SET status, reviewed_by, reviewed_at
```

---

## 5. Chat (Community Service) — Internal Design

### 5.1 Module Responsibilities

| Module | Lines (approx.) | Responsibility |
|--------|-----------------|----------------|
| `chat_routes.py` | 140 | 7 chat endpoints |
| `chat_services.py` | 375 | Room CRUD, DM creation, message sending, bot integration |

### 5.2 Message Sending — Data Flow

```
POST /api/v1/chat/rooms/{room_id}/messages {body: "Hello @pulse"}
  → Validate user is room member
  → INSERT messages (room_id, sender_id, body)
  → Process @mentions:
      → Parse @usernames from body
      → For each mentioned user in room: create_notification()
  → publish_event(f"chat:room:{room_id}", {event: "message_created", message})
  → IF @pulse mentioned:
      → schedule_chat_bot_reply(room_id, message_id)
        → threading.Thread(target=_generate_chat_bot_reply, daemon=True).start()
        → Bot builds context, calls Groq API, creates reply message
        → publish_event(f"chat:room:{room_id}", {event: "message_created", bot_message})
  → Return 201 {message}
```

---

## 6. Gateway — Internal Design

### 6.1 Components

| Component | Responsibility |
|-----------|---------------|
| `ROUTE_MAP` | List of (URL prefix, backend service URL) tuples |
| `proxy()` | Catch-all route that forwards HTTP requests via httpx |
| `proxy_uploads()` | Proxies `GET /uploads/*` to Core service (avatars, attachments) |
| `_redis_subscriber_loop()` | Long-running async task: Redis → WebSocket bridge |
| `_redis_channel_to_ws_channel()` | Channel name mapping (e.g., `chat:room:X` → `chat:X`) |
| `ConnectionManager` | Manages WebSocket connections per channel (from shared lib) |

### 6.2 Route Resolution

```python
ROUTE_MAP = [
    ("/api/v1/auth",          "http://core:8001"),
    ("/api/v1/uploads",       "http://core:8001"),
    ("/api/v1/users",         "http://core:8001"),
    ("/api/v1/notifications", "http://core:8001"),
    ("/api/v1/categories",    "http://community:8002"),
    ("/api/v1/threads",       "http://community:8002"),
    ("/api/v1/posts",         "http://community:8002"),
    ("/api/v1/search",        "http://community:8002"),
    ("/api/v1/admin",         "http://community:8002"),
    ("/api/v1/chat",          "http://community:8002"),
]
```

### 6.3 Redis-to-WebSocket Bridge

```
_redis_subscriber_loop():
  1. Connect to Redis
  2. PSUBSCRIBE thread:*, chat:room:*, notifications:*
  3. SUBSCRIBE global
  4. Loop:
     a. get_message(timeout=1.0) via asyncio.to_thread()
     b. Parse message type (message/pmessage)
     c. Map Redis channel → WS channel via _redis_channel_to_ws_channel()
     d. Check if any WS clients connected to that channel
     e. JSON parse payload
     f. connection_manager.broadcast(ws_channel, payload)
  5. On error: log, sleep 2s, reconnect
  6. On cancellation (shutdown): clean up pubsub
```

---

## 7. Shared Library — Key Components

### 7.1 Auth Helpers (`shared/core/auth_helpers.py`)

```python
def get_current_user(token: str, db: Session) -> User:
    """FastAPI dependency — validates JWT and returns User."""
    payload = safe_decode_token(token)         # Decode JWT (HS256)
    user = db.query(User).get(payload["sub"])  # Load user
    if not user.is_active or user.is_banned:   # Check status
        raise HTTPException(401)
    user.last_seen = datetime.now(UTC)         # Update online status
    db.commit()
    return user
```

### 7.2 Event Publishing (`shared/core/events.py`)

```python
def publish_event(channel: str, data: dict) -> None:
    """Publish a JSON event to Redis pub/sub. Silently swallows errors."""
    try:
        client = get_redis_client()
        client.publish(channel, json.dumps(data, default=str))
    except Exception:
        pass  # No Redis in tests — silently ignore

class ConnectionManager:
    """Manages WebSocket connections by channel. Used by gateway."""
    connections: dict[str, list[WebSocket]]

    async def connect(channel, ws): ...
    def disconnect(channel, ws): ...
    async def broadcast(channel, data):
        # Try/except per connection, auto-remove dead ones
```

### 7.3 Bot Service (`shared/services/bot.py`)

```
schedule_forum_bot_reply(thread_id, post_id):
  → Start daemon thread
  → _generate_forum_bot_reply(thread_id, post_id):
      → Own SessionLocal() DB session
      → build_thread_context() — gather recent posts
      → get_thread_participants() — list active users
      → _web_search() — Tavily (primary) + DuckDuckGo (fallback)
      → Call Groq API (groq/compound-mini) with system prompt + context
      → _strip_citations() — remove [n] artifacts
      → INSERT posts (bot reply)
      → publish_event(f"thread:{thread_id}", {event: "post_created", post})
      → Retry logic: 3 retries, exponential backoff (2s, 4s, 8s) for 429
```

---

## 8. Database Access Pattern

All services use the same pattern:

```python
# Dependency injection via FastAPI
from shared.core.database import get_db

@router.get("/endpoint")
def my_endpoint(db: Session = Depends(get_db)):
    # db is a SQLAlchemy Session bound to PostgreSQL (prod) or SQLite (tests)
    result = db.query(Model).filter(...).all()
    return result
```

- **Connection pool**: SQLAlchemy engine with default pool settings.
- **Session lifecycle**: One session per request via `get_db()` generator.
- **Migrations**: `init_db()` calls `Base.metadata.create_all()` + `_run_migrations()` with raw SQL `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.

---

## 9. Error Handling Pattern

```python
# All services use FastAPI's HTTPException
from fastapi import HTTPException, status

def get_thread(db: Session, thread_id: int) -> Thread:
    thread = db.query(Thread).get(thread_id)
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thread not found.",
        )
    return thread

# Role guards
def _ensure_staff(user: User) -> None:
    if user.role not in ("admin", "moderator"):
        raise HTTPException(status_code=403, detail="Staff access required.")

def _ensure_admin(user: User) -> None:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")

def _assert_manageable_target(actor: User, target: User) -> None:
    role_rank = {"member": 1, "moderator": 2, "admin": 3}
    if role_rank[actor.role] <= role_rank[target.role]:
        raise HTTPException(status_code=403, detail="Cannot manage this user.")
```

---

## 10. Test Architecture

### 10.1 Composite App Pattern

Tests mount all service routers into a single FastAPI app backed by SQLite:

```python
# conftest.py
composite = FastAPI()
composite.include_router(auth_routes.router,    prefix="/api/v1/auth")
composite.include_router(user_routes.router,    prefix="/api/v1/users")
composite.include_router(upload_routes.upload_router, prefix="/api/v1/uploads")
composite.include_router(forum_routes.category_router, prefix="/api/v1/categories")
composite.include_router(forum_routes.thread_router,   prefix="/api/v1/threads")
composite.include_router(forum_routes.post_router,     prefix="/api/v1/posts")
composite.include_router(forum_routes.search_router,   prefix="/api/v1/search")
composite.include_router(notification_routes.router,    prefix="/api/v1/notifications")
composite.include_router(admin_routes.router,          prefix="/api/v1/admin")
composite.include_router(chat_routes.router,           prefix="/api/v1/chat")
```

### 10.2 Test Database

- **Engine**: SQLite file (`test_services.db`) with `check_same_thread=False`.
- **Setup**: `Base.metadata.drop_all()` + `create_all()` per test (autouse fixture).
- **Seeding**: 4 default categories inserted per test.
- **Cleanup**: `rm -f test_services.db` after test run.

### 10.3 Mocking

- **SMTP**: Autouse fixture patches `_send_verification_email` and `_send_moderation_email` to no-ops.
- **Redis**: Not mocked. `publish_event()` silently swallows errors when Redis is unavailable.
- **External APIs**: Not mocked. Bot tests excluded via `-k "not subscribe"`.

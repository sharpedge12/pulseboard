# PulseBoard Interview Preparation Guide

> Likely interview questions, how to answer them, and which files to reference.

---

## Table of Contents

1. [Architecture Questions](#1-architecture-questions)
2. [Backend / Python Questions](#2-backend--python-questions)
3. [Database Questions](#3-database-questions)
4. [Authentication & Security Questions](#4-authentication--security-questions)
5. [Frontend / React Questions](#5-frontend--react-questions)
6. [Real-Time / WebSocket Questions](#6-real-time--websocket-questions)
7. [Testing Questions](#7-testing-questions)
8. [DevOps / Docker Questions](#8-devops--docker-questions)
9. [Design Decision Questions](#9-design-decision-questions)
10. [Code-Specific Deep Dives](#10-code-specific-deep-dives)

---

## 1. Architecture Questions

### Q: Why microservices instead of a monolith?

**Answer:** We started with 7 microservices but consolidated to 3 (Core + Community + Gateway) based on the trade-offs:

- **Pros**: Independent deployment, technology flexibility, team ownership boundaries, fault isolation
- **Cons**: Network latency between services, distributed transaction complexity, operational overhead
- **Our compromise**: Shared database (no data isolation) reduces complexity while keeping service boundaries. The Gateway acts as the single entry point, simplifying the frontend.

**Reference**: `docs/adr/0001-consolidate-microservices.md`, `docker-compose.yml`

### Q: Why a shared database instead of database-per-service?

**Answer:** With 2 backend services and a small team, the operational cost of separate databases (data sync, eventual consistency, distributed joins) outweighs the benefits. All services import the same SQLAlchemy models from `services/shared/`. The trade-off is tight coupling at the data layer, but for a forum app this is acceptable.

**Reference**: `services/shared/shared/core/database.py`, `services/shared/shared/models/`

### Q: What does the Gateway service do?

**Answer:** It's a reverse proxy + WebSocket hub:
1. Routes HTTP requests to Core or Community based on URL path prefix
2. Manages 4 WebSocket channels for real-time updates
3. Bridges Redis pub/sub events to WebSocket clients
4. Applies rate limiting on auth endpoints
5. Proxies static file serving (uploads) to Core

**Reference**: `services/gateway/app/main.py`

### Q: How would you scale this system?

**Answer:**
- **Horizontal scaling**: Run multiple Core/Community instances behind a load balancer. The Gateway already proxies by path, so you'd add a load balancer in front.
- **Database**: Read replicas for search/listing queries, connection pooling with PgBouncer.
- **Redis**: Redis Cluster for pub/sub at scale. The current rate limiter is in-memory (single process) — would need Redis-backed rate limiting.
- **WebSocket**: Sticky sessions or a dedicated WebSocket service with Redis adapter (like Socket.IO with Redis).
- **File uploads**: Move from local filesystem to S3/CloudFront.

---

## 2. Backend / Python Questions

### Q: Explain FastAPI dependency injection

**Answer:** FastAPI uses `Depends()` to create a chain of dependencies that run before each request handler. In our app:

```python
# Chain: extract token -> decode JWT -> lookup user -> check permissions
def get_current_user(
    token: str = Depends(oauth2_scheme),    # Step 1: Extract Bearer token
    db: Session = Depends(get_db),          # Step 2: Get DB session
) -> User:
    payload = safe_decode_token(token)       # Step 3: Decode JWT
    user = db.query(User).get(payload["sub"])# Step 4: Lookup user
    return user

# Route uses the dependency
@router.post("/threads")
def create_thread(
    data: ThreadCreateRequest,
    current_user: User = Depends(get_current_user),  # Auth required
    db: Session = Depends(get_db),
):
    ...
```

The `get_db()` generator pattern ensures the session is always closed (like a context manager).

**Reference**: `shared/core/auth_helpers.py`, `shared/core/database.py`

### Q: What is Pydantic and why use it?

**Answer:** Pydantic is a data validation library. We use it for:
1. **Request validation** — Automatically validates incoming JSON against schema (type checking, constraints like `min_length`, `max_length`, `pattern`)
2. **Response serialization** — Converts SQLAlchemy models to JSON-safe dicts
3. **Security** — `field_validator` decorators sanitize user input to prevent XSS
4. **Documentation** — FastAPI auto-generates OpenAPI docs from Pydantic schemas

```python
class ThreadCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    body: str = Field(..., min_length=1, max_length=40000)
    category_id: int = Field(..., ge=1)  # Must be >= 1

    @field_validator("title")
    @classmethod
    def sanitize_title(cls, v: str) -> str:
        return sanitize_text(v)  # Strip XSS from user input
```

**Reference**: `shared/schemas/` (all files)

### Q: How does the `@pulse` AI bot work?

**Answer:**
1. When a user mentions `@pulse` in a thread or chat message, the backend detects it
2. A **background daemon thread** is spawned (not blocking the HTTP response)
3. The thread builds context (thread title, recent posts, user profiles)
4. Sends a request to Groq's `compound-mini` model (which has built-in web search)
5. If Groq fails, falls back to Tavily search, then DuckDuckGo
6. Creates a reply post/message attributed to the `pulse` user
7. Publishes a Redis event for real-time delivery
8. **Retry logic**: 3 retries with exponential backoff (2s, 4s, 8s) for rate limits

**Reference**: `shared/services/bot.py`

---

## 3. Database Questions

### Q: Explain the database schema design

**Answer:** 24 tables organized around 5 domains:
1. **Auth**: `users`, `refresh_tokens`, `email_verification_tokens`, `password_reset_tokens`, `oauth_accounts`
2. **Forum**: `categories`, `threads`, `posts`, `tags`, `thread_tags`, `votes`, `reactions`, `thread_subscriptions`
3. **Moderation**: `content_reports`, `moderation_actions`, `category_moderators`, `category_requests`
4. **Chat**: `chat_rooms`, `chat_room_members`, `messages`
5. **System**: `notifications`, `attachments`, `audit_logs`, `friend_requests`

Key patterns: self-referential FK (posts), junction tables (thread_tags, chat_room_members), polymorphic entity_type (votes, reports).

**Reference**: `docs/database-design.md`, `shared/models/`

### Q: How do nested comments work?

**Answer:** The `Post` model has a self-referential foreign key:

```python
class Post(TimestampMixin, Base):
    id = ...
    thread_id = ...         # Which thread this belongs to
    parent_post_id = ...    # NULL = top-level reply, otherwise nested under another post
    body = ...
```

To build the tree, `_build_post_tree()` uses an **O(n) two-pass algorithm**:
1. First pass: create a hash map of `post_id -> serialized_post`
2. Second pass: attach each post to its parent's `replies` list
3. Return only posts where `parent_post_id` is NULL (top-level)

This is much better than recursive SQL queries (which would be O(depth × n)).

**Reference**: `community/app/forum_services.py` → `_build_post_tree()`

### Q: Why no Alembic for migrations?

**Answer:** Trade-off decision for a small project:
- `create_all()` handles initial schema creation
- `_run_migrations()` uses raw SQL `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for incremental changes
- Simpler than maintaining migration files, but loses migration history and rollback capability
- For a production app, Alembic would be strongly recommended

**Reference**: `shared/core/database.py` → `_run_migrations()`

### Q: How do you handle the concurrent table creation race condition?

**Answer:** Both Core and Community call `init_db()` on startup. With Docker Compose they start simultaneously, causing a TOCTOU race in `create_all()` (both check table doesn't exist, both try to create it). Solution:

1. `init_db()` retries up to 3 times with exponential backoff when it catches `DuplicateTable`
2. Docker Compose sets `depends_on: core` for the Community service (soft ordering)
3. On the final retry, it logs a warning and proceeds (tables already exist from the other service)

**Reference**: `shared/core/database.py` → `init_db()`

---

## 4. Authentication & Security Questions

### Q: Explain the JWT authentication flow

**Answer:**
1. User logs in with email + password
2. Server verifies password hash, creates **access token** (30 min, HS256) and **refresh token** (7 days)
3. Refresh token is stored in DB (for revocation)
4. Frontend stores both in localStorage, sends access token as `Authorization: Bearer <token>` header
5. `get_current_user` dependency decodes JWT on every request
6. When access token expires, frontend calls `/auth/refresh` with the refresh token
7. Server creates new pair, deletes old refresh token (rotation)

**Reference**: `shared/core/security.py`, `core/app/auth_services.py`

### Q: Why HS256 instead of RS256?

**Answer:** HS256 is symmetric (same key signs and verifies). RS256 is asymmetric (private key signs, public key verifies). We use HS256 because:
- All services share the same `SECRET_KEY` (from environment)
- No need for public key distribution
- Simpler infrastructure
- RS256 is better when third parties need to verify tokens without knowing the signing key

### Q: How do you prevent XSS?

**Answer:** Defense in depth with 3 layers:
1. **Pydantic `field_validator`** — `sanitize_text()` strips `<script>`, `<iframe>`, `javascript:` URIs, `onerror=` handlers from all user input
2. **React auto-escaping** — React escapes rendered text by default (we intentionally do NOT use `html.escape()` server-side to avoid double-escaping)
3. **Content Security Policy header** — `script-src 'self'` blocks inline scripts even if XSS payload gets through

**Reference**: `shared/services/sanitize.py`, `shared/core/security_headers.py`

### Q: How do you secure file uploads?

**Answer:** 5 layers of validation:
1. **MIME type whitelist** — Only allow image/jpeg, image/png, image/gif, etc.
2. **Extension whitelist** — Only `.jpg`, `.png`, `.gif`, etc.
3. **Extension-MIME consistency** — `.jpg` must have `image/jpeg` MIME
4. **Magic byte verification** — Read first 32 bytes, verify file signature (e.g., JPEG starts with `FF D8 FF`)
5. **Filename sanitization** — Strip path components (prevent `../../etc/passwd`), replace unsafe chars

**Reference**: `shared/services/storage.py`

### Q: What is CSRF and how do you handle it?

**Answer:** Cross-Site Request Forgery tricks a logged-in user's browser into making requests. We handle it:
- **JWT in Authorization header** (not cookies) — CSRF only works with automatic cookie-based auth
- **OAuth state nonce** — Random string sent with OAuth redirect, verified on callback to prevent authorization code injection
- **SameSite cookies** are not used (we use localStorage)

**Reference**: `core/app/auth_oauth.py` (state nonce)

---

## 5. Frontend / React Questions

### Q: Explain React Context and how you use it

**Answer:** React Context provides a way to pass data through the component tree without prop drilling. We have 2 contexts:

1. **AuthContext** — Stores session tokens, user profile, provides login/logout/refresh functions
2. **ThemeContext** — Stores dark/light preference, persisted to localStorage

Pattern: `createContext()` → `Provider` wraps the app → `useAuth()`/`useTheme()` custom hooks consume it.

**Reference**: `frontend/src/context/AuthContext.jsx`, `ThemeContext.jsx`

### Q: How do you handle real-time updates in React?

**Answer:** Custom hooks that manage WebSocket connections:

```javascript
function useThreadLiveUpdates(threadId, { onPostCreated, onVoteUpdated }) {
    useEffect(() => {
        const ws = new WebSocket(`${WS_BASE_URL}/ws/thread/${threadId}`);
        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            if (data.type === 'post_created') onPostCreated(data);
            if (data.type === 'vote_updated') onVoteUpdated(data);
        };
        return () => ws.close();  // Cleanup on unmount
    }, [threadId]);
}
```

The page component passes callbacks that update React state, triggering re-renders.

**Reference**: `frontend/src/hooks/useThreadLiveUpdates.js`

### Q: How does the @mention autocomplete work?

**Answer:** `MentionTextarea.jsx` implements:
1. Monitors text input for `@` character
2. Extracts the partial username after `@`
3. **Debounces** API calls (waits 300ms after typing stops)
4. Shows dropdown with matching users
5. **Keyboard navigation**: arrow keys move selection, Enter inserts mention
6. When dropdown is open, Enter inserts the selected mention (not submit)
7. When dropdown is closed, Enter delegates to parent's submit handler

**Reference**: `frontend/src/components/MentionTextarea.jsx`

### Q: How do you handle pagination?

**Answer:**
- **Backend**: `list_threads()` accepts `page` and `page_size` query params, returns `PaginatedThreadsResponse` with `items`, `total`, `page`, `page_size`, `total_pages`
- **Frontend**: Reusable `Pagination` component generates page buttons with ellipsis
- **URL sync**: Page number stored in URL query string (`?page=3`) via `useSearchParams`
- **Reset**: Changing category/sort/time filter resets page to 1

**Reference**: `community/app/forum_services.py` → `list_threads()`, `frontend/src/components/Pagination.jsx`

---

## 6. Real-Time / WebSocket Questions

### Q: How does the Redis pub/sub bridge work?

**Answer:**
1. Backend services call `publish_event(channel, payload)` which does `redis.publish()`
2. Gateway subscribes to Redis patterns: `thread:*`, `chat:room:*`, `notifications:*`, `global`
3. Gateway runs a background task (`asyncio.to_thread`) that polls Redis for messages
4. When a message arrives, it maps the Redis channel to a WebSocket channel
5. `ConnectionManager.broadcast()` sends to all connected WebSocket clients on that channel
6. Dead connections are automatically cleaned up during broadcast

**Reference**: `services/gateway/app/main.py`, `shared/core/events.py`

### Q: Why Redis pub/sub instead of direct WebSocket from services?

**Answer:**
- Services don't know about WebSocket connections (separation of concerns)
- Multiple Gateway instances can subscribe to the same Redis channels (horizontal scaling)
- Backend services remain stateless HTTP servers
- Redis handles the fan-out from 1 publisher to N gateway instances

---

## 7. Testing Questions

### Q: How do you test microservices without Docker?

**Answer:** The test `conftest.py` creates a **composite app** that mounts all service routers into a single FastAPI process with SQLite:

```python
app = FastAPI()
app.include_router(auth_router)      # From Core service
app.include_router(thread_router)    # From Community service
app.include_router(admin_router)     # From Community service
# All share the same SQLite database
```

This avoids inter-service HTTP calls and Docker dependencies. Trade-off: doesn't test the Gateway proxy layer or Redis pub/sub.

**Reference**: `services/tests/conftest.py`

### Q: What does each test file cover?

**Answer:**
- `test_auth.py` (5 tests): Registration, email verification, login blocked for unverified, login+profile flow, token refresh
- `test_forum.py` (7 tests): Admin creates category, member/mod can't create directly, mod requests category, thread+post flow, search, default categories
- `test_audit.py` (10 tests): Audit log creation on thread/post/register/login/category, admin sees all logs, member sees only own, action filter, entity_type filter, pagination
- `test_validation.py` (9 tests): XSS stripped from threads/posts, special chars preserved, JPEG/GIF upload, bad MIME rejected, magic byte mismatch rejected, invalid entity_type rejected, vote value=0 rejected

---

## 8. DevOps / Docker Questions

### Q: Explain your Docker Compose setup

**Answer:** 7 services:
- `db` (PostgreSQL 16) — healthcheck with `pg_isready`
- `redis` (Redis 7) — healthcheck with `redis-cli ping`
- `mailhog` — Development SMTP server
- `core` — Depends on db (healthy) + redis (healthy) + mailhog
- `community` — Depends on core (started) + db + redis
- `gateway` — Depends on core + community
- `frontend` — Depends on gateway

### Q: How did you handle the Docker upload permission issue?

**Answer:** Named Docker volumes are created as root, but the app runs as non-root `appuser`. Solution:
1. Created `services/docker-entrypoint.sh` that runs as root
2. Entrypoint does `chown -R appuser:appgroup /app/uploads`
3. Then drops to `appuser` via `gosu` (like `su` but signal-safe)
4. All 3 Dockerfiles use this shared entrypoint

**Reference**: `services/docker-entrypoint.sh`, Dockerfiles

---

## 9. Design Decision Questions

### Q: Why plain CSS instead of Tailwind/CSS-in-JS?

**Answer:** Design constraints for the project:
- No external CSS framework dependency
- CSS custom properties (`:root` variables) enable dark/light theming
- Single `global.css` file (3,336 lines) with BEM-inspired class names
- System fonts (no Google Fonts) — faster load, better privacy

### Q: Why consolidate from 7 services to 3?

**Answer:** Original 7 services (Auth, User, Forum, Moderation, Chat, Notification, Gateway) had:
- High inter-service HTTP overhead (every request crossed 2-3 service boundaries)
- Complex Docker Compose with many containers
- Shared database made service isolation artificial

Consolidated to: Core (auth+user+notification) and Community (forum+moderation+chat) based on domain coupling — features that frequently call each other belong in the same service.

**Reference**: `docs/adr/0001-consolidate-microservices.md`

### Q: Why `pbkdf2_sha256` instead of bcrypt?

**Answer:**
- Python 3.13+ deprecates the `crypt` module that bcrypt depends on
- PBKDF2-SHA256 is NIST-recommended (SP 800-132)
- No 72-byte password length limit (bcrypt silently truncates)
- passlib's `CryptContext` allows zero-downtime algorithm migration later

---

## 10. Code-Specific Deep Dives

### The vote upsert pattern
```python
# forum_votes.py -> cast_vote()
existing = db.query(Vote).filter(Vote.user_id == user.id, ...).first()
if existing:
    if existing.value == new_value:
        db.delete(existing)      # Click same button = undo
    else:
        existing.value = new_value  # Click opposite = flip
else:
    db.add(Vote(value=new_value))   # First vote
```

### The sliding window rate limiter
```python
# rate_limit.py
# Time: [------|-----window-----|NOW]
#        expired    kept entries
# Clean entries older than window, count remaining
entries = [t for t in entries if t > (now - window)]
if len(entries) >= limit:
    return 429  # Too Many Requests
entries.append(now)
```

### The WebSocket connection manager
```python
# events.py -> ConnectionManager
# Connections grouped by channel: {"thread:5": [ws1, ws2, ws3]}
async def broadcast(channel, message):
    dead = []
    for ws in self.connections[channel]:
        try:
            await ws.send_json(message)
        except:
            dead.append(ws)       # Mark dead connections
    for ws in dead:
        self.disconnect(ws)       # Clean up (prevent memory leak)
```

### The OAuth2 state nonce
```python
# auth_oauth.py
# Step 1: Generate random state, store in memory
state = secrets.token_urlsafe(32)
_pending_states[state] = time.time()

# Step 2: Include state in redirect URL to Google
redirect_url = f"https://accounts.google.com/o/oauth2/auth?state={state}&..."

# Step 3: On callback, verify state matches
if request.query_params["state"] not in _pending_states:
    raise HTTPException(400, "Invalid state")  # CSRF attempt!
```

---

## Quick Reference: What to Say for Each Technology

| Technology | What to say |
|-----------|-------------|
| **FastAPI** | "Async Python web framework with automatic OpenAPI docs, Pydantic validation, and dependency injection" |
| **SQLAlchemy** | "Python ORM with declarative models, session management, and relationship loading strategies" |
| **Pydantic** | "Data validation library that ensures type safety and business rules at the API boundary" |
| **JWT** | "Stateless authentication tokens signed with HS256, carrying user ID and expiration" |
| **Redis** | "In-memory data store used for pub/sub real-time event distribution between services" |
| **WebSocket** | "Full-duplex communication protocol for pushing server events to connected browsers" |
| **React Context** | "React's built-in state management for sharing auth/theme state without prop drilling" |
| **React Router v6** | "Client-side routing with layout routes, protected routes, and URL param sync" |
| **Docker Compose** | "Multi-container orchestration with health checks, dependency ordering, and shared volumes" |
| **PostgreSQL** | "Relational database with foreign keys, unique constraints, and full-text capabilities" |

---

*Study the source code comments alongside this guide. Every file has been annotated with
interview-relevant explanations of WHAT it does and WHY.*

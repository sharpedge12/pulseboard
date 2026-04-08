# System Design Concepts in PulseBoard

> Every system design concept used in this project — explained with code examples
> and file references so you can discuss them confidently in an interview.

---

## Table of Contents

1. [API Gateway Pattern](#1-api-gateway-pattern)
2. [Microservice Architecture](#2-microservice-architecture)
3. [Pub/Sub Messaging (Event-Driven)](#3-pubsub-messaging-event-driven)
4. [Connection Pooling](#4-connection-pooling)
5. [Rate Limiting (Sliding Window)](#5-rate-limiting-sliding-window)
6. [Caching & Singleton Resources](#6-caching--singleton-resources)
7. [Session-Per-Request (Unit of Work)](#7-session-per-request-unit-of-work)
8. [Pagination Strategies](#8-pagination-strategies)
9. [Authentication & Token Architecture](#9-authentication--token-architecture)
10. [Reverse Proxy & Load Balancing](#10-reverse-proxy--load-balancing)
11. [WebSocket & Real-Time Communication](#11-websocket--real-time-communication)
12. [Database Design & Shared Schema](#12-database-design--shared-schema)
13. [Race Condition Handling (TOCTOU)](#13-race-condition-handling-toctou)
14. [Graceful Degradation & Fault Tolerance](#14-graceful-degradation--fault-tolerance)
15. [Health Checks & Dependency Ordering](#15-health-checks--dependency-ordering)
16. [Defense in Depth (Security Layers)](#16-defense-in-depth-security-layers)
17. [Horizontal Scaling Considerations](#17-horizontal-scaling-considerations)
18. [CAP Theorem & Trade-offs](#18-cap-theorem--trade-offs)
19. [Idempotency](#19-idempotency)
20. [Infrastructure as Code](#20-infrastructure-as-code)

---

## 1. API Gateway Pattern

**Concept:** A single entry point for all client requests. The gateway routes
each request to the correct backend service, hiding the internal service topology
from the frontend.

**Why it matters:**
- Frontend only knows one URL (`localhost:8000`) — doesn't know Core or Community exist
- Centralizes cross-cutting concerns (CORS, rate limiting, WebSocket management)
- Backend services can be moved, split, or scaled without changing the frontend

**File:** `services/gateway/app/main.py:79-115`

```python
# Route map — ordered list of (prefix, backend_url) tuples
ROUTE_MAP = [
    ("/api/v1/auth",          settings.core_service_url),       # -> Core :8001
    ("/api/v1/users",         settings.core_service_url),
    ("/api/v1/uploads",       settings.core_service_url),
    ("/api/v1/notifications", settings.core_service_url),
    ("/api/v1/categories",    settings.community_service_url),  # -> Community :8002
    ("/api/v1/threads",       settings.community_service_url),
    ("/api/v1/posts",         settings.community_service_url),
    ("/api/v1/search",        settings.community_service_url),
    ("/api/v1/admin",         settings.community_service_url),
    ("/api/v1/chat",          settings.community_service_url),
]

def _resolve_backend(path: str) -> str | None:
    """O(n) prefix match — first matching prefix wins."""
    for prefix, service_url in ROUTE_MAP:
        if path.startswith(prefix):
            return service_url
    return None
```

**The proxy function:**

```python
async def _proxy(request: Request, target_base: str) -> Response:
    """Forward the incoming request to the target backend service."""
    url = f"{target_base}{request.url.path}"
    if request.url.query:
        url += f"?{request.url.query}"
    headers = dict(request.headers)
    body = await request.body()
    async with httpx.AsyncClient() as client:
        resp = await client.request(request.method, url, headers=headers, content=body)
    return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))
```

**Interview talking point:** "Our gateway does prefix-based routing. In
production at scale, you'd use a trie data structure for O(k) matching where k
is the path length, or use an off-the-shelf gateway like Kong, Envoy, or AWS API
Gateway."

---

## 2. Microservice Architecture

**Concept:** Break a monolithic application into independently deployable services,
each owning a specific business domain.

**Our architecture:**

```
┌─────────────────────────────────────────────────────┐
│                 Browser (React SPA)                  │
└─────────────────┬───────────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────────┐
│            API Gateway (Port 8000)                    │
│  Reverse Proxy + WebSocket Hub + Redis Bridge        │
└──────┬──────────────────────────────┬───────────────┘
       │                              │
┌──────▼──────────┐     ┌────────────▼────────────────┐
│ Core (Port 8001)│     │ Community (Port 8002)        │
│ Auth, Users,    │     │ Threads, Posts, Votes,       │
│ Notifications,  │     │ Chat, Moderation, Tags,      │
│ File Uploads    │     │ Reports, Admin Dashboard     │
└──────┬──────────┘     └────────────┬────────────────┘
       │                              │
       └──────────┬───────────────────┘
                  │
       ┌──────────▼──────────┐
       │  Shared PostgreSQL  │
       │  + Redis + MailHog  │
       └─────────────────────┘
```

**Service boundaries (from 7 → 2+1 consolidation):**

| Service | Port | Responsibility |
|---------|------|---------------|
| **Gateway** | 8000 | Routing, CORS, WebSocket, rate limiting |
| **Core** | 8001 | Auth, users, profiles, friends, uploads, notifications |
| **Community** | 8002 | Categories, threads, posts, votes, reactions, tags, chat, moderation |

**Reference:** `docs/adr/0001-consolidate-microservices.md`, `docker-compose.yml`

**Interview talking point:** "We started with 7 services (Auth, User,
Notification, Forum, Moderation, Chat, Gateway) but consolidated to 3 because
the operational overhead — Docker images, network calls, deployment configs — was
too high for the team size. This matches Amazon's 'two-pizza team' guideline:
each microservice should be owned by one team."

---

## 3. Pub/Sub Messaging (Event-Driven)

**Concept:** Services publish events to a message broker without knowing who
will consume them. Subscribers react to events without knowing who produced them.
This decouples services.

**Our implementation:** Redis pub/sub as the event bus.

**Publisher (any service):**

**File:** `services/shared/shared/core/events.py:67-108`

```python
def publish_event(channel: str, payload: dict) -> None:
    """Fire-and-forget event publishing."""
    try:
        client = get_redis_client()
        client.publish(channel, json.dumps(jsonable_encoder(payload)))
    except RedisError:
        logger.warning("Redis publish skipped")   # Graceful degradation
```

**Subscriber (Gateway bridge):**

**File:** `services/gateway/app/main.py:135-150`

```python
# Subscribe to Redis patterns
pubsub.psubscribe("thread:*", "chat:room:*", "notifications:*", "global")

# Bridge: Redis message -> WebSocket broadcast
message = pubsub.get_message(timeout=1.0)
if message:
    channel = _redis_channel_to_ws_channel(message["channel"])
    await connection_manager.broadcast(channel, json.loads(message["data"]))
```

**The full event flow:**

```
User creates post
  → forum_services.create_post()
  → publish_event("thread:5", {type: "post_created", ...})
  → Redis PUBLISH "thread:5"
  → Gateway background task receives it
  → connection_manager.broadcast("thread:5", data)
  → All connected WebSocket clients get the update
  → React hook re-renders the UI
```

**Interview talking point:** "We use Redis pub/sub which is fire-and-forget —
messages aren't persisted. If you need guaranteed delivery (e.g., payment
processing), you'd use RabbitMQ or Kafka with acknowledgments and dead-letter
queues."

---

## 4. Connection Pooling

**Concept:** Maintain a pool of reusable connections instead of creating a new
connection for every request. Eliminates the overhead of TCP handshake, TLS
negotiation, and authentication on every query.

**Database connection pool:**

**File:** `services/shared/shared/core/database.py:70-94`

```python
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,    # <-- Test each connection before use
                           #     Detects stale connections from DB restarts
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
```

`pool_pre_ping=True` sends a lightweight `SELECT 1` before each connection use.
If the connection is dead (e.g., PostgreSQL restarted), SQLAlchemy transparently
replaces it from the pool. Without this, stale connections cause
`OperationalError` on the first query after a DB restart.

**Redis connection pool (implicit):**

**File:** `services/shared/shared/core/redis.py:48-80`

```python
_redis_client: redis.Redis | None = None

def get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        # redis.from_url() creates a client backed by a ConnectionPool
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client
```

The `redis-py` library internally uses a `ConnectionPool` (default 2^31
connections). The singleton pattern ensures all code paths share one pool.

**Interview talking point:** "Without connection pooling, a server handling 1000
requests/second would need 1000 simultaneous TCP connections. With pooling,
SQLAlchemy's default pool of 5 connections (with 10 overflow) handles this by
reusing connections. The `pool_pre_ping` option adds ~0.5ms latency per query
but prevents connection errors after DB restarts."

---

## 5. Rate Limiting (Sliding Window)

**Concept:** Restrict the number of requests a client can make within a time
window. Prevents brute-force attacks, DoS, and API abuse.

**Algorithm:** Sliding window counter — each request timestamp is recorded,
and timestamps older than the window are evicted.

**File:** `services/shared/shared/core/rate_limit.py:139-227`

```python
class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, rate_limit=20, window_seconds=60, paths=None):
        self._rate_limit = rate_limit
        self._window_seconds = window_seconds
        self._paths = paths or ["/api/v1/auth/"]    # Only rate-limit auth endpoints
        self._requests: dict[str, list[float]] = defaultdict(list)

    def _clean_and_count(self, client_ip: str) -> int:
        """Sliding window: evict old timestamps, return current count."""
        now = time.monotonic()
        cutoff = now - self._window_seconds
        # Keep only timestamps within the window
        self._requests[client_ip] = [
            ts for ts in self._requests[client_ip] if ts > cutoff
        ]
        return len(self._requests[client_ip])

    async def dispatch(self, request, call_next):
        if not self._should_limit(request.url.path):
            return await call_next(request)              # Skip non-auth endpoints

        client_ip = request.client.host
        count = self._clean_and_count(client_ip)

        if count >= self._rate_limit:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests."},
                headers={"Retry-After": str(self._window_seconds)},
            )

        self._requests[client_ip].append(time.monotonic())
        return await call_next(request)
```

**Where it's applied:**

```python
# File: services/gateway/app/main.py
app.add_middleware(RateLimitMiddleware, rate_limit=20, paths=["/api/v1/auth/"])

# File: services/core/app/main.py
app.add_middleware(RateLimitMiddleware, rate_limit=20, paths=["/api/v1/auth/"])
```

**Visualization:**

```
Window = 60 seconds, Limit = 20 requests

Timeline:
|--[req1]--[req2]--[req3]--..--[req20]--[req21]--|
                                          ↑
                                     HTTP 429!
                                     Retry-After: 60

After 60s, oldest timestamps expire and new requests are allowed.
```

**Limitation:** This is in-memory (per-process). In a multi-instance deployment,
each instance has its own counter. To fix this, use Redis-backed rate limiting
(e.g., `INCR` + `EXPIRE`).

**Interview talking point:** "We use `time.monotonic()` instead of
`time.time()` because monotonic clocks are immune to system clock changes
(NTP sync, manual adjustments). If someone sets the clock back, `time.time()`
would make old timestamps appear recent, breaking the eviction logic."

---

## 6. Caching & Singleton Resources

**Concept:** Avoid creating expensive resources repeatedly by caching them at
the module level or using `lru_cache`.

**Settings singleton (with `@lru_cache`):**

**File:** `services/shared/shared/core/config.py`

```python
@lru_cache          # Python's built-in memoization decorator
def get_settings() -> Settings:
    return Settings()           # Reads from .env — expensive I/O

settings = get_settings()       # Created once when module is first imported
```

**Redis client singleton (lazy initialization):**

**File:** `services/shared/shared/core/redis.py:48-80`

```python
_redis_client: redis.Redis | None = None    # Module-level, starts as None

def get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:                # Lazy: only created on first call
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client                     # All subsequent calls reuse it
```

**WebSocket manager singleton:**

**File:** `services/shared/shared/core/events.py:234-241`

```python
connection_manager = ConnectionManager()    # One instance for entire process
```

**Interview talking point:** "The singleton pattern is critical for resources
that represent shared state or expensive connections. Creating a Redis client
per request would exhaust file descriptors. The `@lru_cache` approach is
Pythonic and thread-safe for read-only values."

---

## 7. Session-Per-Request (Unit of Work)

**Concept:** Each HTTP request gets its own database session. All operations
within the request happen in a single transaction. Either all changes commit
or all roll back.

**File:** `services/shared/shared/core/database.py:107-142`

```python
def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that provides a scoped database session."""
    db: Session = SessionLocal()
    try:
        yield db       # Request handler uses this session
    finally:
        db.close()     # Always runs — even on exceptions
```

**How it's used in business logic:**

```python
# File: services/community/app/forum_services.py
def create_thread(db: Session, data: ThreadCreateRequest, author: User) -> dict:
    thread = Thread(title=data.title, body=data.body, author_id=author.id)
    db.add(thread)
    db.flush()         # Get thread.id without committing

    for tag_name in data.tag_names:
        tag = db.query(Tag).filter(Tag.name == tag_name).first()
        if not tag:
            tag = Tag(name=tag_name)
            db.add(tag)
            db.flush()
        db.add(ThreadTag(thread_id=thread.id, tag_id=tag.id))

    create_notification(db, mentioned_user.id, "mention", ...)
    audit.record(db, author.id, THREAD_CREATED, "thread", thread.id)

    db.commit()        # ALL changes committed atomically
    # If any step fails → entire transaction rolls back (no partial data)
```

**Key distinction:**
- `db.flush()` → sends SQL to DB, gets auto-generated IDs, but NOT permanent
- `db.commit()` → makes it permanent
- `db.close()` → returns connection to pool (via `finally` block)

**Interview talking point:** "This is the Unit of Work pattern from Martin
Fowler. It prevents partial updates — you never have a thread without its tags,
or a notification without its parent post. If you need to cross service
boundaries in a transaction, you'd need distributed transactions (2PC, Saga),
which we avoid by using a shared database."

---

## 8. Pagination Strategies

**Concept:** Return large datasets in manageable chunks instead of loading
everything into memory.

**Our implementation: Offset pagination**

**File:** `services/shared/shared/schemas/thread.py:221-244`

```python
class PaginatedThreadsResponse(BaseModel):
    items: list[ThreadListItemResponse]
    total: int               # Total matching rows
    page: int                # Current page number (1-indexed)
    page_size: int           # Items per page
    total_pages: int         # Computed: ceil(total / page_size)
```

**Backend query:**

```python
# File: services/community/app/forum_services.py
def list_threads(db, page=1, page_size=20, ...):
    query = select(Thread).where(...)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": items, "total": total, "page": page,
            "page_size": page_size, "total_pages": ceil(total / page_size)}
```

**Frontend — URL as state:**

```jsx
// File: frontend/src/pages/HomePage.jsx
const [searchParams, setSearchParams] = useSearchParams();
const page = parseInt(searchParams.get("page")) || 1;

const goToPage = (p) => {
    const params = new URLSearchParams(searchParams);
    if (p === 1) params.delete("page");    // Clean URLs
    else params.set("page", p);
    setSearchParams(params);
};
```

**Offset vs Cursor pagination:**

| Feature | Offset (ours) | Cursor |
|---------|---------------|--------|
| Jump to page N | Yes | No |
| "Page 3 of 12" UI | Yes | No |
| Stable during inserts | No (items shift) | Yes |
| Performance at page 10,000 | O(n) — slow | O(1) — fast |
| Implementation complexity | Simple | More complex |

**Interview talking point:** "We chose offset pagination because the UI needs
jump-to-page and page count display. For a real-time feed (like Twitter), you'd
use cursor-based pagination with `WHERE id > :last_seen_id ORDER BY id DESC
LIMIT :size` to avoid missing or duplicating items as new content is added."

---

## 9. Authentication & Token Architecture

**Concept:** Stateless authentication using JWT access tokens (short-lived) +
refresh tokens (long-lived, revocable).

**Token flow:**

```
1. User logs in → Server returns access_token (30 min) + refresh_token (7 days)
2. Every request sends access_token in Authorization header
3. When access_token expires → Client sends refresh_token to get new pair
4. On logout → refresh_token is revoked (deleted from DB)
```

**File:** `services/shared/shared/core/security.py`

```python
def create_token(subject, expires_delta, token_type="access", extra_claims=None):
    """JWT factory — builds tokens with standard claims."""
    payload = {
        "sub": subject,                                # User ID
        "exp": datetime.now(UTC) + expires_delta,      # Expiration
        "type": token_type,                            # "access" or "refresh"
        "iat": int(datetime.now(UTC).timestamp()),     # Issued at
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")

def create_access_token(subject: str) -> str:
    return create_token(subject, timedelta(minutes=30))

def create_refresh_token(subject: str, token_id: str) -> str:
    return create_token(subject, timedelta(days=7), token_type="refresh",
                        extra_claims={"token_id": token_id})
```

**Auth guard (runs on every authenticated request):**

**File:** `services/shared/shared/core/auth_helpers.py`

```python
def get_current_user(token=Depends(oauth2_scheme), db=Depends(get_db)) -> User:
    payload = safe_decode_token(token)
    if not payload or payload.get("type") != "access":     # Guard 1: valid JWT
        raise HTTPException(401)
    user = db.query(User).get(int(payload["sub"]))
    if not user or user.is_banned or not user.is_active:   # Guard 2: valid user
        raise HTTPException(401)
    user.last_seen = datetime.now(UTC)                     # Side effect: online status
    db.commit()
    return user
```

**Why short-lived access tokens + refresh tokens:**
- If an access token is stolen, it's only valid for 30 minutes
- Refresh tokens can be revoked server-side (stored in `refresh_tokens` table)
- The 30-minute window is a trade-off between security and UX (fewer re-logins)

**Interview talking point:** "JWTs are stateless — the server doesn't need to
look up a session store on every request. The trade-off is that you can't
instantly revoke an access token (you have to wait for expiry). We mitigate
this by checking `is_banned` and `is_active` on every request in
`get_current_user()`."

---

## 10. Reverse Proxy & Load Balancing

**Concept:** A reverse proxy sits between clients and backend servers. It
forwards client requests to the appropriate backend and returns responses.
Unlike a forward proxy (which hides the client), a reverse proxy hides the
backend topology.

**File:** `services/gateway/app/main.py`

```python
async def _proxy(request: Request, target_base: str) -> Response:
    """Full reverse proxy — forwards method, path, query, headers, body."""
    url = f"{target_base}{request.url.path}"
    if request.url.query:
        url += f"?{request.url.query}"
    headers = dict(request.headers)       # Copy ALL headers (including Auth)
    body = await request.body()
    async with httpx.AsyncClient() as client:
        resp = await client.request(request.method, url, headers=headers, content=body)
    return Response(content=resp.content, status_code=resp.status_code,
                    headers=dict(resp.headers))
```

**What our proxy handles:**
- All HTTP methods (GET, POST, PUT, PATCH, DELETE)
- Query strings (pagination, filters)
- Request bodies (JSON, multipart file uploads)
- Headers (Authorization, Content-Type, etc.)

**What a production proxy would also handle:**
- Load balancing (round-robin, least connections, weighted)
- SSL/TLS termination
- Request/response compression
- Circuit breaking (stop forwarding to a failing backend)
- Request retries

---

## 11. WebSocket & Real-Time Communication

**Concept:** WebSockets provide full-duplex communication over a single TCP
connection. Unlike HTTP (request-response), either side can send messages at
any time — enabling real-time updates without polling.

**Our WebSocket architecture:**

```
Browser (4 hooks)        Gateway (4 endpoints)         Redis (4 patterns)
─────────────────       ─────────────────────         ─────────────────
useThreadLiveUpdates → /ws/threads/{thread_id}  ← psubscribe("thread:*")
useChatRoom          → /ws/chat/{room_id}       ← psubscribe("chat:room:*")
useNotifications     → /ws/notifications        ← psubscribe("notifications:*")
useGlobalUpdates     → /ws/global               ← psubscribe("global")
```

**Connection management:**

**File:** `services/shared/shared/core/events.py:134-218`

```python
class ConnectionManager:
    def __init__(self):
        self.connections: dict[str, list[WebSocket]] = defaultdict(list)

    async def connect(self, channel: str, websocket: WebSocket):
        await websocket.accept()                           # HTTP 101 Upgrade
        self.connections[channel].append(websocket)        # Register

    async def broadcast(self, channel: str, message: dict):
        dead = []
        for ws in list(self.connections[channel]):         # Snapshot copy
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)                            # Collect dead sockets
        for ws in dead:
            self.disconnect(channel, ws)                   # Cleanup after loop
```

**Why snapshot copy (`list(...)`):** Iterating over a list while removing items
causes index errors. The snapshot ensures stable iteration while dead connections
are cleaned up afterward.

**Interview talking point:** "We use WebSockets for low-latency updates (new
posts appear instantly, chat messages arrive in real-time). The alternative is
long polling (HTTP connection held open) or Server-Sent Events (SSE, one-way).
WebSockets are bidirectional, which is essential for chat where the client both
sends and receives messages."

---

## 12. Database Design & Shared Schema

**Concept:** All 3 services share a single PostgreSQL database with 24 tables.
This is a deliberate architectural trade-off.

**24 tables across 2 services:**

| Service | Tables |
|---------|--------|
| **Core** | `users`, `refresh_tokens`, `email_verification_tokens`, `password_reset_tokens`, `oauth_accounts`, `friend_requests`, `notifications`, `attachments` |
| **Community** | `categories`, `threads`, `thread_subscriptions`, `posts`, `tags`, `thread_tags`, `votes`, `reactions`, `content_reports`, `moderation_actions`, `category_moderators`, `category_requests`, `chat_rooms`, `chat_room_members`, `messages` |
| **Shared** | `audit_logs` |

**Polymorphic FK pattern (4 tables):**

```python
class Vote(Base):
    entity_type = Column(String(20))    # "thread" or "post"
    entity_id = Column(Integer)         # Thread.id or Post.id
    # No FK constraint — referential integrity enforced in application layer
```

Used by: `votes`, `reactions`, `content_reports`, `attachments`.

**Trade-offs of shared database:**

| Advantage | Disadvantage |
|-----------|-------------|
| ACID transactions across services | Tight coupling at data layer |
| Simple JOINs (user data + thread data) | Can't independently scale DB per service |
| No eventual consistency issues | Schema changes affect all services |
| No data sync infrastructure needed | No technology flexibility (all use PostgreSQL) |

**Interview talking point:** "We use shared database because our 2 backend
services are tightly coupled (Community needs user data for every thread). The
alternative — database-per-service with API calls — would add network latency to
every query that needs user info. For a forum with 3 services and a small team,
the operational cost of distributed data isn't justified."

---

## 13. Race Condition Handling (TOCTOU)

**Concept:** TOCTOU (Time-of-Check to Time-of-Use) — a race condition where
the state changes between checking a condition and acting on it.

**Our problem:** Both Core and Community call `init_db()` → `create_all()` at
startup. In Docker, they start concurrently and both try to create the same
tables simultaneously.

**File:** `services/shared/shared/core/database.py:214-292`

```python
def init_db() -> None:
    """Initialize database schema with retry logic for concurrent startup."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            Base.metadata.create_all(bind=engine)    # Check-and-create tables
            break
        except ProgrammingError as exc:
            if "DuplicateTable" in str(exc) or "already exists" in str(exc):
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)         # Exponential backoff: 1s, 2s, 4s
                    continue
            raise
    _run_migrations()   # ADD COLUMN IF NOT EXISTS — inherently idempotent
```

**The race:**

```
Time  Core Service        Community Service
────  ─────────────       ──────────────────
t=0   CHECK: "users" table exists? No    CHECK: "users" table exists? No
t=1   CREATE TABLE users  ─┐
t=2                        │  CREATE TABLE users → ERROR: DuplicateTable!
t=3                        │  Retry after 1s → tables now exist → success
```

**Solution components:**
1. **Retry with exponential backoff** — wait 1s, 2s, 4s before retrying
2. **Idempotent migrations** — `ADD COLUMN IF NOT EXISTS` is safe to run multiple times
3. **Docker dependency ordering** — `depends_on: core: condition: service_started`

**Interview talking point:** "TOCTOU is a classic concurrency bug. The fix is
to not prevent the race but to handle it gracefully. `IF NOT EXISTS` in SQL
is the database equivalent of optimistic concurrency control — try the
operation, handle the conflict if it happens."

---

## 14. Graceful Degradation & Fault Tolerance

**Concept:** The system continues operating (at reduced functionality) when a
component fails, rather than crashing entirely.

**Redis failure — events silently skipped:**

**File:** `services/shared/shared/core/events.py:67-108`

```python
def publish_event(channel: str, payload: dict) -> None:
    try:
        client = get_redis_client()
        client.publish(channel, json.dumps(jsonable_encoder(payload)))
    except RedisError:
        logger.warning("Redis publish skipped")    # Log and continue
        # The post/thread/message is still created — only real-time
        # updates are temporarily unavailable
```

**Impact of Redis outage:**
- Thread creation, post creation, chat messages → still work (DB operations succeed)
- Real-time updates → temporarily unavailable (users need to refresh)
- Rate limiting → still works (in-memory, no Redis dependency)

**Bot retry with backoff:**

**File:** `services/shared/shared/services/bot.py`

```python
for attempt in range(3):
    try:
        response = httpx.post(GROQ_API_URL, json=payload)
        if response.status_code == 429:          # Rate limited
            time.sleep(2 ** (attempt + 1))       # 2s, 4s, 8s
            continue
        return response.json()["choices"][0]["message"]["content"]
    except Exception:
        if attempt == 2:
            return "I'm having trouble connecting right now."
```

**Interview talking point:** "Graceful degradation means defining what
'partially working' looks like. Our Redis failure mode is: everything works
except real-time updates. Users see their content after a page refresh. This is
much better than the entire app crashing because Redis is down."

---

## 15. Health Checks & Dependency Ordering

**Concept:** Containers declare health check commands that Docker uses to
determine if the service is ready to accept connections. Other services can
depend on this health status before starting.

**File:** `docker-compose.yml`

```yaml
db:
  image: postgres:16-alpine
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
    interval: 5s
    timeout: 5s
    retries: 5

redis:
  image: redis:7-alpine
  healthcheck:
    test: ["CMD", "redis-cli", "ping"]
    interval: 5s
    timeout: 5s
    retries: 5

core:
  depends_on:
    db:
      condition: service_healthy       # Wait for PostgreSQL to accept connections
    redis:
      condition: service_healthy       # Wait for Redis to accept connections
    mailhog:
      condition: service_started       # Just wait for container to start

community:
  depends_on:
    core:
      condition: service_started       # Wait for Core to start (table creation)
    db:
      condition: service_healthy
    redis:
      condition: service_healthy
```

**Startup order:**

```
1. db (PostgreSQL)     → healthy when pg_isready succeeds
2. redis               → healthy when redis-cli ping returns PONG
3. mailhog             → started (no health check needed)
4. core                → starts after db + redis are healthy
5. community           → starts after core + db + redis
6. gateway             → starts after core + community
7. frontend            → starts after gateway
```

**Interview talking point:** "`service_healthy` waits for the health check
to pass (e.g., PostgreSQL actually accepting connections). `service_started`
only waits for the container to start (the process is running, but may not be
ready yet). We use `service_started` for Core→Community because Core's
health depends on DB being ready, and Community just needs tables to exist."

---

## 16. Defense in Depth (Security Layers)

**Concept:** Multiple independent security layers so that if one layer fails,
others still protect the system. No single point of failure in security.

**Our 6 security layers:**

```
Layer 1: Frontend validation      — Immediate user feedback
Layer 2: Pydantic schemas         — Type checking, length limits, regex patterns
Layer 3: XSS sanitization         — Strip dangerous HTML/JS constructs
Layer 4: Business logic checks    — Permission checks, ownership verification
Layer 5: Database constraints     — NOT NULL, UNIQUE, FK constraints
Layer 6: Security headers/CSP     — Browser-enforced protections
```

**See `docs/validation-security.md` for detailed code examples of each layer.**

**Additional security measures:**

| Measure | Implementation | File |
|---------|---------------|------|
| Rate limiting | Sliding-window per IP | `rate_limit.py` |
| CORS | Restrict to frontend origin | All 3 `main.py` files |
| CSP | Restrict script/style/img sources | `security_headers.py` |
| JWT short expiry | 30 min access, 7 day refresh | `security.py` |
| Password hashing | pbkdf2_sha256 (150K iterations) | `security.py` |
| File upload validation | 5-layer security (magic bytes, extension, MIME) | `storage.py` |

**Interview talking point:** "Defense in depth means assuming every layer can
be bypassed. Even though React escapes output (preventing most XSS), we still
sanitize input on the backend, set CSP headers, and validate on the frontend.
If a future developer bypasses React and renders raw HTML, the backend
sanitization and CSP still protect users."

---

## 17. Horizontal Scaling Considerations

**Concept:** Adding more server instances to handle more load, rather than
upgrading a single server (vertical scaling).

**What would need to change for horizontal scaling:**

| Component | Current | Scaled |
|-----------|---------|--------|
| **Load balancer** | None (single gateway) | Nginx/HAProxy in front of multiple gateway instances |
| **Rate limiting** | In-memory per process | Redis-backed (`INCR` + `EXPIRE`) for shared state |
| **WebSockets** | Single gateway manages all connections | Redis adapter (socket.io pattern) or sticky sessions |
| **File uploads** | Local filesystem | S3/GCS with CDN (CloudFront) |
| **Database** | Single PostgreSQL | Read replicas for search/listing, PgBouncer for connection pooling |
| **Session/cache** | In-memory (singletons) | Redis for shared session state |

**What already works at scale:**
- Redis pub/sub — multiple publisher instances can publish to same channels
- Stateless JWT — any gateway instance can validate tokens (no session store)
- Database — SQLAlchemy connection pooling handles concurrent access
- Docker Compose → Kubernetes with replica sets

**Interview talking point:** "The hardest part of scaling this system is
WebSocket state. Each gateway instance holds its own `ConnectionManager`. If
user A connects to gateway-1 and user B to gateway-2, they can't see each other's
real-time events. The fix is to have all gateways subscribe to Redis pub/sub
(which we already do), so Redis acts as the shared event bus across instances."

---

## 18. CAP Theorem & Trade-offs

**Concept:** In a distributed system, you can only guarantee 2 of 3 properties:
- **Consistency** — Every read returns the most recent write
- **Availability** — Every request gets a response
- **Partition tolerance** — System works despite network failures between nodes

**Our choice: CP (Consistency + Partition tolerance)**

- **Consistency:** Single PostgreSQL database = strong consistency. All services
  read the same data. No eventual consistency issues.
- **Partition tolerance:** If Core and Community can't reach PostgreSQL, they
  return 500 errors (not stale data).
- **Availability sacrifice:** If PostgreSQL goes down, the entire backend is
  unavailable.

**Where we trade consistency for availability:**
- Redis pub/sub is fire-and-forget — real-time events may be lost during Redis
  outages, but core functionality continues.
- Bot replies are async (background thread) — they may arrive late or fail
  without affecting the user's post.

**Interview talking point:** "A forum needs strong consistency — if you upvote
a post, the next page load must show your vote. Eventually consistent databases
(like Cassandra or DynamoDB) might show stale vote counts. We accept reduced
availability (500 errors during DB outage) in exchange for guaranteed consistency."

---

## 19. Idempotency

**Concept:** An operation is idempotent if calling it multiple times produces
the same result as calling it once. Critical for retry logic and distributed
systems.

**Examples in our codebase:**

1. **Database migrations:**
   ```sql
   ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen TIMESTAMP;
   -- Safe to run 100 times — only adds the column once
   ```

2. **Seed script:**
   ```python
   # File: services/seed.py
   existing_admin = db.query(User).filter(User.email == "admin@pulseboard.app").first()
   if existing_admin:
       print("Seed data already exists. Skipping.")
       return
   ```

3. **Vote toggle (upsert pattern):**
   ```python
   # File: services/community/app/forum_services.py
   existing_vote = db.query(Vote).filter(Vote.user_id == user.id, ...).first()
   if existing_vote and existing_vote.value == value:
       db.delete(existing_vote)     # Same vote again → toggle off
   elif existing_vote:
       existing_vote.value = value  # Different vote → flip
   else:
       db.add(Vote(...))            # New vote → create
   ```

4. **`create_all()` with retry:**
   ```python
   Base.metadata.create_all(bind=engine)    # CREATE TABLE IF NOT EXISTS
   # PostgreSQL's IF NOT EXISTS makes this safe to call concurrently
   ```

**Interview talking point:** "Idempotency is essential for reliability. If a
network timeout causes a retry, an idempotent operation won't create duplicate
data. Our seed script checks for existing data before inserting, our migrations
use `IF NOT EXISTS`, and our vote endpoint uses upsert semantics."

---

## 20. Infrastructure as Code

**Concept:** Define your entire infrastructure in version-controlled
configuration files, so environments are reproducible and changes are tracked.

**File:** `docker-compose.yml`

```yaml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/var/lib/redis/data

  gateway:
    build: ./services/gateway
    ports: ["8000:8000"]
    depends_on:
      core: { condition: service_started }
      community: { condition: service_started }

  core:
    build: ./services/core
    depends_on:
      db: { condition: service_healthy }
      redis: { condition: service_healthy }

  # ... etc
```

**What Docker Compose gives us:**
- **Reproducibility:** `docker compose up --build` creates identical environment every time
- **Isolation:** Each service runs in its own container with its own filesystem
- **Networking:** Containers communicate by service name (DNS resolution)
- **Volume management:** Named volumes persist data across container restarts
- **Dependency ordering:** `depends_on` + `condition` ensures correct startup order

**Interview talking point:** "Docker Compose is our development infrastructure-as-code.
In production, you'd use Kubernetes for orchestration (auto-scaling, rolling
updates, self-healing). The Dockerfiles we wrote are directly reusable — you'd
just replace the Compose file with Kubernetes manifests (Deployments, Services,
Ingress)."

---

*Each concept above is implemented with real code in the PulseBoard codebase.
Open the referenced files and look for the inline comments that explain the
system design rationale.*

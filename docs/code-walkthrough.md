# PulseBoard Code Walkthrough

> A complete guide to the codebase for interview preparation. Read this to understand
> how every piece fits together before diving into individual files.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture at a Glance](#2-architecture-at-a-glance)
3. [How a Request Flows Through the System](#3-how-a-request-flows-through-the-system)
4. [Shared Library (`services/shared/`)](#4-shared-library)
5. [Core Service (`services/core/`)](#5-core-service)
6. [Community Service (`services/community/`)](#6-community-service)
7. [Gateway Service (`services/gateway/`)](#7-gateway-service)
8. [Frontend (`frontend/src/`)](#8-frontend)
9. [Real-Time Architecture](#9-real-time-architecture)
10. [Authentication Deep Dive](#10-authentication-deep-dive)
11. [Database Design](#11-database-design)
12. [Testing Strategy](#12-testing-strategy)
13. [Security Layers](#13-security-layers)
14. [File-by-File Reference](#14-file-by-file-reference)

---

## 1. Project Overview

PulseBoard is a **Reddit-style discussion forum** built as a **microservice architecture**:

- **2 backend services** (Core + Community) behind an **API Gateway**
- **React SPA** frontend with real-time WebSocket updates
- **PostgreSQL** database (shared by all services)
- **Redis** for pub/sub real-time events
- **Docker Compose** for orchestration

### What makes this project interesting for interviews:

| Topic | Where to find it |
|-------|-----------------|
| Microservice architecture | Gateway reverse proxy, service discovery |
| JWT authentication + OAuth2 | `shared/core/security.py`, `core/auth_oauth.py` |
| Real-time WebSocket | Gateway Redis bridge, frontend hooks |
| Role-based access control | `shared/core/auth_helpers.py`, admin services |
| Database design (24 tables) | `shared/models/` |
| Input validation & XSS prevention | `shared/services/sanitize.py`, Pydantic schemas |
| File upload security | Magic byte validation in `shared/services/storage.py` |
| Pagination | Backend `PaginatedThreadsResponse`, frontend `Pagination` component |
| AI integration | `@pulse` bot with Groq API in `shared/services/bot.py` |
| Nested comment trees | Self-referential `Post.parent_post_id`, `_build_post_tree()` |

---

## 2. Architecture at a Glance

```
                     Browser (React SPA)
                           |
                    :5173 (Vite dev)
                           |
                   +-------+-------+
                   |   Gateway     |  :8000
                   |  (reverse     |
                   |   proxy +     |
                   |   WebSocket   |
                   |   hub)        |
                   +---+-------+---+
                       |       |
              +--------+       +--------+
              |                         |
        +-----+-----+           +------+------+
        | Core      |           | Community   |
        | :8001     |           | :8002       |
        |           |           |             |
        | Auth      |           | Forum       |
        | Users     |           | Moderation  |
        | Uploads   |           | Chat        |
        | Notifs    |           | Admin       |
        +-----------+           +-------------+
              |                         |
              +--------+--------+-------+
                       |        |
                  PostgreSQL  Redis
                    :5432     :6379
```

### Gateway Route Map

| Frontend calls... | Gateway forwards to... |
|-------------------|----------------------|
| `/api/v1/auth/*` | Core `:8001` |
| `/api/v1/users/*` | Core `:8001` |
| `/api/v1/uploads/*` | Core `:8001` |
| `/api/v1/notifications/*` | Core `:8001` |
| `/api/v1/categories/*` | Community `:8002` |
| `/api/v1/threads/*` | Community `:8002` |
| `/api/v1/posts/*` | Community `:8002` |
| `/api/v1/search/*` | Community `:8002` |
| `/api/v1/admin/*` | Community `:8002` |
| `/api/v1/chat/*` | Community `:8002` |
| `/uploads/*` (static) | Core `:8001` |

---

## 3. How a Request Flows Through the System

### Example: User creates a new thread

```
1. User fills out form in HomePage.jsx
2. Frontend calls POST /api/v1/threads (via apiRequest in api.js)
3. Request hits Gateway (:8000)
4. Gateway adds Authorization header, proxies to Community (:8002)
5. Community's forum_routes.py receives the request
6. FastAPI dependency chain runs:
   a. oauth2_scheme extracts Bearer token from header
   b. get_current_user decodes JWT, looks up user in DB
   c. require_can_participate checks user is verified + not suspended
7. forum_services.create_thread() runs:
   a. Creates Thread row in DB
   b. Assigns tags (get-or-create pattern)
   c. Links draft attachments to the thread
   d. Detects @mentions in title/body
   e. Creates notifications for mentioned users
   f. Triggers @pulse bot if mentioned
   g. Records audit log entry
   h. Publishes Redis event for real-time updates
8. Response flows back: Community -> Gateway -> Frontend
9. Redis event flows: Community -> Redis -> Gateway -> WebSocket -> All connected browsers
```

### Example: User logs in

```
1. LoginPage.jsx submits email + password
2. POST /api/v1/auth/login -> Gateway -> Core
3. auth_services.authenticate_user():
   a. Finds user by email
   b. Verifies password hash (pbkdf2_sha256)
   c. Checks is_verified, is_banned, is_active
   d. Creates access token (JWT, 30 min)
   e. Creates refresh token (JWT, 7 days) + DB row
   f. Records audit log
4. Frontend stores tokens in localStorage
5. AuthContext.jsx fetches profile via /api/v1/users/me
6. All subsequent requests include Authorization: Bearer <token>
```

---

## 4. Shared Library

**Location:** `services/shared/shared/`

This is a pip-installable Python package (`pip install -e services/shared`) imported by all 3 backend services. It contains everything shared: models, schemas, config, security, utilities.

### 4.1 Core (`shared/core/`)

| File | What it does | Key concept |
|------|-------------|-------------|
| `config.py` | Loads environment variables via pydantic-settings | Typed configuration, `@lru_cache` singleton |
| `database.py` | SQLAlchemy engine, session factory, `init_db()` | Dependency injection with `get_db()`, retry for race conditions |
| `security.py` | Password hashing (passlib) + JWT tokens (python-jose) | `pbkdf2_sha256`, HS256 symmetric signing |
| `auth_helpers.py` | FastAPI auth dependencies | `get_current_user`, RBAC, `require_can_participate` |
| `redis.py` | Redis client singleton | Lazy initialization, connection reuse |
| `events.py` | Redis pub/sub + WebSocket manager | `publish_event()`, `ConnectionManager` with dead cleanup |
| `logging.py` | Structured logging setup | `dictConfig`, called once per service at startup |
| `rate_limit.py` | Per-IP sliding window rate limiter | In-memory counter, applied to auth endpoints |
| `security_headers.py` | Security HTTP headers middleware | CSP, X-Frame-Options, X-Content-Type-Options |

### 4.2 Models (`shared/models/`)

24 database tables across 13 files. Key relationships:

```
User --< Thread --< Post (self-referential: parent_post_id)
  |        |          |
  |        +--< Vote  +--< Vote
  |        +--< Reaction  +--< Reaction
  |        +--< ThreadSubscription
  |        +--< ThreadTag >-- Tag
  |
  +--< FriendRequest
  +--< Notification
  +--< ChatRoomMember >-- ChatRoom --< Message
  +--< OAuthAccount
  +--< AuditLog
  +--< ContentReport
  +--< Attachment

Category --< Thread
CategoryModerator (junction: User <-> Category)
ModerationAction (references User as target)
CategoryRequest (pending category creation by mods)
```

### 4.3 Schemas (`shared/schemas/`)

Pydantic models for API validation. Key patterns:
- **`field_validator`** with `sanitize_text()` on every user-input field (XSS prevention)
- **`pattern=`** on enums like role, action_type (prevents injection)
- **List bounds** (`max_length=20` on attachment_ids, `max_length=50` on member_ids)
- **Different response shapes**: `UserMeResponse` (private) vs `UserPublicProfileResponse` (public)

### 4.4 Services (`shared/services/`)

| File | Purpose | Used by |
|------|---------|---------|
| `sanitize.py` | Strip XSS from user input (NOT `html.escape` — React handles that) | All schemas |
| `audit.py` | Create audit log entries with role-based visibility | Core + Community |
| `bot.py` | @pulse AI bot (Groq API + web search) | Community |
| `notifications.py` | Create notification rows | Core + Community |
| `mentions.py` | Extract @username from text, create mention notifications | Community |
| `email.py` | Send moderation emails via SMTP | Community (admin) |
| `moderation.py` | Get moderator's scoped category IDs | Community (admin) |
| `attachments.py` | Link draft uploads to entities | Core + Community |
| `storage.py` | File upload: MIME check, magic bytes, sanitize filename | Core (uploads) |

---

## 5. Core Service

**Location:** `services/core/app/`
**Port:** 8001
**Responsibility:** Auth, users, uploads, notifications

### Files and their roles:

| File | Endpoints | What it handles |
|------|----------|----------------|
| `main.py` | — | FastAPI app setup, lifespan, middleware |
| `auth_routes.py` | `POST /auth/register`, `/login`, `/refresh`, `/verify-email`, `/forgot-password`, `/reset-password`, `/oauth/{provider}/login`, `/oauth/{provider}/callback` | All authentication flows |
| `auth_services.py` | — | Business logic: hash passwords, verify credentials, create/rotate tokens |
| `auth_oauth.py` | — | Google + GitHub OAuth2 Authorization Code flow |
| `auth_email.py` | — | SMTP email sending (verification, password reset) |
| `user_routes.py` | `GET/PUT /users/me`, `POST /users/me/avatar`, `GET /users/search`, `POST /users/{id}/friend`, `POST /users/{id}/report` | User profiles, friends, search |
| `user_services.py` | — | Avatar upload, friend requests, user serialization |
| `notification_routes.py` | `GET /notifications`, `PATCH /notifications/{id}/read`, `POST /notifications/read-all` | Notification management |
| `notification_services.py` | — | Query/update notification rows |
| `upload_routes.py` | `POST /uploads`, `GET /uploads/limits` | File upload with validation |

---

## 6. Community Service

**Location:** `services/community/app/`
**Port:** 8002
**Responsibility:** Forum, moderation, chat

### Files and their roles:

| File | Endpoints | What it handles |
|------|----------|----------------|
| `main.py` | — | App setup, seeds default categories on startup |
| `forum_routes.py` | CRUD for `/categories`, `/threads`, `/posts`, `/search` | All forum endpoints |
| `forum_services.py` | — | Thread/post creation, pagination, nested post tree building, bot trigger |
| `forum_votes.py` | — | Vote upsert (+1/-1), reaction toggle, report creation |
| `forum_search.py` | — | SQL ILIKE search across threads and posts |
| `forum_seed.py` | — | Seeds 4 default categories on startup |
| `admin_routes.py` | `/admin/*` | Dashboard, user/thread management, reports, audit logs |
| `admin_services.py` | — | Role changes, suspend/ban, lock/pin, report resolution |
| `chat_routes.py` | `/chat/*` | Room listing, creation, messaging |
| `chat_services.py` | — | Room management, DM dedup, message creation with bot trigger |

### Key algorithm: Building nested post trees

```python
# forum_services.py -> _build_post_tree()
# O(n) two-pass algorithm:

# Pass 1: Create a dict mapping post_id -> serialized post
lookup = {post.id: serialize(post) for post in posts}

# Pass 2: Attach each post to its parent
for post in posts:
    if post.parent_post_id and post.parent_post_id in lookup:
        lookup[post.parent_post_id]["replies"].append(lookup[post.id])

# Return only top-level posts (those without a parent)
return [p for p in lookup.values() if p["parent_post_id"] is None]
```

---

## 7. Gateway Service

**Location:** `services/gateway/app/main.py` (single file)
**Port:** 8000

The Gateway is the **single entry point** for the frontend. It does:

1. **Reverse proxy** — forwards HTTP requests to Core or Community based on path
2. **WebSocket hub** — manages 4 WebSocket channels:
   - `thread:{id}` — live post/vote/reaction updates
   - `chat:{roomId}` — real-time chat messages
   - `notifications:{userId}` — personal notifications
   - `global` — app-wide events (new categories, etc.)
3. **Redis pub/sub bridge** — subscribes to Redis patterns, broadcasts to WebSocket clients
4. **Upload proxy** — forwards `/uploads/*` to Core for static file serving
5. **Rate limiting** — 20 req/min on auth endpoints

---

## 8. Frontend

**Location:** `frontend/src/`
**Tech:** React 18 + Vite 6 + React Router v6 + plain CSS

### Architecture:

```
main.jsx
  └─ App.jsx (routes)
       └─ MainLayout.jsx (navbar + Outlet)
            ├─ HomePage.jsx (feed + create thread)
            ├─ ThreadPage.jsx (detail + comments)
            ├─ ChatPage.jsx (rooms + messages)
            ├─ ProfilePage.jsx (own/other profile)
            ├─ AdminPage.jsx (7-tab dashboard)
            ├─ DashboardPage.jsx (member stats)
            ├─ PeoplePage.jsx (user search)
            └─ LoginPage.jsx (auth forms)
```

### State management:

- **AuthContext** — session tokens, profile, login/logout
- **ThemeContext** — dark/light theme toggle
- No Redux/Zustand — uses React Context + local state

### Key components:

| Component | Pattern it demonstrates |
|-----------|----------------------|
| `ThreadCard.jsx` | Card layout, event propagation, auth-gated actions |
| `MentionTextarea.jsx` | Debounced search, keyboard navigation, cursor management |
| `Pagination.jsx` | Page number algorithm with ellipsis |
| `ProtectedRoute.jsx` | Route guard HOC, redirect with location state |
| `NotificationCenter.jsx` | Dropdown with data grouping, real-time updates |
| `UserActionModal.jsx` | Modal pattern, conditional action rendering |

### Custom hooks:

| Hook | What it connects to |
|------|-------------------|
| `useNotifications.js` | WebSocket `/ws/notifications` + REST polling |
| `useThreadLiveUpdates.js` | WebSocket `/ws/thread/{id}` |
| `useChatRoom.js` | WebSocket `/ws/chat/{roomId}` + REST messages |
| `useGlobalUpdates.js` | WebSocket `/ws/global` |
| `useLocalStorage.js` | localStorage with JSON serialization |

---

## 9. Real-Time Architecture

```
1. User creates a post in Community service
2. forum_services.create_post() calls publish_event("thread:{id}", {...})
3. publish_event() -> Redis PUBLISH on channel "thread:{id}"
4. Gateway's Redis bridge (background task):
   a. pubsub.get_message() receives the event
   b. Maps Redis channel "thread:{id}" to WebSocket channel "thread:{id}"
   c. connection_manager.broadcast("thread:{id}", payload)
5. All browsers viewing that thread receive the WebSocket message
6. useThreadLiveUpdates hook fires callback -> React updates state
```

### Channel patterns:

| Redis channel | WebSocket channel | Event types |
|--------------|-------------------|-------------|
| `thread:{id}` | `thread:{id}` | post_created, vote_updated, reaction_updated |
| `chat:room:{id}` | `chat:{id}` | message_created |
| `notifications:{userId}` | `notifications:{userId}` | new_notification |
| `global` | `global` | category_created, thread_pinned |

---

## 10. Authentication Deep Dive

### Token lifecycle:

```
Register -> Verification email -> Verify email -> Login
                                                    |
                                          Access token (30 min)
                                          Refresh token (7 days, in DB)
                                                    |
                                          Token expires -> /refresh
                                                    |
                                          New access + refresh tokens
                                          Old refresh token deleted
```

### OAuth2 flow (Google/GitHub):

```
1. Frontend redirects to /api/v1/auth/oauth/google/login
2. Gateway proxies to Core
3. Core generates state nonce (CSRF), stores in memory, redirects to Google
4. User logs in at Google, grants consent
5. Google redirects to /api/v1/auth/oauth/google/callback?code=...&state=...
6. Core validates state nonce, exchanges code for Google access token
7. Core fetches user info from Google API
8. Core creates or links user account
9. Core redirects to frontend with access_token + refresh_token as URL params
```

### Key security decisions:

- **HS256** (symmetric) not RS256 — acceptable because all services share the same secret
- **`pbkdf2_sha256`** not bcrypt — avoids crypt module deprecation, NIST recommended
- **last_seen updated on every request** — pragmatic online status without WebSocket heartbeats
- **Generic error messages** — "Could not validate credentials" prevents user enumeration

---

## 11. Database Design

### 24 tables, key design decisions:

1. **No Alembic** — uses `create_all()` + raw `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
2. **Shared database** — all services access the same PostgreSQL instance
3. **Self-referential posts** — `Post.parent_post_id -> Post.id` for nested comments
4. **Junction tables** — `ThreadTag`, `ChatRoomMember`, `CategoryModerator`, `ThreadSubscription`
5. **Polymorphic entities** — `Vote.entity_type` can be "thread" or "post"
6. **Soft status flags** — `User.is_banned`, `is_suspended`, `is_active` instead of deletion
7. **TimestampMixin** — `created_at` + `updated_at` with `server_default=func.now()`

### SQLite for tests:

The test suite uses SQLite (file `test_services.db`) instead of PostgreSQL. This means:
- No Docker required for tests
- `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` is skipped (SQLite limitation)
- Tests are faster but don't catch PostgreSQL-specific issues

---

## 12. Testing Strategy

**31 tests total** across 4 files:

| File | Tests | What it covers |
|------|-------|---------------|
| `test_auth.py` | 5 | Register, verify email, login, refresh tokens |
| `test_forum.py` | 7 | Categories, threads, posts, search, default categories |
| `test_audit.py` | 10 | Audit log creation, role-based visibility, filtering, pagination |
| `test_validation.py` | 9 | XSS sanitization, file upload validation, magic bytes |

### Test architecture:

`conftest.py` creates a **composite app** that mounts ALL service routers into a single FastAPI process with a SQLite database. This avoids needing Docker or inter-service HTTP calls during testing.

```python
# Simplified conftest.py pattern:
app = FastAPI()
app.include_router(auth_router, prefix="/api/v1/auth")
app.include_router(user_router, prefix="/api/v1/users")
app.include_router(thread_router, prefix="/api/v1/threads")
# ... all routers in one process

client = TestClient(app)  # Synchronous HTTP client for testing
```

### Key test patterns:

- **`register_verified_user()` helper** — creates a user, marks as verified, returns auth token
- **Autouse fixtures** — patch `_send_verification_email` and `_send_moderation_email` to no-ops
- **SMTP timeout=2** — prevents test hangs if email sending is accidentally called

---

## 13. Security Layers

### Input validation (defense in depth):

```
Layer 1: Frontend validation (uploadUtils.js, form validation)
Layer 2: Pydantic schema validation (type checking, field constraints)
Layer 3: field_validator sanitization (sanitize_text strips XSS)
Layer 4: Business logic checks (ownership, permissions)
Layer 5: Database constraints (unique, foreign key, check)
```

### File upload security (5 layers):

```
Layer 1: MIME type whitelist (ALLOWED_CONTENT_TYPES)
Layer 2: File extension whitelist (ALLOWED_EXTENSIONS)
Layer 3: Extension-MIME consistency check (_EXTENSION_MIME_MAP)
Layer 4: Magic byte verification (first 32 bytes match known signatures)
Layer 5: Filename sanitization (strip paths, replace unsafe chars)
```

### HTTP security headers:

| Header | Attack it prevents |
|--------|--------------------|
| `X-Content-Type-Options: nosniff` | MIME type sniffing |
| `X-Frame-Options: DENY` | Clickjacking |
| `X-XSS-Protection: 1; mode=block` | Reflected XSS (legacy browsers) |
| `Content-Security-Policy` | Script injection, data exfiltration |
| `Referrer-Policy: strict-origin-when-cross-origin` | Token leakage in URLs |
| `Cache-Control: no-store` (authenticated) | Sensitive data caching |

---

## 14. File-by-File Reference

### Backend (78 Python files)

#### Shared Library (`services/shared/shared/`)

**Core infrastructure:**
- `core/config.py` — Centralized settings from environment variables
- `core/database.py` — SQLAlchemy engine, session factory, init_db with retry
- `core/security.py` — Password hashing + JWT token creation/decoding
- `core/auth_helpers.py` — FastAPI auth dependencies (get_current_user, require_roles)
- `core/redis.py` — Singleton Redis client
- `core/events.py` — Redis pub/sub + WebSocket connection manager
- `core/logging.py` — Structured logging configuration
- `core/rate_limit.py` — Sliding window rate limiter middleware
- `core/security_headers.py` — Security headers middleware

**ORM Models (24 tables):**
- `models/base.py` — TimestampMixin (created_at, updated_at)
- `models/user.py` — User, UserRole, RefreshToken, EmailVerificationToken, PasswordResetToken
- `models/thread.py` — Thread, ThreadSubscription
- `models/post.py` — Post (self-referential for nesting)
- `models/category.py` — Category
- `models/chat.py` — ChatRoom, ChatRoomMember, Message
- `models/vote.py` — Vote, Reaction, ContentReport, ModerationAction, CategoryModerator, CategoryRequest
- `models/tag.py` — Tag, ThreadTag
- `models/notification.py` — Notification
- `models/friendship.py` — FriendRequest with status enum
- `models/oauth_account.py` — OAuthAccount
- `models/attachment.py` — Attachment
- `models/audit_log.py` — AuditLog

**Pydantic Schemas:**
- `schemas/auth.py` — Register, login, token, OAuth, password reset schemas
- `schemas/user.py` — User profile response shapes (me, public, list)
- `schemas/thread.py` — Thread CRUD with sanitization + pagination
- `schemas/post.py` — Post CRUD with nested reply support
- `schemas/category.py` — Category with slug validation
- `schemas/chat.py` — Chat room and message schemas
- `schemas/vote.py` — Vote (+1/-1), reaction, report schemas
- `schemas/tag.py` — Tag with lowercase normalization
- `schemas/admin.py` — Admin actions with strict pattern validation
- `schemas/search.py` — Polymorphic search results
- `schemas/notification.py` — Notification with unread count
- `schemas/upload.py` — File upload metadata

**Shared Services:**
- `services/sanitize.py` — XSS sanitization (strips dangerous tags, NOT html.escape)
- `services/audit.py` — Audit log recording with role-based visibility
- `services/bot.py` — @pulse AI bot (Groq API, web search, retry logic)
- `services/notifications.py` — Notification creation helper
- `services/mentions.py` — @mention extraction and notification
- `services/email.py` — SMTP email sending for moderation
- `services/moderation.py` — Moderator scope helper
- `services/attachments.py` — Two-phase upload linking
- `services/storage.py` — File upload: MIME check, magic bytes, filename sanitization

#### Core Service (`services/core/app/`)
- `main.py` — FastAPI app with lifespan, middleware, router mounting
- `auth_routes.py` — Authentication endpoints
- `auth_services.py` — Auth business logic
- `auth_oauth.py` — Google + GitHub OAuth2 flows
- `auth_email.py` — SMTP email sending
- `user_routes.py` — User profile and friend endpoints
- `user_services.py` — User business logic
- `notification_routes.py` — Notification endpoints
- `notification_services.py` — Notification business logic
- `upload_routes.py` — File upload endpoint

#### Community Service (`services/community/app/`)
- `main.py` — FastAPI app with category seeding
- `forum_routes.py` — Forum CRUD endpoints
- `forum_services.py` — Thread/post logic with nested tree building
- `forum_votes.py` — Vote/reaction/report logic
- `forum_search.py` — SQL ILIKE search
- `forum_seed.py` — Default category seeding
- `admin_routes.py` — Admin/mod endpoints
- `admin_services.py` — Admin business logic (role, suspend, ban, reports)
- `chat_routes.py` — Chat room/message endpoints
- `chat_services.py` — Chat business logic with bot trigger

#### Gateway Service (`services/gateway/app/`)
- `main.py` — Reverse proxy + WebSocket hub + Redis bridge

#### Tests (`services/tests/`)
- `conftest.py` — Composite test app, SQLite DB, fixtures
- `test_auth.py` — 5 authentication tests
- `test_forum.py` — 7 forum tests
- `test_audit.py` — 10 audit log tests
- `test_validation.py` — 9 input validation + upload security tests

#### Seed Script
- `services/seed.py` — Populates DB with 16 users, 8 categories, 22 threads, 138 posts, etc.

### Frontend (34 JS/JSX files)

**Entry + Routing:**
- `main.jsx` — React root with providers
- `App.jsx` — Route definitions

**Context:**
- `context/AuthContext.jsx` — Auth state (tokens, profile, login/logout)
- `context/ThemeContext.jsx` — Dark/light theme toggle

**Layout:**
- `layouts/MainLayout.jsx` — Top navbar with search, notifications, user menu

**Pages:**
- `pages/HomePage.jsx` — Thread feed with filters, pagination, create form
- `pages/ThreadPage.jsx` — Thread detail with nested comments
- `pages/ChatPage.jsx` — Chat interface with rooms and messages
- `pages/ProfilePage.jsx` — User profile editing and viewing
- `pages/AdminPage.jsx` — 7-tab admin dashboard
- `pages/DashboardPage.jsx` — Member dashboard with stats
- `pages/PeoplePage.jsx` — User search
- `pages/LoginPage.jsx` — Login/register with OAuth
- `pages/ProfileLookupPage.jsx` — Username-to-profile resolver
- `pages/PasswordResetPages.jsx` — Password reset flow
- `pages/VerifyEmailPage.jsx` — Email verification

**Components:**
- `components/ThreadCard.jsx` — Thread summary card with votes
- `components/UserIdentity.jsx` — User avatar + name + role badge
- `components/NotificationCenter.jsx` — Notification dropdown
- `components/UserActionModal.jsx` — User action modal (message/friend/report)
- `components/MentionTextarea.jsx` — @mention autocomplete textarea
- `components/Pagination.jsx` — Reusable pagination with ellipsis
- `components/AttachmentList.jsx` — File attachment display
- `components/LoginPrompt.jsx` — Guest login prompt banner
- `components/ProtectedRoute.jsx` — Auth route guard
- `components/RichText.jsx` — @mention link renderer

**Hooks:**
- `hooks/useLocalStorage.js` — localStorage sync
- `hooks/useNotifications.js` — Notification WebSocket + REST
- `hooks/useThreadLiveUpdates.js` — Thread real-time updates
- `hooks/useChatRoom.js` — Chat room WebSocket
- `hooks/useGlobalUpdates.js` — Global event WebSocket

**Utilities:**
- `lib/api.js` — API client (fetch wrapper, auth headers, base URL)
- `lib/timeUtils.js` — Time formatting (formatTimeAgo, isUserOnline)
- `lib/uploadUtils.js` — Client-side file validation

### CSS
- `styles/global.css` — Complete design system (3,336 lines, Reddit-inspired dark/light theme)

---

*This document covers the entire PulseBoard codebase. Each file has detailed inline comments
explaining what it does and why — read the source code alongside this guide.*

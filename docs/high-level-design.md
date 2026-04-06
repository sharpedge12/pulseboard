# High-Level Design (HLD)

## 1. System Overview

PulseBoard is a real-time discussion forum with threaded conversations, live chat, moderation tools, and an AI bot. The system uses a **microservice architecture** with 3 backend services behind an API gateway, a React SPA frontend, and shared infrastructure (PostgreSQL, Redis, SMTP).

---

## 2. Architecture Diagram

```
┌──────────────────────────────────────────────────────────┐
│                    Browser (React SPA)                    │
│                      Port 5173                           │
└──────────────────────┬───────────────────────────────────┘
                       │ HTTP + WebSocket
                       ▼
┌──────────────────────────────────────────────────────────┐
│                  API Gateway (Port 8000)                  │
│  ┌─────────────┐ ┌──────────────┐ ┌───────────────────┐ │
│  │ Reverse Proxy│ │ WebSocket Hub│ │ Redis→WS Bridge   │ │
│  │  (httpx)     │ │ (4 channels) │ │ (pub/sub listener)│ │
│  └──────┬──────┘ └──────────────┘ └───────────────────┘ │
│         │         Static Files: /uploads                 │
└─────────┼────────────────────────────────────────────────┘
          │
    ┌─────┼──────────────────────────────────┐
    │     │          Backend Services          │
    │     ▼                                    │
    │  ┌─────────────────────────────────┐    │
    │  │  Core Service (Port 8001)       │    │
    │  │  /api/v1/auth/*                 │    │
    │  │  /api/v1/users/*                │    │
    │  │  /api/v1/uploads/*              │    │
    │  │  /api/v1/notifications/*        │    │
    │  └─────────────────────────────────┘    │
    │                                          │
    │  ┌─────────────────────────────────┐    │
    │  │  Community Service (Port 8002)  │    │
    │  │  /api/v1/categories/*           │    │
    │  │  /api/v1/threads/*              │    │
    │  │  /api/v1/posts/*                │    │
    │  │  /api/v1/search/*               │    │
    │  │  /api/v1/admin/*                │    │
    │  │  /api/v1/chat/*                 │    │
    │  └─────────────────────────────────┘    │
    └──────────────────────────────────────────┘
          │            │            │
    ┌─────┴────┐ ┌─────┴────┐ ┌────┴─────┐
    │PostgreSQL│ │  Redis   │ │ MailHog  │
    │  16      │ │   7      │ │  (SMTP)  │
    │ Port 5432│ │ Port 6379│ │Port 1025 │
    └──────────┘ └──────────┘ └──────────┘
```

---

## 3. Service Map

| Service | Port | Responsibility | Key Entities |
|---------|------|---------------|--------------|
| **Gateway** | 8000 | Reverse proxy, CORS, WebSocket hub (4 channels), Redis-to-WS bridge, static file serving (`/uploads`) | No DB access |
| **Core** | 8001 | User lifecycle: registration, login, JWT/OAuth, email verification, password reset, profiles, friends, avatars, file uploads, notifications | `users`, `refresh_tokens`, `email_verification_tokens`, `password_reset_tokens`, `oauth_accounts`, `friend_requests`, `notifications`, `attachments` |
| **Community** | 8002 | Content lifecycle: categories, threads, posts, votes, reactions, tags, search, content reports, moderation actions, category moderators, category requests, chat rooms, messages, DM, @pulse bot integration | `categories`, `threads`, `thread_subscriptions`, `posts`, `tags`, `thread_tags`, `votes`, `reactions`, `content_reports`, `moderation_actions`, `category_moderators`, `category_requests`, `chat_rooms`, `chat_room_members`, `messages` |

---

## 4. Gateway Route Map

| URL Prefix | Target Service |
|------------|---------------|
| `/api/v1/auth/*` | Core (8001) |
| `/api/v1/users/*` | Core (8001) |
| `/api/v1/uploads/*` | Core (8001) |
| `/api/v1/notifications/*` | Core (8001) |
| `/api/v1/categories/*` | Community (8002) |
| `/api/v1/threads/*` | Community (8002) |
| `/api/v1/posts/*` | Community (8002) |
| `/api/v1/search/*` | Community (8002) |
| `/api/v1/admin/*` | Community (8002) |
| `/api/v1/chat/*` | Community (8002) |

WebSocket endpoints are handled directly by the gateway:

| Endpoint | Channel Pattern | Auth |
|----------|----------------|------|
| `/ws/notifications` | `notifications:{user_id}` | JWT required |
| `/ws/threads/{thread_id}` | `thread:{thread_id}` | Public |
| `/ws/chat/{room_id}` | `chat:{room_id}` | JWT required |
| `/ws/global` | `global` | Public |

---

## 5. Data Architecture

All services share a single **PostgreSQL 16** database with **23 tables**. There is no data isolation between services — the shared library (`pulseboard-shared`) contains all SQLAlchemy models and Pydantic schemas.

### Table ownership by service

| Service | Tables (13) |
|---------|-------------|
| **Core** | `users`, `refresh_tokens`, `email_verification_tokens`, `password_reset_tokens`, `oauth_accounts`, `friend_requests`, `notifications`, `attachments` |
| **Community** | `categories`, `threads`, `thread_subscriptions`, `posts`, `tags`, `thread_tags`, `votes`, `reactions`, `content_reports`, `moderation_actions`, `category_moderators`, `category_requests`, `chat_rooms`, `chat_room_members`, `messages` |

### Polymorphic FK pattern

Four tables use a discriminator + generic ID pattern: `votes`, `reactions`, `content_reports`, `attachments`. Referential integrity is enforced at the application level.

---

## 6. Real-Time Architecture

```
┌──────────┐    publish_event()     ┌───────┐    subscribe     ┌─────────┐
│  Service  │ ──────────────────── > │ Redis │ < ────────────── │ Gateway │
│(Core/     │   PUBLISH channel     │Pub/Sub│   PSUBSCRIBE     │  Bridge │
│Community/ │   {event, data}       │       │   thread:*       │         │
│Chat)      │                       │       │   chat:room:*    │         │
└──────────┘                        │       │   notifications:*│         │
                                    │       │   global         │         │
                                    └───────┘                  └────┬────┘
                                                                    │
                                                          broadcast │
                                                                    ▼
                                                            ┌──────────────┐
                                                            │  WebSocket   │
                                                            │  Clients     │
                                                            └──────────────┘
```

- **Redis pub/sub** is the inter-service event bus.
- Services call `publish_event(channel, payload)` after mutations.
- The gateway subscribes to Redis patterns and forwards events to connected WebSocket clients.
- Channel mapping: `chat:room:X` (Redis) maps to `chat:X` (WebSocket).

---

## 7. Authentication Flow

1. **Registration**: Core service creates user + email verification token, sends verification email via SMTP.
2. **Email verification**: User clicks link, Core marks `is_verified=True`.
3. **Login**: Core validates credentials (pbkdf2_sha256), checks `is_verified`, `is_active`, `is_banned`. Issues JWT access token (30 min) + refresh token (7 days).
4. **OAuth**: Google/GitHub OAuth2 flow. Core exchanges authorization code for provider tokens, creates/links user, issues JWT.
5. **Token refresh**: Core validates refresh token, revokes old, issues new pair.
6. **Auth guard**: Every authenticated request passes through `get_current_user()` (shared helper) which validates the JWT, loads the user, updates `last_seen`, and checks suspension/ban status.

---

## 8. Thread Listing with Pagination

The system displays threads with full pagination support:

- **Endpoint**: `GET /api/v1/threads`
- **Query params**: `category` (slug), `sort` (new/top/trending), `time_range` (all/year/month/week/day/hour), `page` (>=1), `page_size` (1-100, default 20), `tag`
- **Response**: `PaginatedThreadsResponse` with `items`, `total`, `page`, `page_size`, `total_pages`
- **Frontend**: Reusable `Pagination` component with numbered pages, ellipsis, Prev/Next buttons. Page number synced to URL `?page=N`.

---

## 9. Technology Stack

| Layer | Technology |
|-------|------------|
| Frontend | React 18, Vite 6, React Router DOM 6, Axios, plain CSS |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2, Pydantic v2, Uvicorn |
| Database | PostgreSQL 16 |
| Cache/Pub-Sub | Redis 7 |
| Auth | JWT (python-jose HS256), OAuth2 (Google + GitHub), passlib (pbkdf2_sha256) |
| Email | smtplib + MailHog (dev) |
| AI Bot | Groq Compound Mini + Tavily search + DuckDuckGo fallback |
| Infrastructure | Docker, Docker Compose |

---

## 10. Deployment Topology

```
docker compose up --build

┌─────────────────────────────────────────┐
│           Docker Compose Network         │
│                                          │
│  ┌──────────┐  ┌──────┐  ┌──────────┐  │
│  │ gateway  │  │ core │  │community │  │
│  │  :8000   │  │:8001 │  │  :8002   │  │
│  └──────────┘  └──────┘  └──────────┘  │
│                                          │
│  ┌────┐  ┌───────┐  ┌──────┐           │
│  │ db │  │ redis │  │mail  │           │
│  │5432│  │ 6379  │  │ hog  │           │
│  └────┘  └───────┘  └──────┘           │
│                                          │
│  ┌──────────┐                           │
│  │ frontend │                           │
│  │  :5173   │                           │
│  └──────────┘                           │
│                                          │
│  Volumes: postgres_data, redis_data,    │
│           upload_data (shared: gateway,  │
│           core, community)              │
└─────────────────────────────────────────┘
```

---

## 11. Non-Functional Requirements

| Requirement | Approach |
|-------------|----------|
| **Scalability** | Horizontal scaling behind a load balancer. Redis pub/sub supports multi-instance deployments. Database is the bottleneck (single PostgreSQL). |
| **Availability** | Docker Compose health checks on db and redis. Services wait for healthy dependencies before starting. |
| **Security** | JWT with short-lived access tokens (30 min). CORS restricted to frontend origin. OAuth2 for social login. Password hashing with pbkdf2_sha256. |
| **Observability** | Structured logging via Python `logging`. Health check endpoints on every service. |
| **Performance** | Connection pooling via SQLAlchemy. Redis pub/sub for real-time (no polling). Pagination on all list endpoints. |

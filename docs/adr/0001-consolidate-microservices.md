# ADR-0001: Consolidate Seven Microservices into Three

- **Status:** Accepted
- **Date:** 2026-04-01
- **Decision Makers:** Development Team

## Context

PulseBoard was decomposed from a monolithic backend into **7 microservices** (gateway, auth, user, forum, chat, notification, moderation) behind an API gateway. While this architecture provided clear separation of concerns, several issues emerged during development and operation:

1. **Operational overhead.** Seven backend services plus a gateway means 8 Dockerfiles, 8 `requirements.txt` files, 8 health checks, and 8 containers to monitor. For an internship-scale project with a single shared PostgreSQL database, this is excessive.

2. **Notification service is trivially small.** At 150 total lines of Python (3 routes, 67-line services.py), the notification service does not justify its own container, Dockerfile, port, and health check. The shared library (`shared/services/notifications.py`) already contains the `create_notification()` helper used by all other services.

3. **Auth and User are tightly coupled.** Auth creates `User` records; User manages profiles, avatars, and friends for those same records. Both services operate on the `users` table. User's `get_current_user()` auth helper is shared code that both services depend on identically.

4. **Moderation operates entirely on Forum entities.** Moderation's 21 routes manage threads (lock/pin/unlock/unpin), content reports (on threads and posts), user suspensions, and category requests. All of these are forum-domain entities. Moderation has no unique database tables — every table it touches (`threads`, `posts`, `content_reports`, `moderation_actions`, `category_moderators`, `category_requests`) is also used by the forum service.

5. **All services share one database.** There is no data isolation between services. The shared library contains all 23 SQLAlchemy models and all Pydantic schemas. Merging services has zero schema migration cost.

6. **Test infrastructure already proves co-location works.** The test `conftest.py` mounts all service routers into a single composite FastAPI app, demonstrating that routers from different services can coexist in one process without conflict.

## Decision

Consolidate from **7 microservices** (+ gateway) to **3 services** (+ gateway):

| Current Services | Consolidated Service | Port | Rationale |
|-----------------|---------------------|------|-----------|
| Gateway | **Gateway** (unchanged) | 8000 | Reverse proxy + WebSocket hub; no DB access. Must remain separate. |
| Auth + User + Notification | **Core** | 8001 | User lifecycle: registration, authentication, profiles, friends, uploads, notifications. Tight coupling via `users` table. |
| Forum + Moderation + Chat | **Community** | 8002 | Content lifecycle: categories, threads, posts, votes, reactions, tags, search, reports, mod actions, chat rooms, messages, DM. All user-generated content in one service. |

### Service boundary rationale

**Core (auth + user + notification):**
- Auth creates users; User manages user profiles; Notification delivers events to users. All three are anchored on the `users` table.
- Absorbing the 150-line notification service eliminates one container with negligible complexity increase.
- Upload routes (`/api/v1/uploads`) stay in Core since they are part of the user service domain.

**Community (forum + moderation + chat):**
- Moderation's 21 admin routes operate exclusively on forum entities (threads, posts, reports, categories).
- Combining them reduces inter-service coupling for operations like "lock a thread" or "resolve a report on a post."
- Category requests (moderator submits, admin approves, category is created) become internal function calls instead of cross-service operations.
- Chat rooms, messages, and DMs are user-generated content that shares the same database and authentication patterns as forum content. Merging chat into community simplifies the deployment topology with negligible complexity increase.

**Gateway (kept separate):**
- The gateway is architecturally distinct: it is a reverse proxy with no database access, handling CORS, WebSocket multiplexing, and the Redis-to-WebSocket bridge.
- It must remain separate to serve as the single entry point for the frontend.

## Consequences

### Positive

- **Reduced operational overhead**: 3 containers instead of 8 (63% reduction). Fewer Dockerfiles, fewer health checks, fewer ports to manage.
- **Simpler gateway routing**: Route map shrinks from 10 prefix rules to 6.
- **Fewer inter-service boundaries**: Moderation actions on forum content become internal function calls. Notification creation from auth/user events is a direct import.
- **Faster Docker builds**: Fewer images to build and cache.
- **Easier debugging**: Related functionality lives in the same process with shared logs and stack traces.

### Negative

- **Larger service codebases**: Core will be ~1,675 lines (auth 802 + user 723 + notification 150). Community will be ~3,464 lines (forum 1,730 + moderation 1,177 + chat 557). These are still manageable for a project of this scale.
- **Coarser scaling granularity**: Cannot scale auth independently of user/notification. Acceptable since all services share one database anyway — the database is the scaling bottleneck, not the application tier.
- **Router namespace collisions**: Must ensure route prefixes don't conflict when combining routers in one FastAPI app. Mitigated by keeping existing URL prefixes unchanged.

### Neutral

- **No API changes**: All HTTP endpoints retain their existing paths. Frontend code requires zero modifications.
- **No schema changes**: Database tables, models, and Pydantic schemas are unchanged.
- **Test infrastructure**: `conftest.py` needs updates to reflect the new service directory layout but the composite-app pattern remains the same.

## Considered Options

### Option A: Keep 7 services (status quo)

- **Pro**: Maximum separation of concerns.
- **Con**: Excessive operational overhead for a single-database project. Notification service is unjustifiably small. Auth/User coupling forces shared code duplication.
- **Rejected**: The costs outweigh the benefits at this project's scale.

### Option B: Consolidate to 2 services (gateway + monolith)

- **Pro**: Simplest possible deployment.
- **Con**: Loses all microservice benefits. Chat, forum, and auth concerns are genuinely different domains with different scaling and development patterns.
- **Rejected**: Too aggressive. We would lose meaningful architectural boundaries.

### Option C: Consolidate to 3 services (chosen)

- **Pro**: Eliminates the overhead of trivially small services (notification) and tightly coupled pairs (auth+user, forum+moderation) while keeping a clean gateway boundary. Chat shares the same database, auth, and event patterns as forum content, so merging it into community is natural.
- **Con**: Moderate refactoring effort.
- **Chosen**: Best balance of operational simplicity and architectural clarity.

### Option D: Consolidate to 4 services (keep chat separate)

- **Pro**: Chat is an arguably independent domain with room-based access control.
- **Con**: At only 557 lines, chat does not justify its own container, Dockerfile, port, and health check. It shares the same database, Redis pub/sub, and authentication as the community service.
- **Superseded**: Initially chosen, but after further evaluation, chat was merged into community for reduced operational overhead.

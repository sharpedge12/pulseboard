# AGENTS.md


## Overview

This file is for all agentic coding agents, AI pair programmers, and code assistants working in this repository. It provides standardized commands, style guidelines, and agent etiquette to ensure high-quality, maintainable, and consistent code contributions. Please review carefully before making changes.

---

## 1. Build, Lint, and Test Commands

### Backend (Python)

#### Install dependencies

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e services/shared
pip install -r services/<service>/requirements.txt   # for each service
```

#### Lint code

- `flake8 .`  -- Lint all modules
- `black .`   -- Auto-format entire codebase
- `isort .`   -- Organize imports

#### Type checking

- `mypy .`    -- Run static type checker

#### Run all tests

```bash
source .venv/bin/activate
timeout 600 python -m pytest services/tests/test_auth.py services/tests/test_forum.py services/tests/test_audit.py services/tests/test_validation.py -x -v --tb=short -k "not subscribe"
rm -f test_services.db    # clean up after test run
```

#### Run a single test

```bash
pytest services/tests/test_auth.py::test_login_and_me_flow -x -v --tb=short
```

### Frontend (React)

```bash
cd frontend
npm install
npm run dev          # Vite dev server on port 5173
npm run build        # Production build
```

### Docker (full stack)

```bash
docker compose up --build
```

---

## 2. Code Style Guidelines

### 2.1. Imports
- Group imports: standard lib, third-party, local (use isort for ordering).
- Prefer explicit imports over wildcard (`from foo import bar`, **not** `from foo import *`).

### 2.2. Formatting
- Use [PEP8](https://www.python.org/dev/peps/pep-0008/) as default.
- **Indentation:** 4 spaces, never tabs.
- **Max line length:** 88 (Black default).
- **Trailing commas:** Enable for multi-line collections/args.

### 2.3. Types
- Use Python 3 type annotations for all new/changed functions, methods, and classes.
- Use `Optional[...]` for nullable variables, `List`, `Dict`, etc. from `typing` unless using builtins in 3.9+.

### 2.4. Naming Conventions
- Files/modules: `snake_case.py`
- Classes: `CamelCase`
- Functions, methods, variables: `snake_case`
- Constants: `ALL_CAPS_WITH_UNDERSCORES`
- Test functions: `test_` prefix (e.g. `test_sort_order`)

### 2.5. Error Handling
- Catch only exceptions you're prepared to handle.
- Never use bare `except:` (specify the exception type).
- Use logging for recoverable errors, and `raise` for unrecoverable ones.

### 2.6. Docstrings and Comments
- Use triple quotes for docstrings immediately after defs/classes (PEP257).
- Summarize function arguments and return values in docstrings (Google or NumPy style).
- Comments should explain non-obvious logic or constraints, not restate code.

---

## 3. Agentic Cooperation & Etiquette

- Never commit secrets, tokens, or credentials. Place placeholders in `.env`. Add `.env` to `.gitignore`.
- When creating or editing files, preserve user intent and existing conventions.
- Respect project-level linters and CI: your PR must pass all checks before merge.
- Summarize major changes in pull request descriptions.
- Keep changes tightly scoped and make atomic commits.
- Prefer tests with clear assertions and minimal external dependencies/mocks.

---

## 4. Tooling and Configurations

- If `pyproject.toml` is present, check it for formatting/linting tool config.
- Respect `.editorconfig`, `.prettierrc*`, `.eslintrc*`, etc if present.
- Ruff cache (`.ruff_cache/`) is present -- Ruff is used for linting.
- Extend this file with project-specific rules as they are established.

---

## 5. Copilot, Cursor, and Coding Assistant Rules

- Be concise, always use explicit types, flag all TODOs/FIXMEs, and default to safe, readable code.
- If multiple assistants are active, coordinate to avoid duplicate work and comment on PRs you edit.

---

## 6. PulseBoard Project-Specific Rules

### Architecture

- **Microservice-only** -- there is no monolith mode. The `backend/` directory has been deleted.
- 2 backend services + API gateway under `services/`, shared library at `services/shared/`.
- Consolidated from 7 services into 2 (see [ADR-0001](docs/adr/0001-consolidate-microservices.md)).
- Frontend is a React SPA under `frontend/`, communicates with the gateway at `http://localhost:8000`.
- All services share a single PostgreSQL 16 database. Redis 7 pub/sub for inter-service events.

### Service Map

| Service | Port | Responsibility |
|---------|------|---------------|
| **Gateway** | 8000 | Reverse proxy, WebSocket hub, Redis-to-WS bridge, CORS, upload proxy to Core |
| **Core** | 8001 | Auth (register, login, JWT, OAuth, email verification, password reset), user profiles, friends, search, avatars, file uploads, in-app notifications, email dispatch |
| **Community** | 8002 | Categories, threads, posts, votes, reactions, tags, search, pagination, admin dashboard, reports, mod actions, category requests, chat rooms (direct + group), messages, DM, bot integration |
| **Frontend** | 5173 | React SPA (Vite dev server) |

### Gateway Route Map

- `/api/v1/auth/*` -> core:8001
- `/api/v1/users/*`, `/api/v1/uploads/*` -> core:8001
- `/api/v1/notifications/*` -> core:8001
- `/api/v1/categories/*`, `/api/v1/threads/*`, `/api/v1/posts/*`, `/api/v1/search/*` -> community:8002
- `/api/v1/admin/*` -> community:8002
- `/api/v1/chat/*` -> community:8002
- `/uploads/*` -> proxied to core:8001 (static file serving for avatars, attachments)

### Database Schema (24 tables)

`users`, `refresh_tokens`, `email_verification_tokens`, `password_reset_tokens`, `oauth_accounts`, `friend_requests`, `categories`, `threads`, `thread_subscriptions`, `posts`, `tags`, `thread_tags`, `votes`, `reactions`, `content_reports`, `moderation_actions`, `category_moderators`, `category_requests`, `chat_rooms`, `chat_room_members`, `messages`, `notifications`, `attachments`, `audit_logs`

### Environment and Dependencies

- Python virtual environment: create at project root with `python -m venv .venv`.
- Install shared library first: `pip install -e services/shared`.
- Then install service-specific requirements: `pip install -r services/<service>/requirements.txt`.
- Docker Compose: `docker compose up --build` (single compose file).

### Testing

- Tests live at `services/tests/` (configured via `pytest.ini` `testpaths`).
- Tests use SQLite (`test_services.db`) -- no Docker or PostgreSQL required.
- Autouse fixtures patch `_send_verification_email` and `_send_moderation_email` to no-ops.
- SMTP calls use `timeout=2` to prevent test hangs.
- `publish_event()` silently swallows errors -- no Redis mocking needed in tests.
- Composite test app mounts all service routers into a single process via `importlib`.
- Run tests: `pytest services/tests/test_auth.py services/tests/test_forum.py services/tests/test_audit.py services/tests/test_validation.py -x -v --tb=short -k "not subscribe"` (from project root, with venv activated).
- Clean up after: `rm -f test_services.db`.
- **31 tests total**: 5 auth tests + 7 forum tests + 10 audit tests + 9 validation tests. All must pass.

### Code Conventions

- `passlib` uses `pbkdf2_sha256` (not bcrypt). The `crypt` deprecation warning is suppressed via `warnings.catch_warnings()`.
- FastAPI `Query()` uses `pattern=` (not deprecated `regex=`).
- No Alembic -- database init uses `create_all()` + `_run_migrations()` with raw SQL `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.
- AI bot uses Groq Compound Mini (`groq/compound-mini`) + Tavily search + DuckDuckGo fallback.
- Bot runs in background `threading.Thread` (daemon=True) with own `SessionLocal()` DB session.
- Bot retry logic: 3 retries with exponential backoff (2s, 4s, 8s) for 429 rate limits.

### Frontend

- React 18 + Vite 6 + JavaScript, plain CSS (no Tailwind, no CSS modules, no styled-components).
- System fonts (`-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto`) — no Google Fonts.
- React Router DOM v6 with `NavLink`, `useNavigate`, `useParams`, `useSearchParams`.
- All users are clickable everywhere and open a modal with message/friend/report actions.
- Design tokens (CSS custom properties) defined in `:root` in `global.css`. Both dark and light themes supported via `[data-theme="light"]`.
- Reddit-inspired design: `#FF4500` accent, top navbar, card-style feed, nested comments with collapse lines, right sidebar with community info.
- Custom SVG logo (`frontend/public/logo.svg`) — shield with pulse line, used as navbar brand and favicon.
- Pulse bot avatar (`frontend/public/pulse-avatar.svg`) — orange robot head, auto-assigned in frontend for `pulse` username.
- `LoginPrompt` component (`frontend/src/components/LoginPrompt.jsx`) — reusable banner for unauthenticated users attempting protected actions.
- `MentionTextarea` (`frontend/src/components/MentionTextarea.jsx`) — `@mention` autocomplete with debounced user search, keyboard navigation, and avatar display.

### Real-time Architecture

- **Redis pub/sub bridge** in gateway: subscribes to patterns `thread:*`, `chat:room:*`, `notifications:*`, `global`.
- Channel name mapping: `chat:room:X` (Redis) -> `chat:X` (WebSocket) via `_redis_channel_to_ws_channel()`.
- Gateway uses `asyncio.to_thread()` with `pubsub.get_message(timeout=1.0)` for non-blocking Redis polling.
- Forum/chat routes call `publish_event()` (Redis pub/sub, picked up by gateway bridge).
- Frontend hooks: `useThreadLiveUpdates.js`, `useChatRoom.js`, `useNotifications.js`, `useGlobalUpdates.js`.
- `ConnectionManager.broadcast()` handles dead connections with try/except per connection, auto-cleans dead ones.
- WebSocket endpoints catch `(WebSocketDisconnect, Exception)` for robust cleanup.

### Pagination

- Backend: `PaginatedThreadsResponse` schema with `items`, `total`, `page`, `page_size`, `total_pages`.
- `list_threads()` in `services/community/app/forum_services.py` accepts `page`, `page_size`, returns paginated response.
- Route at `GET /api/v1/threads` accepts query params: `category`, `sort`, `time_range`, `page` (ge=1), `page_size` (ge=1, le=100), `tag`.
- Frontend: Reusable `Pagination` component (`frontend/src/components/Pagination.jsx`) with numbered page buttons, ellipsis, Prev/Next, and total count.
- Page number is synced to URL query string (`?page=N`), page 1 omits the param for clean URLs.
- `?community=` and `?page=` params coexist. Changing category/sort/time resets page to 1.

### Keyboard Shortcuts

| Area | Shortcut | Action |
|------|----------|--------|
| **Thread reply** (ThreadPage) | `Enter` | Submit reply |
| **Thread reply** (ThreadPage) | `Shift+Enter` | New line |
| **Chat message** (ChatPage) | `Enter` | Send message |
| **Chat message** (ChatPage) | `Shift+Enter` | New line |
| **New thread body** (HomePage) | `Ctrl/Cmd+Enter` | Publish thread |
| **Edit post inline** (ThreadPage) | `Ctrl/Cmd+Enter` | Save edit |
| **Edit post inline** (ThreadPage) | `Escape` | Cancel edit |
| **Edit thread** (ThreadPage) | `Ctrl/Cmd+Enter` | Save edit |
| **Edit thread** (ThreadPage) | `Escape` | Cancel edit |
| **Profile edit** (ProfilePage) | `Ctrl/Cmd+Enter` | Save profile |

- Quick-reply areas (thread replies, chat) use **Enter to send** (Shift+Enter for newline).
- Long-form/editing areas (new thread, edit thread/post, profile) use **Ctrl+Enter to save** (Enter for newline).
- All shortcuts work on both Windows (`Ctrl`) and macOS (`Cmd`/`metaKey`).
- Visual `.kbd-hint` badges on buttons show the keyboard shortcut.
- `MentionTextarea` internal `onKeyDown` consumes Enter when mention dropdown is open (inserts mention), delegates to external handler when dropdown is closed.

### Online Status / Timestamps

- `last_seen` column on User model (`DateTime(timezone=True), nullable=True`).
- Updated on every authenticated request in `get_current_user()`.
- `_is_online()` checks if `last_seen` within 5 minutes.
- `UserPublicProfileResponse` and `UserListItemResponse` include `created_at`, `last_seen`, `is_online`.
- Frontend uses shared `timeUtils.js` for `formatTimeAgo()`, `formatTime()`, `formatDate()`, `formatLastSeen()`, `isUserOnline()`.

### Bot Architecture

- **Bot code**: `services/shared/shared/services/bot.py`.
- **Bot username**: `pulse`, email: `pulse-bot@pulseboard.app`.
- **Model**: `groq/compound-mini` (Groq Compound Mini with built-in web search).
- **Search**: Tavily (primary) + DuckDuckGo (fallback).
- **Functions**: `build_bot_reply()`, `build_thread_context()`, `build_chat_context()`, `get_thread_participants()`, `get_chat_participants()`, `_tavily_search()`, `_ddg_search()`, `_web_search()`, `_format_user_profile()`, `schedule_forum_bot_reply()`, `schedule_chat_bot_reply()`, `_generate_forum_bot_reply()`, `_generate_chat_bot_reply()`, `_strip_citations()`.

### Authentication

- JWT (HS256) via `python-jose[cryptography]`: access tokens (30 min) + refresh tokens (7 days).
- Password hashing: `passlib` with `pbkdf2_sha256` scheme.
- OAuth: Google (OpenID Connect) + GitHub (`read:user`, `user:email`).
- Email verification required before login.
- SMTP via `smtplib` with MailHog in dev (port 1025, UI at 8025).

---

## 7. Tech Stack Summary

### Backend
| Technology | Version | Purpose |
|---|---|---|
| Python | 3.12 | Runtime |
| FastAPI | >= 0.115.0 | Web framework (all services) |
| Uvicorn `[standard]` | >= 0.30.0 | ASGI server |
| SQLAlchemy | >= 2.0 | ORM (DeclarativeBase, Mapped columns) |
| Pydantic `[email]` | >= 2.0 | Validation and serialization |
| Pydantic-Settings | >= 2.0 | Env/config management |
| psycopg `[binary]` | >= 3.1 | PostgreSQL driver (psycopg 3) |
| python-jose `[cryptography]` | >= 3.3 | JWT tokens (HS256) |
| passlib | >= 1.7.4 | Password hashing (pbkdf2_sha256) |
| httpx | >= 0.27 | HTTP client (inter-service, OAuth, AI) |
| redis (redis-py) | >= 5.0 | Redis pub/sub client |
| email-validator | >= 2.0 | Email validation |
| python-multipart | >= 0.0.9 | File upload parsing |

### Frontend
| Technology | Version | Purpose |
|---|---|---|
| React | 18.3.1 | UI library |
| React DOM | 18.3.1 | DOM renderer |
| React Router DOM | 6.30.3 | Client-side routing (v6) |
| Axios | 1.13.6 | HTTP client |
| Vite | 6.4.1 | Build tool and dev server |
| @vitejs/plugin-react | 4.7.0 | React JSX support |
| Plain CSS | -- | Styling (design tokens, dark/light themes) |
| Google Fonts | -- | Manrope + Space Grotesk |

### Infrastructure
| Technology | Version | Purpose |
|---|---|---|
| Docker | -- | Containerization |
| Docker Compose | v2 | Orchestration |
| PostgreSQL | 16-alpine | Primary database |
| Redis | 7-alpine | Pub/sub events |
| MailHog | latest | Dev SMTP server (port 1025/8025) |

### AI / Bot
| Technology | Details | Purpose |
|---|---|---|
| Groq API | `groq/compound-mini` | LLM for @pulse bot |
| Tavily Search | API | Primary web search |
| DuckDuckGo | Instant Answer API | Fallback web search |

### Testing
| Technology | Purpose |
|---|---|
| pytest + pytest-asyncio | Test runner |
| FastAPI TestClient | HTTP test client |
| SQLite (file-based) | Test database |
| unittest.mock.patch | SMTP mocking |

---

## 8. Completed Features

### Feature 1: Async Bot Replies
- Bot replies run in background `threading.Thread` (daemon=True) with own DB session.
- Triggered by `@pulse` mentions in threads and chat.
- 3 retries with exponential backoff for rate limits.

### Feature 2: Timestamps/Dates Everywhere
- `last_seen` column on User model, updated on every authenticated request.
- Online status indicator (green dot) based on 5-minute threshold.
- Shared `timeUtils.js` with `formatTimeAgo()`, `formatTime()`, `formatDate()`, `formatLastSeen()`, `isUserOnline()`.
- Timestamps on thread cards, replies, chat messages, notifications, room listings.

### Feature 3: Bot Reply Quality
- Citation stripping via `_strip_citations()` removes Groq-generated `[n]` artifacts.
- Enhanced system prompt with personality and conversation context.

### Feature 4: Real-time Updates (Redis-to-WebSocket Bridge)
- Gateway subscribes to Redis pub/sub patterns and broadcasts to WebSocket clients.
- 4 WebSocket channels: thread, chat, notifications, global.
- Dead connection handling with auto-cleanup in `ConnectionManager`.
- Frontend hooks for live updates.

### Feature 5: Thread Pagination
- Reusable `Pagination` component with numbered pages, ellipsis, Prev/Next.
- URL-synced page state (`?page=N`).
- Proper reset on filter changes.

### Feature 6: Keyboard Integrations
- Enter to send for thread replies and chat messages.
- Ctrl/Cmd+Enter to save for long-form areas (new thread, edit, profile).
- Escape to cancel editing.
- Visual `.kbd-hint` badges on action buttons.
- MentionTextarea delegates keyboard events correctly (mention dropdown takes priority).

### Feature 7: Service Consolidation (7 -> 2+1)
- Consolidated Auth + User + Notification into **Core** service (port 8001).
- Consolidated Forum + Moderation + Chat into **Community** service (port 8002).
- Gateway route map updated for 2 backend services.
- Docker Compose rewritten for consolidated topology.
- ADR-0001 documents the decision and rationale.
- HLD and LLD documentation created for the consolidated architecture.

### Feature 8: Activity / Audit Logs
- `AuditLog` model (`audit_logs` table) with actor, action, entity type/id, details, IP address.
- Shared audit service at `services/shared/shared/services/audit.py` with `record()` (same-transaction) and `list_audit_logs()` (paginated, role-based visibility).
- 29 action constants covering threads, posts, users, profiles, friends, moderation, communities, and chat.
- Admin sees all logs; moderator sees own + member actions; member sees own only.
- Backend instrumented: auth (register/login), forum (thread/post CRUD), admin (role changes, suspend/ban, lock/pin, reports, mod actions, category management), chat (room create), user (profile update, avatar upload, friend request send/accept/decline).
- `GET /api/v1/admin/audit-logs` route with `page`, `page_size`, `action`, `entity_type`, `actor_id` query params.
- Frontend "Activity Log" tab (7th tab) in Admin Dashboard with dropdown filters (action type, entity type), color-coded action badges, paginated log entries, dark/light theme support.
- Reuses existing `Pagination` component and `formatTimeAgo()` from `timeUtils.js`.
- 10 dedicated audit tests (`test_audit.py`) covering record creation, endpoint access, role-based visibility, filters, and pagination.

### Feature 9: Reddit-Style Frontend Redesign
- Complete CSS rewrite (~3,260 lines in `global.css`): Reddit dark theme (`#030303` bg, `#1a1a1b` cards, `#FF4500` accent), full light theme (`#dae0e6` canvas, white cards), system fonts.
- Top navbar replaces left sidebar (`MainLayout.jsx`): brand logo, search bar, notification bell, theme toggle, user menu.
- Card-style post feed with vote column on left, compact metadata (`ThreadCard.jsx`, `HomePage.jsx`).
- Reddit-style nested comment threads with border-left collapse lines (`ThreadPage.jsx`).
- Right sidebar with community info panel, rules, trending communities (`HomePage.jsx`).
- All JSX files rewritten with correct CSS class names matching the new design system.
- Pages updated: HomePage, ThreadPage, ChatPage, DashboardPage, ProfilePage, PeoplePage, AdminPage, LoginPage.
- Components updated: ThreadCard, UserIdentity, NotificationCenter, UserActionModal, MentionTextarea, Pagination, AttachmentList.
- Custom SVG logo (`frontend/public/logo.svg`) used as navbar brand and favicon.
- Pulse bot avatar (`frontend/public/pulse-avatar.svg`) — orange robot head with pulse wave detail.
- Google Fonts removed from `index.html` — now uses system fonts.

### Feature 10: UX Improvements
- **Avatar upload fix**: Gateway proxies `/uploads/*` to Core service instead of serving static files locally. Eliminates filesystem-sync issues between Docker containers.
- **Login prompt for guests**: `LoginPrompt` component shows a styled banner with "Log In" button when unauthenticated users attempt protected actions (voting, replying, reacting). Replaces silent no-ops.
- **Notification button differentiation**: "Enable browser notifications" uses bell-with-slash icon (`🔕`) + pulsing glow animation, distinct from the notification center bell (`🔔`).
- **Vote score readability**: Increased vote score font to `text-sm` (13px) with `font-weight: 800`, widened vote column to 48px.
- **Create Post prominence**: Orange circle `+` icon, 2px accent border on hover, `font-weight: 600`.
- **@pulse bot in threads**: Bot is now triggered by `@pulse` mentions in thread creation (not just replies). Previously only worked in post/reply creation.
- **@mention autocomplete fix**: Fixed CSS class name mismatches (`.mention-textarea-wrapper` -> `.mention-wrapper`, `.mention-dropdown-item` -> `.mention-item`, etc.) that prevented the dropdown from rendering properly.
- **Locked thread badge**: Added `.thread-pill-muted` CSS for visually distinguishing "Locked" badges from "Pinned" badges.
- **Pagination active state**: Fixed active page button highlight (changed `pagination-active` to `active` to match `.pagination-btn.active` CSS selector).
- **Panel header**: Added `.panel-header` CSS for password reset and email verification pages.

### Feature 11: Comprehensive Seed/Dummy Data Script
- `services/seed.py` — standalone script that populates the database with realistic demo data for showcasing the platform.
- **16 users** (1 admin, 2 moderators, 12 members, 1 bot) — all with password `password123`.
- **8 categories**: General Discussion, Backend Engineering, Frontend Engineering, DevOps and Deployment, Show and Tell, Feedback and Suggestions, Off-Topic, Help and Support.
- **20 tags**: python, fastapi, react, docker, postgresql, redis, javascript, css, websocket, jwt, oauth, testing, performance, security, deployment, beginner, discussion, bug, feature-request, tutorial.
- **22 threads** with realistic, lengthy discussion content across all categories.
- **138 posts** including deeply nested reply chains (2-3 levels deep).
- **769 votes** (weighted 85% upvote / 15% downvote) + **129 reactions** (emoji).
- **5 chat rooms** (3 group + 2 direct) with **57 messages**.
- **18 friend requests** (13 accepted, 4 pending, 1 declined).
- **5 content reports** (2 pending, 2 resolved, 1 dismissed) + **2 moderation actions** (warnings).
- **4 category requests** (2 pending, 1 approved, 1 rejected).
- **15 notifications** of various types (reply, mention, friend request, report, mod warning).
- **30 audit log entries** covering registration, role changes, category creation, moderation.
- Welcome thread pinned; Community Guidelines thread pinned + locked.
- Idempotent: checks for existing admin user before seeding; safe to run multiple times.
- Supports both SQLite (local dev, `--sqlite` flag) and PostgreSQL (Docker, via `DATABASE_URL_OVERRIDE`).
- Run: `python services/seed.py` (local) or `docker compose exec core python /shared/../seed.py` (Docker).

### Feature 12: Full-Stack Bug Fix Audit
- **User report persistence**: `POST /users/{id}/report` now creates a `ContentReport` row (was only creating notifications). Added duplicate check and audit logging. `_resolve_report_content` in admin_services.py handles `entity_type="user"`. `list_reports` filter shows user reports to all staff.
- **Notification commit bugs**: `update_thread` and `update_post` in `forum_services.py` now call `db.commit()` after creating notifications (previously notifications were created after the main commit and never persisted).
- **Bot race conditions**: `schedule_forum_bot_reply` and `schedule_chat_bot_reply` are now called AFTER `db.commit()` in `create_thread`, `create_post`, and `create_chat_message`. Previously the bot background thread could start before the triggering message was committed.
- **Vote score invisible in dark mode**: Added `background: none; border: none; color: inherit;` to `.vote-score` and `.vote-score-clickable` in global.css.
- **Voters popover positioning**: Added `position: relative` to `.vote-controls`, changed popover from `top: 100%` to `bottom: 100%` (opens above), added `max-height`, `overflow-y: auto`, z-index 50.
- **Avatar upload Docker permissions**: Added `RUN mkdir -p /app/uploads && chown -R appuser:appgroup /app/uploads` before `USER appuser` in all 3 Dockerfiles.
- **Notification enable button**: Removed pulsing 🔕 button from navbar; moved desktop notification toggle to ProfilePage Preferences panel.
- **OAuth avatar URLs**: `ProfilePage.jsx` now guards `avatar_url` with `startsWith('http')` check before calling `assetUrl()`, preventing broken URLs for OAuth users with external avatar URLs.
- **VoteRequest schema**: Added `field_validator` to reject `value=0` (previously `ge=-1, le=1` allowed zero which is meaningless).
- **AuthContext stale closure**: Converted `refreshProfile` from inline function to `useCallback` with proper deps, added to `useMemo` dependency array.
- **AdminPage initial tab**: Admin users no longer start on the wrong tab on first render (profile is null initially so `isAdmin` was false). Added `useEffect` to set correct tab once profile loads.
- **ChatPage send button**: Changed `type="submit"` to `type="button"` (button is not inside a `<form>`).

### Feature 13: File Upload Hardening + GIF Support
- **GIF support**: `image/gif` was already in `ALLOWED_CONTENT_TYPES`; now enforced end-to-end with magic-byte validation, extension whitelist, and frontend `accept` filters.
- **Magic-byte file validation**: `storage.py` now reads the first 32 bytes of every upload and verifies against known file signatures (JPEG `FF D8 FF`, PNG `89 50 4E 47`, GIF `GIF89a`/`GIF87a`, WebP `RIFF..WEBP`, MP4 `ftyp`, WebM EBML header, PDF `%PDF`). Prevents MIME-type spoofing.
- **File extension whitelist**: `ALLOWED_EXTENSIONS` set (`.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`, `.mp4`, `.webm`, `.pdf`, `.txt`, `.doc`, `.docx`). Extension must match the declared MIME type via `_EXTENSION_MIME_MAP`.
- **Filename sanitization**: `_sanitize_filename()` strips path components (prevents directory traversal), replaces unsafe characters with underscores, collapses repeated underscores.
- **Frontend file picker filters**: All 4 `<input type="file">` elements now have `accept` attributes: avatar uses `AVATAR_ACCEPT` (images only incl. GIF), attachments use `ATTACHMENT_ACCEPT` (images + videos + documents).
- **Client-side file validation**: New `frontend/src/lib/uploadUtils.js` with `validateFile()` — checks file size (25 MB max), MIME type, and extension before upload. Error messages shown to user immediately.
- **Upload entity_type whitelist**: `upload_routes.py` now rejects `linked_entity_type` values outside `{draft, thread, post, message, avatars}`.

### Feature 14: Input Validation & Security Hardening
- **XSS sanitization**: New `services/shared/shared/services/sanitize.py` with `sanitize_text()` (strips HTML tags, escapes entities, removes `javascript:`/`data:`/`vbscript:` URIs, removes `onerror=` event handlers) and `sanitize_username()` (alphanumeric + underscore only).
- **All Pydantic schemas sanitized**: `field_validator` decorators on every user-text field: thread title/body, post body, chat message body, chat room name, bio, report reason, moderation reason, category title/description, tag name.
- **Username pattern enforcement**: `RegisterRequest` and `UserUpdateRequest` now require `pattern=r"^[a-zA-Z0-9_]+$"` with `sanitize_username()` validator.
- **Admin schema validation hardened**: `RoleUpdateRequest.role` restricted to `^(admin|moderator|member)$`, `ReportResolveRequest.status` to `^(resolved|dismissed)$`, `ModerationActionRequest.action_type` to `^(warn|suspend|ban)$`, `CategoryRequestReviewRequest.status` to `^(approved|rejected)$`.
- **Admin field constraints added**: `ModerationActionRequest.reason` gets `min_length=3, max_length=2000`, `duration_hours` gets `ge=1, le=8760` (max 1 year), `CategoryRequestCreate` gets `min_length`/`max_length`/`pattern` on all fields, `CategoryModeratorRequest` fields get `ge=1`.
- **List field bounds**: `attachment_ids` capped at 20 items (threads, posts, chat), `tag_names` at 10 items (threads), `member_ids` at 50 items (chat rooms).
- **Search query length limit**: `q` parameter in search endpoint now has `max_length=200`.
- **Search type validation**: `content_type` parameter restricted to `^(thread|post)$`.
- **OAuth provider validation**: `OAuthExchangeRequest.provider` restricted to `^(google|github)$`, `code` capped at 2048 chars.
- **Token field lengths**: `RefreshTokenRequest.refresh_token`, `VerifyEmailRequest.token`, `ResetPasswordRequest.token` all get `min_length=1, max_length=512`.
- **Category ID validation**: `ThreadCreateRequest.category_id` gets `ge=1`.
- **Security headers middleware**: New `SecurityHeadersMiddleware` added to all 3 services — sets `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `X-XSS-Protection: 1; mode=block`, `Referrer-Policy: strict-origin-when-cross-origin`, `Content-Security-Policy` (restrictive), `Permissions-Policy`, and `Cache-Control: no-store` for authenticated responses.
- **Rate limiting**: New `RateLimitMiddleware` with sliding-window per-IP counter — applied to `/api/v1/auth/` endpoints on Gateway (20 req/min) and Core (20 req/min). Returns 429 with `Retry-After` header.

---

*End of AGENTS.md -- Please keep this file updated as new rules or conventions are adopted.*

# API Reference

All HTTP endpoints are prefixed with `/api/v1`. WebSocket endpoints use the `/ws/` prefix. The API gateway on port 8000 proxies requests to the appropriate service.

---

## Auth

Service: **core** (port 8001)

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/api/v1/auth/register` | Register a new user account | No |
| POST | `/api/v1/auth/login` | Authenticate and obtain access + refresh tokens | No |
| POST | `/api/v1/auth/refresh` | Refresh an expired access token | No |
| GET | `/api/v1/auth/oauth/{provider}/login` | Get OAuth authorization URL (google/github) | No |
| GET | `/api/v1/auth/oauth/{provider}/callback` | OAuth callback redirect | No |
| POST | `/api/v1/auth/oauth/exchange` | Exchange OAuth authorization code for app tokens | No |
| POST | `/api/v1/auth/verify-email` | Verify email address via token | No |
| POST | `/api/v1/auth/resend-verification` | Resend verification email | Yes |
| POST | `/api/v1/auth/forgot-password` | Request password reset email | No |
| POST | `/api/v1/auth/reset-password` | Reset password using token | No |

---

## Users

Service: **core** (port 8001)

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/v1/users/me` | Get current user's profile | Yes |
| PATCH | `/api/v1/users/me` | Update current user's profile | Yes |
| POST | `/api/v1/users/me/avatar` | Upload/replace avatar image | Yes |
| GET | `/api/v1/users` | List all users (excluding self) | Yes |
| GET | `/api/v1/users/friends` | List friendships and pending requests | Yes |
| GET | `/api/v1/users/search` | Search users by username | Yes |
| POST | `/api/v1/users/friends/{request_id}/accept` | Accept friend request | Yes |
| POST | `/api/v1/users/friends/{request_id}/decline` | Decline friend request | Yes |
| POST | `/api/v1/users/{user_id}/report` | Report a user | Yes |
| POST | `/api/v1/users/{user_id}/friend` | Send friend request | Yes |
| GET | `/api/v1/users/lookup/{username}` | Look up user by username | Yes |
| GET | `/api/v1/users/{user_id}` | Get user's public profile | Yes |

---

## Uploads

Service: **core** (port 8001)

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/v1/uploads/limits` | Get allowed upload types and max file size | No |
| POST | `/api/v1/uploads` | Upload a file attachment | Yes |

---

## Categories

Service: **community** (port 8002)

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/v1/categories` | List all forum categories | No |
| POST | `/api/v1/categories` | Create a new category | Admin |

---

## Threads

Service: **community** (port 8002)

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/v1/threads` | List threads (filter by category, sort, time range, tag) | No |
| POST | `/api/v1/threads` | Create a new thread | Yes |
| GET | `/api/v1/threads/{thread_id}` | Get full thread detail with posts | No |
| PATCH | `/api/v1/threads/{thread_id}` | Update thread title/body/tags | Yes |
| DELETE | `/api/v1/threads/{thread_id}` | Delete a thread | Yes |
| GET | `/api/v1/threads/{thread_id}/posts` | List all posts in a thread | No |
| POST | `/api/v1/threads/{thread_id}/posts` | Create a reply in a thread | Yes |
| POST | `/api/v1/threads/{thread_id}/subscribe` | Toggle thread subscription | Yes |
| POST | `/api/v1/threads/{thread_id}/vote` | Cast upvote or downvote on thread | Yes |
| DELETE | `/api/v1/threads/{thread_id}/vote` | Remove vote from thread | Yes |
| POST | `/api/v1/threads/{thread_id}/react` | Toggle emoji reaction on thread | Yes |
| POST | `/api/v1/threads/{thread_id}/report` | Report thread for moderation | Yes |
| GET | `/api/v1/threads/{thread_id}/voters` | List users who voted on thread | No |

---

## Posts

Service: **community** (port 8002)

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/v1/posts/{post_id}` | Get a single post | No |
| PATCH | `/api/v1/posts/{post_id}` | Edit a post's body | Yes |
| DELETE | `/api/v1/posts/{post_id}` | Delete a post | Yes |
| POST | `/api/v1/posts/{post_id}/vote` | Cast upvote or downvote on post | Yes |
| DELETE | `/api/v1/posts/{post_id}/vote` | Remove vote from post | Yes |
| POST | `/api/v1/posts/{post_id}/react` | Toggle emoji reaction on post | Yes |
| GET | `/api/v1/posts/{post_id}/voters` | List users who voted on post | No |
| POST | `/api/v1/posts/{post_id}/report` | Report post for moderation | Yes |

---

## Search

Service: **community** (port 8002)

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/v1/search` | Search threads and posts by query, category, author, type, tag | No |

---

## Notifications

Service: **core** (port 8001)

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/v1/notifications` | List current user's notifications | Yes |
| PATCH | `/api/v1/notifications/{notification_id}/read` | Mark one notification as read | Yes |
| PATCH | `/api/v1/notifications/read-all` | Mark all notifications as read | Yes |

---

## Chat

Service: **community** (port 8002)

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/v1/chat/rooms` | List chat rooms the user belongs to | Yes |
| POST | `/api/v1/chat/rooms` | Create a group chat room | Yes |
| POST | `/api/v1/chat/direct/{target_username}` | Create or retrieve a DM room | Yes |
| POST | `/api/v1/chat/rooms/{room_id}/members` | Join a chat room | Yes |
| GET | `/api/v1/chat/rooms/{room_id}` | Get chat room details | Yes |
| GET | `/api/v1/chat/rooms/{room_id}/messages` | List messages in a room | Yes |
| POST | `/api/v1/chat/rooms/{room_id}/messages` | Send a message in a room | Yes |

---

## Admin / Moderation

Service: **community** (port 8002). All endpoints require staff role (admin or moderator) unless noted otherwise.

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/v1/admin/summary` | Admin dashboard summary stats | Staff |
| GET | `/api/v1/admin/users` | List users for admin management | Staff |
| PATCH | `/api/v1/admin/users/{user_id}/role` | Change user's role | Admin |
| PATCH | `/api/v1/admin/users/{user_id}/suspend` | Suspend a user | Staff |
| PATCH | `/api/v1/admin/users/{user_id}/unsuspend` | Unsuspend a user | Staff |
| PATCH | `/api/v1/admin/users/{user_id}/ban` | Ban a user | Admin |
| PATCH | `/api/v1/admin/users/{user_id}/unban` | Unban a user | Admin |
| POST | `/api/v1/admin/users/{user_id}/moderate` | Issue moderation action (warn/suspend/ban) | Staff |
| GET | `/api/v1/admin/threads` | List threads for moderation | Staff |
| PATCH | `/api/v1/admin/threads/{thread_id}/lock` | Lock a thread | Staff |
| PATCH | `/api/v1/admin/threads/{thread_id}/unlock` | Unlock a thread | Staff |
| PATCH | `/api/v1/admin/threads/{thread_id}/pin` | Pin a thread | Staff |
| PATCH | `/api/v1/admin/threads/{thread_id}/unpin` | Unpin a thread | Staff |
| GET | `/api/v1/admin/reports` | List content reports | Staff |
| PATCH | `/api/v1/admin/reports/{report_id}/resolve` | Resolve or dismiss a report | Staff |
| GET | `/api/v1/admin/category-moderators/{user_id}` | Get moderator's category assignments | Admin |
| POST | `/api/v1/admin/category-moderators` | Assign category moderator | Admin |
| DELETE | `/api/v1/admin/category-moderators` | Remove category moderator | Admin |
| POST | `/api/v1/admin/category-requests` | Submit category creation request | Staff |
| GET | `/api/v1/admin/category-requests` | List category requests | Staff |
| PATCH | `/api/v1/admin/category-requests/{request_id}/review` | Approve or reject category request | Admin |
| GET | `/api/v1/admin/audit-logs` | Paginated activity/audit log (query: `page`, `page_size`, `action`, `entity_type`, `actor_id`) | Staff |

---

## WebSocket Endpoints

Handled directly by the gateway (not proxied to backend services).

| Path | Description | Auth |
|------|-------------|------|
| `/ws/notifications` | Per-user notification push (`?token=JWT`) | Yes |
| `/ws/threads/{thread_id}` | Thread live updates (posts, votes, reactions) | No |
| `/ws/chat/{room_id}` | Real-time chat messages (`?token=JWT`) | Yes |
| `/ws/global` | App-wide events (e.g. new category created) | No |

---

## Health Check

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/health` | Returns `{"status": "ok"}` | No |

---

## Summary

| Domain | Endpoints |
|--------|-----------|
| Auth | 10 |
| Users | 12 |
| Uploads | 2 |
| Categories | 2 |
| Threads | 13 |
| Posts | 8 |
| Search | 1 |
| Notifications | 3 |
| Chat | 7 |
| Admin / Moderation | 22 |
| WebSockets | 4 |
| Health | 1 |
| **Total** | **85** |

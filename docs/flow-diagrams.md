# Flow Diagrams

Mermaid sequence diagrams for PulseBoard's core workflows.

---

## 1. Registration and Email Verification

```mermaid
sequenceDiagram
    actor User
    participant FE as Frontend
    participant API as Core Service
    FE->>API: POST /api/v1/auth/register
    API->>DB: INSERT users (is_verified=false)
    API->>DB: INSERT email_verification_tokens
    API->>Mail: Send verification email (smtplib, timeout=2)
    API-->>FE: 201 {"message": "Account created. Please check your email..."}
    FE-->>User: Show "check email" message

    User->>Mail: Click verification link
    Mail-->>FE: Redirect to /verify-email?token=xxx
    FE->>API: POST /api/v1/auth/verify-email {token}
    API->>DB: UPDATE users SET is_verified=true
    API->>DB: UPDATE email_verification_tokens SET used_at=now()
    API-->>FE: 200 {"message": "Email verified successfully."}
    FE-->>User: Redirect to /login
```

---

## 2. Login and Token Refresh

```mermaid
sequenceDiagram
    actor User
    participant FE as Frontend
    participant API as Core Service
    participant DB as PostgreSQL

    User->>FE: Enter email + password
    FE->>API: POST /api/v1/auth/login
    API->>DB: SELECT user WHERE email=...
    API->>API: Verify password (pbkdf2_sha256)
    API->>API: Check is_verified, is_active, is_banned
    API->>DB: INSERT refresh_tokens
    API->>API: Create JWT access_token + refresh_token
    API-->>FE: 200 {access_token, refresh_token, token_type}
    FE->>FE: Store session in localStorage
    FE->>API: GET /api/v1/users/me (Authorization: Bearer)
    API-->>FE: 200 {user profile}
    FE-->>User: Redirect to /dashboard

    Note over FE,API: Later, when access_token expires...

    FE->>API: POST /api/v1/auth/refresh {refresh_token}
    API->>DB: SELECT refresh_tokens WHERE token_id=...
    API->>API: Validate not expired, not revoked
    API->>DB: UPDATE old token SET revoked_at=now()
    API->>DB: INSERT new refresh_token
    API-->>FE: 200 {new access_token, new refresh_token}
```

---

## 3. OAuth Flow (Google / GitHub)

```mermaid
sequenceDiagram
    actor User
    participant FE as Frontend
    participant API as Core Service
    participant OAuth as Google / GitHub
    participant DB as PostgreSQL

    User->>FE: Click "Sign in with Google"
    FE->>API: GET /api/v1/auth/oauth/google/login
    API-->>FE: 200 {authorization_url}
    FE->>OAuth: Redirect to authorization_url

    User->>OAuth: Grant consent
    OAuth-->>API: GET /api/v1/auth/oauth/google/callback?code=xxx
    API-->>FE: Redirect to /login?code=xxx&provider=google

    FE->>API: POST /api/v1/auth/oauth/exchange {code, provider}
    API->>OAuth: Exchange code for OAuth access_token
    OAuth-->>API: {access_token, user_info}
    API->>DB: Find or create user + oauth_account
    API->>DB: INSERT refresh_token
    API->>API: Create JWT tokens
    API-->>FE: 200 {access_token, refresh_token}
    FE->>FE: Store session, fetch profile
```

---

## 4. Thread Creation with Real-Time Updates

```mermaid
sequenceDiagram
    actor Author
    participant FE as Frontend
    participant API as Community Service
    participant DB as PostgreSQL
    participant Redis as Redis Pub/Sub
    participant WS as WebSocket (Global)
    actor Viewers

    Author->>FE: Write thread, click submit
    FE->>API: POST /api/v1/threads {title, body, category_id, tag_names}
    API->>DB: INSERT threads
    API->>DB: INSERT thread_tags (if tags provided)
    API->>DB: INSERT thread_subscriptions (auto-subscribe author)
    API-->>FE: 201 {thread object}
    FE-->>Author: Navigate to /threads/{id}

    Note over API,WS: Not currently broadcast on thread creation
```

---

## 5. Post/Reply with Live Thread Updates

```mermaid
sequenceDiagram
    actor Author
    participant FE as Frontend
    participant API as Community Service
    participant DB as PostgreSQL
    participant Redis as Redis Pub/Sub
    participant WS as WebSocket (/ws/threads/{thread_id})
    actor Viewers

    Author->>FE: Write reply, click submit
    FE->>API: POST /api/v1/threads/{id}/posts {body, parent_post_id?}
    API->>DB: INSERT posts
    API->>API: Process @mentions
    API->>DB: INSERT notifications (for subscribers + mentioned users)
    API->>Redis: PUBLISH thread:{id} {event: post_created, post}
    API-->>FE: 201 {post object}

    Redis-->>WS: Deliver event
    WS-->>Viewers: Send {event: post_created, post}
    Viewers->>FE: useThreadLiveUpdates adds post to state

    Note over API,DB: If @pulse is mentioned, bot reply is also created
    API->>API: build_bot_reply(text) via Groq API
    API->>DB: INSERT posts (bot reply)
    API->>Redis: PUBLISH thread:{id} {event: post_created, bot_post}
```

---

## 6. Chat Message Flow

```mermaid
sequenceDiagram
    actor Sender
    participant FE as Frontend
    participant API as Community Service
    participant DB as PostgreSQL
    participant Redis as Redis Pub/Sub
    participant WS as WebSocket (/ws/chat/{room_id})
    actor Receivers

    Sender->>FE: Type message, click send
    FE->>API: POST /api/v1/chat/rooms/{room_id}/messages {body}
    API->>DB: INSERT messages
    API->>API: Process @mentions
    API->>DB: INSERT notifications (for all room members except sender)
    API->>Redis: PUBLISH chat:room:{room_id} {event: message_created, message}
    API-->>FE: 201 {message object}

    Redis-->>WS: Deliver event
    WS-->>Receivers: Send {event: message_created, message}
    Receivers->>FE: useChatRoom adds message to state

    Note over API,DB: If @pulse is mentioned, bot reply follows same path
```

---

## 7. Notification Delivery

```mermaid
sequenceDiagram
    participant API as Core Service
    participant DB as PostgreSQL
    participant Redis as Redis Pub/Sub
    participant WS as WebSocket (/ws/notifications)
    actor Recipient
    participant FE as Frontend

    API->>DB: INSERT notifications (user_id, type, title, payload)
    API->>Redis: PUBLISH notifications:{user_id} {notification}

    Redis-->>WS: Deliver to user's WebSocket
    WS-->>FE: Send notification JSON
    FE->>FE: useNotifications adds to state, updates unread count
    FE->>FE: Fire browser Notification popup (if permitted)
    FE-->>Recipient: Bell icon shows unread badge

    Recipient->>FE: Open notification drawer
    Recipient->>FE: Click "Mark all read"
    FE->>API: PATCH /api/v1/notifications/read-all
    API->>DB: UPDATE notifications SET is_read=true
```

---

## 8. Moderation Workflow

```mermaid
sequenceDiagram
    actor Reporter
    participant FE as Frontend
    participant API as Community Service
    participant DB as PostgreSQL
    participant Mail as MailHog / SMTP
    actor Staff

    Reporter->>FE: Click report on thread/post/user
    FE->>API: POST /api/v1/threads/{id}/report {reason}
    API->>DB: INSERT content_reports (status=pending)
    Note over API,DB: User reports also INSERT notifications for all staff
    API-->>FE: 201 {report}

    Staff->>FE: Open /admin, view Reports tab
    FE->>API: GET /api/v1/admin/reports?status=pending
    API->>DB: SELECT content_reports (scoped by moderator categories)
    API-->>FE: 200 [reports]

    Staff->>FE: Review report, click Resolve
    FE->>API: PATCH /api/v1/admin/reports/{id}/resolve {resolution}
    API->>DB: UPDATE content_reports SET status=resolved
    API-->>FE: 200 {updated report}

    Note over Staff,API: Optionally issue moderation action
    Staff->>FE: Select warn/suspend/ban, fill reason
    FE->>API: POST /api/v1/admin/users/{id}/moderate {action_type, reason, duration_hours?, report_id?}
    API->>DB: INSERT moderation_actions
    API->>DB: UPDATE users (set is_suspended/is_banned if applicable)
    API->>DB: INSERT notifications (to target user)
    API->>Mail: Send moderation email to target user
    API-->>FE: 200 {action result}
```

---

## 9. Category Request and Approval

```mermaid
sequenceDiagram
    actor Moderator
    participant FE as Frontend
    participant API as Community Service
    participant DB as PostgreSQL
    participant Redis as Redis Pub/Sub
    participant WS as WebSocket (/ws/global)
    actor Admin

    Moderator->>FE: Fill "Request Community" form
    FE->>API: POST /api/v1/admin/category-requests {title, slug, description}
    API->>DB: INSERT category_requests (status=pending)
    API-->>FE: 201 {request}

    Admin->>FE: Open /admin, view Community Requests tab
    FE->>API: GET /api/v1/admin/category-requests?status=pending
    API-->>FE: 200 [requests]

    Admin->>FE: Click Approve
    FE->>API: PATCH /api/v1/admin/category-requests/{id}/review {status: approved}
    API->>DB: INSERT categories (from request data)
    API->>DB: INSERT category_moderators (requester as moderator)
    API->>DB: UPDATE category_requests SET status=approved
    API->>Redis: PUBLISH global {event: category_created}
    API-->>FE: 200 {updated request}

    Redis-->>WS: Broadcast event
    WS-->>FE: All connected clients receive category_created
    FE->>FE: useGlobalUpdates triggers category list refresh
```

---

## 10. WebSocket Connection Lifecycle

```mermaid
sequenceDiagram
    actor User
    participant FE as Frontend
    participant GW as Gateway
    participant Redis as Redis Pub/Sub

    Note over FE,GW: Authenticated WebSocket (notifications, chat)
    FE->>GW: WS connect /ws/notifications?token=JWT
    GW->>GW: Validate JWT, extract user_id
    GW->>GW: Register connection in ConnectionManager
    GW->>Redis: SUBSCRIBE notifications:{user_id}

    loop On Redis message
        Redis-->>GW: Event payload
        GW-->>FE: Forward JSON to WebSocket
    end

    FE->>GW: WS close (navigate away / tab close)
    GW->>Redis: UNSUBSCRIBE notifications:{user_id}
    GW->>GW: Remove from ConnectionManager

    Note over FE,GW: Public WebSocket (threads, global)
    FE->>GW: WS connect /ws/threads/{thread_id}
    GW->>GW: Register connection (no auth)
    GW->>Redis: SUBSCRIBE thread:{thread_id}

    loop On Redis message
        Redis-->>GW: Event payload
        GW-->>FE: Forward JSON to WebSocket
    end

    FE->>GW: WS close
    GW->>Redis: UNSUBSCRIBE thread:{thread_id}
```

---

## 11. Password Reset Flow

```mermaid
sequenceDiagram
    actor User
    participant FE as Frontend
    participant API as Core Service
    participant DB as PostgreSQL
    participant Mail as MailHog / SMTP

    User->>FE: Click "Forgot Password", enter email
    FE->>API: POST /api/v1/auth/forgot-password {email}
    API->>DB: SELECT user WHERE email=...
    API->>DB: INSERT password_reset_tokens
    API->>Mail: Send reset email with token link
    API-->>FE: 200 {"message": "If an account with that email exists, a reset link has been sent."}

    User->>Mail: Click reset link
    Mail-->>FE: Redirect to /reset-password?token=xxx
    User->>FE: Enter new password
    FE->>API: POST /api/v1/auth/reset-password {token, new_password}
    API->>DB: SELECT password_reset_tokens WHERE token=...
    API->>API: Validate not expired, not used
    API->>DB: UPDATE users SET password_hash=hash(new_password)
    API->>DB: UPDATE password_reset_tokens SET used_at=now()
    API-->>FE: 200 {"message": "Password has been reset successfully."}
    FE-->>User: Redirect to /login
```

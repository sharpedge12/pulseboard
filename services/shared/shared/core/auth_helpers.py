"""
auth_helpers.py — FastAPI dependency functions for authentication and
authorization in PulseBoard.

This module lives in the **shared library** so that every microservice
(Core on port 8001, Community on port 8002, and the Gateway on port 8000)
can protect its routes with the exact same authentication logic.

How FastAPI dependency injection works (interview primer):
    FastAPI uses a "Depends()" system where route parameters are
    automatically resolved by calling the specified function.  When you
    write:

        @router.get("/me")
        def me(user: User = Depends(get_current_user)):
            return user

    FastAPI sees the Depends(), calls get_current_user() *before* the
    route handler runs, injects the return value into `user`, and — if
    get_current_user raises an HTTPException — returns the error response
    without ever entering the route body.

    This is PulseBoard's alternative to middleware-based auth: it's
    explicit (each route opts in), composable (dependencies can depend on
    other dependencies), and type-safe (the route gets a full User ORM
    object, not a raw dict).

Dependency chain for a protected route:

    HTTP request
      │
      ▼
    oauth2_scheme          ← extracts Bearer token from header
      │
      ▼
    get_current_user       ← decodes JWT, loads User from DB
      │
      ▼
    require_can_participate  ← (optional) blocks unverified/suspended
      │
      ▼
    route handler          ← receives a fully validated User object

Key design decisions:
    - **Stateless auth**: The JWT contains the user ID; we look up the
      User row on every request.  This costs one SELECT per request but
      guarantees we always see the latest role, ban status, and
      is_active flag — important because an admin might ban a user
      between token issuance and the next request.
    - **last_seen tracking**: Updated on every authenticated request so
      the frontend can show "online" indicators (green dot) for users
      active within the last 5 minutes.
    - **Generic error messages**: All auth failures return the same
      "Could not validate credentials" message to avoid leaking whether
      a user ID exists, is banned, etc. (prevents user enumeration).
"""

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.core.config import settings
from shared.core.database import get_db
from shared.core.security import safe_decode_token
from shared.models.user import User, UserRole

# --------------------------------------------------------------------------
# OAuth2 bearer token extractor
# --------------------------------------------------------------------------
# OAuth2PasswordBearer is a FastAPI "security scheme" that does one thing:
# it reads the `Authorization: Bearer <token>` header from the incoming
# request and returns the raw token string.  If the header is missing, it
# automatically returns a 401 response with a WWW-Authenticate header.
#
# The `tokenUrl` parameter does NOT change runtime behaviour — it tells
# Swagger UI / OpenAPI where the login endpoint is, so the interactive
# docs page can show a "login" button that POSTs credentials to that URL
# and stores the returned token for subsequent "Try it out" requests.
#
# In PulseBoard the login endpoint is POST /api/v1/auth/login (handled
# by the Core service).
# --------------------------------------------------------------------------
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_v1_prefix}/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    The primary authentication dependency — used by virtually every
    protected route in the application.

    Execution flow:
        1. `oauth2_scheme` extracts the Bearer token from the
           Authorization header (injected via Depends).
        2. `get_db` yields a SQLAlchemy Session (injected via Depends).
        3. We decode the JWT with safe_decode_token (returns None on
           failure instead of raising — see security.py).
        4. We verify the token is an *access* token (not a refresh token)
           to prevent token type confusion attacks.
        5. We extract the "sub" (subject) claim, which is the user's
           database ID.
        6. We look up the User row by ID.  This DB hit on every request
           is intentional — it ensures we see real-time ban/role changes.
        7. We reject banned or deactivated accounts.
        8. We update `last_seen` to power the online status feature.
        9. We return the User ORM object so the route handler can use it
           directly (e.g. user.id, user.role, user.username).

    Why not cache the user in the token?
        We *could* embed username, role, etc. in JWT claims to skip the
        DB lookup.  But then a role change or ban wouldn't take effect
        until the token expires (up to 30 minutes).  For a forum with
        moderation, that's unacceptable — a banned user could keep
        posting for half an hour.

    Args:
        token: The raw JWT string, auto-extracted from the Authorization
               header by oauth2_scheme.
        db:    A SQLAlchemy database session, auto-provided by the get_db
               dependency (yields a session, commits/rolls back after the
               request).

    Raises:
        HTTPException 401: If the token is invalid, expired, not an
                           access token, the user doesn't exist, or the
                           user is banned/inactive.

    Returns:
        The authenticated User ORM instance.
    """
    # Decode the JWT.  safe_decode_token returns None for ANY failure
    # (expired, bad signature, malformed) — we don't distinguish because
    # the client should just re-authenticate regardless of the reason.
    payload = safe_decode_token(token)

    # Guard 1: Token must decode successfully AND must be an access token.
    # Without the type check, someone could use a refresh token (which is
    # longer-lived and meant only for the /auth/refresh endpoint) as an
    # access token, effectively bypassing the short expiry window.
    if not payload or payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials.",
        )

    # Guard 2: The "sub" claim must exist and be non-empty.  This is a
    # defensive check — our create_token always sets "sub", but a
    # hand-crafted or corrupted token might not have it.
    subject = payload.get("sub")
    if not subject:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials.",
        )

    # Look up the user by primary key.  scalar_one_or_none() returns the
    # User object or None (never raises for zero results, unlike
    # scalar_one() which would raise NoResultFound).
    user = db.execute(select(User).where(User.id == int(subject))).scalar_one_or_none()

    # Guard 3: User must exist AND not be banned AND be active.
    # We use the same error message for all three cases to prevent
    # enumeration — an attacker shouldn't be able to distinguish "this
    # user ID doesn't exist" from "this user is banned".
    if not user or user.is_banned or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials.",
        )

    # --------------------------------------------------------------------------
    # Online status tracking
    # --------------------------------------------------------------------------
    # We stamp `last_seen` with the current UTC time on every authenticated
    # request.  The frontend (and UserPublicProfileResponse schema) uses this
    # to show a green "online" dot if `last_seen` is within the last 5 minutes
    # (see _is_online() in the Core service's user routes).
    #
    # This is a pragmatic approach — no WebSocket heartbeat or Redis presence
    # tracking needed.  The trade-off is a small extra DB write on every
    # request, but for a discussion forum's traffic volume, it's negligible.
    # --------------------------------------------------------------------------
    user.last_seen = datetime.now(timezone.utc)
    db.commit()

    return user


def require_roles(current_user: User, allowed_roles: set[UserRole]) -> User:
    """
    Role-Based Access Control (RBAC) check.

    PulseBoard has three roles: member, moderator, admin.  This function
    is called explicitly inside route handlers (not as a Depends) because
    different routes need different role sets:

        # Only admins can change user roles
        require_roles(current_user, {UserRole.admin})

        # Admins and moderators can lock threads
        require_roles(current_user, {UserRole.admin, UserRole.moderator})

    Why not make this a Depends() too?
        Because the allowed_roles set varies per route.  FastAPI's
        Depends() doesn't easily support parameterised dependencies
        (you'd need a factory function returning a closure).  Calling
        require_roles() directly is simpler and equally readable.

    Args:
        current_user:  The User object from get_current_user().
        allowed_roles: A set of UserRole enum values that are permitted.

    Raises:
        HTTPException 403: If the user's role is not in the allowed set.

    Returns:
        The same User object (for convenient chaining / assignment).
    """
    if current_user.role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to perform this action.",
        )
    return current_user


def require_verified_user(current_user: User) -> User:
    """
    Ensure the user has verified their email address.

    PulseBoard requires email verification before login (enforced at the
    login endpoint), but this extra check exists as a safety net for
    edge cases — e.g. if verification status is revoked after login, or
    if a future code path bypasses the login check.

    This is also used as a building block by require_can_participate(),
    which adds the suspension check on top.

    Args:
        current_user: The authenticated User from get_current_user().

    Raises:
        HTTPException 403: If the user's email is not verified.

    Returns:
        The same User object.
    """
    if not current_user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verify your email before using this feature.",
        )
    return current_user


def require_can_participate(current_user: User) -> User:
    """
    Gate for write operations — blocks unverified and suspended users.

    "Participate" means any action that creates or modifies content:
    creating threads, posting replies, sending chat messages, voting,
    reacting, etc.  Read-only operations (viewing threads, browsing
    profiles) do NOT require this check — suspended users can still
    read the forum.

    This is the strictest common authorization check.  The composition
    pattern is intentional:
        require_can_participate
          └── calls require_verified_user  (email must be verified)
          └── then checks is_suspended     (not serving a suspension)

    Suspension vs. ban:
        - **Suspended**: Temporary punishment — the user can log in and
          read, but can't post.  Checked here.
        - **Banned**: Permanent — the user can't even authenticate.
          Checked earlier in get_current_user().

    Usage in a route:
        @router.post("/threads")
        def create_thread(
            ...,
            current_user: User = Depends(get_current_user),
        ):
            require_can_participate(current_user)
            ...

    Args:
        current_user: The authenticated User from get_current_user().

    Raises:
        HTTPException 403: If the user is unverified (via
                           require_verified_user) or suspended.

    Returns:
        The same User object.
    """
    # First, ensure email is verified (delegates to require_verified_user).
    require_verified_user(current_user)

    # Then check suspension status.  Suspensions have a duration
    # (set by moderators via ModerationActionRequest.duration_hours),
    # but the expiry logic lives in the admin service — here we just
    # check the boolean flag.
    if current_user.is_suspended:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Suspended users cannot post or chat.",
        )
    return current_user

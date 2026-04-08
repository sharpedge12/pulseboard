"""
OAuth2 Authentication — Google & GitHub
=======================================

This module implements the **OAuth2 Authorization Code** flow for Google and
GitHub.  OAuth lets users log in with their existing Google/GitHub accounts
instead of creating a new username and password.

The OAuth2 Authorization Code flow has three steps:

    1. **Authorization**: We redirect the user to the provider's consent page
       (e.g. ``accounts.google.com``).  We include a ``state`` parameter
       (a random nonce) to prevent CSRF attacks.

    2. **Callback**: After the user consents, the provider redirects back to
       our ``/callback`` endpoint with an authorization ``code`` and the
       ``state``.  We forward these to the frontend.

    3. **Token Exchange**: The frontend sends the ``code`` and ``state`` to
       ``POST /oauth/exchange``.  We:
       a. Validate the ``state`` nonce (CSRF protection).
       b. Exchange the ``code`` with the provider for an access token.
       c. Use that access token to fetch the user's email and profile.
       d. Find an existing user or create a new one.
       e. Issue our own JWT tokens.

Key interview concepts:
  - **CSRF prevention via state nonce**: Without the ``state`` parameter, an
    attacker could trick a user into authorizing the attacker's account.
  - **Provider-user linking via ``OAuthAccount``**: A user can have multiple
    OAuth providers linked (Google + GitHub).  The ``provider_user_id``
    uniquely identifies the user at each provider.
  - **Create-or-link logic**: If the OAuth email matches an existing verified
    user, we link the OAuth account to that user.  If no user exists, we
    create one.  We reject linking to unverified accounts (security risk).
  - **Username collision handling**: If the provider-suggested username is
    taken, we append a random suffix.
"""

import secrets
from urllib.parse import urlencode

from fastapi import HTTPException, status
import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from shared.core.config import settings
from shared.core.security import create_access_token
from shared.models.oauth_account import OAuthAccount
from shared.models.user import User, UserRole
from shared.schemas.auth import AuthResponse, UserPreview

from app.auth_services import _create_refresh_token_record

# ---------------------------------------------------------------------------
# In-memory OAuth state store
# ---------------------------------------------------------------------------
# This stores the CSRF state nonces generated during the authorization step.
# When the user returns via the callback, we validate and consume the nonce.
#
# LIMITATION: This is an in-memory set, so it is lost on server restart and
# does not work across multiple processes.  In production, this should be
# replaced with a Redis-backed store with TTL expiration.
# ---------------------------------------------------------------------------
_oauth_state_store: set[str] = set()


# ---------------------------------------------------------------------------
# Provider configuration
# ---------------------------------------------------------------------------
# Each provider has its own URLs for authorization, token exchange, and
# user info retrieval.  ``client_id`` and ``client_secret`` are loaded
# from environment variables via ``settings``.
#
# Lambda wrappers are used because settings may not be available at import
# time (e.g. during testing with overridden env vars).
# ---------------------------------------------------------------------------

PROVIDER_CONFIG = {
    "google": {
        "client_id": lambda: settings.google_client_id,
        "client_secret": lambda: settings.google_client_secret,
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
        # OpenID Connect scopes: email + profile info.
        "scopes": ["openid", "email", "profile"],
    },
    "github": {
        "client_id": lambda: settings.github_client_id,
        "client_secret": lambda: settings.github_client_secret,
        "auth_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "userinfo_url": "https://api.github.com/user",
        # GitHub requires a separate endpoint to fetch emails.
        "emails_url": "https://api.github.com/user/emails",
        # GitHub scopes: read user profile + email addresses.
        "scopes": ["read:user", "user:email"],
    },
}


# ---------------------------------------------------------------------------
# Step 1: Build the authorization URL
# ---------------------------------------------------------------------------


def build_oauth_authorization_url(provider: str) -> str:
    """Construct the URL that the frontend redirects the user's browser to.

    This URL points to the OAuth provider's consent page (e.g. Google's
    "Choose an account" screen).  It includes:
      - ``client_id``: Identifies our application to the provider.
      - ``redirect_uri``: Where the provider sends the user back after consent.
      - ``response_type=code``: We want an authorization code (not an implicit token).
      - ``scope``: What data we're requesting (email, profile).
      - ``state``: A random CSRF nonce to validate on the callback.

    Args:
        provider: ``"google"`` or ``"github"``.

    Returns:
        The full authorization URL as a string.

    Raises:
        HTTPException 404: If the provider is not supported.
    """
    provider_config = PROVIDER_CONFIG.get(provider)
    if not provider_config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OAuth provider not supported.",
        )

    client_id = provider_config["client_id"]()
    if not client_id:
        # If OAuth is not configured (no client_id in env), redirect to
        # a frontend page that shows a "not configured" message.
        return f"{settings.oauth_frontend_success_url}?oauth_provider={provider}&status=not-configured"

    params = {
        "client_id": client_id,
        "redirect_uri": f"{settings.oauth_redirect_base}{settings.api_v1_prefix}/auth/oauth/{provider}/callback",
        "response_type": "code",  # Authorization Code flow (not Implicit).
        "scope": " ".join(provider_config["scopes"]),
        "state": _generate_oauth_state(provider),  # CSRF protection nonce.
    }
    return f"{provider_config['auth_url']}?{urlencode(params)}"


def _generate_oauth_state(provider: str) -> str:
    """Create a unique, unguessable CSRF state token for an OAuth flow.

    The state is formatted as ``{provider}:{random_nonce}`` so we can verify
    on the callback that the state belongs to the expected provider.

    The nonce is generated with ``secrets.token_urlsafe(32)`` which produces
    43 characters of cryptographically secure randomness.

    Args:
        provider: The OAuth provider name (embedded in the state).

    Returns:
        The state string to include in the authorization URL.
    """
    nonce = secrets.token_urlsafe(32)
    state = f"{provider}:{nonce}"
    _oauth_state_store.add(state)
    return state


def validate_oauth_state(state: str | None, provider: str) -> None:
    """Validate and consume the OAuth CSRF state token.

    This function implements two security checks:
      1. The state must exist in our store (it was generated by us).
      2. The state must start with the expected provider prefix (prevents
         cross-provider state reuse attacks).

    After validation, the state is discarded so it cannot be reused.

    Args:
        state: The state parameter from the OAuth callback.
        provider: The expected provider (from the URL path).

    Raises:
        HTTPException 400: If the state is missing, unknown, or mismatched.
    """
    if not state or state not in _oauth_state_store:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state parameter.",
        )
    # Consume the nonce (single-use).
    _oauth_state_store.discard(state)
    # Verify the provider prefix matches.
    if not state.startswith(f"{provider}:"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth state does not match expected provider.",
        )


# ---------------------------------------------------------------------------
# Step 3: Exchange the code for tokens and find/create the user
# ---------------------------------------------------------------------------


def exchange_oauth_code(
    db: Session,
    provider: str,
    code: str,
    state: str | None = None,
) -> AuthResponse:
    """Complete the OAuth flow: validate state, exchange code, find/create user.

    This is the main entry point called by ``POST /oauth/exchange``.

    The logic follows this decision tree:
      1. Does an ``OAuthAccount`` with this provider + provider_user_id exist?
         -> Yes: Log in as that user.
      2. Does a local user with the same email exist?
         -> Yes + verified: Link the OAuth account to the existing user.
         -> Yes + unverified: Reject (security: prevents account hijacking).
      3. No existing user:
         -> Create a new user (auto-verified since the provider confirmed
            the email) and link the OAuth account.

    Args:
        db: SQLAlchemy session.
        provider: ``"google"`` or ``"github"``.
        code: The authorization code from the provider.
        state: The CSRF state nonce (validated and consumed).

    Returns:
        AuthResponse with PulseBoard JWT tokens and user preview.

    Raises:
        HTTPException 400: Invalid state, unverified email conflict, or
            failed token exchange with the provider.
        HTTPException 404: Unsupported provider.
        HTTPException 409: Race condition during user creation.
    """
    if provider not in PROVIDER_CONFIG:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OAuth provider not supported.",
        )

    # Validate CSRF state parameter (consume the nonce).
    validate_oauth_state(state, provider)

    # Exchange the authorization code with the provider to get user info.
    identity = _resolve_oauth_identity(provider, code.strip())
    resolved_email = identity["email"]
    resolved_provider_user_id = identity["provider_user_id"]
    resolved_username = identity["username"]

    # --- Decision tree: find or create the local user ---

    # Case 1: Check if this OAuth identity is already linked to a local user.
    existing_account = db.execute(
        select(OAuthAccount).where(
            OAuthAccount.provider == provider,
            OAuthAccount.provider_user_id == resolved_provider_user_id,
        )
    ).scalar_one_or_none()

    if existing_account:
        # Returning user — just load their account.
        user = db.execute(
            select(User).where(User.id == existing_account.user_id)
        ).scalar_one()
    else:
        # Case 2: Check if a local user with the same email exists.
        user = db.execute(
            select(User).where(User.email == resolved_email)
        ).scalar_one_or_none()
        if user:
            # SECURITY: Only link OAuth to a *verified* account.
            # Without this check, an attacker could register with someone
            # else's email, skip verification, and then OAuth-link to
            # hijack the real owner's account.
            if not user.is_verified:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="An unverified account with this email exists. Please verify your email first.",
                )
        if not user:
            # Case 3: No existing user — create a new one.
            # Handle username collisions by appending a random suffix.
            username = resolved_username
            for _attempt in range(5):
                conflict = db.execute(
                    select(User).where(User.username == username)
                ).scalar_one_or_none()
                if not conflict:
                    break
                # Truncate the base username and append a random hex suffix.
                username = f"{resolved_username[:42]}_{secrets.token_hex(3)}"
            else:
                # After 5 attempts, use a longer random suffix.
                username = f"{resolved_username[:36]}_{secrets.token_hex(6)}"

            user = User(
                email=resolved_email,
                username=username,
                password_hash=None,  # OAuth users have no local password.
                role=UserRole.MEMBER,
                is_verified=True,  # Provider confirmed the email.
            )
            db.add(user)
            try:
                db.flush()  # Attempt to insert; may fail on unique constraints.
            except IntegrityError:
                db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Account creation conflict. Please try again.",
                )

        # Link the OAuth provider to the local user account.
        db.add(
            OAuthAccount(
                user_id=user.id,
                provider=provider,
                provider_user_id=resolved_provider_user_id,
                provider_email=resolved_email,
            )
        )

    # Issue PulseBoard JWT tokens for the user.
    refresh_token = _create_refresh_token_record(db, user)
    db.commit()
    db.refresh(user)

    return AuthResponse(
        access_token=create_access_token(str(user.id)),
        refresh_token=refresh_token,
        user=UserPreview(
            id=user.id,
            username=user.username,
            email=user.email,
            role=user.role.value,
            is_verified=user.is_verified,
        ),
    )


# ---------------------------------------------------------------------------
# OAuth identity resolution
# ---------------------------------------------------------------------------


def _resolve_oauth_identity(provider: str, code: str) -> dict[str, str]:
    """Exchange the authorization code with the provider and fetch user info.

    This function performs the server-to-server HTTP calls:
      1. POST to the provider's token endpoint to exchange the code for an
         access token.
      2. GET the user info endpoint with the access token to retrieve the
         user's email, name, and provider-specific user ID.

    For GitHub, the email may not be in the user profile (if set to private),
    so we fall back to the ``/user/emails`` endpoint to find the primary email.

    Args:
        provider: ``"google"`` or ``"github"``.
        code: The authorization code from the provider callback.

    Returns:
        A dict with ``email``, ``provider_user_id``, and ``username``.

    Raises:
        HTTPException 400: If the provider does not return an access token
            or the HTTP request fails.
    """
    provider_config = PROVIDER_CONFIG[provider]
    client_id = provider_config["client_id"]()
    client_secret = provider_config["client_secret"]()

    # If OAuth credentials are not configured, generate a synthetic identity.
    # This allows testing the OAuth flow without real provider credentials.
    if not client_id or not client_secret:
        return _synthetic_identity(provider, code)

    try:
        with httpx.Client(timeout=10.0) as client:
            # --- Step 1: Exchange the code for an access token ---
            token_response = client.post(
                provider_config["token_url"],
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": f"{settings.oauth_redirect_base}{settings.api_v1_prefix}/auth/oauth/{provider}/callback",
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
            )
            token_response.raise_for_status()
            token_data = token_response.json()
            access_token = token_data.get("access_token")
            if not access_token:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="OAuth provider did not return an access token.",
                )

            # --- Step 2: Fetch user info using the access token ---

            if provider == "google":
                # Google uses OpenID Connect — the userinfo endpoint returns
                # email, name, and a stable user ID (``sub``).
                userinfo = client.get(
                    provider_config["userinfo_url"],
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                userinfo.raise_for_status()
                data = userinfo.json()
                return {
                    "email": data["email"],
                    "provider_user_id": str(data["sub"]),  # Google's stable user ID.
                    # Convert display name to a URL-safe username.
                    "username": (data.get("name") or data.get("email", "google-user"))
                    .replace(" ", "_")
                    .lower()[:50],
                }

            # GitHub: Fetch the user profile.
            userinfo = client.get(
                provider_config["userinfo_url"],
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
            )
            userinfo.raise_for_status()
            data = userinfo.json()
            email = data.get("email")

            # GitHub may not include the email in the profile if it's private.
            # Fall back to the /user/emails endpoint to find the primary email.
            if not email:
                emails_response = client.get(
                    provider_config["emails_url"],
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                    },
                )
                emails_response.raise_for_status()
                emails = emails_response.json()
                # Find the primary email from the list.
                primary_email = next(
                    (item["email"] for item in emails if item.get("primary")),
                    None,
                )
                email = primary_email or f"github_{data['id']}@example.com"

            return {
                "email": email,
                "provider_user_id": str(data["id"]),  # GitHub's numeric user ID.
                "username": (data.get("login") or email.split("@")[0]).lower()[:50],
            }
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"OAuth exchange failed for {provider}.",
        ) from exc


def _synthetic_identity(provider: str, code: str) -> dict[str, str]:
    """Generate a fake OAuth identity for testing when credentials are not configured.

    This allows the OAuth flow to be tested end-to-end in development
    without real Google/GitHub API keys.  The code parameter is used as a
    seed to generate deterministic (but fake) user data.

    Args:
        provider: The OAuth provider name.
        code: The authorization code (used to derive unique values).

    Returns:
        A dict with synthetic ``email``, ``provider_user_id``, and ``username``.
    """
    return {
        "email": f"{provider}_{code[:12].lower()}@example.com",
        "provider_user_id": f"{provider}:{code[:24]}",
        "username": f"{provider}_{code[:10].lower()}",
    }

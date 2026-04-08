"""
Authentication Routes — Core Service
=====================================

This module defines the HTTP endpoints for user authentication.  It is a
**thin controller layer**: each route validates the incoming request, delegates
to a service function (``auth_services`` or ``auth_oauth``), and returns the
result.  Business logic lives in the service layer, not here.

Endpoints and the overall auth lifecycle:

    1. ``POST /register``      — Create account + send verification email.
    2. ``POST /verify-email``  — Click email link -> mark user as verified.
    3. ``POST /login``         — Exchange email+password for JWT tokens.
    4. ``POST /refresh``       — Exchange a refresh token for a new token pair.
    5. ``POST /forgot-password`` — Request a password-reset email.
    6. ``POST /reset-password``  — Submit new password with reset token.
    7. ``GET  /oauth/{provider}/login``    — Get OAuth authorization URL.
    8. ``GET  /oauth/{provider}/callback`` — OAuth provider redirects here.
    9. ``POST /oauth/exchange``            — Exchange OAuth code for JWT tokens.

The ``register -> verify -> login`` flow enforces email verification:
  - ``register_user`` hashes the password, creates the user row, and sends
    a verification email with a unique token.
  - The user clicks the link, which calls ``verify-email`` with the token.
  - Only verified users can log in; unverified users get HTTP 403.

Key interview concepts:
  - **Separation of concerns**: routes handle HTTP; services handle logic.
  - **Dependency Injection**: ``Depends(get_db)`` gives each request its own
    database session; ``Depends(get_current_user)`` extracts and validates
    the JWT from the ``Authorization`` header.
  - **HTTP status codes**: 201 for resource creation, 302 for redirects,
    401 for bad credentials, 403 for forbidden, 404 for not found.
"""

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from shared.core.config import settings
from shared.core.database import get_db
from shared.core.auth_helpers import get_current_user
from shared.models.user import User
from shared.schemas.auth import (
    AuthResponse,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    OAuthExchangeRequest,
    OAuthProviderResponse,
    RefreshTokenRequest,
    RegisterRequest,
    ResetPasswordRequest,
    VerifyEmailRequest,
)

from app.auth_services import (
    authenticate_user,
    refresh_user_tokens,
    register_user,
    request_password_reset,
    resend_verification_email,
    reset_password,
    verify_user_email,
)
from app.auth_oauth import build_oauth_authorization_url, exchange_oauth_code

router = APIRouter()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@router.post(
    "/register", response_model=MessageResponse, status_code=status.HTTP_201_CREATED
)
def register(
    payload: RegisterRequest, db: Session = Depends(get_db)
) -> MessageResponse:
    """Create a new user account.

    Flow:
      1. Validate ``payload`` (Pydantic checks email format, username pattern,
         password length).
      2. Delegate to ``register_user`` which hashes the password, inserts the
         user row, creates an email-verification token, and sends the email.
      3. Return a message instructing the user to check their inbox.

    Args:
        payload: Contains ``email``, ``username``, and ``password``.
        db: SQLAlchemy session (injected by FastAPI's DI).

    Returns:
        MessageResponse: Success message.

    Raises:
        HTTPException 400: If email or username already exists.
    """
    return register_user(db, payload)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> AuthResponse:
    """Authenticate a user with email and password.

    On success, returns a JWT access token (30-min TTL) and a refresh token
    (7-day TTL).  The access token is sent in the ``Authorization: Bearer``
    header on subsequent requests.

    Args:
        payload: Contains ``email`` and ``password``.
        db: SQLAlchemy session.

    Returns:
        AuthResponse: ``access_token``, ``refresh_token``, and ``user`` preview.

    Raises:
        HTTPException 401: Invalid credentials.
        HTTPException 403: Account banned/suspended or email not verified.
    """
    return authenticate_user(db, payload)


# ---------------------------------------------------------------------------
# Token Refresh
# ---------------------------------------------------------------------------


@router.post("/refresh", response_model=AuthResponse)
def refresh_tokens(
    payload: RefreshTokenRequest,
    db: Session = Depends(get_db),
) -> AuthResponse:
    """Exchange a valid refresh token for a new access+refresh token pair.

    This implements **token rotation**: the old refresh token is revoked and
    a new one is issued.  If a revoked token is ever reused, it signals a
    potential token theft, and the server rejects it.

    Args:
        payload: Contains ``refresh_token`` (the old refresh JWT).
        db: SQLAlchemy session.

    Returns:
        AuthResponse: Fresh token pair + user preview.

    Raises:
        HTTPException 401: Token is invalid, revoked, or expired.
    """
    return refresh_user_tokens(db, payload.refresh_token)


# ---------------------------------------------------------------------------
# OAuth — Step 1: Get the authorization URL
# ---------------------------------------------------------------------------


@router.get("/oauth/{provider}/login", response_model=OAuthProviderResponse)
def oauth_login(provider: str) -> OAuthProviderResponse:
    """Return the OAuth provider's authorization URL for the frontend to redirect to.

    This is the **first step** of the OAuth2 Authorization Code flow:
      1. Frontend calls this endpoint to get the URL.
      2. Frontend redirects the user's browser to that URL (e.g. Google's
         consent screen).
      3. After the user consents, the provider redirects back to our
         ``/callback`` endpoint with an authorization ``code``.

    Args:
        provider: ``"google"`` or ``"github"``.

    Returns:
        OAuthProviderResponse: Contains the ``authorization_url`` to redirect to.
    """
    return OAuthProviderResponse(
        provider=provider,
        authorization_url=build_oauth_authorization_url(provider),
    )


# ---------------------------------------------------------------------------
# OAuth — Step 2: Provider redirects back here with the auth code
# ---------------------------------------------------------------------------


@router.get("/oauth/{provider}/callback")
def oauth_callback(
    provider: str,
    code: str | None = None,
    state: str | None = None,
) -> RedirectResponse:
    """Handle the OAuth provider's redirect after user consent.

    The provider (Google/GitHub) redirects the user's browser to this URL
    with a ``code`` query parameter.  Rather than processing the code here
    (which would require rendering HTML), we forward the code to the
    frontend's success page as query parameters.  The frontend then calls
    ``POST /oauth/exchange`` to complete the flow.

    This two-step approach (callback -> frontend -> exchange) avoids mixing
    server-side rendering with our SPA architecture.

    Args:
        provider: ``"google"`` or ``"github"``.
        code: The authorization code from the OAuth provider.
        state: The CSRF state nonce we generated earlier (validated in exchange).

    Returns:
        RedirectResponse: HTTP 302 redirect to the frontend with code + state.
    """
    # Forward the authorization code and state to the frontend
    params = {"provider": provider}
    if code:
        params["code"] = code
    if state:
        params["state"] = state

    redirect_url = f"{settings.oauth_frontend_success_url}?{urlencode(params)}"
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)


# ---------------------------------------------------------------------------
# OAuth — Step 3: Exchange the code for JWT tokens
# ---------------------------------------------------------------------------


@router.post("/oauth/exchange", response_model=AuthResponse)
def oauth_exchange(
    payload: OAuthExchangeRequest,
    db: Session = Depends(get_db),
) -> AuthResponse:
    """Exchange an OAuth authorization code for PulseBoard JWT tokens.

    This is the **final step** of the OAuth2 flow:
      1. Validate the CSRF ``state`` nonce.
      2. Exchange the ``code`` with the provider for an access token.
      3. Fetch the user's email/profile from the provider.
      4. Find or create a local user account.
      5. Issue PulseBoard access + refresh tokens.

    Args:
        payload: Contains ``provider``, ``code``, and optional ``state``.
        db: SQLAlchemy session.

    Returns:
        AuthResponse: JWT tokens + user preview.

    Raises:
        HTTPException 400: Invalid state, failed token exchange.
        HTTPException 404: Unsupported provider.
        HTTPException 409: Account creation conflict (race condition).
    """
    return exchange_oauth_code(db, payload.provider, payload.code, payload.state)


# ---------------------------------------------------------------------------
# Email Verification
# ---------------------------------------------------------------------------


@router.post("/verify-email", response_model=MessageResponse)
def verify_email(
    payload: VerifyEmailRequest,
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Verify a user's email address using the token from the verification email.

    The token is a ``secrets.token_urlsafe(32)`` value stored in the
    ``email_verification_tokens`` table.  On success, the user's
    ``is_verified`` flag is set to True, allowing them to log in.

    Args:
        payload: Contains the ``token`` string from the email link.
        db: SQLAlchemy session.

    Returns:
        MessageResponse: Confirmation that the email was verified.

    Raises:
        HTTPException 400: Token already used or expired.
        HTTPException 404: Token not found.
    """
    verify_user_email(db, payload.token)
    return MessageResponse(message="Email verified successfully.")


@router.post("/resend-verification", response_model=MessageResponse)
def resend_verification(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Re-send the verification email for the currently authenticated user.

    Requires a valid JWT (the user registered but hasn't verified yet).
    If the email is already verified, returns HTTP 400.

    Args:
        current_user: The authenticated user (from JWT).
        db: SQLAlchemy session.

    Returns:
        MessageResponse: Confirmation that the email was re-sent.
    """
    resend_verification_email(db, current_user)
    return MessageResponse(message="Verification email has been reissued.")


# ---------------------------------------------------------------------------
# Password Reset
# ---------------------------------------------------------------------------


@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(
    payload: ForgotPasswordRequest,
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Initiate the password-reset flow by sending a reset email.

    SECURITY: Always returns the same success message regardless of whether
    the email exists.  This prevents **email enumeration** attacks where an
    attacker probes the API to discover which emails are registered.

    Args:
        payload: Contains the user's ``email``.
        db: SQLAlchemy session.

    Returns:
        MessageResponse: Generic "if that email exists..." message.
    """
    return request_password_reset(db, payload.email)


@router.post("/reset-password", response_model=MessageResponse)
def reset_password_endpoint(
    payload: ResetPasswordRequest,
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Complete the password reset by setting a new password.

    The user clicks the reset link in their email, which contains a token.
    The frontend collects the new password and submits it here along with
    the token.

    On success, the password is updated and ALL existing refresh tokens for
    that user are revoked (forcing re-login on all devices).

    Args:
        payload: Contains ``token`` and ``new_password``.
        db: SQLAlchemy session.

    Returns:
        MessageResponse: Confirmation of successful reset.

    Raises:
        HTTPException 400: Token already used or expired.
        HTTPException 404: Token or user not found.
    """
    return reset_password(db, payload.token, payload.new_password)

"""
Authentication Business Logic — Core Service
=============================================

This module contains the **service layer** for authentication.  While
``auth_routes.py`` handles HTTP concerns (status codes, request parsing),
this module handles the actual business logic:

  - **register_user**: Hash password, check uniqueness, create user, send
    verification email, record audit log.
  - **authenticate_user**: Verify credentials, check account status (banned,
    verified), issue JWT tokens.
  - **refresh_user_tokens**: Validate and rotate refresh tokens.
  - **verify_user_email**: Consume a verification token and mark the user
    as verified.
  - **request_password_reset / reset_password**: Token-based password reset
    with automatic session invalidation.

Key interview concepts:
  - **Password hashing**: We never store plaintext passwords.  ``hash_password``
    uses PBKDF2-SHA256 (via passlib), which applies a salt and thousands of
    iterations to make brute-force attacks computationally expensive.
  - **JWT token pair**: Short-lived access tokens (30 min) for API access,
    long-lived refresh tokens (7 days) stored server-side for rotation.
  - **Token rotation**: When a refresh token is used, it is revoked and a new
    one is issued.  If a revoked token is reused, it signals theft.
  - **Email verification**: Prevents fake accounts and ensures the user
    controls the email address.
  - **Audit logging**: Every security-relevant action (register, login, password
    reset) is recorded for compliance and forensics.
"""

from datetime import datetime, timezone
import secrets

from fastapi import HTTPException, status
from jose import JWTError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from shared.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from shared.models.user import (
    EmailVerificationToken,
    PasswordResetToken,
    RefreshToken,
    User,
    UserRole,
)
from shared.schemas.auth import (
    AuthResponse,
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    UserPreview,
)

from app.auth_email import (
    issue_email_verification_token,
    issue_password_reset_token,
)
from shared.services.audit import record as audit_record
from shared.services import audit as audit_actions


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime.

    Using ``timezone.utc`` ensures we never accidentally compare naive and
    aware datetimes, which would raise a TypeError in Python.
    """
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """Normalise a datetime to UTC, attaching tzinfo if it is naive.

    SQLite (used in tests) stores datetimes without timezone info.  PostgreSQL
    (used in production) stores them with timezone.  This helper unifies
    both cases so comparisons like ``expires_at <= _utcnow()`` always work.

    Args:
        value: A datetime that may or may not have tzinfo.

    Returns:
        A timezone-aware datetime in UTC.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------


def _build_auth_response(user: User, refresh_token_value: str) -> AuthResponse:
    """Construct the standard authentication response with JWT tokens.

    This is called after successful login, token refresh, and OAuth exchange.
    It creates a **new access token** from the user's ID and packages it
    with the refresh token and a user preview (subset of user fields safe
    to send to the frontend).

    Args:
        user: The authenticated SQLAlchemy User model instance.
        refresh_token_value: The raw JWT string for the refresh token.

    Returns:
        AuthResponse with access_token, refresh_token, and user preview.
    """
    return AuthResponse(
        access_token=create_access_token(str(user.id)),
        refresh_token=refresh_token_value,
        user=UserPreview(
            id=user.id,
            username=user.username,
            email=user.email,
            role=user.role.value,
            is_verified=user.is_verified,
        ),
    )


def _create_refresh_token_record(db: Session, user: User) -> str:
    """Create a refresh token and persist its metadata in the database.

    Refresh tokens are JWTs, but we also store a record in the
    ``refresh_tokens`` table so we can:
      - Revoke individual tokens (e.g. on logout or password reset).
      - Implement token rotation (revoke old, issue new).
      - Detect reuse of revoked tokens (potential theft).

    The ``token_id`` is a random identifier embedded in the JWT and stored
    in the DB.  When the client presents the refresh token, we decode it
    to get the ``token_id``, then look it up in the DB to check revocation.

    Args:
        db: SQLAlchemy session (must be flushed/committed by the caller).
        user: The user to issue the token for.

    Returns:
        The raw JWT refresh token string to send to the client.
    """
    # Generate a unique ID for this refresh token (stored both in the JWT
    # payload and in the database for lookup).
    token_id = secrets.token_urlsafe(24)

    # Create the JWT with the user's ID as ``sub`` and the token_id.
    token_value = create_refresh_token(str(user.id), token_id)

    # Persist the token metadata so we can revoke it later.
    refresh_record = RefreshToken(
        user_id=user.id,
        token_id=token_id,
        # Decode the JWT we just created to extract its expiration time.
        expires_at=datetime.fromtimestamp(
            decode_token(token_value)["exp"], tz=timezone.utc
        ),
    )
    db.add(refresh_record)
    db.flush()  # Assign an ID without committing (caller controls the transaction).
    return token_value


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_user(db: Session, payload: RegisterRequest) -> MessageResponse:
    """Create a new user account with email verification.

    This is the first step of the ``register -> verify -> login`` lifecycle.

    Steps:
      1. Check that the email and username are not already taken.
         We use an OR query to check both in a single DB round-trip.
      2. Hash the password using PBKDF2-SHA256 (never store plaintext!).
      3. Insert the user row with ``is_verified=False`` (default).
      4. Create a verification token and send the verification email.
      5. Record an audit log entry for compliance.
      6. Commit the transaction (user + token + audit log atomically).

    Args:
        db: SQLAlchemy session.
        payload: Validated registration data (email, username, password).

    Returns:
        MessageResponse instructing the user to check their email.

    Raises:
        HTTPException 400: If email or username already exists.
    """
    # Check for existing users with the same email OR username.
    existing_user = db.execute(
        select(User).where(
            or_(User.email == payload.email, User.username == payload.username)
        )
    ).scalar_one_or_none()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email or username already exists.",
        )

    # Create the user with a hashed password.  ``hash_password`` applies
    # PBKDF2-SHA256 with a random salt, producing a string like
    # ``$pbkdf2-sha256$29000$salt$hash``.
    user = User(
        email=payload.email,
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=UserRole.MEMBER,  # New users always start as regular members.
    )
    db.add(user)
    db.flush()  # Get the user.id assigned by the DB (needed for foreign keys).

    # Create and email the verification token.
    issue_email_verification_token(db, user)

    # Record an audit log entry for the registration event.
    audit_record(
        db,
        actor_id=user.id,
        action=audit_actions.USER_REGISTER,
        entity_type="user",
        entity_id=user.id,
        details={"username": user.username, "email": user.email},
    )

    # Commit everything atomically: user + verification token + audit log.
    db.commit()
    return MessageResponse(
        message="Account created. Please check your email and verify your account before logging in."
    )


# ---------------------------------------------------------------------------
# Login (Authentication)
# ---------------------------------------------------------------------------


def authenticate_user(db: Session, payload: LoginRequest) -> AuthResponse:
    """Verify credentials and issue JWT tokens.

    This function implements a **multi-gate** authentication check:
      1. Does the user exist and does the password match?
      2. Is the account banned or deactivated?
      3. Has the user verified their email?

    Each gate returns a different HTTP error to help the frontend show
    the right message.

    SECURITY NOTE: The password check uses ``verify_password`` which is
    a constant-time comparison to prevent timing attacks (an attacker
    cannot determine if the email exists by measuring response time).

    Args:
        db: SQLAlchemy session.
        payload: Contains ``email`` and ``password``.

    Returns:
        AuthResponse with JWT tokens and user preview.

    Raises:
        HTTPException 401: Bad email or password.
        HTTPException 403: Account banned/suspended or email not verified.
    """
    # Gate 1: Find user by email and verify password.
    user = db.execute(
        select(User).where(User.email == payload.email)
    ).scalar_one_or_none()
    if (
        not user
        or not user.password_hash  # OAuth-only users have no password.
        or not verify_password(payload.password, user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    # Gate 2: Check if the account is banned or deactivated.
    if user.is_banned or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account cannot sign in.",
        )

    # Gate 3: Require email verification before allowing login.
    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email before logging in. Check your inbox for the verification link.",
        )

    # All gates passed — issue tokens.
    refresh_token_value = _create_refresh_token_record(db, user)

    # Record the login event in the audit log.
    audit_record(
        db,
        actor_id=user.id,
        action=audit_actions.USER_LOGIN,
        entity_type="user",
        entity_id=user.id,
    )

    db.commit()
    db.refresh(user)  # Re-read user from DB to get any updated fields.
    return _build_auth_response(user, refresh_token_value)


# ---------------------------------------------------------------------------
# Token Refresh (Rotation)
# ---------------------------------------------------------------------------


def refresh_user_tokens(db: Session, refresh_token: str) -> AuthResponse:
    """Validate a refresh token and issue a new token pair (rotation).

    Token rotation works as follows:
      1. Decode the JWT to extract ``token_id`` and ``user_id``.
      2. Look up the token in the database.
      3. Check that it has not been revoked or expired.
      4. Revoke the current token (set ``revoked_at``).
      5. Issue a brand-new refresh token.

    If a previously-revoked token is presented, it means either:
      - A legitimate user's token was stolen and the attacker is using it.
      - The user accidentally reused an old token.
    In both cases we reject the request (the stored token has ``revoked_at`` set).

    Args:
        db: SQLAlchemy session.
        refresh_token: The raw JWT refresh token string from the client.

    Returns:
        AuthResponse with a fresh access+refresh token pair.

    Raises:
        HTTPException 401: Token is invalid, revoked, or expired.
        HTTPException 404: User no longer exists.
    """
    # Step 1: Decode the JWT.  If the signature is invalid or the token
    # is malformed, jose raises JWTError.
    try:
        payload = decode_token(refresh_token)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token.",
        ) from exc

    token_id = payload.get("token_id")
    user_id = payload.get("sub")
    token_type = payload.get("type")

    # Ensure this is actually a refresh token (not an access token).
    if token_type != "refresh" or not token_id or not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token.",
        )

    # Step 2: Look up the token record in the database.
    stored_token = db.execute(
        select(RefreshToken).where(RefreshToken.token_id == token_id)
    ).scalar_one_or_none()

    # Step 3: Check for revocation (token reuse detection).
    if not stored_token or stored_token.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked.",
        )

    # Step 4: Check expiration.
    if _as_utc(stored_token.expires_at) <= _utcnow():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has expired.",
        )

    # Verify the user still exists (they could have been deleted).
    user = db.execute(select(User).where(User.id == int(user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
        )

    # Step 5: Revoke the old token and issue a new one (rotation).
    stored_token.revoked_at = _utcnow()
    new_refresh_token_value = _create_refresh_token_record(db, user)

    db.commit()
    db.refresh(user)
    return _build_auth_response(user, new_refresh_token_value)


# ---------------------------------------------------------------------------
# Email Verification
# ---------------------------------------------------------------------------


def verify_user_email(db: Session, token: str) -> User:
    """Consume a verification token and mark the user's email as verified.

    Verification tokens are single-use: once ``used_at`` is set, the token
    cannot be reused.  Tokens also expire after 24 hours.

    Args:
        db: SQLAlchemy session.
        token: The ``secrets.token_urlsafe(32)`` value from the email link.

    Returns:
        The updated User model with ``is_verified=True``.

    Raises:
        HTTPException 400: Token already used or expired.
        HTTPException 404: Token or user not found.
    """
    # Look up the token in the database.
    verification_token = db.execute(
        select(EmailVerificationToken).where(EmailVerificationToken.token == token)
    ).scalar_one_or_none()
    if not verification_token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Verification token not found.",
        )

    # Prevent reuse of already-consumed tokens.
    if verification_token.used_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification token has already been used.",
        )

    # Check expiration (tokens are valid for 24 hours).
    if _as_utc(verification_token.expires_at) <= _utcnow():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification token has expired.",
        )

    # Find the user associated with this token.
    user = db.execute(
        select(User).where(User.id == verification_token.user_id)
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
        )

    # Mark the user as verified and consume the token.
    user.is_verified = True
    verification_token.used_at = _utcnow()

    db.commit()
    db.refresh(user)
    return user


def resend_verification_email(db: Session, user: User) -> None:
    """Re-issue and re-send the email verification token.

    Called when a logged-in but unverified user requests a new verification
    email (e.g. the original expired or went to spam).

    Args:
        db: SQLAlchemy session.
        user: The currently authenticated user (must not be already verified).

    Raises:
        HTTPException 400: If the email is already verified.
    """
    if user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is already verified.",
        )
    # ``issue_email_verification_token`` deletes any existing unused tokens
    # for this user, creates a new one, and sends the email.
    issue_email_verification_token(db, user)
    db.commit()


# ---------------------------------------------------------------------------
# Password Reset
# ---------------------------------------------------------------------------


def request_password_reset(db: Session, email: str) -> MessageResponse:
    """Initiate a password reset by creating a token and emailing it.

    SECURITY: This function always returns the same success message regardless
    of whether the email exists in the database.  This prevents **email
    enumeration attacks** where an attacker submits different emails and
    observes the response to determine which ones are registered.

    We also skip the reset for OAuth-only accounts (no ``password_hash``),
    since they authenticate via Google/GitHub and have no password to reset.

    Args:
        db: SQLAlchemy session.
        email: The email address to send the reset link to.

    Returns:
        MessageResponse: Generic "if that email exists..." message.
    """
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user and user.password_hash:
        issue_password_reset_token(db, user)
        db.commit()
    # Always return the same message to prevent email enumeration.
    return MessageResponse(
        message="If an account with that email exists, a password reset link has been sent."
    )


def reset_password(db: Session, token: str, new_password: str) -> MessageResponse:
    """Validate the reset token and update the user's password.

    After the password is changed, ALL existing refresh tokens for the user
    are revoked.  This is a critical security measure: if the password was
    reset because of a compromise, we need to invalidate any sessions the
    attacker may have established.

    Args:
        db: SQLAlchemy session.
        token: The reset token from the email link.
        new_password: The user's chosen new password.

    Returns:
        MessageResponse confirming the password was reset.

    Raises:
        HTTPException 400: Token already used or expired.
        HTTPException 404: Token or user not found.
    """
    # Look up the reset token in the database.
    reset_token = db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token == token)
    ).scalar_one_or_none()
    if not reset_token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reset token not found.",
        )

    # Prevent reuse of already-consumed tokens.
    if reset_token.used_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset token has already been used.",
        )

    # Check expiration (reset tokens are valid for 1 hour).
    if _as_utc(reset_token.expires_at) <= _utcnow():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset token has expired.",
        )

    user = db.execute(
        select(User).where(User.id == reset_token.user_id)
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
        )

    # Update the password hash and consume the reset token.
    user.password_hash = hash_password(new_password)
    reset_token.used_at = _utcnow()

    # Invalidate all existing refresh tokens so stolen sessions can't
    # persist after a password change.  This forces the user (and any
    # attacker) to re-authenticate with the new password.
    active_tokens = (
        db.execute(
            select(RefreshToken).where(
                RefreshToken.user_id == user.id,
                RefreshToken.revoked_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    for rt in active_tokens:
        rt.revoked_at = _utcnow()

    db.commit()
    return MessageResponse(
        message="Password has been reset successfully. You can now log in."
    )

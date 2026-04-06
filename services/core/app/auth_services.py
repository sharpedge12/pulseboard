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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _build_auth_response(user: User, refresh_token_value: str) -> AuthResponse:
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
    token_id = secrets.token_urlsafe(24)
    token_value = create_refresh_token(str(user.id), token_id)
    refresh_record = RefreshToken(
        user_id=user.id,
        token_id=token_id,
        expires_at=datetime.fromtimestamp(
            decode_token(token_value)["exp"], tz=timezone.utc
        ),
    )
    db.add(refresh_record)
    db.flush()
    return token_value


def register_user(db: Session, payload: RegisterRequest) -> MessageResponse:
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

    user = User(
        email=payload.email,
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=UserRole.MEMBER,
    )
    db.add(user)
    db.flush()
    issue_email_verification_token(db, user)
    audit_record(
        db,
        actor_id=user.id,
        action=audit_actions.USER_REGISTER,
        entity_type="user",
        entity_id=user.id,
        details={"username": user.username, "email": user.email},
    )
    db.commit()
    return MessageResponse(
        message="Account created. Please check your email and verify your account before logging in."
    )


def authenticate_user(db: Session, payload: LoginRequest) -> AuthResponse:
    user = db.execute(
        select(User).where(User.email == payload.email)
    ).scalar_one_or_none()
    if (
        not user
        or not user.password_hash
        or not verify_password(payload.password, user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    if user.is_banned or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account cannot sign in.",
        )

    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email before logging in. Check your inbox for the verification link.",
        )

    refresh_token_value = _create_refresh_token_record(db, user)
    audit_record(
        db,
        actor_id=user.id,
        action=audit_actions.USER_LOGIN,
        entity_type="user",
        entity_id=user.id,
    )
    db.commit()
    db.refresh(user)
    return _build_auth_response(user, refresh_token_value)


def refresh_user_tokens(db: Session, refresh_token: str) -> AuthResponse:
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

    if token_type != "refresh" or not token_id or not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token.",
        )

    stored_token = db.execute(
        select(RefreshToken).where(RefreshToken.token_id == token_id)
    ).scalar_one_or_none()
    if not stored_token or stored_token.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked.",
        )

    if _as_utc(stored_token.expires_at) <= _utcnow():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has expired.",
        )

    user = db.execute(select(User).where(User.id == int(user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
        )

    stored_token.revoked_at = _utcnow()
    new_refresh_token_value = _create_refresh_token_record(db, user)
    db.commit()
    db.refresh(user)
    return _build_auth_response(user, new_refresh_token_value)


def verify_user_email(db: Session, token: str) -> User:
    verification_token = db.execute(
        select(EmailVerificationToken).where(EmailVerificationToken.token == token)
    ).scalar_one_or_none()
    if not verification_token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Verification token not found.",
        )

    if verification_token.used_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification token has already been used.",
        )

    if _as_utc(verification_token.expires_at) <= _utcnow():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification token has expired.",
        )

    user = db.execute(
        select(User).where(User.id == verification_token.user_id)
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
        )

    user.is_verified = True
    verification_token.used_at = _utcnow()
    db.commit()
    db.refresh(user)
    return user


def resend_verification_email(db: Session, user: User) -> None:
    if user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is already verified.",
        )
    issue_email_verification_token(db, user)
    db.commit()


def request_password_reset(db: Session, email: str) -> MessageResponse:
    """Initiate a password reset. Always returns success to avoid email enumeration."""
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user and user.password_hash:
        issue_password_reset_token(db, user)
        db.commit()
    return MessageResponse(
        message="If an account with that email exists, a password reset link has been sent."
    )


def reset_password(db: Session, token: str, new_password: str) -> MessageResponse:
    """Validate the reset token and update the user's password."""
    reset_token = db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token == token)
    ).scalar_one_or_none()
    if not reset_token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reset token not found.",
        )

    if reset_token.used_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset token has already been used.",
        )

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

    user.password_hash = hash_password(new_password)
    reset_token.used_at = _utcnow()

    # Invalidate all existing refresh tokens so stolen sessions can't
    # persist after a password change.
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

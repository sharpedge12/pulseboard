from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.core.config import settings
from shared.core.database import get_db
from shared.core.security import safe_decode_token
from shared.models.user import User, UserRole

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_v1_prefix}/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Decode JWT, look up user, reject banned/inactive accounts."""
    payload = safe_decode_token(token)
    if not payload or payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials.",
        )

    subject = payload.get("sub")
    if not subject:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials.",
        )

    user = db.execute(select(User).where(User.id == int(subject))).scalar_one_or_none()
    if not user or user.is_banned or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials.",
        )

    # Update last_seen on every authenticated request
    user.last_seen = datetime.now(timezone.utc)
    db.commit()

    return user


def require_roles(current_user: User, allowed_roles: set[UserRole]) -> User:
    """Raise 403 if user role is not in allowed_roles."""
    if current_user.role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to perform this action.",
        )
    return current_user


def require_verified_user(current_user: User) -> User:
    """Raise 403 if user email is not verified."""
    if not current_user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verify your email before using this feature.",
        )
    return current_user


def require_can_participate(current_user: User) -> User:
    """Raise 403 if user is unverified or suspended."""
    require_verified_user(current_user)
    if current_user.is_suspended:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Suspended users cannot post or chat.",
        )
    return current_user

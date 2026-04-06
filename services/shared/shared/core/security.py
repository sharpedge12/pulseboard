import warnings
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore", message="'crypt' is deprecated", category=DeprecationWarning
    )
    from passlib.context import CryptContext

from shared.core.config import settings

password_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(password: str) -> str:
    return password_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return password_context.verify(plain_password, hashed_password)


def create_token(
    subject: str,
    expires_delta: timedelta,
    token_type: str = "access",
    extra_claims: dict[str, Any] | None = None,
) -> str:
    expire_at = datetime.now(timezone.utc) + expires_delta
    payload: dict[str, Any] = {"sub": subject, "exp": expire_at, "type": token_type}
    if extra_claims:
        payload.update(extra_claims)
    payload["iat"] = int(datetime.now(UTC).timestamp())
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def create_access_token(subject: str) -> str:
    return create_token(
        subject, timedelta(minutes=settings.access_token_expire_minutes)
    )


def create_refresh_token(subject: str, token_id: str) -> str:
    return create_token(
        subject,
        timedelta(days=settings.refresh_token_expire_days),
        token_type="refresh",
        extra_claims={"token_id": token_id},
    )


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])


def safe_decode_token(token: str) -> dict[str, Any] | None:
    try:
        return decode_token(token)
    except JWTError:
        return None

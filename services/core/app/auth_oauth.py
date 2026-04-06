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

# In-memory store for OAuth state nonces.  In a multi-process deployment
# this should be replaced with a Redis-backed store, but for a single
# gateway process this is sufficient.
_oauth_state_store: set[str] = set()


PROVIDER_CONFIG = {
    "google": {
        "client_id": lambda: settings.google_client_id,
        "client_secret": lambda: settings.google_client_secret,
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
        "scopes": ["openid", "email", "profile"],
    },
    "github": {
        "client_id": lambda: settings.github_client_id,
        "client_secret": lambda: settings.github_client_secret,
        "auth_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "userinfo_url": "https://api.github.com/user",
        "emails_url": "https://api.github.com/user/emails",
        "scopes": ["read:user", "user:email"],
    },
}


def build_oauth_authorization_url(provider: str) -> str:
    provider_config = PROVIDER_CONFIG.get(provider)
    if not provider_config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OAuth provider not supported.",
        )

    client_id = provider_config["client_id"]()
    if not client_id:
        return f"{settings.oauth_frontend_success_url}?oauth_provider={provider}&status=not-configured"

    params = {
        "client_id": client_id,
        "redirect_uri": f"{settings.oauth_redirect_base}{settings.api_v1_prefix}/auth/oauth/{provider}/callback",
        "response_type": "code",
        "scope": " ".join(provider_config["scopes"]),
        "state": _generate_oauth_state(provider),
    }
    return f"{provider_config['auth_url']}?{urlencode(params)}"


def _generate_oauth_state(provider: str) -> str:
    """Create a unique, unguessable CSRF state token for an OAuth flow."""
    nonce = secrets.token_urlsafe(32)
    state = f"{provider}:{nonce}"
    _oauth_state_store.add(state)
    return state


def validate_oauth_state(state: str | None, provider: str) -> None:
    """Validate and consume the OAuth CSRF state token."""
    if not state or state not in _oauth_state_store:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state parameter.",
        )
    _oauth_state_store.discard(state)
    if not state.startswith(f"{provider}:"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth state does not match expected provider.",
        )


def exchange_oauth_code(
    db: Session,
    provider: str,
    code: str,
    state: str | None = None,
) -> AuthResponse:
    if provider not in PROVIDER_CONFIG:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OAuth provider not supported.",
        )

    # Validate CSRF state parameter
    validate_oauth_state(state, provider)

    identity = _resolve_oauth_identity(provider, code.strip())
    resolved_email = identity["email"]
    resolved_provider_user_id = identity["provider_user_id"]
    resolved_username = identity["username"]

    existing_account = db.execute(
        select(OAuthAccount).where(
            OAuthAccount.provider == provider,
            OAuthAccount.provider_user_id == resolved_provider_user_id,
        )
    ).scalar_one_or_none()

    if existing_account:
        user = db.execute(
            select(User).where(User.id == existing_account.user_id)
        ).scalar_one()
    else:
        user = db.execute(
            select(User).where(User.email == resolved_email)
        ).scalar_one_or_none()
        if user:
            # C4: Only link OAuth to an existing account if the user has
            # verified their email.  Without this check an attacker can
            # register with someone else's email, skip verification, and
            # then OAuth-link to hijack the real owner's account.
            if not user.is_verified:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="An unverified account with this email exists. Please verify your email first.",
                )
        if not user:
            # C5: Handle username collisions by appending a random suffix
            username = resolved_username
            for _attempt in range(5):
                conflict = db.execute(
                    select(User).where(User.username == username)
                ).scalar_one_or_none()
                if not conflict:
                    break
                username = f"{resolved_username[:42]}_{secrets.token_hex(3)}"
            else:
                username = f"{resolved_username[:36]}_{secrets.token_hex(6)}"

            user = User(
                email=resolved_email,
                username=username,
                password_hash=None,
                role=UserRole.MEMBER,
                is_verified=True,
            )
            db.add(user)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Account creation conflict. Please try again.",
                )

        db.add(
            OAuthAccount(
                user_id=user.id,
                provider=provider,
                provider_user_id=resolved_provider_user_id,
                provider_email=resolved_email,
            )
        )

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


def _resolve_oauth_identity(provider: str, code: str) -> dict[str, str]:
    provider_config = PROVIDER_CONFIG[provider]
    client_id = provider_config["client_id"]()
    client_secret = provider_config["client_secret"]()
    if not client_id or not client_secret:
        return _synthetic_identity(provider, code)

    try:
        with httpx.Client(timeout=10.0) as client:
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

            if provider == "google":
                userinfo = client.get(
                    provider_config["userinfo_url"],
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                userinfo.raise_for_status()
                data = userinfo.json()
                return {
                    "email": data["email"],
                    "provider_user_id": str(data["sub"]),
                    "username": (data.get("name") or data.get("email", "google-user"))
                    .replace(" ", "_")
                    .lower()[:50],
                }

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
                primary_email = next(
                    (item["email"] for item in emails if item.get("primary")),
                    None,
                )
                email = primary_email or f"github_{data['id']}@example.com"

            return {
                "email": email,
                "provider_user_id": str(data["id"]),
                "username": (data.get("login") or email.split("@")[0]).lower()[:50],
            }
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"OAuth exchange failed for {provider}.",
        ) from exc


def _synthetic_identity(provider: str, code: str) -> dict[str, str]:
    return {
        "email": f"{provider}_{code[:12].lower()}@example.com",
        "provider_user_id": f"{provider}:{code[:24]}",
        "username": f"{provider}_{code[:10].lower()}",
    }

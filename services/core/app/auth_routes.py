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


@router.post(
    "/register", response_model=MessageResponse, status_code=status.HTTP_201_CREATED
)
def register(
    payload: RegisterRequest, db: Session = Depends(get_db)
) -> MessageResponse:
    return register_user(db, payload)


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> AuthResponse:
    return authenticate_user(db, payload)


@router.post("/refresh", response_model=AuthResponse)
def refresh_tokens(
    payload: RefreshTokenRequest,
    db: Session = Depends(get_db),
) -> AuthResponse:
    return refresh_user_tokens(db, payload.refresh_token)


@router.get("/oauth/{provider}/login", response_model=OAuthProviderResponse)
def oauth_login(provider: str) -> OAuthProviderResponse:
    return OAuthProviderResponse(
        provider=provider,
        authorization_url=build_oauth_authorization_url(provider),
    )


@router.get("/oauth/{provider}/callback")
def oauth_callback(
    provider: str,
    code: str | None = None,
    state: str | None = None,
) -> RedirectResponse:
    params = {"provider": provider}
    if code:
        params["code"] = code
    if state:
        params["state"] = state

    redirect_url = f"{settings.oauth_frontend_success_url}?{urlencode(params)}"
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)


@router.post("/oauth/exchange", response_model=AuthResponse)
def oauth_exchange(
    payload: OAuthExchangeRequest,
    db: Session = Depends(get_db),
) -> AuthResponse:
    return exchange_oauth_code(db, payload.provider, payload.code, payload.state)


@router.post("/verify-email", response_model=MessageResponse)
def verify_email(
    payload: VerifyEmailRequest,
    db: Session = Depends(get_db),
) -> MessageResponse:
    verify_user_email(db, payload.token)
    return MessageResponse(message="Email verified successfully.")


@router.post("/resend-verification", response_model=MessageResponse)
def resend_verification(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    resend_verification_email(db, current_user)
    return MessageResponse(message="Verification email has been reissued.")


@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(
    payload: ForgotPasswordRequest,
    db: Session = Depends(get_db),
) -> MessageResponse:
    return request_password_reset(db, payload.email)


@router.post("/reset-password", response_model=MessageResponse)
def reset_password_endpoint(
    payload: ResetPasswordRequest,
    db: Session = Depends(get_db),
) -> MessageResponse:
    return reset_password(db, payload.token, payload.new_password)

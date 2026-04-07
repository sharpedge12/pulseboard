from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from shared.services.sanitize import sanitize_text, sanitize_username


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def clean_username(cls, v: str) -> str:
        return sanitize_username(v)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserPreview(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: EmailStr
    role: str
    is_verified: bool


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserPreview


class OAuthProviderResponse(BaseModel):
    provider: str
    authorization_url: str


class OAuthCallbackPayload(BaseModel):
    code: str | None = None
    state: str | None = None


class OAuthExchangeRequest(BaseModel):
    provider: str = Field(pattern=r"^(google|github)$")
    code: str = Field(min_length=1, max_length=2048)
    state: str | None = None


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=512)


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class MessageResponse(BaseModel):
    message: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=8, max_length=128)

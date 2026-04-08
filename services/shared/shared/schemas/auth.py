"""
Authentication & Authorization Schemas
=======================================

This module defines Pydantic models for every auth-related API endpoint:
registration, login, token refresh, email verification, password reset,
and OAuth (Google/GitHub).

**Interview Concept: Why validate auth inputs so strictly?**

Authentication is the #1 attack surface in any web application.  Weak
validation here can lead to:

- **Credential stuffing** — Without length limits, attackers can send
  enormous payloads to overload the server (DoS via large password).
- **SQL injection / XSS** — Unsanitized usernames could contain malicious
  scripts or SQL fragments that execute when displayed or queried.
- **Token forgery** — Without length bounds on tokens, attackers could
  probe with absurdly large values to exploit buffer issues.

Every field in these schemas has explicit ``min_length`` / ``max_length``
constraints — this is a defense-in-depth practice.

**Interview Concept: Request vs Response schemas**

Notice the pattern: ``*Request`` schemas validate incoming client data,
while ``*Response`` schemas control what the server sends back.  This
separation ensures that sensitive fields (like raw passwords) never
accidentally appear in API responses.
"""

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from shared.services.sanitize import sanitize_text, sanitize_username


class RegisterRequest(BaseModel):
    """
    Schema for user registration (POST /api/v1/auth/register).

    Validates the three required fields for creating a new account.
    Each field has strict constraints to prevent abuse:

    - ``email``: Uses Pydantic's ``EmailStr`` type which runs a full
      RFC 5322 email validation (via the ``email-validator`` library).
      This catches typos like "user@" or "user@.com" before they hit
      the database.

    - ``username``: Constrained to 3-50 characters, alphanumeric + underscores
      only.  The regex ``^[a-zA-Z0-9_]+$`` is enforced at the Pydantic level
      AND double-checked by the ``sanitize_username`` validator.  This
      prevents usernames like ``<script>alert(1)</script>`` or ``../admin``.

    - ``password``: 8-128 characters.  The min ensures basic password strength;
      the max prevents denial-of-service via extremely long passwords (since
      password hashing is CPU-intensive, a 1MB password would be expensive
      to hash).
    """

    email: EmailStr
    username: str = Field(min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(min_length=8, max_length=128)

    # -- Field Validator --
    # Even though the regex pattern already restricts characters, this validator
    # provides an extra sanitization pass: stripping whitespace and removing any
    # non-alphanumeric/underscore characters that might slip through encoding
    # tricks (e.g., Unicode homoglyphs).  Defense-in-depth: never trust a single
    # layer of validation.
    @field_validator("username")
    @classmethod
    def clean_username(cls, v: str) -> str:
        return sanitize_username(v)


class LoginRequest(BaseModel):
    """
    Schema for user login (POST /api/v1/auth/login).

    Only requires email + password.  The same length constraints from
    RegisterRequest apply here — we don't want to accept a 1GB password
    string even at login time, since it still gets compared against the
    hash.
    """

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserPreview(BaseModel):
    """
    Lightweight user snapshot embedded inside AuthResponse.

    **Interview Concept: ``ConfigDict(from_attributes=True)``**

    This setting (formerly ``orm_mode = True`` in Pydantic v1) tells
    Pydantic to read values from object attributes instead of requiring
    a plain dict.  This is essential for bridging SQLAlchemy models →
    Pydantic schemas.  Without it, ``UserPreview.model_validate(user_orm)``
    would fail because SQLAlchemy objects aren't dicts.

    Note: This only includes safe-to-expose fields.  The password hash,
    ``is_suspended``, ``is_banned``, and other internal fields are
    intentionally excluded.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: EmailStr
    role: str
    is_verified: bool


class AuthResponse(BaseModel):
    """
    Response returned after successful login or registration.

    **Interview Concept: JWT Token Structure**

    This response follows the OAuth 2.0 Bearer Token pattern:
    - ``access_token`` — Short-lived JWT (30 min) used in the
      ``Authorization: Bearer <token>`` header for every API call.
    - ``refresh_token`` — Long-lived token (7 days) used to obtain a
      new access token without re-entering credentials.
    - ``token_type`` — Always "bearer"; tells the client how to use the token.
    - ``user`` — A preview of the authenticated user's profile, so the
      frontend can display the username/role immediately without an
      extra API call.
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserPreview


class OAuthProviderResponse(BaseModel):
    """
    Response containing the OAuth provider's authorization URL.

    Returned by GET /api/v1/auth/oauth/{provider} to tell the frontend
    where to redirect the user for third-party login (Google/GitHub).
    The ``authorization_url`` includes query params like ``client_id``,
    ``redirect_uri``, ``scope``, and a CSRF ``state`` token.
    """

    provider: str
    authorization_url: str


class OAuthCallbackPayload(BaseModel):
    """
    Payload received when the OAuth provider redirects back to our app.

    After the user approves access on Google/GitHub, the provider redirects
    to our callback URL with ``code`` (authorization code) and ``state``
    (CSRF token) as query parameters.  Both are optional because different
    providers may omit ``state``.
    """

    code: str | None = None
    state: str | None = None


class OAuthExchangeRequest(BaseModel):
    """
    Request to exchange an OAuth authorization code for our own JWT tokens.

    **Interview Concept: Strict enum validation with regex**

    The ``provider`` field uses ``pattern=r"^(google|github)$"`` to create
    a whitelist of exactly two allowed values.  This is safer than
    accepting any string and checking it in business logic — Pydantic
    rejects invalid providers at the schema level with a 422, before any
    code runs.  This prevents attackers from probing with values like
    ``"admin"`` or ``"../../etc/passwd"``.

    The ``code`` field is capped at 2048 characters because OAuth
    authorization codes are typically short strings.  A 2048-char limit
    prevents memory abuse from absurdly large payloads.
    """

    provider: str = Field(pattern=r"^(google|github)$")
    code: str = Field(min_length=1, max_length=2048)
    state: str | None = None


class RefreshTokenRequest(BaseModel):
    """
    Request to exchange a refresh token for a new access token.

    The ``max_length=512`` prevents abuse — real refresh tokens are
    much shorter than 512 characters, so anything longer is suspicious.
    """

    refresh_token: str = Field(min_length=1, max_length=512)


class VerifyEmailRequest(BaseModel):
    """
    Request to verify a user's email address using a token from the
    verification email.  Token length is bounded for the same security
    reason as RefreshTokenRequest.
    """

    token: str = Field(min_length=1, max_length=512)


class MessageResponse(BaseModel):
    """
    Generic single-message response used across many endpoints.

    Example: ``{"message": "Email verified successfully"}``.
    Useful for endpoints that don't return structured data but need
    to confirm an action was performed.
    """

    message: str


class ForgotPasswordRequest(BaseModel):
    """
    Request to initiate the password reset flow.

    Only requires the user's email.  The server will send a reset
    link regardless of whether the email exists (to prevent user
    enumeration attacks — an attacker shouldn't be able to discover
    which emails are registered).
    """

    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """
    Request to set a new password using a reset token from the email.

    ``new_password`` has the same 8-128 char constraints as registration.
    ``token`` is bounded to 512 chars to prevent oversized payloads.
    """

    token: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=8, max_length=128)

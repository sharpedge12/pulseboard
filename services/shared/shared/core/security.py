"""
security.py — Password hashing and JWT token management for PulseBoard.

This module is part of the **shared library** (`services/shared/`) which is
installed into every microservice (Core, Community, Gateway) via
`pip install -e services/shared`.  Because authentication logic must be
identical everywhere — the Core service mints tokens and the Gateway /
Community services verify them — this code lives in the shared package
rather than in any single service.

Two responsibilities live here:

1. **Password hashing** — one-way hashing with PBKDF2-SHA256 so that
   plaintext passwords are never stored in the database.
2. **JWT (JSON Web Token) creation & decoding** — stateless bearer tokens
   that let services authenticate requests without calling back to an
   auth server on every request.  The Core service creates tokens at
   login; every other service decodes them to identify the caller.

Interview talking points:
- Why PBKDF2 instead of bcrypt, and how passlib's CryptContext makes
  algorithm migration painless.
- Why JWTs are signed (HS256) but *not* encrypted — and why that's fine.
- The difference between access tokens (short-lived, used on every
  request) and refresh tokens (long-lived, used only to get new access
  tokens).
- The "safe decode" pattern: returning None instead of raising keeps
  calling code clean and avoids try/except boilerplate in every route.
"""

import warnings
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

# python-jose provides JWT encoding/decoding.  We use the [cryptography]
# extra (`python-jose[cryptography]`) which delegates the actual crypto to
# the well-audited `cryptography` C library rather than a pure-Python
# fallback.  JWTError is the base exception for any decode failure —
# expired signature, tampered payload, wrong algorithm, etc.
from jose import JWTError, jwt

# --------------------------------------------------------------------------
# passlib + crypt deprecation suppression
# --------------------------------------------------------------------------
# passlib is a mature password-hashing library that abstracts away hash
# algorithm details.  Internally, some passlib code paths import Python's
# built-in `crypt` module (a thin wrapper around the POSIX crypt(3) call).
# Starting with Python 3.13 that module is deprecated and triggers a
# DeprecationWarning on import.
#
# We suppress the warning because:
#   1. We don't use crypt ourselves — we use PBKDF2, which passlib
#      implements in pure Python + C-extensions (no crypt module needed).
#   2. The warning is noisy in logs and test output but has zero impact on
#      correctness or security.
#   3. The passlib maintainers are aware; the fix will come in a future
#      passlib release.
#
# The `with warnings.catch_warnings()` context manager ensures the filter
# is temporary — it only applies to the import statement inside the block,
# so we don't accidentally hide warnings from other modules.
# --------------------------------------------------------------------------
with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore", message="'crypt' is deprecated", category=DeprecationWarning
    )
    from passlib.context import CryptContext

# Application-wide settings (secret key, algorithm, token lifetimes, etc.)
# loaded once from environment variables / .env via pydantic-settings.
from shared.core.config import settings

# --------------------------------------------------------------------------
# Password hashing context
# --------------------------------------------------------------------------
# CryptContext is passlib's high-level API.  It manages:
#   - Which hash algorithm(s) are acceptable ("schemes").
#   - Which scheme is the *current default* for new hashes.
#   - Automatic re-hashing of passwords stored with an older/deprecated
#     scheme the next time a user logs in ("deprecated='auto'").
#
# Why PBKDF2-SHA256 instead of bcrypt?
#   - bcrypt has a 72-byte password limit (silently truncates longer input).
#   - bcrypt's passlib backend depends on the `crypt` module (see above),
#     which is being removed from the Python stdlib.
#   - PBKDF2-SHA256 has no length limit, ships with Python's hashlib, and
#     is NIST-recommended (SP 800-132).  It is slightly faster per hash
#     than bcrypt, but the default iteration count (600 000 in passlib)
#     keeps brute-force cost high enough for a web app.
#
# If we ever want to migrate to argon2 or scrypt, we just add the new
# scheme to the list and set deprecated=["pbkdf2_sha256"].  Existing users
# are transparently re-hashed on next successful login — zero downtime,
# no migration script needed.
# --------------------------------------------------------------------------
password_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(password: str) -> str:
    """
    Hash a plaintext password for storage in the database.

    Called once during user registration (and again if the user changes
    their password).  The returned string includes the algorithm
    identifier, salt, iteration count, and hash — everything passlib
    needs to verify the password later.

    Example output: "$pbkdf2-sha256$29000$salt$hash"

    Returns:
        The full passlib-encoded hash string (safe to store in a VARCHAR
        column).
    """
    return password_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Compare a plaintext password against its stored hash.

    Called at login time.  passlib extracts the algorithm, salt, and
    iteration count from `hashed_password`, re-hashes `plain_password`
    with the same parameters, and compares the results in constant time
    (to prevent timing side-channel attacks).

    Args:
        plain_password:  The password the user just typed into the login
                         form.
        hashed_password: The hash string stored in the `users` table.

    Returns:
        True if the password matches, False otherwise.
    """
    return password_context.verify(plain_password, hashed_password)


# =========================================================================
# JWT Token Management
# =========================================================================
# JWTs are the backbone of PulseBoard's stateless authentication.  After
# login, the Core service returns an access token + refresh token.  The
# frontend stores them (access in memory, refresh in an httpOnly cookie or
# localStorage) and attaches the access token as a Bearer token in the
# Authorization header on every API request.
#
# The Gateway, Core, and Community services can all decode the token
# independently — no inter-service "who is this user?" call is needed.
# This is a key advantage of JWTs in a microservice architecture.
# =========================================================================


def create_token(
    subject: str,
    expires_delta: timedelta,
    token_type: str = "access",
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """
    Build and sign a JWT with the given claims.

    This is the low-level token factory.  Higher-level helpers
    (create_access_token, create_refresh_token) call this with the
    appropriate lifetimes and types.

    Args:
        subject:        The value for the "sub" (subject) claim — by
                        convention, the user's database primary key as a
                        string.  Using the ID rather than the username
                        means a username change doesn't invalidate tokens.
        expires_delta:  How long until the token expires (e.g. 30 minutes
                        for access, 7 days for refresh).
        token_type:     "access" or "refresh" — stored in the "type"
                        claim so the server can reject a refresh token
                        used as an access token (or vice versa).
        extra_claims:   Any additional key-value pairs to embed in the
                        token (e.g. "token_id" for refresh token
                        revocation).

    Returns:
        A compact JWS string (header.payload.signature) encoded with
        HS256.

    Token anatomy (the "payload" part, base64-decoded):
        {
          "sub": "42",           # user ID — the subject
          "exp": 1714000000,     # expiration timestamp (UTC)
          "type": "access",      # prevents token type confusion
          "iat": 1713990000,     # issued-at timestamp (UTC)
          ...extra_claims
        }

    Why HS256 (HMAC-SHA256)?
        HS256 is a *symmetric* algorithm — the same secret_key signs and
        verifies.  This is simpler than RS256 (asymmetric) because all
        our services share the same secret via environment variables.
        In a zero-trust multi-tenant system you'd use RS256 so that only
        the auth server holds the private key, but for PulseBoard's
        internal microservice mesh, HS256 is appropriate and faster.
    """
    # Compute the absolute expiration time.  datetime.now(timezone.utc)
    # ensures we are not affected by the server's local timezone.
    expire_at = datetime.now(timezone.utc) + expires_delta

    # "sub" (subject) and "exp" (expiration) are registered JWT claims
    # defined in RFC 7519.  "type" is a private claim we use to
    # distinguish access tokens from refresh tokens.
    payload: dict[str, Any] = {"sub": subject, "exp": expire_at, "type": token_type}

    # Merge in any extra claims (e.g. token_id for refresh tokens).
    if extra_claims:
        payload.update(extra_claims)

    # "iat" (issued at) is another registered claim.  It lets us know
    # exactly when the token was created, which is useful for debugging
    # and for "not before" style policies.  We use int(timestamp) because
    # JWT claims should be numeric for interoperability.
    payload["iat"] = int(datetime.now(UTC).timestamp())

    # jwt.encode() serialises the header + payload as JSON, base64url-
    # encodes them, then creates an HMAC-SHA256 signature using
    # settings.secret_key.  The three parts are joined with dots to form
    # the compact JWS token string.
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def create_access_token(subject: str) -> str:
    """
    Create a short-lived access token (default: 30 minutes).

    Access tokens are sent on *every* API request in the Authorization
    header.  They are intentionally short-lived so that if one is leaked
    (e.g. via a log file or XSS), the window of exploitation is small.

    The lifetime is configured via settings.access_token_expire_minutes
    (loaded from the ACCESS_TOKEN_EXPIRE_MINUTES env var, default 30).

    Args:
        subject: The user's database ID as a string.

    Returns:
        A signed JWT string with token_type="access".
    """
    return create_token(
        subject, timedelta(minutes=settings.access_token_expire_minutes)
    )


def create_refresh_token(subject: str, token_id: str) -> str:
    """
    Create a long-lived refresh token (default: 7 days).

    Refresh tokens are used *only* to obtain a new access token once the
    current one expires — they are never sent to regular API endpoints.
    This separation limits exposure: the refresh token is transmitted far
    less frequently than the access token.

    The `token_id` (a UUID generated at login time) is stored both
    inside the JWT and in the `refresh_tokens` database table.  This
    enables **server-side revocation**: if a user logs out or an admin
    revokes sessions, we delete the row from `refresh_tokens`, and any
    subsequent refresh attempt with that token_id is rejected — even
    though the JWT signature is still valid.

    Args:
        subject:  The user's database ID as a string.
        token_id: A unique identifier for this refresh token, stored in
                  the DB for revocation checks.

    Returns:
        A signed JWT string with token_type="refresh" and an embedded
        "token_id" claim.
    """
    return create_token(
        subject,
        timedelta(days=settings.refresh_token_expire_days),
        token_type="refresh",
        extra_claims={"token_id": token_id},
    )


def decode_token(token: str) -> dict[str, Any]:
    """
    Decode and verify a JWT, returning its payload as a dict.

    This performs three checks automatically (via python-jose):
      1. **Signature verification** — the HMAC is recomputed and compared
         to the one in the token; any tampering is detected.
      2. **Expiration check** — if the current time is past the "exp"
         claim, a JWTError (ExpiredSignatureError) is raised.
      3. **Algorithm whitelist** — we pass `algorithms=[settings.algorithm]`
         to prevent the "alg: none" attack, where an attacker crafts a
         token with algorithm="none" and no signature.  By explicitly
         requiring HS256, unsigned tokens are rejected.

    Raises:
        jose.JWTError: If the token is expired, tampered with, uses the
                       wrong algorithm, or is malformed in any way.

    Returns:
        The decoded payload dict (e.g. {"sub": "42", "exp": ..., "type":
        "access", "iat": ...}).
    """
    return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])


def safe_decode_token(token: str) -> dict[str, Any] | None:
    """
    Attempt to decode a JWT, returning None on any failure.

    This is a convenience wrapper around decode_token() that converts
    exceptions into a None return value.  It exists because most callers
    don't care *why* a token is invalid — they just need to know whether
    it is valid or not.

    Without this helper, every route / dependency that decodes a token
    would need its own try/except JWTError block.  The "safe" prefix is
    a common Python convention (like dict.get() vs dict[key]) that
    signals "this won't raise."

    Usage in auth_helpers.py:
        payload = safe_decode_token(token)
        if not payload or payload.get("type") != "access":
            raise HTTPException(401, ...)

    Returns:
        The decoded payload dict on success, or None if the token is
        expired, tampered with, or otherwise invalid.
    """
    try:
        return decode_token(token)
    except JWTError:
        return None

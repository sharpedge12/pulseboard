"""
User & Authentication Token Models
====================================

Database tables defined here:
    - "users"                      -> User (the core identity table)
    - "refresh_tokens"             -> RefreshToken (JWT refresh token tracking)
    - "email_verification_tokens"  -> EmailVerificationToken
    - "password_reset_tokens"      -> PasswordResetToken

This file is the foundation of PulseBoard's authentication and identity system.
Every other model in the system references the users table via foreign keys.

AUTHENTICATION STRATEGY (for interviews):
    PulseBoard uses JWT-based stateless authentication:
      1. User logs in with email + password (or OAuth).
      2. Server returns an ACCESS TOKEN (short-lived, 30 min) and a
         REFRESH TOKEN (long-lived, 7 days).
      3. The access token goes in the Authorization header for every request.
      4. When the access token expires, the client uses the refresh token to
         get a new access token WITHOUT re-entering credentials.
      5. Refresh tokens are stored in the database so they can be individually
         revoked (e.g., "log out all devices").

    This is a common pattern used by Google, GitHub, and most modern APIs.
    The trade-off vs. session cookies: JWTs are stateless (no server-side
    session store needed) but harder to revoke (hence the refresh token table).

ROLE-BASED ACCESS CONTROL (RBAC):
    Three roles: admin > moderator > member.
    - member:    Can create threads, post, vote, react, send friend requests.
    - moderator: Can lock/pin threads, warn/suspend users, resolve reports.
    - admin:     Full access — manage categories, promote/demote users, ban.
    The UserRole enum enforces that only valid role strings are stored.
"""

from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, DateTime, Enum as SqlEnum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.core.database import Base
from shared.models.base import TimestampMixin


class UserRole(str, Enum):
    """
    Enum defining the three authorization levels in PulseBoard.

    WHY str + Enum?
        Inheriting from both `str` and `Enum` means each member's value is a
        plain string ("admin", "moderator", "member"). This makes it:
          - JSON-serializable by default (Pydantic can dump it without custom logic)
          - Comparable with == against plain strings ("admin" == UserRole.ADMIN)
          - Storable in the DB as a VARCHAR via SqlEnum

    WHY AN ENUM INSTEAD OF A PLAIN STRING?
        An enum restricts the value to a fixed set. Without it, a bug could
        insert "Admin" (capitalized) or "superadmin" (nonexistent role) into
        the database. The enum catches these errors at the Python level AND
        at the database level (SQLAlchemy creates a CHECK constraint).
    """

    ADMIN = "admin"
    MODERATOR = "moderator"
    MEMBER = "member"


class User(TimestampMixin, Base):
    """
    The central identity model — every user of PulseBoard has exactly one row here.

    Database table: "users"

    Relationships (this user can have many...):
        - threads:              Threads authored by this user
        - posts:                Replies/comments authored by this user
        - sent_messages:        Chat messages sent by this user
        - notifications:        In-app notifications for this user
        - oauth_accounts:       Linked OAuth providers (Google, GitHub)
        - refresh_tokens:       Active refresh tokens (one per device/session)
        - sent_friend_requests: Friend requests this user initiated
        - received_friend_requests: Friend requests sent TO this user
        - thread_subscriptions: Threads this user is subscribed to for notifications
        - email_verification_tokens: Tokens sent for email verification
        - password_reset_tokens: Tokens sent for password reset

    INTERVIEW NOTES:
        - This table is the most-referenced table in the schema. Almost every
          other table has a foreign key pointing to users.id.
        - The user table has FOUR boolean flags for account state management
          (is_verified, is_active, is_suspended, is_banned). Each serves a
          distinct purpose — see inline comments below.
    """

    __tablename__ = "users"

    # ---- Primary Key --------------------------------------------------------
    # Auto-incrementing integer PK. `index=True` is redundant for a primary key
    # (PKs are always indexed), but it's explicit documentation of intent.
    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # ---- Credentials & Identity ---------------------------------------------

    # Email is unique (one account per email) and indexed for fast login lookups.
    # String(255) matches the RFC 5321 maximum email length.
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)

    # Username is the public display name. Unique so users can be @mentioned
    # unambiguously. Indexed because we search by username in mention autocomplete.
    # String(50) prevents excessively long usernames that would break UI layouts.
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)

    # WHY nullable=True for password_hash?
    # OAuth users (Google/GitHub login) may not have a password at all — they
    # authenticate via the OAuth provider. Setting password_hash to NULL for
    # these users prevents them from accidentally logging in with an empty
    # password. The login endpoint checks `if not user.password_hash: return 401`.
    #
    # WHY "password_hash" AND NOT "password"?
    # We NEVER store plaintext passwords. The column name reminds developers
    # that this is a one-way hash (pbkdf2_sha256 in this project). Even if the
    # database is compromised, attackers can't reverse the hash to get passwords.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Role defaults to MEMBER. SqlEnum creates a CHECK constraint in the database
    # so only 'admin', 'moderator', 'member' are valid values.
    role: Mapped[UserRole] = mapped_column(SqlEnum(UserRole), default=UserRole.MEMBER)

    # Bio is optional user-provided text. Text type has no length limit (unlike
    # String(N)), which is appropriate for free-form content.
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Avatar URL can be either a relative path ("/uploads/avatars/abc.jpg") for
    # locally-uploaded images, or an absolute URL ("https://lh3.google...") for
    # OAuth profile pictures. Nullable because users start without an avatar.
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ---- Account State Flags ------------------------------------------------
    # These four booleans form a state machine for account lifecycle:
    #
    #   is_verified: Has the user clicked the email verification link?
    #                Until verified, the user CANNOT log in. This prevents
    #                fake accounts and ensures we have a valid email for
    #                password resets and notifications.
    #
    #   is_active:   Soft-delete flag. Setting to False "deactivates" the
    #                account without deleting data. The user can't log in,
    #                but their posts/threads remain. This is preferred over
    #                hard DELETE because cascade-deleting all related data
    #                would destroy conversation history.
    #
    #   is_suspended: Temporary punishment by moderators. A suspended user
    #                 can't post/reply but CAN still log in and read content.
    #                 Has an expiration time (suspended_until).
    #
    #   is_banned:   Permanent ban by admins. The user is completely locked
    #                out — cannot log in at all. Unlike is_active=False
    #                (self-deactivation), a ban is a punitive action.
    #
    # WHY SEPARATE FLAGS instead of a single "status" enum?
    #   Because states can overlap: a user can be verified + suspended, or
    #   verified + banned. A single enum would require combinatorial values
    #   like "verified_suspended" which doesn't scale.

    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_suspended: Mapped[bool] = mapped_column(Boolean, default=False)

    # Nullable because most users are never suspended. When a moderator
    # suspends a user for N hours, this is set to now() + N hours. The
    # application checks: if is_suspended AND suspended_until < now(), the
    # suspension has expired and should be lifted automatically.
    suspended_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)

    # ---- Online Status Tracking ---------------------------------------------
    # last_seen is updated on EVERY authenticated request (in get_current_user
    # middleware). The frontend checks: if last_seen is within 5 minutes of
    # now, the user is "online" (green dot). Otherwise they're "offline".
    # Nullable because the user hasn't been seen yet right after registration.
    last_seen: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ---- ORM Relationships --------------------------------------------------
    # These don't create database columns — they define Python-level navigation
    # between related objects. back_populates creates a BIDIRECTIONAL link:
    # user.threads returns all threads by this user, and thread.author returns
    # the user who created it.

    threads = relationship("Thread", back_populates="author")
    posts = relationship("Post", back_populates="author")
    sent_messages = relationship("Message", back_populates="sender")
    notifications = relationship("Notification", back_populates="user")
    oauth_accounts = relationship("OAuthAccount", back_populates="user")
    refresh_tokens = relationship("RefreshToken", back_populates="user")

    # Friend requests need TWO relationships because one User row can appear
    # on either side (requester or recipient). foreign_keys disambiguates which
    # FK column each relationship follows. overlaps="requester" silences a
    # SQLAlchemy warning about overlapping relationship paths.
    sent_friend_requests = relationship(
        "FriendRequest",
        foreign_keys="FriendRequest.requester_id",
        overlaps="requester",
    )
    received_friend_requests = relationship(
        "FriendRequest",
        foreign_keys="FriendRequest.recipient_id",
        overlaps="recipient",
    )

    # cascade="all, delete-orphan" means: if a User is deleted, all their
    # ThreadSubscription rows are automatically deleted too. "delete-orphan"
    # also means: if a subscription is removed from user.thread_subscriptions
    # in Python, SQLAlchemy will DELETE it from the DB (not just unlink it).
    thread_subscriptions = relationship(
        "ThreadSubscription", back_populates="user", cascade="all, delete-orphan"
    )
    email_verification_tokens = relationship(
        "EmailVerificationToken", back_populates="user"
    )
    password_reset_tokens = relationship("PasswordResetToken", back_populates="user")


class RefreshToken(TimestampMixin, Base):
    """
    Tracks issued JWT refresh tokens so they can be individually revoked.

    Database table: "refresh_tokens"

    WHY STORE REFRESH TOKENS IN THE DB?
        Access tokens are stateless (the server never stores them — it just
        validates the signature). But refresh tokens need to be revocable:
          - "Log out everywhere" -> revoke all refresh tokens for a user
          - "Log out this device" -> revoke one specific refresh token
          - Token rotation: when a refresh token is used, the old one is
            revoked and a new one is issued (prevents token replay attacks)

        Without a DB table, there's no way to revoke a token before its
        natural expiration.

    THE TOKEN LIFECYCLE:
        1. User logs in -> new RefreshToken row created (revoked_at = NULL)
        2. Client uses refresh token -> server checks: is revoked_at NULL? is
           expires_at in the future? If both yes, issue new access token.
        3. User logs out -> set revoked_at = now() on this token.
        4. Expired tokens can be periodically cleaned up with a background job.

    Relationships:
        - user: The user this refresh token belongs to (many tokens per user,
                one per device/session).
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)

    # CASCADE: if the user is deleted, all their refresh tokens are deleted too.
    # This is correct — a deleted user shouldn't have valid tokens.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # token_id is NOT the JWT string itself — it's a unique identifier (UUID)
    # embedded inside the JWT. The server extracts the token_id from the JWT
    # and looks it up here. We don't store the raw JWT to avoid leaking it
    # if the database is compromised. Indexed for O(1) lookup on every refresh.
    token_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)

    # When this token expires naturally (even if not revoked).
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # NULL means the token is still valid. Non-NULL means it was explicitly
    # revoked (via logout or token rotation). This is a common "soft revocation"
    # pattern — the row stays for audit purposes, but the token is no longer usable.
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user = relationship("User", back_populates="refresh_tokens")


class EmailVerificationToken(TimestampMixin, Base):
    """
    One-time-use token sent via email to verify a user's email address.

    Database table: "email_verification_tokens"

    FLOW:
        1. User registers -> server creates a random token and sends an email
           with a link like: /verify-email?token=abc123
        2. User clicks the link -> server finds this token row, checks it's not
           expired and not used, then sets user.is_verified = True and
           used_at = now().

    WHY A SEPARATE TABLE (not a column on User)?
        A user might request multiple verification emails (e.g., the first one
        went to spam). Each request creates a new token. Only the most recent
        one should work, but we keep old ones for auditing. A single column on
        User would only track the latest token.

    Relationships:
        - user: The user being verified.
    """

    __tablename__ = "email_verification_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # The random token string (typically a UUID4). Unique to prevent collisions.
    # Indexed for fast lookup when the user clicks the verification link.
    token: Mapped[str] = mapped_column(String(255), unique=True, index=True)

    # Tokens expire (typically 24 hours) to limit the window for interception.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # NULL means unused. Non-NULL means the token was consumed (email verified).
    # This prevents token reuse — once used_at is set, the token is "spent".
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user = relationship("User", back_populates="email_verification_tokens")


class PasswordResetToken(TimestampMixin, Base):
    """
    One-time-use token for the "forgot password" flow.

    Database table: "password_reset_tokens"

    FLOW:
        1. User clicks "Forgot Password" and enters their email.
        2. Server creates a random token and emails a reset link:
           /reset-password?token=xyz789
        3. User clicks the link, enters a new password.
        4. Server finds this token, verifies it's valid, hashes the new
           password, updates user.password_hash, and sets used_at = now().

    SECURITY CONSIDERATIONS:
        - Short expiration (typically 1 hour) — limits attack window.
        - used_at prevents reuse — once the password is changed, the token
          is spent.
        - ondelete=CASCADE: if the user account is deleted, all reset tokens
          are cleaned up automatically.

    Relationships:
        - user: The user requesting the password reset.
    """

    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # Same pattern as EmailVerificationToken: random string, unique, indexed.
    token: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # NULL = token is still usable. Non-NULL = token was consumed.
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user = relationship("User", back_populates="password_reset_tokens")

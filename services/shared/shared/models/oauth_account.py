"""
OAuth Account Model — Linking External Identity Providers
==========================================================

Database table defined here:
    - "oauth_accounts" -> OAuthAccount (links Google/GitHub accounts to users)

WHAT IS OAUTH?
    OAuth 2.0 is a protocol that lets users log in using their existing accounts
    from other services (Google, GitHub, Facebook, etc.) instead of creating a
    new username/password. The flow:
      1. User clicks "Sign in with Google" on PulseBoard.
      2. Browser redirects to Google's login page.
      3. User authenticates with Google (PulseBoard never sees their password).
      4. Google redirects back to PulseBoard with an authorization code.
      5. PulseBoard exchanges the code for the user's Google profile (email,
         name, avatar URL, and a unique Google user ID).
      6. PulseBoard creates or links a local User account.

    This table stores the LINK between external OAuth identities and local
    PulseBoard user accounts.

WHY A SEPARATE TABLE (not columns on User)?
    A user might link MULTIPLE OAuth providers (e.g., both Google and GitHub).
    If we stored provider info as columns on User (google_id, github_id), we'd
    need to add new columns for every new provider. With a separate table, each
    linked provider is a row — adding a new provider (e.g., Discord) requires
    zero schema changes.

    This is the standard pattern used by Django-allauth, NextAuth.js, Passport.js,
    and most authentication libraries.

ACCOUNT LINKING SCENARIOS:
    1. New user, first OAuth login:
       - Create a User row (email from OAuth, no password_hash).
       - Create an OAuthAccount row linking the provider to the user.

    2. Existing user links a new provider:
       - Find the User by email.
       - Create a new OAuthAccount row for the new provider.

    3. Returning user logs in via OAuth:
       - Look up OAuthAccount by (provider, provider_user_id).
       - If found, return the linked User.
       - If not found but email matches an existing User, link them.
"""

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.core.database import Base
from shared.models.base import TimestampMixin


class OAuthAccount(TimestampMixin, Base):
    """
    Links an external OAuth provider identity to a local PulseBoard user.

    Database table: "oauth_accounts"

    Each row represents one OAuth identity linked to one user. A user can have
    multiple OAuthAccount rows (one per provider), but each provider+user_id
    combination should be globally unique (enforced at the application level).

    Relationships:
        - user: The local PulseBoard user this OAuth identity belongs to.
                Accessed via oauth_account.user in Python.

    UNIQUENESS CONSIDERATION:
        The combination (provider, provider_user_id) must be unique across the
        table — you can't link the same Google account to two different PulseBoard
        users. This is enforced at the application level (check before insert).
        In a production system, you'd add:
            UniqueConstraint("provider", "provider_user_id", name="uq_oauth_identity")
        for database-level safety.

    WHY STORE provider_email?
        The email from the OAuth provider might differ from the user's PulseBoard
        email (if they changed it). Storing it separately helps with:
          1. Account linking: find existing users by OAuth email during first login.
          2. Debugging: "which Google account is linked?"
          3. Email verification: OAuth emails are pre-verified by the provider.
    """

    __tablename__ = "oauth_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)

    # The local PulseBoard user this OAuth identity is linked to.
    # CASCADE: if the user is deleted, all their OAuth links are removed.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # The OAuth provider name: "google" or "github" (validated by Pydantic
    # schema with pattern=r"^(google|github)$").
    # Indexed because we query by provider when handling OAuth callbacks.
    provider: Mapped[str] = mapped_column(String(50), index=True)

    # The user's unique ID on the provider's system. For Google, this is the
    # "sub" claim from the OpenID Connect token. For GitHub, it's the numeric
    # user ID from the /user API.
    # This ID is STABLE — it never changes, even if the user changes their
    # email or username on the provider. That's why we use it (not email) as
    # the primary lookup key.
    # Indexed for fast lookups during OAuth login: "find the OAuthAccount
    # where provider='google' AND provider_user_id='12345'".
    provider_user_id: Mapped[str] = mapped_column(String(255), index=True)

    # The email address from the OAuth provider. Nullable because some
    # providers might not return an email (rare, but possible with GitHub
    # if the user's email is set to private). Stored for account linking
    # and debugging purposes.
    provider_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Back-reference to the User model. Enables oauth_account.user navigation.
    user = relationship("User", back_populates="oauth_accounts")

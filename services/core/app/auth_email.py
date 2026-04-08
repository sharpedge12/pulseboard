"""
Email Sending — Verification & Password Reset
==============================================

This module handles all outbound email for the Core service.  It sends two
types of emails:
  1. **Email verification** — sent after registration with a unique token URL.
  2. **Password reset** — sent when a user requests a password reset.
  3. **Moderation notices** — sent when a moderator warns/suspends/bans a user.

Key interview concepts:
  - **SMTP**: Simple Mail Transfer Protocol.  We use Python's built-in
    ``smtplib`` to connect to an SMTP server and send emails.
  - **MailHog**: In development, we use MailHog (a fake SMTP server) so emails
    are captured locally at ``http://localhost:8025`` instead of being sent
    to real inboxes.  This avoids the need for real email credentials in dev.
  - **Timeout=2**: All SMTP connections use a 2-second timeout.  This is
    critical for tests — without it, tests would hang for 30+ seconds
    waiting for a connection to a non-existent SMTP server.
  - **Fire-and-forget pattern**: If the email fails to send (SMTP server down,
    network error), we log a warning but do NOT raise an exception.  The user
    can always request a new verification/reset email.  This prevents email
    delivery failures from blocking user registration.
  - **MIME multipart**: Each email contains both a plain-text and HTML version.
    The recipient's email client chooses which one to display.
  - **Token lifecycle**: Tokens are created in the database BEFORE the email
    is sent.  If email sending fails, the token still exists and the user
    can request a resend.  Tokens expire after a set period (24h for
    verification, 1h for password reset).
"""

from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import logging
import secrets
import smtplib

from sqlalchemy.orm import Session

from shared.core.config import settings
from shared.models.user import EmailVerificationToken, PasswordResetToken, User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Email senders
# ---------------------------------------------------------------------------


def _send_verification_email(user: User, token_value: str) -> None:
    """Send a verification email with a clickable link containing the token.

    The link points to the frontend's verify-email page, which extracts the
    token from the URL and calls ``POST /api/v1/auth/verify-email``.

    Args:
        user: The user to send the email to (uses ``user.email``).
        token_value: The ``secrets.token_urlsafe(32)`` verification token.
    """
    verify_url = f"{settings.frontend_url}/verify-email?token={token_value}"

    # HTML body with a clickable link and a fallback plain-text URL.
    html_body = f"""
    <h2>Welcome to PulseBoard, {user.username}!</h2>
    <p>Please verify your email address by clicking the link below:</p>
    <p><a href="{verify_url}">Verify my email</a></p>
    <p>Or copy this URL into your browser:</p>
    <p>{verify_url}</p>
    <p>This link expires in 24 hours.</p>
    """

    # Build a MIME multipart email with both plain-text and HTML alternatives.
    # The email client will display whichever it supports best.
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "PulseBoard - Verify your email"
    msg["From"] = settings.mail_from
    msg["To"] = user.email
    msg.attach(MIMEText(f"Verify your email: {verify_url}", "plain"))
    msg.attach(MIMEText(html_body, "html"))

    # Fire-and-forget: if sending fails, log a warning but don't crash.
    # The user can always request a new verification email.
    try:
        # ``timeout=2`` prevents tests from hanging when no SMTP server is
        # running.  MailHog is used in dev; in production this would point
        # to a real SMTP relay (e.g. SendGrid, AWS SES).
        with smtplib.SMTP(settings.mail_server, settings.mail_port, timeout=2) as smtp:
            smtp.sendmail(settings.mail_from, [user.email], msg.as_string())
        logger.info("Verification email sent to %s", user.email)
    except Exception as exc:
        # Catch ALL exceptions: network errors, DNS failures, timeouts, etc.
        # We intentionally use a broad except here because email delivery
        # is non-critical — the user can retry.
        logger.warning("Could not send verification email to %s: %s", user.email, exc)


def _send_moderation_email(
    user: User, action_type: str, reason: str, moderator_username: str
) -> None:
    """Send an email notifying a user of a moderation action.

    Called when an admin/moderator warns, suspends, or bans a user.  This
    gives the user an out-of-band notification even if they can no longer
    access the platform.

    Args:
        user: The user being moderated.
        action_type: One of ``"warn"``, ``"suspend"``, ``"ban"``.
        reason: The moderator's stated reason for the action.
        moderator_username: The username of the moderator (for logging only;
            not included in the email to protect moderator identity).
    """
    action_labels = {
        "warn": "Warning",
        "suspend": "Account Suspension",
        "ban": "Account Ban",
    }
    label = action_labels.get(action_type, action_type.capitalize())

    html_body = f"""
    <h2>PulseBoard Moderation Notice</h2>
    <p>Hello {user.username},</p>
    <p>You have received a <strong>{label}</strong> on PulseBoard.</p>
    <p><strong>Reason:</strong> {reason}</p>
    <p>This action was taken by a member of our moderation team.</p>
    <p>If you believe this was a mistake, please contact the forum administrators.</p>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"PulseBoard - {label} Notice"
    msg["From"] = settings.mail_from
    msg["To"] = user.email
    msg.attach(
        MIMEText(
            f"PulseBoard Moderation Notice\n\n"
            f"You have received a {label}.\n"
            f"Reason: {reason}\n\n"
            f"If you believe this was a mistake, please contact the forum administrators.",
            "plain",
        )
    )
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(settings.mail_server, settings.mail_port, timeout=2) as smtp:
            smtp.sendmail(settings.mail_from, [user.email], msg.as_string())
        logger.info("Moderation email (%s) sent to %s", action_type, user.email)
    except Exception as exc:
        logger.warning("Could not send moderation email to %s: %s", user.email, exc)


# ---------------------------------------------------------------------------
# Token issuance
# ---------------------------------------------------------------------------


def issue_email_verification_token(db: Session, user: User) -> EmailVerificationToken:
    """Create a new email verification token and send the verification email.

    Before creating the new token, any existing unused tokens for the same
    user are deleted.  This ensures only one active verification token
    exists per user at a time, preventing confusion from multiple emails.

    The token lifecycle:
      1. Delete any existing unused tokens for this user.
      2. Generate a cryptographically secure random token (43 chars).
      3. Store the token in the database with a 24-hour expiration.
      4. Send the verification email with the token embedded in a URL.

    Args:
        db: SQLAlchemy session (caller is responsible for committing).
        user: The user to issue the token for.

    Returns:
        The created EmailVerificationToken model instance.
    """
    # Delete any existing unused tokens for this user (only one active
    # token should exist at a time).
    db.query(EmailVerificationToken).filter(
        EmailVerificationToken.user_id == user.id,
        EmailVerificationToken.used_at.is_(None),  # Only delete unused tokens.
    ).delete(synchronize_session=False)

    # Create a new token with 24-hour expiration.
    token = EmailVerificationToken(
        user_id=user.id,
        token=secrets.token_urlsafe(32),  # 43-char cryptographically random string.
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db.add(token)
    db.flush()  # Assign the DB-generated ID (caller will commit).

    logger.info("Issued email verification token for user_id=%s", user.id)

    # Send the email (fire-and-forget — failures are logged, not raised).
    _send_verification_email(user, token.token)
    return token


def _send_password_reset_email(user: User, token_value: str) -> None:
    """Send a password reset email with a clickable link containing the token.

    Similar to verification emails, but with a 1-hour expiration and a
    different frontend destination (``/reset-password``).

    Args:
        user: The user requesting the password reset.
        token_value: The reset token to embed in the URL.
    """
    reset_url = f"{settings.frontend_url}/reset-password?token={token_value}"

    html_body = f"""
    <h2>Password Reset Request</h2>
    <p>Hello {user.username},</p>
    <p>We received a request to reset your password. Click the link below to proceed:</p>
    <p><a href="{reset_url}">Reset my password</a></p>
    <p>Or copy this URL into your browser:</p>
    <p>{reset_url}</p>
    <p>This link expires in 1 hour. If you did not request a password reset, please ignore this email.</p>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "PulseBoard - Reset your password"
    msg["From"] = settings.mail_from
    msg["To"] = user.email
    msg.attach(MIMEText(f"Reset your password: {reset_url}", "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(settings.mail_server, settings.mail_port, timeout=2) as smtp:
            smtp.sendmail(settings.mail_from, [user.email], msg.as_string())
        logger.info("Password reset email sent to %s", user.email)
    except Exception as exc:
        logger.warning("Could not send password reset email to %s: %s", user.email, exc)


def issue_password_reset_token(db: Session, user: User) -> None:
    """Create a password-reset token and email it to the user.

    Password reset tokens expire after 1 hour (shorter than verification
    tokens) because a reset link is more security-sensitive — it allows
    changing the account password.

    Like verification tokens, we delete any existing unused tokens before
    creating a new one to ensure only one active reset token per user.

    Args:
        db: SQLAlchemy session (caller is responsible for committing).
        user: The user requesting the password reset.
    """
    # Delete any existing unused reset tokens for this user.
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used_at.is_(None),
    ).delete(synchronize_session=False)

    # Create a new token with 1-hour expiration.
    token = PasswordResetToken(
        user_id=user.id,
        token=secrets.token_urlsafe(32),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db.add(token)
    db.flush()

    logger.info("Issued password reset token for user_id=%s", user.id)
    _send_password_reset_email(user, token.token)

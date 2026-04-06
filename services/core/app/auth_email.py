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


def _send_verification_email(user: User, token_value: str) -> None:
    """Send a verification email via SMTP (MailHog in dev)."""
    verify_url = f"{settings.frontend_url}/verify-email?token={token_value}"

    html_body = f"""
    <h2>Welcome to PulseBoard, {user.username}!</h2>
    <p>Please verify your email address by clicking the link below:</p>
    <p><a href="{verify_url}">Verify my email</a></p>
    <p>Or copy this URL into your browser:</p>
    <p>{verify_url}</p>
    <p>This link expires in 24 hours.</p>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "PulseBoard - Verify your email"
    msg["From"] = settings.mail_from
    msg["To"] = user.email
    msg.attach(MIMEText(f"Verify your email: {verify_url}", "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(settings.mail_server, settings.mail_port, timeout=2) as smtp:
            smtp.sendmail(settings.mail_from, [user.email], msg.as_string())
        logger.info("Verification email sent to %s", user.email)
    except Exception as exc:
        logger.warning("Could not send verification email to %s: %s", user.email, exc)


def _send_moderation_email(
    user: User, action_type: str, reason: str, moderator_username: str
) -> None:
    """Send an email notifying a user of a moderation action (warn/suspend/ban)."""
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


def issue_email_verification_token(db: Session, user: User) -> EmailVerificationToken:
    db.query(EmailVerificationToken).filter(
        EmailVerificationToken.user_id == user.id,
        EmailVerificationToken.used_at.is_(None),
    ).delete(synchronize_session=False)

    token = EmailVerificationToken(
        user_id=user.id,
        token=secrets.token_urlsafe(32),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db.add(token)
    db.flush()

    logger.info("Issued email verification token for user_id=%s", user.id)
    _send_verification_email(user, token.token)
    return token


def _send_password_reset_email(user: User, token_value: str) -> None:
    """Send a password reset email via SMTP."""
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
    """Create a password-reset token and email it to the user."""
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used_at.is_(None),
    ).delete(synchronize_session=False)

    token = PasswordResetToken(
        user_id=user.id,
        token=secrets.token_urlsafe(32),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db.add(token)
    db.flush()

    logger.info("Issued password reset token for user_id=%s", user.id)
    _send_password_reset_email(user, token.token)

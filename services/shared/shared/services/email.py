"""Email sending helpers — used by auth and moderation services."""

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib

from shared.core.config import settings
from shared.models.user import User

logger = logging.getLogger(__name__)


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

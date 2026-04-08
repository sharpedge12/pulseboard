"""
SMTP Email Sending for Moderation Notices
==========================================

INTERVIEW CONTEXT:
    When a moderator takes action against a user (warn, suspend, ban),
    the user should be notified via email in addition to the in-app
    notification.  Email is a reliable out-of-band channel — even if
    the user is banned and can't log in, they can still read the email
    to understand why.

USED BY:
    - **Community service** admin routes: called after a moderation
      action (warn/suspend/ban) is recorded in the database.

WHY IN THE SHARED LAYER?
    Email sending is a cross-cutting concern.  Currently only moderation
    emails live here, but the Core service also sends emails (verification,
    password reset) — those are in Core because they contain auth-specific
    logic.  If more services needed email, this module would be extended.

DEV ENVIRONMENT — MAILHOG:
    In development, emails are sent to MailHog (a fake SMTP server) on
    port 1025.  MailHog has a web UI on port 8025 where developers can
    inspect sent emails.  This avoids accidentally sending real emails
    during development.

THE ``timeout=2`` PARAMETER:
    The SMTP connection uses a 2-second timeout.  This is intentional:
    - In production, if the mail server is down, we don't want the
      moderation action to hang indefinitely.
    - In tests, SMTP is mocked to a no-op, but if the mock fails for
      any reason, the test won't hang for 30+ seconds waiting for a
      connection that will never succeed.
    - The function catches all exceptions and logs a warning — email
      failure never blocks the primary operation (moderation action).

EMAIL FORMAT:
    Emails are sent as ``multipart/alternative`` with both plain text
    and HTML versions.  This follows email best practices:
    - HTML version has formatted content with headers and bold text
    - Plain text version is a fallback for email clients that don't
      render HTML (rare but still exists)
    - The email client chooses which version to display
"""

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
    """Send an email notifying a user of a moderation action (warn/suspend/ban).

    INTERVIEW NOTE — FIRE-AND-FORGET PATTERN:
        This function never raises exceptions to the caller.  If the
        email fails to send (SMTP down, invalid address, timeout), we
        log a warning and move on.  The moderation action itself is
        already committed to the database — email is a best-effort
        secondary notification.

        This is a common pattern for non-critical side effects:
        log the failure, don't block the primary operation.

    Args:
        user: The User object being moderated.  We use ``user.email``
            for the recipient and ``user.username`` for personalisation.
        action_type: One of ``"warn"``, ``"suspend"``, ``"ban"``.
            Mapped to human-readable labels for the email subject/body.
        reason: The moderator's stated reason for the action.  Included
            in the email body so the user understands why.
        moderator_username: The username of the moderator who took the
            action.  Currently not shown to the user in the email (to
            protect moderator identity), but logged for audit purposes.

    Side effects:
        - Opens a TCP connection to the SMTP server (MailHog in dev,
          real SMTP in production)
        - Sends an email (or logs a warning if sending fails)
        - Never raises exceptions
    """
    # Map action_type codes to human-readable email subject labels
    action_labels = {
        "warn": "Warning",
        "suspend": "Account Suspension",
        "ban": "Account Ban",
    }
    label = action_labels.get(action_type, action_type.capitalize())

    # Build the HTML version of the email
    html_body = f"""
    <h2>PulseBoard Moderation Notice</h2>
    <p>Hello {user.username},</p>
    <p>You have received a <strong>{label}</strong> on PulseBoard.</p>
    <p><strong>Reason:</strong> {reason}</p>
    <p>This action was taken by a member of our moderation team.</p>
    <p>If you believe this was a mistake, please contact the forum administrators.</p>
    """

    # Build a multipart/alternative email with both plain text and HTML.
    # The email client decides which to render (most show HTML).
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
        # timeout=2: fail fast if SMTP is unreachable (see module docstring)
        with smtplib.SMTP(settings.mail_server, settings.mail_port, timeout=2) as smtp:
            smtp.sendmail(settings.mail_from, [user.email], msg.as_string())
        logger.info("Moderation email (%s) sent to %s", action_type, user.email)
    except Exception as exc:
        # Fire-and-forget: log the failure, don't block the moderation action
        logger.warning("Could not send moderation email to %s: %s", user.email, exc)

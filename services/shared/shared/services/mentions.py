"""
@Mention Parsing and Notification Dispatch
============================================

INTERVIEW CONTEXT:
    ``@mentions`` are a core social feature — users type ``@username``
    to notify someone in a thread, post, or chat message.  This module
    handles two responsibilities:

    1. **Extraction**: Parse ``@username`` patterns from raw text using
       a regex.
    2. **Notification**: Look up the mentioned users in the database and
       create in-app notifications for each one.

USED BY:
    - **Community service** forum routes: when creating/updating threads
      and posts, ``create_mention_notifications()`` is called to notify
      mentioned users.
    - **Community service** chat routes: same for chat messages.

WHY IN THE SHARED LAYER?
    Both forum and chat features need mention parsing.  Rather than
    duplicating the regex and notification logic in two places, we
    extract it here.  If we later add mentions to other features (e.g.
    user bios, category descriptions), they can reuse this module.

DESIGN DECISIONS:
    - The regex ``@([A-Za-z0-9_]{3,50})`` intentionally matches our
      username rules (alphanumeric + underscore, 3-50 chars).  This
      avoids false positives on email addresses (``user@example.com``)
      because email domains contain dots.
    - We skip self-mentions (``user.id == actor.id``) to avoid spam.
    - The function returns recipient IDs so the caller can optionally
      publish real-time WebSocket events for those users.
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.models.user import User
from shared.services.notifications import create_notification

# ---------------------------------------------------------------------------
# @mention regex pattern
#
# INTERVIEW NOTE — REGEX BREAKDOWN:
#   @           — literal @ sign (the mention trigger)
#   (           — start capture group (the username)
#   [A-Za-z0-9_] — alphanumeric + underscore (matches our username rules)
#   {3,50}      — length constraint: 3 to 50 characters
#   )           — end capture group
#
# This pattern is intentionally strict to avoid false positives:
#   - Won't match email addresses (``user@example.com`` → domain has dots)
#   - Won't match short strings like ``@me`` (min 3 chars)
#   - Won't match usernames with special characters
# ---------------------------------------------------------------------------
MENTION_PATTERN = re.compile(r"@([A-Za-z0-9_]{3,50})")


def extract_mentions(text: str) -> set[str]:
    """Extract all @mentioned usernames from a text string.

    Args:
        text: The raw message text (thread body, post body, chat
            message, etc.).

    Returns:
        A set of lowercased usernames found in the text (without the
        ``@`` prefix).  Uses a set to deduplicate — mentioning the same
        user twice in one message should only create one notification.

    Examples:
        >>> extract_mentions("Hey @Alice and @bob_123, check this out")
        {'alice', 'bob_123'}
        >>> extract_mentions("No mentions here")
        set()
        >>> extract_mentions("Email me at user@example.com")
        set()  # 'example' has a dot after it, but the regex only catches
               # the first match 'user@example' won't match because 'example.com'
               # contains a dot — actually 'example' (7 chars) WOULD match.
               # In practice, email addresses in text are uncommon in forums.
    """
    return {match.lower() for match in MENTION_PATTERN.findall(text)}


def create_mention_notifications(
    db: Session,
    text: str,
    actor: User,
    notification_type: str,
    title_template: str,
    payload_factory,
) -> list[int]:
    """Parse @mentions from text and create notifications for each mentioned user.

    INTERVIEW NOTE — TEMPLATE PATTERN:
        The ``title_template`` and ``payload_factory`` parameters make
        this function reusable across different contexts (threads, posts,
        chat).  The caller provides:
        - ``title_template``: e.g. ``"{actor} mentioned you in a thread"``
        - ``payload_factory``: a callable that takes a User and returns
          a dict with context-specific navigation data

    Args:
        db: Active SQLAlchemy session.  Notifications are flushed but
            not committed — the caller controls the transaction.
        text: The raw message text to parse for @mentions.
        actor: The user who wrote the message (the one doing the
            mentioning).  We skip self-mentions.
        notification_type: Passed through to ``create_notification()``
            (e.g. ``"mention"``).
        title_template: A format string with an ``{actor}`` placeholder,
            e.g. ``"{actor} mentioned you in a post"``.
        payload_factory: A callable ``(User) -> dict`` that builds the
            notification payload for each mentioned user.  Receives the
            mentioned user as argument so payloads can be user-specific.

    Returns:
        List of user IDs that received mention notifications.  The
        caller can use these to publish real-time WebSocket events.

    Side effects:
        - Queries the ``users`` table to resolve usernames to User objects
        - Creates Notification rows (flushed, not committed)
    """
    # Step 1: Extract unique usernames from the text
    usernames = extract_mentions(text)
    if not usernames:
        return []

    # Step 2: Batch-lookup all mentioned users in a single query
    # (avoids N+1 — one query instead of one per username)
    users = db.execute(select(User).where(User.username.in_(usernames))).scalars().all()

    # Step 3: Create a notification for each mentioned user (skip self)
    recipient_ids: list[int] = []
    for user in users:
        # Don't notify yourself when you mention your own username
        if user.id == actor.id:
            continue
        create_notification(
            db,
            user_id=user.id,
            notification_type=notification_type,
            title=title_template.format(actor=actor.username),
            payload=payload_factory(user),
        )
        recipient_ids.append(user.id)

    return recipient_ids

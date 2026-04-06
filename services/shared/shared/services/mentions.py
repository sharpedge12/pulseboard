"""Mention parsing and notification creation — used by forum and chat services."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.models.user import User
from shared.services.notifications import create_notification

MENTION_PATTERN = re.compile(r"@([A-Za-z0-9_]{3,50})")


def extract_mentions(text: str) -> set[str]:
    return {match.lower() for match in MENTION_PATTERN.findall(text)}


def create_mention_notifications(
    db: Session,
    text: str,
    actor: User,
    notification_type: str,
    title_template: str,
    payload_factory,
) -> list[int]:
    usernames = extract_mentions(text)
    if not usernames:
        return []

    users = db.execute(select(User).where(User.username.in_(usernames))).scalars().all()
    recipient_ids: list[int] = []
    for user in users:
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

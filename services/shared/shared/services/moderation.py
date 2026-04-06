"""Moderation helpers — used by forum and moderation services."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.models.user import User, UserRole
from shared.models.vote import CategoryModerator


def get_moderator_category_ids(db: Session, user: User) -> list[int] | None:
    """Return category IDs the moderator is scoped to, or None for admins (all)."""
    if user.role == UserRole.ADMIN:
        return None  # admin sees everything
    rows = (
        db.execute(
            select(CategoryModerator.category_id).where(
                CategoryModerator.user_id == user.id
            )
        )
        .scalars()
        .all()
    )
    return list(rows)

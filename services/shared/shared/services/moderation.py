"""
Moderator Scope Helper — Category-Level Access Control
========================================================

INTERVIEW CONTEXT:
    PulseBoard uses **scoped moderation** — moderators are assigned to
    specific categories and can only moderate content within those
    categories.  This is different from a "global moderator" model
    where all mods can act on all content.

    Scoped moderation is important for large forums because:
    - Different categories may have different rules and community norms
    - Category-specific moderators have domain expertise
    - It limits the blast radius of a compromised moderator account
    - It scales better than a small team of global moderators

    Admins, however, are NOT scoped — they can see and act on everything.

USED BY:
    - **Community service** forum routes: when listing threads, the
      moderator's scope determines which categories' threads they can
      moderate (lock, pin, delete).
    - **Community service** admin routes: when viewing reports, moderators
      only see reports for content in their assigned categories.
    - **Community service** moderation routes: when taking mod actions,
      the system verifies the moderator has scope over the target content.

WHY IN THE SHARED LAYER?
    Both forum-related and admin-related routes in the Community service
    need this function.  It could arguably live in the Community service
    itself, but placing it in the shared layer makes it available if
    the Core service ever needs to check moderator scope (e.g., for
    notification filtering).

DATA MODEL:
    The ``category_moderators`` table is a many-to-many join table
    between ``users`` and ``categories``.  Each row means "this user
    is a moderator of this category."  The ``CategoryModerator`` model
    has ``user_id`` and ``category_id`` columns.

RETURN VALUE CONVENTION:
    - ``None`` means "unrestricted access" (admin — no WHERE clause needed)
    - ``[]`` (empty list) means "no access to any category"
    - ``[1, 3, 7]`` means "only these category IDs"

    Callers use this to build their queries:
    .. code-block:: python

        cat_ids = get_moderator_category_ids(db, user)
        if cat_ids is not None:
            query = query.where(Thread.category_id.in_(cat_ids))
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.models.user import User, UserRole
from shared.models.vote import CategoryModerator


def get_moderator_category_ids(db: Session, user: User) -> list[int] | None:
    """Return category IDs the moderator is scoped to, or None for admins.

    INTERVIEW NOTE — THE ``None`` vs EMPTY LIST DISTINCTION:
        This is a deliberate API design choice:
        - ``None`` = admin, no filtering needed (unrestricted)
        - ``[]`` = moderator with no category assignments (sees nothing)
        - ``[1, 3]`` = moderator assigned to categories 1 and 3

        The caller checks ``if cat_ids is not None:`` to decide whether
        to add a WHERE clause.  Using ``None`` for "no restriction" is
        a common pattern (similar to how SQL ``LIMIT NULL`` means
        "no limit").

    Args:
        db: Active SQLAlchemy session.
        user: The authenticated user whose moderator scope to check.

    Returns:
        ``None`` if the user is an admin (sees everything), or a list
        of category IDs the user is assigned to moderate.  Can be empty
        if the user is a moderator but has no category assignments.

    Side effects:
        Read-only — queries the ``category_moderators`` table.
    """
    # Admins are unrestricted — return None to signal "no filtering"
    if user.role == UserRole.ADMIN:
        return None  # admin sees everything

    # For moderators (and members, though members shouldn't reach this),
    # query the category_moderators join table for their assignments.
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

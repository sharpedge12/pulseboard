"""
Forum Seed — Default Category Seeding on Application Startup.

This module provides a small, idempotent seeding function that inserts a
handful of starter categories into the database so the forum is immediately
usable after a fresh deployment (no admin intervention required).

Why this exists:
    Without at least one category, new users cannot create threads.  Rather
    than forcing an admin to manually create categories via the dashboard,
    the system automatically seeds sensible defaults on first boot.

Idempotency guarantee:
    ``seed_default_categories()`` checks whether *any* category already
    exists in the database.  If even one row is found, the function
    returns immediately without inserting anything.  This makes it safe
    to call on every startup — it will only insert data once.

Called from:
    ``app.main.lifespan()`` — guarded by the
    ``SEED_DEFAULT_CATEGORIES_ON_STARTUP`` environment variable.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.core.database import SessionLocal
from shared.models.category import Category


# The starter categories shipped with every fresh PulseBoard deployment.
# Each dict maps directly to the ``Category`` model columns: title (display
# name), slug (URL-safe identifier), and description (shown in the sidebar).
DEFAULT_CATEGORIES = [
    {
        "title": "General Discussion",
        "slug": "general",
        "description": "Project updates, questions, and broad discussion.",
    },
    {
        "title": "Backend Engineering",
        "slug": "backend",
        "description": "API design, FastAPI, databases, and infrastructure.",
    },
    {
        "title": "Frontend Engineering",
        "slug": "frontend",
        "description": "React UI, UX, and integration work.",
    },
    {
        "title": "DevOps and Deployment",
        "slug": "devops",
        "description": "Docker, Redis, Render, Vercel, and deployment notes.",
    },
]


def seed_default_categories() -> None:
    """
    Insert the default forum categories if the ``categories`` table is empty.

    This function creates its own database session (``SessionLocal``) rather
    than relying on FastAPI's dependency injection because it runs during
    startup — *before* any request context exists.

    Algorithm:
        1. Open a new DB session.
        2. Check whether any ``Category`` row already exists.
        3. If yes → return immediately (idempotent no-op).
        4. If no  → insert each entry from ``DEFAULT_CATEGORIES`` and commit.
        5. Always close the session in the ``finally`` block to prevent
           connection leaks.
    """
    db: Session = SessionLocal()
    try:
        # Idempotency check: if at least one category already exists in the
        # database, skip seeding entirely.  This prevents duplicate inserts
        # when the service restarts.
        existing = db.execute(select(Category.id)).first()
        if existing:
            return

        # Insert each default category.  The ``**item`` unpacking maps dict
        # keys to the Category model's constructor keyword arguments.
        for item in DEFAULT_CATEGORIES:
            db.add(Category(**item))
        db.commit()
    finally:
        # Always close the session to return the connection to the pool,
        # even if an exception occurred during seeding.
        db.close()

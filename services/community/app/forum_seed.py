"""Seed default categories on startup."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.core.database import SessionLocal
from shared.models.category import Category


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
    db: Session = SessionLocal()
    try:
        existing = db.execute(select(Category.id)).first()
        if existing:
            return

        for item in DEFAULT_CATEGORIES:
            db.add(Category(**item))
        db.commit()
    finally:
        db.close()

import logging
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from shared.core.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


engine_kwargs: dict[str, object] = {"pool_pre_ping": True}
if settings.database_url.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(settings.database_url, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _run_migrations() -> None:
    """Apply incremental schema changes that create_all() cannot handle."""
    is_sqlite = settings.database_url.startswith("sqlite")

    alter_statements: list[str] = []

    if not is_sqlite:
        alter_statements = [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS suspended_until TIMESTAMPTZ",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen TIMESTAMPTZ",
            "ALTER TABLE content_reports ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'pending'",
            "ALTER TABLE content_reports ADD COLUMN IF NOT EXISTS resolved_by INTEGER REFERENCES users(id) ON DELETE SET NULL",
            "ALTER TABLE content_reports ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ",
            # audit_logs table is created by create_all(); this is a placeholder
            # in case future columns need to be added incrementally.
        ]

    with engine.begin() as conn:
        for stmt in alter_statements:
            try:
                conn.execute(text(stmt))
                logger.info("Migration applied: %s", stmt[:80])
            except Exception as exc:
                logger.debug("Migration skipped (%s): %s", exc, stmt[:80])


def init_db() -> None:
    import shared.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _run_migrations()

"""
Database engine, session factory, and initialization for all PulseBoard services.

WHY THIS FILE EXISTS:
    In PulseBoard's microservice architecture, all services (Core on port 8001,
    Community on port 8002) share a SINGLE PostgreSQL database. This file sets
    up the SQLAlchemy engine, session factory, and table creation logic that
    every service imports. It lives in the shared library so database setup code
    isn't duplicated across services.

KEY CONCEPTS FOR INTERVIEWS:
    1. Engine: the connection pool to the database (one per process).
    2. Session: a short-lived "conversation" with the database (one per request).
    3. DeclarativeBase: the parent class that all ORM models inherit from,
       enabling SQLAlchemy to track which tables/columns exist.
    4. Dependency Injection: get_db() is a FastAPI dependency that automatically
       provides a session to route handlers and cleans it up afterward.

ARCHITECTURE FIT:
    Core and Community each run this module on startup. Both connect to the same
    PostgreSQL instance, so we need retry logic to handle the race condition when
    both try to CREATE TABLE at the same time (see init_db()).

    Tests override the database URL to use SQLite (via database_url_override in
    config.py), so no PostgreSQL is needed to run the test suite.
"""

import logging
import time
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from shared.core.config import settings

# Logger for this module. Using __name__ means log messages will show as
# "shared.core.database" in the output, making it easy to trace which module
# produced a log line.
logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """
    SQLAlchemy declarative base class — the root of all ORM models.

    WHY THIS EXISTS:
        Every ORM model in the project (User, Thread, Post, ChatRoom, etc.)
        inherits from this class. SQLAlchemy uses this inheritance to:
          - Automatically discover all models via Base.metadata
          - Track the mapping between Python classes and database tables
          - Power Base.metadata.create_all() which creates all tables at once

    WHY DeclarativeBase (SQLAlchemy 2.0+) instead of declarative_base():
        DeclarativeBase is the modern SQLAlchemy 2.0 approach. It's a real
        class (not a factory function), which gives us proper type hints,
        better IDE support, and compatibility with modern type checkers like
        mypy. The old declarative_base() function is considered legacy.

    INTERVIEW NOTE:
        This class is intentionally empty (just `pass`). Its power comes from
        being a base class — all models like `class User(Base)` register
        themselves in Base.metadata simply by inheriting from it.
    """

    pass


# =============================================================================
# Engine configuration
# =============================================================================

# Engine keyword arguments. We build this dict conditionally because SQLite
# and PostgreSQL need different settings.
engine_kwargs: dict[str, object] = {"pool_pre_ping": True}
# pool_pre_ping=True tells SQLAlchemy to send a lightweight "ping" query before
# reusing a connection from the pool. This detects stale/dead connections
# (e.g. if PostgreSQL restarted) and replaces them transparently. Without this,
# a request could fail with "connection reset" after a database restart.

if settings.database_url.startswith("sqlite"):
    # SQLite-specific workaround: SQLite's default behavior forbids using a
    # connection created in one thread from another thread. FastAPI is async
    # and may use multiple threads, so we disable this check. This is safe
    # because SQLAlchemy's session management ensures proper serialization.
    # This branch only runs during testing (prod always uses PostgreSQL).
    engine_kwargs["connect_args"] = {"check_same_thread": False}

# create_engine() creates the connection POOL, not a single connection.
# SQLAlchemy maintains a pool of reusable database connections to avoid the
# overhead of opening/closing TCP connections for every request. The engine
# is a module-level singleton — created once when this module is first imported.
engine = create_engine(settings.database_url, **engine_kwargs)

# sessionmaker() creates a SESSION FACTORY — a callable that produces new
# Session objects with consistent configuration. Think of it as a "template"
# for sessions.
#   - autocommit=False: changes are NOT automatically committed. You must
#     explicitly call db.commit(). This prevents accidental partial writes.
#   - autoflush=False: SQLAlchemy won't automatically flush pending changes
#     to the DB before queries. This gives us more control over when SQL
#     statements are sent, which is important for performance and debugging.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency that provides a database session per request.

    HOW IT WORKS:
        This is a generator function (it uses `yield`). FastAPI's dependency
        injection system recognizes generator dependencies and handles them
        specially:
          1. Before the route handler runs: everything up to `yield` executes
             (creates the session).
          2. The yielded value (db) is injected into the route handler.
          3. After the route handler returns (or raises): the `finally` block
             runs (closes the session).

    WHY THIS PATTERN (generator, not context manager):
        FastAPI's Depends() system needs a generator, not a context manager.
        The generator pattern ensures the session is ALWAYS closed, even if
        the route handler raises an exception. This prevents connection leaks
        that would eventually exhaust the connection pool.

    USAGE IN ROUTE HANDLERS:
        @router.get("/users")
        def list_users(db: Session = Depends(get_db)):
            return db.query(User).all()
        # db is automatically closed after the response is sent

    INTERVIEW TIP:
        This is an example of the "Unit of Work" pattern — each HTTP request
        gets its own database session (unit of work) that is committed or
        rolled back as a whole, then discarded.
    """
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _run_migrations() -> None:
    """
    Apply incremental schema changes that create_all() cannot handle.

    WHY THIS EXISTS (no Alembic):
        SQLAlchemy's create_all() only CREATES tables — it does NOT modify
        existing tables. If you add a new column to a model, create_all()
        won't add it to an already-existing table. Normally you'd use Alembic
        (SQLAlchemy's migration tool) for this, but Alembic adds complexity
        (migration files, version tracking, upgrade/downgrade scripts).

        For this project, we use a simpler approach: raw ALTER TABLE statements
        with IF NOT EXISTS. This is idempotent — safe to run on every startup
        because adding a column that already exists is a no-op.

    WHY NOT ALEMBIC:
        Alembic is the right choice for large teams and production systems.
        For this project (a learning/portfolio project), the manual approach
        is simpler and teaches you what Alembic does under the hood. In an
        interview, you should mention this trade-off.

    SQLITE CAVEAT:
        SQLite has very limited ALTER TABLE support (no ADD COLUMN IF NOT
        EXISTS, no DROP COLUMN before 3.35). That's why we skip all ALTER
        statements when running on SQLite — tests create tables from scratch
        each time anyway, so migrations aren't needed.
    """
    is_sqlite = settings.database_url.startswith("sqlite")

    # List of ALTER TABLE statements to apply. Each one adds a column that
    # was introduced after the initial schema was designed.
    alter_statements: list[str] = []

    if not is_sqlite:
        alter_statements = [
            # suspended_until: added to support time-limited user suspensions
            # (moderators can suspend a user for N hours). Nullable because
            # most users are never suspended.
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS suspended_until TIMESTAMPTZ",
            # last_seen: tracks when a user last made an authenticated request,
            # used for online/offline status indicators in the UI.
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen TIMESTAMPTZ",
            # The next 3 columns were added to content_reports to support a
            # report resolution workflow (pending -> resolved/dismissed).
            "ALTER TABLE content_reports ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'pending'",
            "ALTER TABLE content_reports ADD COLUMN IF NOT EXISTS resolved_by INTEGER REFERENCES users(id) ON DELETE SET NULL",
            "ALTER TABLE content_reports ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ",
            # audit_logs table is created by create_all(); this is a placeholder
            # in case future columns need to be added incrementally.
        ]

    # engine.begin() opens a connection AND starts a transaction. All ALTER
    # statements run inside this transaction — if one fails, they all roll back.
    with engine.begin() as conn:
        for stmt in alter_statements:
            try:
                # text() wraps a raw SQL string so SQLAlchemy can execute it.
                # We use raw SQL here because ALTER TABLE isn't part of
                # SQLAlchemy's ORM — it's a DDL (Data Definition Language)
                # operation, not a query.
                conn.execute(text(stmt))
                logger.info("Migration applied: %s", stmt[:80])
            except Exception as exc:
                # If the column already exists or the statement fails for any
                # reason, we log it at DEBUG level and move on. This makes the
                # migration idempotent — safe to run repeatedly.
                logger.debug("Migration skipped (%s): %s", exc, stmt[:80])


def init_db() -> None:
    """
    Create all database tables and run migrations. Called once at service startup.

    WHY RETRY LOGIC:
        In Docker Compose, Core (port 8001) and Community (port 8002) start
        at roughly the same time, and both call init_db(). Both will try to
        execute CREATE TABLE IF NOT EXISTS for the same tables simultaneously.

        Despite the "IF NOT EXISTS" clause, there's a TOCTOU (Time-of-Check
        to Time-of-Use) race condition at the database level:
          1. Service A checks: does table "users" exist? No.
          2. Service B checks: does table "users" exist? No.
          3. Service A creates table "users". Success.
          4. Service B creates table "users". FAILS — DuplicateTable error.

        This is a real concurrency bug that surfaces in production. The retry
        logic catches DuplicateTable errors and retries with exponential
        backoff (2s, 4s), giving the other service time to finish. On the
        final attempt, we just log a warning and proceed — the tables exist
        (created by the other service), so everything is fine.

    INTERVIEW TIP:
        TOCTOU races are a classic concurrency problem. The fix here is
        optimistic: try the operation, catch the conflict, retry. This is
        simpler than using distributed locks (which would require Redis or
        similar coordination).
    """
    # This import triggers Python to execute shared/models/__init__.py, which
    # imports ALL model files (User, Thread, Post, etc.). Each model class
    # inherits from Base, which registers it in Base.metadata. Without this
    # import, create_all() wouldn't know about any tables.
    # noqa: F401 suppresses the "imported but unused" linter warning — the
    # import IS used, just for its side effect of registering models.
    import shared.models  # noqa: F401

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            # create_all() inspects Base.metadata (which knows about all models
            # thanks to the import above) and issues CREATE TABLE IF NOT EXISTS
            # for every model. It's idempotent in theory, but TOCTOU races
            # break that guarantee under concurrent access (see docstring).
            Base.metadata.create_all(bind=engine)
            break  # Success — exit the retry loop
        except ProgrammingError as exc:
            # ProgrammingError is SQLAlchemy's wrapper for database-level errors.
            # We check the error message for "DuplicateTable" (PostgreSQL) or
            # "already exists" (generic) to distinguish race conditions from
            # real errors (e.g. permission denied, invalid SQL).
            if "DuplicateTable" in str(exc) or "already exists" in str(exc):
                if attempt < max_retries:
                    # Exponential backoff: wait 2s, then 4s. This gives the
                    # other service time to finish creating tables.
                    wait = attempt * 2
                    logger.warning(
                        "Table creation race detected (attempt %d/%d), "
                        "retrying in %ds...",
                        attempt,
                        max_retries,
                        wait,
                    )
                    time.sleep(wait)
                else:
                    # Final attempt: the tables were created by the other
                    # service, so we can safely proceed. This is not an error.
                    logger.warning(
                        "Table creation race on final attempt; tables "
                        "likely created by another service — proceeding."
                    )
            else:
                # Non-race error (permissions, syntax, etc.) — re-raise so
                # the service fails loudly on startup instead of running with
                # a broken database.
                raise

    # After tables exist, apply any ALTER TABLE migrations for columns that
    # were added after the initial schema.
    _run_migrations()

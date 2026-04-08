"""
Timestamp Mixin — Automatic created_at / updated_at for Every Model
====================================================================

Database table: N/A (this is a mixin, not a standalone model)

WHAT IS A MIXIN?
    A mixin is a class that provides reusable columns/methods to other classes
    via multiple inheritance. TimestampMixin is NOT a table itself — it adds
    created_at and updated_at columns to any model that inherits from it.

    Example usage:
        class User(TimestampMixin, Base):   # User gets created_at + updated_at
        class Thread(TimestampMixin, Base): # Thread gets them too

    This avoids copy-pasting the same two column definitions into every model.
    In database design, nearly every table should have timestamps for auditing
    and debugging ("when was this row created? when was it last changed?").

WHY NOT JUST A BASE CLASS?
    Python supports multiple inheritance. If TimestampMixin were a subclass of
    Base, then models would have a diamond inheritance problem (both the mixin
    and the model inherit from Base). By making it a plain class (no parent),
    we sidestep this. SQLAlchemy is designed to handle mixins with mapped_column
    — it copies the column definitions into any subclass that uses them.

INTERVIEW TIP:
    Mixins are a textbook example of the DRY principle (Don't Repeat Yourself).
    Every table in PulseBoard has created_at and updated_at because every model
    inherits from TimestampMixin. If you needed to add a "deleted_at" column
    for soft deletes, you'd add it here once and every table would get it.
"""

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    """
    Mixin that adds created_at and updated_at timestamp columns to any model.

    These columns are managed by the DATABASE SERVER, not Python code. This is
    an important distinction — see the notes on server_default below.
    """

    # -------------------------------------------------------------------------
    # created_at — When the row was first inserted
    # -------------------------------------------------------------------------
    # server_default=func.now() means the DEFAULT value is set by the DATABASE,
    # not by Python. In PostgreSQL, this translates to:
    #     created_at TIMESTAMPTZ DEFAULT NOW()
    #
    # WHY server_default INSTEAD OF default?
    #   - `default=func.now()` would set the value in Python before sending the
    #     INSERT to the database. The timestamp would reflect the Python app
    #     server's clock.
    #   - `server_default=func.now()` tells the database to set the value using
    #     ITS OWN clock. This is better because:
    #       1. All timestamps use the same clock (the DB server's), avoiding
    #          clock skew between multiple app servers.
    #       2. The column has a DEFAULT in the actual DDL, so even raw SQL
    #          INSERTs (outside SQLAlchemy) get correct timestamps.
    #       3. It's visible in the schema (pg_dump, migrations, etc.).
    #
    # WHY DateTime(timezone=True)?
    #   This creates a TIMESTAMPTZ column in PostgreSQL, which stores times in
    #   UTC and converts to the session's timezone on read. Without timezone=True,
    #   you'd get TIMESTAMP (no timezone), which silently drops timezone info —
    #   a notorious source of bugs in global applications.
    #
    # WHAT IS func.now()?
    #   func.now() is SQLAlchemy's cross-database wrapper for the current time.
    #   It translates to NOW() in PostgreSQL, CURRENT_TIMESTAMP in SQLite, etc.
    # -------------------------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # -------------------------------------------------------------------------
    # updated_at — When the row was last modified
    # -------------------------------------------------------------------------
    # This uses the same server_default for initial creation, PLUS:
    #
    # onupdate=func.now() tells SQLAlchemy to automatically set this column to
    # NOW() every time an UPDATE statement is issued for this row. Note that
    # onupdate is a PYTHON-SIDE behavior (SQLAlchemy sets it before sending the
    # UPDATE), not a database trigger. If you update the row via raw SQL, this
    # column won't change automatically — you'd need a database trigger for that.
    #
    # INTERVIEW NOTE:
    #   If asked "how would you ensure updated_at is always correct even for
    #   raw SQL updates?", the answer is a database trigger:
    #       CREATE TRIGGER update_timestamp BEFORE UPDATE ON <table>
    #       FOR EACH ROW EXECUTE FUNCTION update_modified_column();
    #   But for an ORM-heavy app where all writes go through SQLAlchemy,
    #   onupdate=func.now() is simpler and sufficient.
    # -------------------------------------------------------------------------
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

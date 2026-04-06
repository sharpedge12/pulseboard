"""PulseBoard shared library — models, schemas, auth helpers, config, database.

Database symbols (Base, engine, SessionLocal, get_db, init_db) are NOT
imported at package level to avoid triggering ``create_engine()`` in
services that don't need a database connection (e.g. the API gateway).
Import them directly from ``shared.core.database`` when needed.
"""

from shared.core.config import settings
from shared.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    safe_decode_token,
    verify_password,
)

__all__ = [
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "hash_password",
    "safe_decode_token",
    "settings",
    "verify_password",
]

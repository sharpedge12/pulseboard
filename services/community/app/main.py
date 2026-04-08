"""
Community Service — FastAPI Application Entry Point.

This is the main module for the Community microservice in the PulseBoard
discussion forum platform. The Community service is responsible for all
forum-related functionality: categories (subreddit-like communities),
threads (posts), replies (comments), votes, reactions, reports, chat rooms,
and admin/moderation operations.

Architecture context:
    - This service runs on port 8002 behind the API gateway (port 8000).
    - It shares a PostgreSQL database with the Core service (port 8001).
    - Real-time events are published via Redis pub/sub to the gateway,
      which bridges them to WebSocket clients in the browser.

Key design decisions:
    - The ``lifespan`` context manager initialises the database tables on
      startup (via ``create_all``) and optionally seeds default categories
      so the forum is not empty on first deploy.
    - CORS is configured here for direct access during development, but in
      production all traffic arrives through the gateway which handles CORS
      centrally.
    - Security headers (X-Frame-Options, CSP, etc.) are added via a custom
      middleware to harden every HTTP response.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from shared.core.config import settings
from shared.core.database import init_db
from shared.core.logging import configure_logging

from app.forum_routes import category_router, thread_router, post_router, search_router
from app.admin_routes import router as admin_router
from app.chat_routes import router as chat_router
from app.forum_seed import seed_default_categories

# Set up structured logging before any request is handled.
configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """
    Application lifespan handler — runs once at startup, then yields
    control to the ASGI server for the lifetime of the process.

    Startup steps:
        1. ``init_db()`` — calls ``Base.metadata.create_all()`` to ensure
           all ORM tables exist.  There is no Alembic; instead, a helper
           ``_run_migrations()`` uses raw ``ALTER TABLE … ADD COLUMN IF NOT
           EXISTS`` SQL for schema evolution.
        2. ``seed_default_categories()`` — optionally inserts a small set
           of starter categories (General, Backend, Frontend, DevOps) so
           the forum is usable immediately.  Controlled by the environment
           variable ``SEED_DEFAULT_CATEGORIES_ON_STARTUP`` (default True).

    The ``yield`` suspends this function until the application shuts down,
    at which point any cleanup code after yield would execute (none needed
    here).
    """
    # Ensure all database tables are created (idempotent — safe to call
    # repeatedly because SQLAlchemy's create_all() is a no-op for tables
    # that already exist).
    init_db()

    # Seed default forum categories if the env flag is set.  The seed
    # function is idempotent: it checks whether ANY category already
    # exists and skips seeding if so.
    if settings.seed_default_categories_on_startup:
        seed_default_categories()

    yield  # Hand control to the ASGI server; app is now ready to serve.


# ---------------------------------------------------------------------------
# FastAPI application instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title="PulseBoard Community Service",
    version="0.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

# CORS middleware allows the React frontend (port 5173) to make cross-origin
# requests during local development.  In production the gateway handles CORS,
# but having it here too provides defence in depth.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security headers middleware adds protective HTTP headers to every response:
#   - X-Content-Type-Options: nosniff
#   - X-Frame-Options: DENY
#   - Content-Security-Policy (restrictive)
#   - Cache-Control: no-store (for authenticated responses)
from shared.core.security_headers import SecurityHeadersMiddleware  # noqa: E402

app.add_middleware(SecurityHeadersMiddleware)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    """
    Simple health-check endpoint used by Docker health checks, load
    balancers, and orchestrators (e.g. ``docker compose``, Kubernetes)
    to verify the service is alive and responding.

    Returns:
        A JSON object ``{"status": "ok", "service": "community"}``.
    """
    return {"status": "ok", "service": "community"}


# ---------------------------------------------------------------------------
# Router registration
# ---------------------------------------------------------------------------
# Each router is mounted under the versioned API prefix (``/api/v1``).
# The gateway reverse-proxies these paths from port 8000 to this service.
#
# Route mapping (gateway → community):
#   /api/v1/categories/* → category_router
#   /api/v1/threads/*    → thread_router
#   /api/v1/posts/*      → post_router
#   /api/v1/search/*     → search_router
#   /api/v1/admin/*      → admin_router
#   /api/v1/chat/*       → chat_router

prefix = settings.api_v1_prefix
app.include_router(category_router, prefix=prefix + "/categories", tags=["categories"])
app.include_router(thread_router, prefix=prefix + "/threads", tags=["threads"])
app.include_router(post_router, prefix=prefix + "/posts", tags=["posts"])
app.include_router(search_router, prefix=prefix + "/search", tags=["search"])
app.include_router(admin_router, prefix=prefix + "/admin", tags=["admin"])
app.include_router(chat_router, prefix=prefix + "/chat", tags=["chat"])

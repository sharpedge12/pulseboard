"""
PulseBoard Core Service — Application Entry Point
===================================================

This is the FastAPI application factory for the **Core** microservice, which is
responsible for authentication, user management, file uploads, and notifications.

In a microservice architecture the "main.py" file wires everything together:
  1. Database initialisation (creating tables on first run).
  2. Middleware stack (security headers, rate limiting, CORS).
  3. Router mounting (mapping URL prefixes to route modules).
  4. Static file serving (user-uploaded avatars and attachments).

Key interview concepts demonstrated here:
  - **Lifespan context manager** (ASGI startup/shutdown hook).
  - **Middleware ordering** — middleware is applied in LIFO order in FastAPI
    (the last middleware added runs first on the request path).
  - **Dependency Injection** — FastAPI uses ``Depends()`` throughout routers;
    this file just registers the routers under URL prefixes.
  - **Static file serving** — ``StaticFiles`` lets Uvicorn serve uploaded images
    directly without a separate Nginx/CDN in development.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from shared.core.config import settings
from shared.core.database import init_db
from shared.core.logging import configure_logging

from app.auth_routes import router as auth_router
from app.user_routes import router as user_router
from app.upload_routes import upload_router
from app.notification_routes import router as notification_router

# Configure structured logging before anything else runs so that all
# subsequent log calls use a consistent format.
configure_logging()


# ---------------------------------------------------------------------------
# Lifespan — Startup & Shutdown
# ---------------------------------------------------------------------------
# FastAPI uses an async context manager to run code when the server starts
# (before ``yield``) and when the server shuts down (after ``yield``).
# This replaced the older ``@app.on_event("startup")`` pattern.
#
# ``init_db()`` calls SQLAlchemy's ``Base.metadata.create_all()`` to ensure
# all tables exist, then runs lightweight ``ALTER TABLE ... ADD COLUMN``
# migrations for columns added after the initial schema was created.
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Startup: initialise database tables and run migrations.

    The ``_`` parameter is the FastAPI app instance — unused here but
    required by the lifespan protocol.  Everything before ``yield``
    runs at startup; everything after would run at shutdown (nothing
    needed in this service).
    """
    init_db()
    yield  # Server is running and accepting requests between yield and exit.


# ---------------------------------------------------------------------------
# FastAPI Application Instance
# ---------------------------------------------------------------------------
# ``title`` and ``version`` populate the auto-generated OpenAPI docs at /docs.
# ``lifespan`` hooks into the ASGI startup/shutdown lifecycle above.
# ---------------------------------------------------------------------------

app = FastAPI(
    title="PulseBoard Core Service",
    version="0.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware Stack
# ---------------------------------------------------------------------------
# IMPORTANT: FastAPI middleware is applied in **reverse** order.  The last
# middleware added is the outermost layer (runs first on a request).  So
# the effective execution order for an incoming request is:
#
#   1. SecurityHeadersMiddleware   (added last  -> runs first)
#   2. RateLimitMiddleware         (added second -> runs second)
#   3. CORSMiddleware              (added first  -> runs third)
#
# CORSMiddleware:
#   Handles Cross-Origin Resource Sharing.  The frontend (localhost:5173)
#   needs to call this backend (localhost:8001), which is a different
#   origin.  Without CORS headers the browser blocks the request.
#   - ``allow_credentials=True`` lets the browser send cookies/auth headers.
#   - ``allow_methods=["*"]`` permits GET, POST, PATCH, DELETE, etc.
#   - ``allow_headers=["*"]`` permits Authorization, Content-Type, etc.
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from shared.core.security_headers import SecurityHeadersMiddleware  # noqa: E402
from shared.core.rate_limit import RateLimitMiddleware  # noqa: E402

# SecurityHeadersMiddleware:
#   Adds defensive HTTP headers to every response (X-Content-Type-Options,
#   X-Frame-Options, Content-Security-Policy, etc.) to mitigate XSS,
#   clickjacking, and MIME-sniffing attacks.
app.add_middleware(SecurityHeadersMiddleware)

# RateLimitMiddleware:
#   Sliding-window per-IP rate limiter.  Only applied to auth endpoints
#   (``/api/v1/auth/``) to prevent brute-force login / registration abuse.
#   20 requests per 60 seconds per IP.  Returns HTTP 429 with a
#   ``Retry-After`` header when the limit is exceeded.
app.add_middleware(
    RateLimitMiddleware,
    rate_limit=20,
    window_seconds=60,
    paths=[f"{settings.api_v1_prefix}/auth/"],
)


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------
# A minimal endpoint used by Docker health checks, load balancers, and
# Kubernetes readiness probes to verify the service is alive.  It does
# NOT check database connectivity (that would be a "readiness" check).
# ---------------------------------------------------------------------------


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    """Return a simple JSON indicating the service is running.

    Returns:
        dict: ``{"status": "ok", "service": "core"}``
    """
    return {"status": "ok", "service": "core"}


# ---------------------------------------------------------------------------
# Router Registration
# ---------------------------------------------------------------------------
# Each router module defines a group of related endpoints.  ``include_router``
# prepends the ``prefix`` to every route in that router.  For example,
# ``auth_router`` has ``@router.post("/register")``, which becomes
# ``POST /api/v1/auth/register`` after the prefix is applied.
#
# The ``tags`` parameter groups endpoints in the auto-generated Swagger docs.
# ---------------------------------------------------------------------------

prefix = settings.api_v1_prefix  # Typically "/api/v1"

app.include_router(auth_router, prefix=prefix + "/auth", tags=["auth"])
app.include_router(user_router, prefix=prefix + "/users", tags=["users"])
app.include_router(upload_router, prefix=prefix + "/uploads", tags=["uploads"])
app.include_router(
    notification_router, prefix=prefix + "/notifications", tags=["notifications"]
)

# ---------------------------------------------------------------------------
# Static File Serving for Uploads
# ---------------------------------------------------------------------------
# ``StaticFiles`` serves files from the local filesystem at the ``/uploads``
# URL path.  When a user uploads an avatar, it is saved to e.g.
# ``uploads/avatars/abc123.jpg`` on disk, and the public URL becomes
# ``/uploads/avatars/abc123.jpg``.
#
# In production this would typically be handled by Nginx or a CDN, but
# for local development and Docker Compose this is sufficient.
#
# ``mkdir(parents=True, exist_ok=True)`` ensures the upload directory exists
# on first startup, even in a fresh container.
# ---------------------------------------------------------------------------

_upload_root = Path(settings.upload_dir)
_upload_root.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(_upload_root)), name="uploads")

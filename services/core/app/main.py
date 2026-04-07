"""PulseBoard Core Service — auth, users, uploads, notifications."""

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

configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(
    title="PulseBoard Core Service",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from shared.core.security_headers import SecurityHeadersMiddleware  # noqa: E402
from shared.core.rate_limit import RateLimitMiddleware  # noqa: E402

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    RateLimitMiddleware,
    rate_limit=20,
    window_seconds=60,
    paths=[f"{settings.api_v1_prefix}/auth/"],
)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "core"}


prefix = settings.api_v1_prefix
app.include_router(auth_router, prefix=prefix + "/auth", tags=["auth"])
app.include_router(user_router, prefix=prefix + "/users", tags=["users"])
app.include_router(upload_router, prefix=prefix + "/uploads", tags=["uploads"])
app.include_router(
    notification_router, prefix=prefix + "/notifications", tags=["notifications"]
)

# Serve uploaded files (avatars, attachments) as static assets
_upload_root = Path(settings.upload_dir)
_upload_root.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(_upload_root)), name="uploads")

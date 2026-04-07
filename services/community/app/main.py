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

configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    if settings.seed_default_categories_on_startup:
        seed_default_categories()
    yield


app = FastAPI(
    title="PulseBoard Community Service",
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

app.add_middleware(SecurityHeadersMiddleware)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "community"}


prefix = settings.api_v1_prefix
app.include_router(category_router, prefix=prefix + "/categories", tags=["categories"])
app.include_router(thread_router, prefix=prefix + "/threads", tags=["threads"])
app.include_router(post_router, prefix=prefix + "/posts", tags=["posts"])
app.include_router(search_router, prefix=prefix + "/search", tags=["search"])
app.include_router(admin_router, prefix=prefix + "/admin", tags=["admin"])
app.include_router(chat_router, prefix=prefix + "/chat", tags=["chat"])

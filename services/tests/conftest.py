"""Test configuration for PulseBoard microservices.

Creates a composite FastAPI app that mounts all service routers into one
process, backed by an in-memory SQLite database. This validates the
microservice code without requiring Docker or multiple processes.

The key challenge is that every service has its own ``app/`` package.
We use importlib to load each service's modules under unique names
(e.g. ``core_app_auth_routes``, ``community_app_forum_routes``) to avoid
conflicts.
"""

import importlib
import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import patch

# Force SQLite for tests
os.environ.setdefault("DATABASE_URL_OVERRIDE", "sqlite:///./test_services.db")

SERVICES_DIR = Path(__file__).resolve().parents[1]
SHARED_DIR = SERVICES_DIR / "shared"

# Ensure shared is importable
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

import shared.models  # noqa: E402,F401
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from shared.core.database import Base, get_db  # noqa: E402
from shared.models.category import Category  # noqa: E402
from shared.models.user import EmailVerificationToken  # noqa: E402

# ---------------------------------------------------------------------------
# Database setup (SQLite for tests)
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = "sqlite:///./test_services.db"
engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Default categories (same as community seed)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# importlib helper — load a module from a file path under a unique name
# ---------------------------------------------------------------------------


def _import_from_path(module_name: str, file_path: Path):
    """Import a Python file as a module with the given unique name."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module

    # Each service's app/ directory has internal imports like
    # ``from app.services import ...``. We need to make sure ``app``
    # resolves to that particular service's app package.
    service_dir = file_path.parent.parent  # e.g. services/core
    app_pkg_dir = file_path.parent  # e.g. services/core/app

    # Temporarily insert the service dir into sys.path so internal
    # ``from app.xxx`` imports work.
    old_app = sys.modules.pop("app", None)

    # Clean out any previously cached sub-modules from a different service
    stale_keys = [k for k in sys.modules if k.startswith("app.")]
    for k in stale_keys:
        sys.modules.pop(k, None)

    sys.path.insert(0, str(service_dir))
    # Register a fresh ``app`` package pointing at this service's app dir
    app_init = app_pkg_dir / "__init__.py"
    app_spec = importlib.util.spec_from_file_location(
        "app", app_init, submodule_search_locations=[str(app_pkg_dir)]
    )
    if app_spec and app_spec.loader:
        app_mod = importlib.util.module_from_spec(app_spec)
        sys.modules["app"] = app_mod
        app_spec.loader.exec_module(app_mod)

    spec.loader.exec_module(module)

    # Restore sys.path (leave app cached — will be overwritten per service)
    sys.path.pop(0)

    return module


# ---------------------------------------------------------------------------
# Composite test app — mounts all service routers
# ---------------------------------------------------------------------------


_verify_user_email_func = None


def _build_composite_app() -> FastAPI:
    """Import all service routers and mount them into a single FastAPI app."""
    global _verify_user_email_func
    composite = FastAPI(title="PulseBoard Test Composite")

    # Core service — auth routes
    core_auth_routes = _import_from_path(
        "core_app_auth_routes", SERVICES_DIR / "core" / "app" / "auth_routes.py"
    )
    composite.include_router(
        core_auth_routes.router, prefix="/api/v1/auth", tags=["auth"]
    )

    # Capture auth service's verify function before it gets overwritten
    auth_svc = sys.modules.get("app.auth_services")
    if auth_svc and hasattr(auth_svc, "verify_user_email"):
        _verify_user_email_func = auth_svc.verify_user_email

    # Core service — user routes
    core_user_routes = _import_from_path(
        "core_app_user_routes", SERVICES_DIR / "core" / "app" / "user_routes.py"
    )
    composite.include_router(
        core_user_routes.router, prefix="/api/v1/users", tags=["users"]
    )

    # Core service — upload routes
    core_upload_routes = _import_from_path(
        "core_app_upload_routes",
        SERVICES_DIR / "core" / "app" / "upload_routes.py",
    )
    composite.include_router(
        core_upload_routes.upload_router,
        prefix="/api/v1/uploads",
        tags=["uploads"],
    )

    # Core service — notification routes
    core_notification_routes = _import_from_path(
        "core_app_notification_routes",
        SERVICES_DIR / "core" / "app" / "notification_routes.py",
    )
    composite.include_router(
        core_notification_routes.router,
        prefix="/api/v1/notifications",
        tags=["notifications"],
    )

    # Community service — forum routes
    community_forum_routes = _import_from_path(
        "community_app_forum_routes",
        SERVICES_DIR / "community" / "app" / "forum_routes.py",
    )
    composite.include_router(
        community_forum_routes.category_router,
        prefix="/api/v1/categories",
        tags=["categories"],
    )
    composite.include_router(
        community_forum_routes.thread_router,
        prefix="/api/v1/threads",
        tags=["threads"],
    )
    composite.include_router(
        community_forum_routes.post_router,
        prefix="/api/v1/posts",
        tags=["posts"],
    )
    composite.include_router(
        community_forum_routes.search_router,
        prefix="/api/v1/search",
        tags=["search"],
    )

    # Community service — admin/moderation routes
    community_admin_routes = _import_from_path(
        "community_app_admin_routes",
        SERVICES_DIR / "community" / "app" / "admin_routes.py",
    )
    composite.include_router(
        community_admin_routes.router, prefix="/api/v1/admin", tags=["admin"]
    )

    # Chat routes (now part of community service)
    chat_routes = _import_from_path(
        "community_app_chat_routes",
        SERVICES_DIR / "community" / "app" / "chat_routes.py",
    )
    composite.include_router(chat_routes.router, prefix="/api/v1/chat", tags=["chat"])

    # Override DB dependency
    composite.dependency_overrides[get_db] = override_get_db

    return composite


app = _build_composite_app()
verify_user_email = _verify_user_email_func


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def setup_database() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        for item in DEFAULT_CATEGORIES:
            db.add(Category(**item))
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _mock_smtp():
    """Disable SMTP email sending during tests to avoid connection hangs."""
    targets = [
        "shared.services.email._send_moderation_email",
    ]
    # Also patch in any loaded email modules
    for mod_name in list(sys.modules):
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        if "email" in mod_name and hasattr(mod, "_send_verification_email"):
            targets.append(f"{mod_name}._send_verification_email")
        if "email" in mod_name and hasattr(mod, "_send_moderation_email"):
            targets.append(f"{mod_name}._send_moderation_email")

    targets = list(set(targets))

    patches = [patch(t) for t in targets]
    mocks = [p.start() for p in patches]
    for m in mocks:
        m.return_value = None
    yield
    for p in patches:
        p.stop()


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db_session() -> Session:
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def register_verified_user(
    client: TestClient, email: str, username: str
) -> dict[str, object]:
    """Register a user, verify email, and return the login response JSON."""
    client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "username": username,
            "password": "supersecret",
        },
    )
    db = TestingSessionLocal()
    try:
        token = db.execute(select(EmailVerificationToken)).scalars().all()[-1]
        if verify_user_email:
            verify_user_email(db, token.token)
        else:
            raise RuntimeError("verify_user_email not loaded")
    finally:
        db.close()

    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "supersecret"},
    )
    return login_response.json()

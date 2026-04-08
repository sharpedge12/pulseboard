"""
Test configuration and fixtures for PulseBoard microservices.

INTERVIEW CONCEPTS:

1. **Composite Test App Pattern**:
   In a microservices architecture, each service runs as a separate process
   with its own FastAPI app. Testing them individually is easy, but testing
   cross-service flows (e.g. "register a user, then create a thread") requires
   all services to be running simultaneously.

   Instead of spinning up Docker or multiple processes, we mount ALL service
   routers into a SINGLE FastAPI app ("composite app"). This lets us test
   the entire API surface in one process, using a single test database.

   The trade-off: we don't test inter-service HTTP calls or the gateway's
   reverse proxy, but we DO test all the actual business logic, routes,
   and database operations.

2. **SQLite Test Database**:
   Production uses PostgreSQL, but tests use SQLite for speed and simplicity.
   No database server needed — SQLite is a file-based database (or in-memory).
   This is possible because we use SQLAlchemy's ORM abstraction, which
   generates the correct SQL dialect for each database backend.

   ``check_same_thread=False`` is required because SQLite's default mode
   only allows the creating thread to use a connection. FastAPI's test
   client may use a different thread.

3. **importlib Module Loading**:
   The tricky part of the composite app is that both Core and Community
   services have an ``app/`` package with internal imports like
   ``from app.services import ...``. If we import both naively, Python's
   module system would confuse them (both are "app").

   The solution: use ``importlib`` to load each module under a unique name
   (e.g. ``core_app_auth_routes``, ``community_app_forum_routes``). We also
   temporarily swap ``sys.modules["app"]`` so each service's internal
   imports resolve to its own ``app/`` package.

4. **Autouse Fixtures**:
   Fixtures with ``autouse=True`` run automatically for every test function
   without needing to be explicitly requested. We use two:
   - ``setup_database``: drops and recreates all tables before each test
     (clean slate for test isolation)
   - ``_mock_smtp``: patches email-sending functions to no-ops so tests
     don't try to connect to an SMTP server

5. **Dependency Override**:
   FastAPI's ``dependency_overrides`` dict lets us replace dependencies
   at test time. We override ``get_db`` (which normally connects to
   PostgreSQL) with our SQLite session factory.
"""

import importlib
import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Force SQLite for tests by setting the DATABASE_URL_OVERRIDE env var.
# This must happen BEFORE importing any database-related modules, because
# the database engine is configured at import time based on this env var.
# ---------------------------------------------------------------------------
os.environ.setdefault("DATABASE_URL_OVERRIDE", "sqlite:///./test_services.db")

# Compute directory paths relative to this file's location.
# SERVICES_DIR = services/ (parent of tests/)
# SHARED_DIR = services/shared/ (the shared library)
SERVICES_DIR = Path(__file__).resolve().parents[1]
SHARED_DIR = SERVICES_DIR / "shared"

# Ensure the shared library is importable by adding its directory to sys.path.
# This is necessary because we're running tests from the project root, not
# from inside the services/ directory.
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

# Import shared.models to trigger SQLAlchemy model registration.
# Without this, Base.metadata wouldn't know about our tables.
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
# INTERVIEW NOTE:
# We create a dedicated SQLAlchemy engine and session factory for tests.
# ``check_same_thread=False`` is a SQLite-specific setting required because
# FastAPI's TestClient may issue requests from a different thread than the
# one that created the database connection. PostgreSQL doesn't need this
# because its connections are inherently thread-safe.

TEST_DATABASE_URL = "sqlite:///./test_services.db"
engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},  # Required for SQLite + threads
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    """Replacement for the production ``get_db`` dependency.

    Yields a SQLite-backed database session instead of PostgreSQL.
    This function has the same signature as the production ``get_db``
    (a generator that yields a session), so FastAPI can use it as a
    drop-in replacement via dependency_overrides.

    INTERVIEW NOTE:
        The ``try/finally`` pattern ensures the session is always closed,
        even if the request handler raises an exception. This prevents
        connection leaks.
    """
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Default categories seeded into the test database
# ---------------------------------------------------------------------------
# These mirror the categories created by the Community service on startup.
# Tests that reference categories (e.g. creating threads) need these to exist.

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
    """Import a Python file as a module with the given unique name.

    This is the key mechanism that allows the composite test app to work.
    It loads a Python file (e.g. ``services/core/app/auth_routes.py``) as a
    module with a unique name (e.g. ``core_app_auth_routes``), avoiding
    name collisions between services that both have ``app/`` packages.

    Args:
        module_name: A unique name for this module in ``sys.modules``.
        file_path: Absolute path to the ``.py`` file to import.

    Returns:
        The loaded module object.

    INTERVIEW NOTE — how this solves the import conflict:
        Both Core and Community services have ``app/auth_services.py``,
        ``app/services.py``, etc. Python's import system uses ``sys.modules``
        as a cache keyed by module name. If Core's ``app.auth_services`` is
        cached, importing Community's would return Core's version.

        Our solution:
        1. Remove the stale ``app`` and ``app.*`` entries from ``sys.modules``
        2. Temporarily add the target service's directory to ``sys.path``
        3. Register a fresh ``app`` package pointing to this service's ``app/`` dir
        4. Execute the module file (its internal ``from app.xxx`` imports now
           resolve correctly)
        5. Restore ``sys.path``

        This is essentially "hot-swapping" what ``app`` means for each service.
    """
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {file_path}")
    module = importlib.util.module_from_spec(spec)
    # Register the module under its unique name BEFORE executing it.
    # This ensures that if the module imports itself (circular), it finds
    # the correct entry in sys.modules.
    sys.modules[module_name] = module

    # Determine the service and app directories from the file path.
    # e.g. file_path = services/core/app/auth_routes.py
    #      service_dir = services/core
    #      app_pkg_dir = services/core/app
    service_dir = file_path.parent.parent  # e.g. services/core
    app_pkg_dir = file_path.parent  # e.g. services/core/app

    # Save and clear the current ``app`` module to avoid cross-service contamination.
    # If we previously loaded Core's ``app`` package, we need to remove it before
    # loading Community's ``app`` package.
    old_app = sys.modules.pop("app", None)

    # Clean out any previously cached sub-modules from a different service.
    # e.g. if Core's ``app.auth_services`` is cached, it would conflict with
    # Community's ``app.auth_services``.
    stale_keys = [k for k in sys.modules if k.startswith("app.")]
    for k in stale_keys:
        sys.modules.pop(k, None)

    # Temporarily add the service directory to sys.path so that
    # ``from app.xxx`` imports inside the module resolve to the correct
    # service's app/ package.
    sys.path.insert(0, str(service_dir))

    # Register a fresh ``app`` package pointing at this service's app/ directory.
    # ``submodule_search_locations`` tells Python where to find sub-modules
    # when code does ``from app.services import ...``.
    app_init = app_pkg_dir / "__init__.py"
    app_spec = importlib.util.spec_from_file_location(
        "app", app_init, submodule_search_locations=[str(app_pkg_dir)]
    )
    if app_spec and app_spec.loader:
        app_mod = importlib.util.module_from_spec(app_spec)
        sys.modules["app"] = app_mod
        app_spec.loader.exec_module(app_mod)

    # Now execute the actual module file. Its internal imports like
    # ``from app.services import ...`` will resolve to this service's app/.
    spec.loader.exec_module(module)

    # Restore sys.path — remove the service dir we added temporarily.
    # The ``app`` module stays cached in sys.modules (it will be overwritten
    # next time we load a module from a different service).
    sys.path.pop(0)

    return module


# ---------------------------------------------------------------------------
# Composite test app — mounts all service routers into one FastAPI instance
# ---------------------------------------------------------------------------

# Module-level variable to hold the verify_user_email function reference.
# Captured during Core service import so tests can programmatically verify
# user emails without sending actual emails.
_verify_user_email_func = None


def _build_composite_app() -> FastAPI:
    """Import all service routers and mount them into a single FastAPI app.

    This function creates the composite test application by:
    1. Importing each service's route modules using ``_import_from_path``
    2. Including each router with the correct URL prefix
    3. Overriding the database dependency to use our SQLite test database

    Returns:
        A FastAPI app with all routes from all services mounted under
        their production URL prefixes.

    INTERVIEW NOTE:
        The prefix mapping here must match the production gateway's ROUTE_MAP.
        If the gateway routes ``/api/v1/auth/*`` to Core, then we include
        Core's auth router at ``prefix="/api/v1/auth"`` here. This ensures
        tests exercise the exact same URL paths as production.
    """
    global _verify_user_email_func
    composite = FastAPI(title="PulseBoard Test Composite")

    # ---- Core service routers (auth, users, uploads, notifications) ----

    # Import Core's auth_routes.py under a unique module name to avoid
    # conflicts with Community's modules
    core_auth_routes = _import_from_path(
        "core_app_auth_routes", SERVICES_DIR / "core" / "app" / "auth_routes.py"
    )
    composite.include_router(
        core_auth_routes.router, prefix="/api/v1/auth", tags=["auth"]
    )

    # Capture the verify_user_email function from Core's auth_services module.
    # We need this in the ``register_verified_user`` helper to programmatically
    # verify user emails (simulating clicking the verification link).
    auth_svc = sys.modules.get("app.auth_services")
    if auth_svc and hasattr(auth_svc, "verify_user_email"):
        _verify_user_email_func = auth_svc.verify_user_email

    # Import and mount Core's user routes
    core_user_routes = _import_from_path(
        "core_app_user_routes", SERVICES_DIR / "core" / "app" / "user_routes.py"
    )
    composite.include_router(
        core_user_routes.router, prefix="/api/v1/users", tags=["users"]
    )

    # Import and mount Core's upload routes
    core_upload_routes = _import_from_path(
        "core_app_upload_routes",
        SERVICES_DIR / "core" / "app" / "upload_routes.py",
    )
    composite.include_router(
        core_upload_routes.upload_router,
        prefix="/api/v1/uploads",
        tags=["uploads"],
    )

    # Import and mount Core's notification routes
    core_notification_routes = _import_from_path(
        "core_app_notification_routes",
        SERVICES_DIR / "core" / "app" / "notification_routes.py",
    )
    composite.include_router(
        core_notification_routes.router,
        prefix="/api/v1/notifications",
        tags=["notifications"],
    )

    # ---- Community service routers (forum, admin, chat) ----

    # Import Community's forum_routes.py — this module exports FOUR routers
    # (categories, threads, posts, search), each mounted at its own prefix
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

    # Import and mount Community's admin/moderation routes
    community_admin_routes = _import_from_path(
        "community_app_admin_routes",
        SERVICES_DIR / "community" / "app" / "admin_routes.py",
    )
    composite.include_router(
        community_admin_routes.router, prefix="/api/v1/admin", tags=["admin"]
    )

    # Import and mount Community's chat routes
    chat_routes = _import_from_path(
        "community_app_chat_routes",
        SERVICES_DIR / "community" / "app" / "chat_routes.py",
    )
    composite.include_router(chat_routes.router, prefix="/api/v1/chat", tags=["chat"])

    # Override the production database dependency with our SQLite test database.
    # Every route handler that depends on ``get_db`` will now receive a
    # SQLite session instead of a PostgreSQL session.
    composite.dependency_overrides[get_db] = override_get_db

    return composite


# Build the composite app at module load time (when conftest.py is imported).
# This means the app is built once and reused across all test modules.
app = _build_composite_app()

# Export the verify function so test helpers can use it
verify_user_email = _verify_user_email_func


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def setup_database() -> None:
    """Reset the database to a clean state before each test.

    INTERVIEW NOTE:
        ``autouse=True`` means this fixture runs automatically for EVERY test
        function — no need to explicitly include it as a parameter.

        The pattern is:
        1. Drop all tables (removes all data and schema)
        2. Recreate all tables (fresh schema from SQLAlchemy models)
        3. Seed default categories (needed by forum tests)

        This ensures complete test isolation — no test can be affected by
        data left behind by a previous test. The trade-off is speed: dropping
        and recreating tables for every test is slower than truncating, but
        it's more reliable and catches schema-related bugs.
    """
    # Drop all tables — complete clean slate
    Base.metadata.drop_all(bind=engine)
    # Recreate all tables from the SQLAlchemy model definitions
    Base.metadata.create_all(bind=engine)
    # Seed the default categories that forum tests depend on
    db = TestingSessionLocal()
    try:
        for item in DEFAULT_CATEGORIES:
            db.add(Category(**item))
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _mock_smtp():
    """Disable SMTP email sending during tests to avoid connection hangs.

    INTERVIEW NOTE:
        Without this fixture, tests that trigger email sending (registration,
        password reset, moderation notifications) would try to connect to an
        SMTP server. If no server is running, the test hangs for the SMTP
        timeout duration (2 seconds per attempt) or fails with a connection
        error.

        We use ``unittest.mock.patch`` to replace the email functions with
        no-ops (functions that return None immediately). The targets list
        is built dynamically by scanning ``sys.modules`` for any loaded
        module that has email-sending functions.

        ``yield`` makes this a "setup/teardown" fixture:
        - Before yield: patches are applied
        - After yield: patches are removed (functions restored to originals)
    """
    targets = [
        "shared.services.email._send_moderation_email",
    ]
    # Dynamically discover email-sending functions in all loaded modules.
    # This catches email functions loaded by different services' modules.
    for mod_name in list(sys.modules):
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        if "email" in mod_name and hasattr(mod, "_send_verification_email"):
            targets.append(f"{mod_name}._send_verification_email")
        if "email" in mod_name and hasattr(mod, "_send_moderation_email"):
            targets.append(f"{mod_name}._send_moderation_email")

    # Deduplicate targets (the same function might be found multiple times)
    targets = list(set(targets))

    # Apply all patches: each target function becomes a Mock that returns None
    patches = [patch(t) for t in targets]
    mocks = [p.start() for p in patches]
    for m in mocks:
        m.return_value = None
    yield
    # Teardown: remove all patches, restoring original functions
    for p in patches:
        p.stop()


@pytest.fixture
def client() -> TestClient:
    """Provide a FastAPI TestClient for making HTTP requests in tests.

    INTERVIEW NOTE:
        ``TestClient`` wraps the FastAPI app and provides a requests-like
        interface for testing. It runs the app in a background thread (not
        a separate process), so tests are fast and can share memory.

        Using ``with TestClient(app) as test_client`` triggers the app's
        lifespan events (startup/shutdown). ``yield`` makes the client
        available to the test function.
    """
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db_session() -> Session:
    """Provide a direct database session for test assertions.

    INTERVIEW NOTE:
        Sometimes tests need to query the database directly to verify
        side effects (e.g. check that an audit log was created, or that
        a user's ``is_verified`` flag was set to True). This fixture
        provides a raw SQLAlchemy session for those checks.

        This session is SEPARATE from the one used by the API routes
        (which get theirs via ``override_get_db``). This means you may
        need to call ``db_session.expire_all()`` to refresh cached data
        after making API calls that modify the database.
    """
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
    """Register a user, verify their email, and return the login response.

    This is a convenience helper that performs the full auth flow in one call:
    1. Register a new user via ``POST /api/v1/auth/register``
    2. Look up the verification token in the database
    3. Call the verify function directly (bypasses email delivery)
    4. Log in and return the response containing access_token and refresh_token

    Args:
        client: The TestClient to make HTTP requests with.
        email: Email address for the new user.
        username: Username for the new user.

    Returns:
        The JSON body from the login response, containing:
        - ``access_token``: JWT for authenticating subsequent requests
        - ``refresh_token``: JWT for obtaining new access tokens
        - User profile data

    INTERVIEW NOTE:
        In production, the user would receive a verification email with a
        link containing the token. Here we skip that by querying the database
        for the token directly and calling ``verify_user_email()`` programmatically.
        This is a common testing pattern — bypass external dependencies
        (email, SMS, etc.) and call the underlying function directly.
    """
    # Step 1: Register the user
    client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "username": username,
            "password": "supersecret",
        },
    )
    # Step 2: Find the verification token in the database
    db = TestingSessionLocal()
    try:
        # Get the most recently created verification token.
        # ``all()[-1]`` gets the last element (most recent token).
        token = db.execute(select(EmailVerificationToken)).scalars().all()[-1]
        # Step 3: Verify the user's email by calling the service function directly
        if verify_user_email:
            verify_user_email(db, token.token)
        else:
            raise RuntimeError("verify_user_email not loaded")
    finally:
        db.close()

    # Step 4: Log in with the verified account and return the response
    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "supersecret"},
    )
    return login_response.json()

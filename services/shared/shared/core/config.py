"""
Centralized configuration for the entire PulseBoard microservice platform.

WHY THIS FILE EXISTS:
    In a microservice architecture, you have multiple services (Core, Community,
    Gateway) that all need the same configuration values — database credentials,
    JWT secrets, OAuth keys, etc. Instead of duplicating config logic in each
    service, we define ONE Settings class in the shared library that every
    service imports.

HOW IT WORKS:
    We use pydantic-settings (not plain pydantic) because it automatically loads
    values from environment variables and .env files, with full type validation.
    This means:
      - In Docker: values come from docker-compose.yml environment blocks
      - In local dev: values come from a .env file at the project root
      - If a value is missing, the default defined here is used
      - If a value has the wrong type (e.g. "abc" for an int port), pydantic
        raises a validation error at startup — fail fast, not at runtime.

ARCHITECTURE FIT:
    This file lives in services/shared/ which is installed as an editable
    package (`pip install -e services/shared`) into every service's virtualenv.
    All three services import it as:
        from shared.core.config import settings

    This is the SINGLE SOURCE OF TRUTH for all configuration across the platform.
"""

from functools import lru_cache

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables / .env file.

    WHY pydantic-settings instead of os.getenv():
        - Typed: each field has a Python type annotation, so you get
          autocomplete, type checking, and automatic coercion (str -> int).
        - Validated: pydantic raises immediately if a required value is missing
          or has the wrong type — you find config errors at startup, not when
          a user hits an endpoint 3 hours later.
        - Documented: the class itself serves as documentation of every config
          knob the application supports, with sensible defaults.
        - Testable: you can instantiate Settings(secret_key="test") in tests
          to override specific values without touching environment variables.
    """

    # SettingsConfigDict tells pydantic-settings WHERE to find the .env file
    # and HOW to parse it. `extra="ignore"` means if the .env file has variables
    # that don't match any field here, they're silently ignored (useful because
    # the same .env file may contain variables for other tools like Docker).
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # -------------------------------------------------------------------------
    # General application settings
    # -------------------------------------------------------------------------
    project_name: str = "Advanced Real-Time Discussion Forum"
    environment: str = "development"  # "development", "production", "testing"
    backend_host: str = "0.0.0.0"  # Bind to all interfaces (required in Docker)
    backend_port: int = 8000  # Default port; each service overrides this
    frontend_url: str = "http://localhost:5173"  # Vite dev server URL, used for CORS
    api_v1_prefix: str = "/api/v1"  # All API routes are prefixed with this

    # -------------------------------------------------------------------------
    # Authentication & JWT settings
    # -------------------------------------------------------------------------
    # secret_key is used to sign JWT tokens (HS256). In production, this MUST
    # be overridden via environment variable. The "change-me" default ensures
    # local dev works out of the box but is obviously insecure.
    secret_key: str = "change-me"

    # database_url_override allows bypassing the constructed PostgreSQL URL
    # entirely. This is critical for two scenarios:
    #   1. Tests: pytest sets this to "sqlite:///test_services.db" so tests
    #      run against SQLite without needing a PostgreSQL server.
    #   2. Custom deployments: a managed database service (e.g. AWS RDS) may
    #      provide a full connection string that doesn't match our parts format.
    database_url_override: str | None = None

    # JWT algorithm. HS256 = HMAC-SHA256, a symmetric algorithm where the same
    # secret_key is used for both signing and verification. This works for our
    # architecture because the same shared library (and thus the same secret)
    # is used by all services. If services were owned by different teams, we'd
    # need RS256 (asymmetric) instead.
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30  # Short-lived for security
    refresh_token_expire_days: int = 7  # Longer-lived; stored in DB for revocation

    # -------------------------------------------------------------------------
    # PostgreSQL connection settings (individual parts)
    # These are combined into database_url by the computed_field below.
    # In Docker Compose, "db" resolves to the PostgreSQL container hostname.
    # -------------------------------------------------------------------------
    postgres_server: str = "db"
    postgres_port: int = 5432
    postgres_db: str = "discussion_forum"
    postgres_user: str = "forum_user"
    postgres_password: str = "forum_password"

    # -------------------------------------------------------------------------
    # Redis settings
    # Redis is used for pub/sub event broadcasting between services. When
    # a new post is created in the Community service, it publishes an event
    # to Redis. The Gateway subscribes and pushes it over WebSocket to
    # connected browsers. This decouples the services — Community doesn't
    # need to know about WebSocket connections.
    # -------------------------------------------------------------------------
    redis_url: str = "redis://redis:6379/0"

    # -------------------------------------------------------------------------
    # Email / SMTP settings
    # In development, we use MailHog (a fake SMTP server) so emails are
    # captured and viewable at http://localhost:8025 without sending real mail.
    # In production, these would point to a real SMTP provider.
    # -------------------------------------------------------------------------
    mail_from: str = "no-reply@example.com"
    mail_server: str = "mailhog"
    mail_port: int = 1025  # MailHog's SMTP port (not the standard 587/465)

    # -------------------------------------------------------------------------
    # OAuth2 settings (Google + GitHub)
    # These are empty by default because OAuth requires registering your app
    # with each provider to get client credentials. The app works without OAuth
    # (users can still register with email/password); OAuth is optional.
    # -------------------------------------------------------------------------
    google_client_id: str = ""
    google_client_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    # Base URL for OAuth redirect callbacks (where providers send users back)
    oauth_redirect_base: str = "http://localhost:8000"
    # After OAuth completes, the backend redirects the browser here with tokens
    oauth_frontend_success_url: str = "http://localhost:5173/login"

    # -------------------------------------------------------------------------
    # File upload settings
    # Avatars, attachments, etc. are stored on disk under this directory.
    # In Docker, this path is inside the container. The Gateway proxies
    # /uploads/* requests to the Core service which serves these files.
    # -------------------------------------------------------------------------
    upload_dir: str = "uploads"
    max_upload_size_mb: int = 25

    # Whether to create default forum categories (General, Help, etc.) on
    # first startup. Useful for fresh deployments; the seed script is more
    # comprehensive for demo/dev environments.
    seed_default_categories_on_startup: bool = False

    # -------------------------------------------------------------------------
    # AI Bot settings (@pulse bot)
    # The bot uses Groq's API (which provides fast inference for LLMs) with
    # the "compound-mini" model that has built-in web search capabilities.
    # Tavily is the primary web search provider; DuckDuckGo is the fallback.
    # -------------------------------------------------------------------------
    groq_api_key: str = ""  # Empty = bot disabled (no API calls made)
    groq_model: str = "groq/compound-mini"
    groq_api_url: str = "https://api.groq.com/openai/v1/chat/completions"
    tavily_api_key: str = ""

    # -------------------------------------------------------------------------
    # Service discovery URLs (microservice-to-microservice communication)
    # These are Docker Compose service hostnames. The Gateway uses these to
    # proxy incoming HTTP requests to the correct backend service:
    #   /api/v1/auth/*    -> core:8001
    #   /api/v1/threads/* -> community:8002
    # In Docker networking, "core" and "community" resolve to container IPs.
    # -------------------------------------------------------------------------
    core_service_url: str = "http://core:8001"
    community_service_url: str = "http://community:8002"
    # Chat was originally a separate service but was consolidated into Community
    # (see ADR-0001). This field is kept so any old config referencing
    # chat_service_url still works — it just points to the same place.
    chat_service_url: str = "http://community:8002"
    gateway_port: int = 8000

    @computed_field
    @property
    def database_url(self) -> str:
        """
        Construct the full database connection URL.

        WHY computed_field:
            This is a Pydantic v2 pattern that creates a field whose value is
            derived from other fields. It appears in serialization (e.g. if you
            dump settings to JSON for debugging) just like a regular field, but
            its value is computed on access.

        WHY the override pattern:
            We want two ways to configure the database:
            1. Set individual parts (postgres_server, postgres_port, etc.) — this
               is the default for Docker Compose where each part is a separate
               env var matching the docker-compose.yml service definitions.
            2. Set database_url_override to a full connection string — this is
               used by tests (SQLite) and managed database services (e.g.
               "postgresql+psycopg://user:pass@rds-host:5432/mydb").

            The override takes priority because it's the more explicit option.

        DRIVER NOTE:
            We use "postgresql+psycopg" (psycopg 3), NOT "postgresql+psycopg2"
            (psycopg 2). psycopg 3 is the modern async-capable PostgreSQL driver
            with better performance and native asyncio support.
        """
        if self.database_url_override:
            return self.database_url_override
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_server}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field
    @property
    def allowed_origins(self) -> list[str]:
        """
        CORS allowed origins list.

        WHY this exists as a computed field:
            FastAPI's CORSMiddleware expects a list of allowed origins. By
            deriving it from frontend_url, we ensure CORS and the frontend URL
            are always in sync. If we ever need to allow multiple origins
            (e.g. a staging frontend + production frontend), we'd extend this
            list here rather than changing CORS config in each service.

        SECURITY NOTE:
            In production, this should NEVER be ["*"]. Restricting origins to
            the exact frontend domain prevents other websites from making
            authenticated API requests on behalf of our users (CSRF-like attacks).
        """
        return [self.frontend_url]


@lru_cache
def get_settings() -> Settings:
    """
    Create and cache a singleton Settings instance.

    WHY lru_cache (singleton pattern):
        Parsing environment variables and .env files has a cost. More
        importantly, we want every part of the application to use the SAME
        Settings object — not create a new one each time and potentially get
        different values if env vars change mid-process.

        @lru_cache with no arguments means: call this function once, cache the
        result forever, and return the cached result on all subsequent calls.
        This is Python's idiomatic singleton pattern for immutable objects.

    WHY a function and not just `Settings()` at module level:
        Wrapping it in a function makes it easy to override in tests using
        FastAPI's dependency_overrides or by clearing the cache:
            get_settings.cache_clear()
        Direct module-level instantiation would be harder to patch.
    """
    return Settings()


# Module-level convenience instance. Most of the codebase does:
#     from shared.core.config import settings
# rather than calling get_settings() each time. This works because Python
# modules are only executed once and then cached in sys.modules, so this
# line runs exactly once at import time across the entire process.
settings = get_settings()

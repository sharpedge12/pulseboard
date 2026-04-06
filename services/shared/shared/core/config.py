from functools import lru_cache

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    project_name: str = "Advanced Real-Time Discussion Forum"
    environment: str = "development"
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    frontend_url: str = "http://localhost:5173"
    api_v1_prefix: str = "/api/v1"
    secret_key: str = "change-me"
    database_url_override: str | None = None
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    postgres_server: str = "db"
    postgres_port: int = 5432
    postgres_db: str = "discussion_forum"
    postgres_user: str = "forum_user"
    postgres_password: str = "forum_password"
    redis_url: str = "redis://redis:6379/0"
    mail_from: str = "no-reply@example.com"
    mail_server: str = "mailhog"
    mail_port: int = 1025
    google_client_id: str = ""
    google_client_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    oauth_redirect_base: str = "http://localhost:8000"
    oauth_frontend_success_url: str = "http://localhost:5173/login"
    upload_dir: str = "uploads"
    max_upload_size_mb: int = 25
    seed_default_categories_on_startup: bool = False
    groq_api_key: str = ""
    groq_model: str = "groq/compound-mini"
    groq_api_url: str = "https://api.groq.com/openai/v1/chat/completions"
    tavily_api_key: str = ""

    # Service ports (microservice mode)
    core_service_url: str = "http://core:8001"
    community_service_url: str = "http://community:8002"
    # Chat is now part of the community service (consolidated).
    # Kept for backward compatibility — defaults to community URL.
    chat_service_url: str = "http://community:8002"
    gateway_port: int = 8000

    @computed_field
    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_server}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field
    @property
    def allowed_origins(self) -> list[str]:
        return [self.frontend_url]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

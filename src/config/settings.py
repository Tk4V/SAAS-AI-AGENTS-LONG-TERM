"""Application configuration loaded from environment variables.

All values are read once at process startup and cached. The same `Settings`
instance is reused everywhere via `get_settings()`, including by Alembic when
it imports this module to resolve the database URL for migrations.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    app_env: Literal["local", "dev", "prod"] = "local"
    app_name: str = "clyde-ai"
    app_version: str = "0.1.0"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    debug: bool = False

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8000
    api_prefix: str = "/api/v1"
    allowed_origins_raw: str = Field(
        default="http://localhost:3000",
        alias="ALLOWED_ORIGINS",
        description="Comma-separated list of allowed CORS origins.",
    )

    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "clyde"
    db_user: str = "clyde"
    db_password: SecretStr = SecretStr("clyde")
    db_ssl: str = Field(
        default="disable",
        description="Set to 'require' for RDS, 'disable' for local Docker Postgres.",
    )
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle_sec: int = 1800

    jwt_secret: SecretStr = SecretStr("change-me-shared-with-django")
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_audience: str = "clyde-ai"

    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model_opus: str = "claude-opus-4-6"
    anthropic_model_sonnet: str = "claude-sonnet-4-6"
    anthropic_model_haiku: str = "claude-haiku-4-5"
    anthropic_max_tokens: int = 8192

    voyage_api_key: SecretStr = SecretStr("")
    voyage_model: str = "voyage-3-large"
    voyage_dimensions: int = 1024

    github_oauth_client_id: SecretStr = SecretStr("")
    github_oauth_client_secret: SecretStr = SecretStr("")
    github_webhook_secret: SecretStr = SecretStr("")
    github_api_base: str = "https://api.github.com"

    oauth_state_ttl_sec: int = 600
    oauth_callback_base_url: str = "http://localhost:8000"
    frontend_redirect_url: str = "http://localhost:3000/integrations"

    langchain_tracing_v2: bool = False
    langchain_api_key: SecretStr = SecretStr("")
    langchain_project: str = "clyde-ai"
    langchain_endpoint: str = "https://api.smith.langchain.com"

    sandbox_timeout_sec: int = 300
    sandbox_memory_limit: str = "2g"
    sandbox_cpu_limit: float = 1.0
    sandbox_network: str = "none"

    max_fix_attempts: int = 3
    max_review_iterations: int = 3
    max_qa_iterations: int = 3

    fernet_key: SecretStr = SecretStr("")

    sentry_dsn: str | None = None
    prometheus_enabled: bool = False

    @property
    def allowed_origins(self) -> list[str]:
        """Split the comma-separated ALLOWED_ORIGINS string into a list."""
        return [origin.strip() for origin in self.allowed_origins_raw.split(",") if origin.strip()]

    def _build_db_url(self, driver: str) -> str:
        """Compose a SQLAlchemy URL using the structured DB_ fields.

        URL.create handles percent-encoding of special characters in the
        password, which matters for our RDS users that use punctuation.
        """
        query: dict[str, str] = {}
        if self.db_ssl and self.db_ssl != "disable":
            # asyncpg accepts "ssl", psycopg and libpq accept "sslmode".
            ssl_key = "ssl" if "asyncpg" in driver else "sslmode"
            query[ssl_key] = self.db_ssl
        return URL.create(
            drivername=driver,
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            query=query,
        ).render_as_string(hide_password=False)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """Async URL used by the application engine."""
        return self._build_db_url("postgresql+asyncpg")

    @property
    def database_url_sync(self) -> str:
        """Synchronous URL for tools that do not speak asyncpg."""
        return self._build_db_url("postgresql+psycopg")

    @property
    def database_url_libpq(self) -> str:
        """Plain libpq URL used by psycopg-based libraries (LangGraph checkpointer)."""
        return self._build_db_url("postgresql")

    @property
    def is_production(self) -> bool:
        return self.app_env == "prod"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

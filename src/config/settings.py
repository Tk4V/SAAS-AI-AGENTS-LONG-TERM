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

    # AWS Secrets Manager — when both are set the DB password is fetched from SM
    # instead of DB_PASSWORD, enabling automatic rotation without env-file changes.
    aws_secret_manager: str = Field(
        default="",
        description="Secrets Manager secret name that holds the DB password JSON.",
    )
    aws_region: str = Field(
        default="",
        description="AWS region for the Secrets Manager client (e.g. 'us-east-1').",
    )
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle_sec: int = 1800

    jwt_secret: SecretStr = SecretStr("change-me-shared-with-django")
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_audience: str = "clyde-ai"

    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model_haiku: str = "claude-haiku-4-5"

    github_oauth_client_id: SecretStr = SecretStr("")
    github_oauth_client_secret: SecretStr = SecretStr("")
    github_webhook_secret: SecretStr = SecretStr("")
    github_api_base: str = "https://api.github.com"

    jira_oauth_client_id: SecretStr = SecretStr("")
    jira_oauth_client_secret: SecretStr = SecretStr("")

    oauth_state_ttl_sec: int = 600
    oauth_callback_base_url: str = "http://localhost:8000"
    frontend_redirect_url: str = "http://localhost:3000/integrations"

    max_fix_attempts: int = 3

    fernet_key: SecretStr = SecretStr("")

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
    def database_url_no_password(self) -> str:
        """Async URL with an empty password — used when a Secrets Manager callable
        supplies the credential at connect time via ``connect_args``."""
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.db_user,
            password="",
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            query={"ssl": self.db_ssl} if self.db_ssl and self.db_ssl != "disable" else {},
        ).render_as_string(hide_password=False)

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

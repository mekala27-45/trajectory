"""Configuration, validated at import and failing fast.

A results service that starts happily with no database URL and no API key, then returns
500s on the first write, is worse than one that refuses to start. Every setting that the
service cannot function without is required here, and the error names the environment
variable rather than surfacing as a stack trace three requests later.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Everything the service reads from the environment."""

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = Field(
        description=(
            "SQLAlchemy URL for Postgres. Use the psycopg driver, for example "
            "postgresql+psycopg://user:pass@host/db. Neon's pooled connection string works."
        )
    )
    trajectory_api_key: str = Field(
        min_length=8,
        description=(
            "Bearer token required on write routes. Read routes are public. A short or "
            "empty key is refused at startup rather than accepted and then relied upon."
        ),
    )
    trajectory_cors_origins: str = Field(
        default="http://localhost:3000",
        description="Comma separated list of allowed origins for browser requests.",
    )
    trajectory_log_format: Literal["json", "console"] = Field(
        default="json", description="JSON in production, pretty in development."
    )
    trajectory_log_level: str = Field(default="INFO", description="Minimum log level.")
    trajectory_rate_limit_writes_per_minute: int = Field(
        default=30,
        ge=1,
        le=10_000,
        description="Write requests allowed per minute per client. Reads are not limited.",
    )
    trajectory_max_bundle_runs: int = Field(
        default=200,
        ge=1,
        description="Runs accepted in one ingest request. Larger bundles have to be chunked.",
    )
    trajectory_public_base_url: str = Field(
        default="", description="Public URL of this service, used in the OpenAPI servers list."
    )
    trajectory_db_pool_size: int = Field(
        default=5,
        ge=1,
        le=50,
        description=(
            "Connection pool size. Keep this small: the free tiers this is designed for "
            "cap connections, and one machine serving a read mostly API does not need more."
        ),
    )

    @field_validator("database_url")
    @classmethod
    def _must_be_postgres(cls, value: str) -> str:
        """Refuse a URL the service cannot actually use.

        SQLite would appear to work and then lose the data on the next deploy, because the
        filesystem of a scale-to-zero machine is not durable. Failing here is kinder.
        """
        if not value.startswith(
            ("postgresql://", "postgresql+psycopg://", "postgresql+asyncpg://")
        ):
            raise ValueError(
                f"DATABASE_URL must be a Postgres URL, got {value.split(':', 1)[0]!r}. "
                "The service stores results that have to outlive a deploy."
            )
        return value

    @property
    def sqlalchemy_url(self) -> str:
        """Normalise to the psycopg driver the service ships with."""
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        return self.database_url

    @property
    def cors_origins(self) -> list[str]:
        """Allowed origins as a list, with blanks dropped."""
        return [
            origin.strip() for origin in self.trajectory_cors_origins.split(",") if origin.strip()
        ]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process.

    Raises:
        pydantic.ValidationError: If a required variable is missing. That is deliberate:
            the service refuses to start rather than accepting requests it cannot serve.
    """
    return Settings.model_validate({})

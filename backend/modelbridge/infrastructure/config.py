import base64
import ipaddress
from typing import Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MODELBRIDGE_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    database_url: SecretStr
    encryption_keys: SecretStr
    environment: str = Field(default="development", pattern="^(development|production|test)$")
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    worker_id: str = Field(default="worker-1", min_length=1, max_length=64, pattern=r"^[\w-]+$")
    heartbeat_seconds: int = Field(default=5, ge=1, le=10)
    worker_stale_seconds: int = Field(default=30, ge=15, le=120)
    registration_enabled: bool = False
    session_seconds: int = Field(default=3600, ge=300, le=86400)
    max_request_bytes: int = Field(default=65536, ge=1024, le=1048576)
    requests_per_minute: int = Field(default=120, ge=10, le=10000)
    anonymous_requests_per_minute: int = Field(default=30, ge=5, le=1000)
    allowed_database_networks: list[str] = Field(default_factory=list)
    connection_timeout_seconds: int = Field(default=5, ge=1, le=15)
    max_connections_per_workspace: int = Field(default=10, ge=1, le=100)
    schema_discoveries_per_minute: int = Field(default=5, ge=1, le=60)
    max_batch_size: int = Field(default=100, ge=1, le=500)
    max_active_jobs_per_workspace: int = Field(default=2, ge=1, le=10)
    job_retry_limit: int = Field(default=3, ge=0, le=5)
    allowed_webhook_networks: list[str] = Field(default_factory=list)

    @field_validator("allowed_database_networks", "allowed_webhook_networks")
    @classmethod
    def check_networks(cls, value: list[str]) -> list[str]:
        return [str(ipaddress.ip_network(network, strict=False)) for network in value]

    @field_validator("database_url")
    @classmethod
    def check_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
        except ArgumentError as error:
            raise ValueError("A valid PostgreSQL metadata URL is required") from error
        if url.drivername != "postgresql+psycopg" or not url.host or not url.database:
            raise ValueError("Use postgresql+psycopg with an explicit host and database")
        if url.password is None or len(url.password) < 16:
            raise ValueError("Metadata password must contain at least 16 characters")
        return value

    @field_validator("encryption_keys")
    @classmethod
    def check_keys(cls, value: SecretStr) -> SecretStr:
        keys = value.get_secret_value().split(",")
        try:
            decoded = [base64.b64decode(key, altchars=b"-_", validate=True) for key in keys]
        except (ValueError, TypeError) as error:
            raise ValueError("Encryption keys must be URL-safe base64 Fernet keys") from error
        if not keys or any(len(key) != 32 for key in decoded) or len(set(keys)) != len(keys):
            raise ValueError("Provide distinct 32-byte Fernet keys, newest first")
        return value

    @field_validator("cors_origins")
    @classmethod
    def check_origins(cls, value: list[str]) -> list[str]:
        from urllib.parse import urlsplit

        for origin in value:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("CORS origins must be explicit HTTP(S) origins, not wildcards")
        return value

    @model_validator(mode="after")
    def check_production(self) -> Self:
        if self.environment == "production":
            url = make_url(self.database_url.get_secret_value())
            if url.query.get("sslmode") != "verify-full":
                raise ValueError("Production metadata connections require sslmode=verify-full")
            if any(not origin.startswith("https://") for origin in self.cors_origins):
                raise ValueError("Production CORS origins must use HTTPS")
        return self

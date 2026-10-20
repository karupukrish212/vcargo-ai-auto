from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration loaded from environment variables and a local, untracked .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    mysql_host: str = ""
    mysql_port: int = Field(default=3306, ge=1, le=65535)
    mysql_database: str = ""
    mysql_user: str = ""
    mysql_password: SecretStr = SecretStr("")
    mysql_connect_timeout_seconds: int = Field(default=5, ge=1, le=60)
    mysql_read_timeout_seconds: int = Field(default=10, ge=1, le=120)
    mysql_execution_timeout_ms: int = Field(default=5000, ge=1, le=120000)
    mysql_pool_size: int = Field(default=5, ge=1, le=50)
    mysql_pool_recycle_seconds: int = Field(default=1800, ge=60)
    mysql_ssl_ca: Path | None = None
    mysql_ssl_cert: Path | None = None
    mysql_ssl_key: Path | None = None
    schema_output_path: Path = Path("metadata/generated/schema_raw.json")

    @model_validator(mode="before")
    @classmethod
    def empty_optional_paths(cls, data: object) -> object:
        if isinstance(data, dict):
            data = dict(data)
            for key in ("mysql_ssl_ca", "mysql_ssl_cert", "mysql_ssl_key"):
                if data.get(key) == "":
                    data[key] = None
        return data

    @model_validator(mode="after")
    def validate_database_safety(self) -> "Settings":
        if self.mysql_user.casefold() in {"root", "admin", "administrator"}:
            raise ValueError("Use a dedicated read-only MySQL account")
        if self.mysql_database.casefold() in {
            "mysql",
            "sys",
            "information_schema",
            "performance_schema",
        }:
            raise ValueError("The target must be an application database")
        if bool(self.mysql_ssl_cert) != bool(self.mysql_ssl_key):
            raise ValueError("MYSQL_SSL_CERT and MYSQL_SSL_KEY must be supplied together")
        if self.mysql_ssl_cert and not self.mysql_ssl_ca:
            raise ValueError("Client TLS certificates require MYSQL_SSL_CA")
        if self.app_env == "production" and self.database_configured and not self.mysql_ssl_ca:
            raise ValueError("Production database connections require MYSQL_SSL_CA")
        return self

    @property
    def database_configured(self) -> bool:
        return all(
            (
                self.mysql_host,
                self.mysql_database,
                self.mysql_user,
                self.mysql_password.get_secret_value(),
            )
        )

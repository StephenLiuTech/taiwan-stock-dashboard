"""Validated application settings."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed settings for the PAMS application."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="PAMS_",
        extra="ignore",
    )

    environment: Literal["development", "test", "staging", "production"] = Field(
        default="development"
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO"
    )
    database_url: str = Field(default="sqlite:///data/pams.db")
    app_title: str = Field(default="PAMS")
    email_transport: Literal["microsoft_graph", "resend", "smtp"] = "resend"
    resend_api_key: SecretStr | None = None
    microsoft_client_id: str | None = None
    microsoft_tenant: str = "consumers"
    microsoft_token_cache: Path | None = Path("data/msal_token_cache.json")
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    email_from: str | None = None
    email_to: str | None = None

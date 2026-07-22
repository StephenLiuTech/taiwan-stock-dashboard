"""Validated loading for non-secret YAML configuration."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

from core.constants import PROJECT_ROOT
from domain import Currency


class ApplicationMetadata(BaseModel):
    """User-visible application metadata."""

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)


class DatabaseConfig(BaseModel):
    """Non-secret local database configuration."""

    path: str = Field(min_length=1)


class PortfolioDisplayConfig(BaseModel):
    """Portfolio presentation defaults."""

    decimal_places: int = Field(ge=0, le=6)
    show_zero_positions: bool


class AppConfig(BaseModel):
    """Validated application configuration document."""

    application: ApplicationMetadata
    default_currency: Currency
    database: DatabaseConfig
    portfolio_display: PortfolioDisplayConfig


class LoggingConfig(BaseModel):
    """Validated logging configuration document."""

    level: str = Field(pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    format: str = Field(min_length=1)


class ConfigurationError(ValueError):
    """Raised when a YAML configuration document is invalid."""


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ConfigurationError(f"Unable to load configuration: {path}") from error
    if not isinstance(raw, dict):
        raise ConfigurationError(f"Configuration must be a mapping: {path}")
    return raw


def load_app_config(path: Path | None = None) -> AppConfig:
    """Load and validate application YAML."""
    config_path = path or PROJECT_ROOT / "config" / "app.yaml"
    try:
        return AppConfig.model_validate(_read_yaml(config_path))
    except ValidationError as error:
        raise ConfigurationError(
            f"Invalid application configuration: {config_path}"
        ) from error


def load_logging_config(path: Path | None = None) -> LoggingConfig:
    """Load and validate logging YAML."""
    config_path = path or PROJECT_ROOT / "config" / "logging.yaml"
    try:
        return LoggingConfig.model_validate(_read_yaml(config_path))
    except ValidationError as error:
        raise ConfigurationError(
            f"Invalid logging configuration: {config_path}"
        ) from error

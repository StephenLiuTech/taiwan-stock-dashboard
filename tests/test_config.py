"""Smoke tests for application configuration."""

import pytest

from config.loader import get_settings
from config.settings import Settings


def test_settings_have_safe_local_defaults() -> None:
    """Settings expose a usable local-development baseline."""
    settings = Settings(_env_file=None)

    assert settings.environment == "development"
    assert settings.database_url.startswith("sqlite:///")


def test_settings_load_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings load supported PAMS-prefixed environment variables."""
    monkeypatch.setenv("PAMS_ENVIRONMENT", "test")
    monkeypatch.setenv("PAMS_APP_TITLE", "PAMS smoke test")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.environment == "test"
    assert settings.app_title == "PAMS smoke test"
    get_settings.cache_clear()

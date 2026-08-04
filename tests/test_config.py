"""Smoke tests for application configuration."""

from decimal import Decimal

import pytest

from config.loader import get_settings
from config.settings import Settings


def test_settings_have_safe_local_defaults() -> None:
    """Settings expose a usable local-development baseline."""
    settings = Settings(_env_file=None)

    assert settings.environment == "development"
    assert settings.database_url.startswith("sqlite:///")
    assert settings.email_transport == "resend"
    assert settings.microsoft_tenant == "consumers"
    assert settings.microsoft_token_cache.as_posix() == "data/msal_token_cache.json"
    assert settings.market_http_timeout_seconds == 15
    assert settings.market_http_attempts == 4


def test_settings_load_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings load supported PAMS-prefixed environment variables."""
    monkeypatch.setenv("PAMS_ENVIRONMENT", "test")
    monkeypatch.setenv("PAMS_APP_TITLE", "PAMS smoke test")
    monkeypatch.setenv("PAMS_MARKET_HTTP_TIMEOUT_SECONDS", "20")
    monkeypatch.setenv("PAMS_MARKET_HTTP_ATTEMPTS", "3")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.environment == "test"
    assert settings.app_title == "PAMS smoke test"
    assert settings.market_http_timeout_seconds == 20
    assert settings.market_http_attempts == 3
    get_settings.cache_clear()


def test_smtp_password_is_secret_and_not_exposed_in_settings_repr() -> None:
    settings = Settings(_env_file=None, smtp_password="super-secret")

    assert settings.smtp_password is not None
    assert settings.smtp_password.get_secret_value() == "super-secret"
    assert "super-secret" not in repr(settings)


def test_resend_api_key_is_secret_and_not_exposed_in_settings_repr() -> None:
    settings = Settings(_env_file=None, resend_api_key="re_super-secret")

    assert settings.resend_api_key is not None
    assert settings.resend_api_key.get_secret_value() == "re_super-secret"
    assert "re_super-secret" not in repr(settings)


def test_modular_report_settings_have_safe_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.report_show_allocation is True
    assert settings.report_show_transactions is True
    assert settings.report_event_horizon_days == 30
    assert settings.report_dividend_scope == "current_year"
    assert settings.report_hide_empty_optional_sections is True
    assert settings.report_news_limit == 5
    assert settings.risk_single_holding_warning_pct == Decimal("30")

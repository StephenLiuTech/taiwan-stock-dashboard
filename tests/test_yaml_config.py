"""Validated YAML configuration tests."""

from pathlib import Path

import pytest

from config.yaml_loader import ConfigurationError, load_app_config, load_logging_config
from domain import Currency


def test_application_yaml_loads() -> None:
    config = load_app_config()
    assert config.application.version == "0.8.0"
    assert config.default_currency is Currency.TWD


def test_logging_yaml_loads() -> None:
    assert load_logging_config().level == "INFO"


def test_invalid_yaml_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text("application: missing-required-sections", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_app_config(path)

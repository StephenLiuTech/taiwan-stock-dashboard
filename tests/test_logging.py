"""Smoke tests for logging setup."""

import logging

from core.logging import configure_logging, get_logger


def test_logging_setup_configures_root_logger() -> None:
    """Logging setup applies the requested level and exposes named loggers."""
    configure_logging("warning")

    assert logging.getLogger().level == logging.WARNING
    assert get_logger("pams.smoke").name == "pams.smoke"

"""Smoke tests for logging setup."""

import logging

from core.logging import configure_logging, get_logger


def test_logging_setup_configures_root_logger() -> None:
    """Logging setup applies the requested level and exposes named loggers."""
    configure_logging("warning")

    assert logging.getLogger().level == logging.WARNING
    assert get_logger("pams.smoke").name == "pams.smoke"


def test_verbose_logging_keeps_pams_debug_and_suppresses_noisy_libraries() -> None:
    """Verbose mode remains useful without leaking image-library internals."""
    configure_logging("debug")

    assert (
        get_logger("pams.application.send_daily_report").getEffectiveLevel()
        == logging.DEBUG
    )
    assert logging.getLogger("matplotlib").getEffectiveLevel() == logging.WARNING
    assert (
        logging.getLogger("matplotlib.font_manager").getEffectiveLevel()
        == logging.WARNING
    )
    assert logging.getLogger("PIL").getEffectiveLevel() == logging.WARNING

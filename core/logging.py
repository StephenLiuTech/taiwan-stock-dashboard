"""Central logging configuration."""

import logging

from core.constants import DEFAULT_LOG_FORMAT

_NOISY_THIRD_PARTY_LOGGERS = (
    "matplotlib",
    "matplotlib.font_manager",
    "PIL",
)


def configure_logging(
    level: str = "INFO", log_format: str = DEFAULT_LOG_FORMAT
) -> None:
    """Configure the process-wide standard-library logger."""
    logging.basicConfig(
        level=level.upper(),
        format=log_format,
        force=True,
    )
    for logger_name in _NOISY_THIRD_PARTY_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger for a module."""
    return logging.getLogger(name)

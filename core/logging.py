"""Central logging configuration."""

import logging

from core.constants import DEFAULT_LOG_FORMAT


def configure_logging(
    level: str = "INFO", log_format: str = DEFAULT_LOG_FORMAT
) -> None:
    """Configure the process-wide standard-library logger."""
    logging.basicConfig(
        level=level.upper(),
        format=log_format,
        force=True,
    )


def get_logger(name: str) -> logging.Logger:
    """Return a named logger for a module."""
    return logging.getLogger(name)

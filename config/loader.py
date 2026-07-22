"""Configuration loading helpers."""

from functools import lru_cache

from config.settings import Settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache validated application settings."""
    return Settings()

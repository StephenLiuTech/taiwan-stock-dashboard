"""Local database setup for PAMS."""

from database.schema import initialize_schema
from database.sqlite import initialize_database

__all__ = ["initialize_database", "initialize_schema"]

"""Local database setup for PAMS."""

from database.provider import DatabaseHandle, open_database
from database.schema import initialize_schema
from database.sqlite import initialize_database

__all__ = [
    "DatabaseHandle",
    "initialize_database",
    "initialize_schema",
    "open_database",
]

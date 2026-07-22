"""Application-layer exception types."""


class ApplicationError(Exception):
    """Base class for failures owned by an application workflow."""


class ProductionDatabaseProtectedError(ApplicationError, ValueError):
    """Raised when demo generation targets the production database."""

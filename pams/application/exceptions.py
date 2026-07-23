"""Application-layer exception types."""


class ApplicationError(Exception):
    """Base class for failures owned by an application workflow."""


class ProductionDatabaseProtectedError(ApplicationError, ValueError):
    """Raised when demo generation targets the production database."""


class HoldingRebuildError(ApplicationError, ValueError):
    """Base class for holding rebuild safety failures."""


class EmptyTransactionHistoryError(HoldingRebuildError):
    """Raised when applying without any transaction history."""


class UnmatchedHoldingsError(HoldingRebuildError):
    """Raised when migration warnings have not been explicitly accepted."""


class DuplicateTransactionError(ApplicationError, ValueError):
    """Raised when a transaction ID already exists."""


class MissingQuoteError(ApplicationError, ValueError):
    """Raised when a holding cannot be valued with a latest quote."""

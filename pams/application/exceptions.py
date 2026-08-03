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


class HoldingQueryError(ApplicationError, ValueError):
    """Base class for transaction-derived holding query failures."""


class HoldingNotFoundError(HoldingQueryError):
    """Raised when a requested active holding symbol does not exist."""


class AmbiguousHoldingSymbolError(HoldingQueryError):
    """Raised when one symbol identifies active holdings in multiple markets."""


class InvalidHoldingHistoryError(HoldingQueryError):
    """Raised when the transaction ledger cannot produce valid holdings."""


class MissingQuoteError(ApplicationError, ValueError):
    """Raised when a holding cannot be valued with a latest quote."""


class ValuationDataUnavailableError(ApplicationError, ValueError):
    """Raised when no holdings are available for current valuation."""


class ValuationRepositoryError(ApplicationError):
    """Raised when valuation inputs cannot be loaded from persistence."""


class PortfolioAnalyticsError(ApplicationError, ValueError):
    """Base class for analytics application workflow failures."""


class InvalidAnalyticsPeriodError(PortfolioAnalyticsError):
    """Raised when the requested analytics start follows its end."""


class AnalyticsDataUnavailableError(PortfolioAnalyticsError):
    """Raised when no valid snapshot history exists for a requested period."""


class AnalyticsRepositoryError(PortfolioAnalyticsError):
    """Raised when snapshot history cannot be loaded from persistence."""


class AnalyticsProcessingError(PortfolioAnalyticsError):
    """Raised when analytics processing fails unexpectedly."""

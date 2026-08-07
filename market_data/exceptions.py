"""Typed market-data ingestion failures."""

from datetime import date

from domain import Market


class MarketDataError(RuntimeError):
    """Base class for market-data ingestion failures."""


class ProviderDataError(MarketDataError):
    """The provider failed or returned structurally invalid data."""


class ProviderRateLimitError(ProviderDataError):
    """The configured provider rejected a request because its quota was exhausted."""


class ProviderAuthenticationError(ProviderDataError):
    """The provider rejected or could not validate configured credentials."""


class ProviderSymbolError(ProviderDataError):
    """The provider rejected one requested instrument symbol."""


class ProviderInformationError(ProviderDataError):
    """The provider returned a non-specific informational response."""


class MarketDateUnavailableError(ProviderDataError):
    """The official date-query endpoint has no dataset for one requested date."""


class SourceDateError(ProviderDataError):
    """The authoritative source date is absent, mixed, or stale."""


class SourceDateMismatchError(SourceDateError):
    """The official dataset is not for the requested trading date."""

    def __init__(self, market: Market, requested: date, actual: date) -> None:
        self.market = market
        self.requested = requested
        self.actual = actual
        super().__init__(
            f"{market.value} source date {actual.isoformat()} does not match "
            f"requested date {requested.isoformat()}"
        )


class SymbolNotFoundError(MarketDataError):
    """A requested symbol is absent from an otherwise valid dataset."""


class SuspendedSecurityError(MarketDataError):
    """A requested security has no usable closing trade."""

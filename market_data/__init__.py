"""Official Taiwan market-data ingestion components."""

from market_data.dividend_payments import (
    MOPSDividendPaymentProvider,
    OfficialDividendPaymentProvider,
    RawDividendPayment,
)
from market_data.dividends import (
    CompositeDividendSource,
    OfficialDividendProvider,
    RawDividendEvent,
    TPExDividendProvider,
    TPExHistoricalDividendProvider,
    TWSEDividendProvider,
    TWSEHistoricalDividendProvider,
)
from market_data.engine import MarketDataEngine, MarketDataRefreshResult
from market_data.exceptions import (
    MarketDataError,
    MarketDateUnavailableError,
    ProviderDataError,
    SourceDateError,
    SourceDateMismatchError,
    SuspendedSecurityError,
    SymbolNotFoundError,
    TemporaryProviderUnavailableError,
)
from market_data.normalizer import QuoteNormalizationError, QuoteNormalizer
from market_data.providers import (
    CurrentTPExProvider,
    DateAwareTPExProvider,
    HistoricalTPExProvider,
    HistoricalTWSEProvider,
    TPExProvider,
    TWSEProvider,
)

__all__ = [
    "HistoricalTPExProvider",
    "CurrentTPExProvider",
    "DateAwareTPExProvider",
    "HistoricalTWSEProvider",
    "CompositeDividendSource",
    "MarketDataEngine",
    "MarketDateUnavailableError",
    "MarketDataError",
    "MarketDataRefreshResult",
    "OfficialDividendProvider",
    "OfficialDividendPaymentProvider",
    "MOPSDividendPaymentProvider",
    "RawDividendEvent",
    "RawDividendPayment",
    "QuoteNormalizationError",
    "QuoteNormalizer",
    "ProviderDataError",
    "SourceDateError",
    "SourceDateMismatchError",
    "SuspendedSecurityError",
    "SymbolNotFoundError",
    "TemporaryProviderUnavailableError",
    "TPExProvider",
    "TPExDividendProvider",
    "TPExHistoricalDividendProvider",
    "TWSEDividendProvider",
    "TWSEHistoricalDividendProvider",
    "TWSEProvider",
]

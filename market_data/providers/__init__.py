"""Latest-only and explicit-date official market-data providers."""

from collections.abc import Sequence
from typing import Protocol

from domain import Market
from market_data.providers.current_tpex import (
    CurrentTPExProvider,
    DateAwareTPExProvider,
)
from market_data.providers.global_markets import (
    AlphaVantageFXRateProvider,
    AlphaVantageRequestPacer,
    AlphaVantageUSMarketDataProvider,
    FXRateProvider,
    USMarketDataProvider,
)
from market_data.providers.historical_tpex import HistoricalTPExProvider
from market_data.providers.historical_twse import HistoricalTWSEProvider
from market_data.providers.latest import TPExProvider, TWSEProvider
from market_data.transport import JSONRecord


class MarketDataProvider(Protocol):
    """Fetch raw end-of-day records for one market."""

    market: Market
    source: str

    def fetch(self) -> Sequence[JSONRecord]: ...


__all__ = [
    "HistoricalTPExProvider",
    "CurrentTPExProvider",
    "DateAwareTPExProvider",
    "HistoricalTWSEProvider",
    "AlphaVantageFXRateProvider",
    "AlphaVantageRequestPacer",
    "AlphaVantageUSMarketDataProvider",
    "FXRateProvider",
    "MarketDataProvider",
    "TPExProvider",
    "TWSEProvider",
    "USMarketDataProvider",
]

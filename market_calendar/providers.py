"""Official exchange availability-date providers."""

from datetime import date
from typing import Protocol

from domain import Market
from market_data.exceptions import MarketDataError, ProviderDataError, SourceDateError
from market_data.normalizer import QuoteNormalizer
from market_data.providers import MarketDataProvider


class MarketDateProvider(Protocol):
    """Expose the latest date published by one official market source."""

    market: Market

    def latest_available_date(self) -> date: ...


class OfficialMarketDateProvider:
    """Extract one authoritative date from an exchange's latest dataset."""

    def __init__(
        self,
        provider: MarketDataProvider,
        normalizer: QuoteNormalizer | None = None,
    ) -> None:
        self.provider = provider
        self.market = provider.market
        self.normalizer = normalizer or QuoteNormalizer()
        self._cached_date: date | None = None

    def latest_available_date(self) -> date:
        """Return the source date only when the full dataset agrees on it."""
        if self._cached_date is not None:
            return self._cached_date
        try:
            records = self.provider.fetch()
        except MarketDataError:
            raise
        except Exception as error:
            raise ProviderDataError(
                f"{self.market.value} calendar provider request failed"
            ) from error
        if not records:
            raise ProviderDataError(
                f"{self.market.value} calendar provider returned no official data"
            )
        dates = {self.normalizer.extract_trade_date(record) for record in records}
        if len(dates) != 1:
            raise SourceDateError(
                f"{self.market.value} official data contains mixed source dates"
            )
        self._cached_date = dates.pop()
        return self._cached_date

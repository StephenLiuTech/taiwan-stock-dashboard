"""Official exchange availability-date providers."""

from collections.abc import Callable
from datetime import date, timedelta
from typing import Protocol

from domain import Market
from market_data.exceptions import (
    MarketDataError,
    MarketDateUnavailableError,
    ProviderDataError,
    SourceDateError,
)
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

    def latest_available_date(self) -> date:
        """Fetch live data and return its date only when all records agree."""
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
        return dates.pop()


class OfficialHistoricalMarketDateProvider:
    """Discover the latest live date through official date-query endpoints."""

    def __init__(
        self,
        market: Market,
        provider_factory: Callable[[date], MarketDataProvider],
        *,
        today: Callable[[], date] = date.today,
        maximum_lookback_days: int = 31,
        normalizer: QuoteNormalizer | None = None,
    ) -> None:
        self.market = market
        self.provider_factory = provider_factory
        self.today = today
        self.maximum_lookback_days = maximum_lookback_days
        self.normalizer = normalizer or QuoteNormalizer()

    def latest_available_date(self) -> date:
        """Probe official dates newest-first without assuming weekends or holidays."""
        current_date = self.today()
        for offset in range(self.maximum_lookback_days + 1):
            candidate = current_date - timedelta(days=offset)
            try:
                records = self.provider_factory(candidate).fetch()
            except MarketDateUnavailableError:
                continue
            except MarketDataError:
                raise
            except Exception as error:
                raise ProviderDataError(
                    f"{self.market.value} live date resolution failed"
                ) from error
            if not records:
                continue
            dates = {self.normalizer.extract_trade_date(record) for record in records}
            if dates != {candidate}:
                actual = ", ".join(sorted(value.isoformat() for value in dates))
                raise SourceDateError(
                    f"{self.market.value} historical date probe for "
                    f"{candidate.isoformat()} returned {actual or 'no source date'}"
                )
            return candidate
        raise ProviderDataError(
            f"{self.market.value} has no official dataset within "
            f"{self.maximum_lookback_days + 1} calendar days"
        )

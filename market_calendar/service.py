"""Cross-market official trading-date resolution."""

from dataclasses import dataclass
from datetime import date

from domain import Market
from market_calendar.providers import MarketDateProvider
from market_data.exceptions import ProviderDataError


@dataclass(frozen=True)
class MarketAvailability:
    """Latest official dates and their current joint ingestibility."""

    twse_date: date
    tpex_date: date

    @property
    def synchronized(self) -> bool:
        return self.twse_date == self.tpex_date

    @property
    def commonly_ingestible_date(self) -> date | None:
        return self.twse_date if self.synchronized else None


class MarketDataNotSynchronizedError(RuntimeError):
    """Both latest-only sources are valid but expose different dates."""

    def __init__(self, availability: MarketAvailability) -> None:
        self.availability = availability
        super().__init__(
            "official sources are not synchronized "
            f"(TWSE={availability.twse_date.isoformat()}, "
            f"TPEx={availability.tpex_date.isoformat()})"
        )


class MarketCalendar:
    """Resolve a date consistently available from every required market."""

    def __init__(self, providers: tuple[MarketDateProvider, ...]) -> None:
        self.providers = {provider.market: provider for provider in providers}

    def market_availability(self) -> MarketAvailability:
        """Return each latest source date without claiming common availability."""
        required = {Market.TWSE, Market.TPEX}
        missing = required - self.providers.keys()
        if missing:
            names = ", ".join(sorted(market.value for market in missing))
            raise ProviderDataError(f"Market Calendar missing providers: {names}")
        return MarketAvailability(
            twse_date=self.providers[Market.TWSE].latest_available_date(),
            tpex_date=self.providers[Market.TPEX].latest_available_date(),
        )

    def latest_commonly_ingestible_date(self) -> date:
        """Return a date only when both configured latest-only sources match."""
        availability = self.market_availability()
        if availability.commonly_ingestible_date is None:
            raise MarketDataNotSynchronizedError(availability)
        return availability.commonly_ingestible_date

    def latest_available_trading_date(self) -> date:
        """Compatibility alias for the strict common-ingestibility contract."""
        return self.latest_commonly_ingestible_date()

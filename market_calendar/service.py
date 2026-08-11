"""Cross-market official trading-date resolution."""

import logging
from dataclasses import dataclass
from datetime import date

from domain import Market
from market_calendar.providers import MarketDateProvider
from market_data.exceptions import ProviderDataError, TemporaryProviderUnavailableError

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarketAvailability:
    """Latest official dates and the newest date both markets can provide."""

    twse_date: date | None
    tpex_date: date | None
    twse_source: str = "live_provider"
    tpex_source: str = "live_provider"
    twse_error: str | None = None
    tpex_error: str | None = None

    @property
    def synchronized(self) -> bool:
        return (
            self.twse_date is not None
            and self.tpex_date is not None
            and self.twse_date == self.tpex_date
        )

    @property
    def commonly_ingestible_date(self) -> date | None:
        """Return the newest date jointly available through current providers."""
        if self.twse_date is None or self.tpex_date is None:
            return None
        return min(self.twse_date, self.tpex_date)


class MarketCalendarUnavailableError(TemporaryProviderUnavailableError):
    """The live calendar lacks one or more dates after bounded retries."""

    def __init__(self, availability: MarketAvailability) -> None:
        self.availability = availability
        unavailable = ", ".join(
            market
            for market, value in (
                ("TWSE", availability.twse_date),
                ("TPEx", availability.tpex_date),
            )
            if value is None
        )
        super().__init__(f"temporary market availability failure: {unavailable}")


class MarketDataNotSynchronizedError(RuntimeError):
    """Legacy staggered-publication error retained for API compatibility."""

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
        """Probe each market independently and retain every successful result."""
        required = {Market.TWSE, Market.TPEX}
        missing = required - self.providers.keys()
        if missing:
            names = ", ".join(sorted(market.value for market in missing))
            raise ProviderDataError(f"Market Calendar missing providers: {names}")
        values: dict[Market, date | None] = {}
        errors: dict[Market, str | None] = {}
        for market in (Market.TWSE, Market.TPEX):
            try:
                values[market] = self.providers[market].latest_available_date()
                errors[market] = None
            except TemporaryProviderUnavailableError as error:
                values[market] = None
                errors[market] = type(error).__name__
                _LOGGER.warning(
                    "Market availability check temporarily unavailable: "
                    "market=%s error=%s",
                    market.value,
                    type(error).__name__,
                )
        return MarketAvailability(
            twse_date=values[Market.TWSE],
            tpex_date=values[Market.TPEX],
            twse_source=("live_provider" if values[Market.TWSE] else "unavailable"),
            tpex_source=("live_provider" if values[Market.TPEX] else "unavailable"),
            twse_error=errors[Market.TWSE],
            tpex_error=errors[Market.TPEX],
        )

    def latest_commonly_ingestible_date(self) -> date:
        """Return the newest date available from both configured markets."""
        availability = self.market_availability()
        value = availability.commonly_ingestible_date
        if value is None:
            raise MarketCalendarUnavailableError(availability)
        return value

    def latest_available_trading_date(self) -> date:
        """Compatibility alias for the joint-availability contract."""
        return self.latest_commonly_ingestible_date()

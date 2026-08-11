"""Offline tests for official-data market calendar behavior."""

from collections.abc import Sequence
from datetime import date

import pytest

from domain import Market
from market_calendar import (
    MarketCalendar,
    MarketCalendarUnavailableError,
    OfficialHistoricalMarketDateProvider,
    OfficialMarketDateProvider,
)
from market_data import (
    MarketDateUnavailableError,
    ProviderDataError,
    SourceDateError,
    TemporaryProviderUnavailableError,
)
from market_data.transport import JSONRecord


class StaticProvider:
    def __init__(self, market: Market, records: Sequence[JSONRecord]) -> None:
        self.market = market
        self.source = "calendar-test"
        self.records = records

    def fetch(self) -> Sequence[JSONRecord]:
        return self.records


def date_provider(market: Market, source_date: str) -> OfficialMarketDateProvider:
    return OfficialMarketDateProvider(
        StaticProvider(market, [{"Date": source_date, "Code": "test"}])
    )


def test_calendar_uses_twse_date_when_twse_is_earlier() -> None:
    calendar = MarketCalendar(
        (
            date_provider(Market.TWSE, "1150721"),
            date_provider(Market.TPEX, "1150722"),
        )
    )
    availability = calendar.market_availability()
    assert availability.twse_date == date(2026, 7, 21)
    assert availability.tpex_date == date(2026, 7, 22)
    assert availability.synchronized is False
    assert availability.commonly_ingestible_date == date(2026, 7, 21)
    assert calendar.latest_commonly_ingestible_date() == date(2026, 7, 21)


def test_calendar_uses_tpex_date_when_tpex_is_earlier() -> None:
    calendar = MarketCalendar(
        (
            date_provider(Market.TWSE, "1150723"),
            date_provider(Market.TPEX, "1150722"),
        )
    )
    availability = calendar.market_availability()
    assert availability.synchronized is False
    assert availability.commonly_ingestible_date == date(2026, 7, 22)
    assert calendar.latest_available_trading_date() == date(2026, 7, 22)


def test_calendar_returns_matching_market_date() -> None:
    calendar = MarketCalendar(
        (
            date_provider(Market.TWSE, "1150722"),
            date_provider(Market.TPEX, "1150722"),
        )
    )
    availability = calendar.market_availability()
    assert availability.synchronized is True
    assert availability.commonly_ingestible_date == date(2026, 7, 22)
    assert calendar.latest_commonly_ingestible_date() == date(2026, 7, 22)


def test_calendar_rejects_no_official_data() -> None:
    provider = OfficialMarketDateProvider(StaticProvider(Market.TWSE, []))
    with pytest.raises(ProviderDataError, match="no official data"):
        provider.latest_available_date()


def test_calendar_rejects_mixed_dates_from_one_source() -> None:
    provider = OfficialMarketDateProvider(
        StaticProvider(
            Market.TWSE,
            [{"Date": "1150721"}, {"Date": "1150722"}],
        )
    )
    with pytest.raises(SourceDateError, match="mixed"):
        provider.latest_available_date()


def test_calendar_requires_both_official_markets() -> None:
    calendar = MarketCalendar((date_provider(Market.TWSE, "1150722"),))
    with pytest.raises(ProviderDataError, match="TPEx"):
        calendar.latest_available_trading_date()


class AvailabilityProvider:
    def __init__(
        self, market: Market, value: date | TemporaryProviderUnavailableError
    ) -> None:
        self.market = market
        self.value = value

    def latest_available_date(self) -> date:
        if isinstance(self.value, TemporaryProviderUnavailableError):
            raise self.value
        return self.value


@pytest.mark.parametrize("failed_market", [Market.TWSE, Market.TPEX])
def test_calendar_preserves_the_successful_source_when_the_other_is_transient(
    failed_market: Market,
) -> None:
    successful_market = Market.TPEX if failed_market is Market.TWSE else Market.TWSE
    calendar = MarketCalendar(
        (
            AvailabilityProvider(
                failed_market, TemporaryProviderUnavailableError("HTTP 520")
            ),
            AvailabilityProvider(successful_market, date(2026, 8, 11)),
        )
    )

    availability = calendar.market_availability()

    assert getattr(availability, f"{successful_market.value.lower()}_date") == date(
        2026, 8, 11
    )
    assert getattr(availability, f"{failed_market.value.lower()}_date") is None
    assert availability.commonly_ingestible_date is None
    with pytest.raises(MarketCalendarUnavailableError):
        calendar.latest_commonly_ingestible_date()


def test_official_latest_provider_does_not_cache_source_date() -> None:
    source = StaticProvider(Market.TWSE, [{"Date": "1150724", "Code": "test"}])
    provider = OfficialMarketDateProvider(source)

    assert provider.latest_available_date() == date(2026, 7, 24)
    source.records = [{"Date": "1150727", "Code": "test"}]
    assert provider.latest_available_date() == date(2026, 7, 27)


def test_historical_live_date_resolution_discovers_latest_official_date() -> None:
    calls: list[date] = []

    class DateQueryProvider:
        market = Market.TWSE
        source = "historical-calendar-test"

        def __init__(self, requested_date: date) -> None:
            self.requested_date = requested_date

        def fetch(self) -> Sequence[JSONRecord]:
            calls.append(self.requested_date)
            if self.requested_date != date(2026, 7, 27):
                raise MarketDateUnavailableError("no data")
            return [{"Date": "1150727", "Code": "test"}]

    provider = OfficialHistoricalMarketDateProvider(
        Market.TWSE,
        DateQueryProvider,
        today=lambda: date(2026, 7, 27),
    )

    assert provider.latest_available_date() == date(2026, 7, 27)
    assert calls == [date(2026, 7, 27)]


def test_weekend_or_holiday_resolves_from_official_availability() -> None:
    calls: list[date] = []

    class DateQueryProvider:
        market = Market.TWSE
        source = "historical-calendar-test"

        def __init__(self, requested_date: date) -> None:
            self.requested_date = requested_date

        def fetch(self) -> Sequence[JSONRecord]:
            calls.append(self.requested_date)
            if self.requested_date != date(2026, 7, 24):
                raise MarketDateUnavailableError("no official data")
            return [{"Date": "1150724", "Code": "test"}]

    provider = OfficialHistoricalMarketDateProvider(
        Market.TWSE,
        DateQueryProvider,
        today=lambda: date(2026, 7, 27),
    )

    assert provider.latest_available_date() == date(2026, 7, 24)
    assert calls == [
        date(2026, 7, 27),
        date(2026, 7, 26),
        date(2026, 7, 25),
        date(2026, 7, 24),
    ]

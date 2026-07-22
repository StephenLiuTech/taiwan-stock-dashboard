"""Offline tests for official-data market calendar behavior."""

from collections.abc import Sequence
from datetime import date

import pytest

from domain import Market
from market_calendar import (
    MarketCalendar,
    MarketDataNotSynchronizedError,
    OfficialMarketDateProvider,
)
from market_data import ProviderDataError, SourceDateError
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


def test_calendar_distinguishes_unsynchronized_latest_dates() -> None:
    calendar = MarketCalendar(
        (
            date_provider(Market.TWSE, "1150721"),
            date_provider(Market.TPEX, "1150722"),
        )
    )
    availability = calendar.market_availability()
    assert availability.twse_date == date(2026, 7, 21)
    assert availability.tpex_date == date(2026, 7, 22)
    assert availability.commonly_ingestible_date is None
    with pytest.raises(MarketDataNotSynchronizedError):
        calendar.latest_commonly_ingestible_date()


def test_calendar_returns_matching_market_date() -> None:
    calendar = MarketCalendar(
        (
            date_provider(Market.TWSE, "1150722"),
            date_provider(Market.TPEX, "1150722"),
        )
    )
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

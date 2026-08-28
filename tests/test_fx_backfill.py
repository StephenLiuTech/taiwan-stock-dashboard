"""Bounded provider, application, persistence, and CLI FX backfill tests."""

import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest

from domain import Currency, FxRate
from market_data.providers import AlphaVantageFXRateProvider
from pams.application import BackfillFxRatesUseCase
from pams.cli import build_parser
from repositories.market_data_uow import SQLiteMarketDataUnitOfWork
from repositories.sqlite import SQLiteFxRateRepository


class SizeDocumentTransport:
    def __init__(self, documents: dict[str, dict[str, object]]) -> None:
        self.documents = documents
        self.output_sizes: list[str] = []

    def get_document(self, url: str) -> dict[str, object]:
        output_size = parse_qs(urlsplit(url).query)["outputsize"][0]
        self.output_sizes.append(output_size)
        return self.documents[output_size]


class FixedProvider:
    source = "fixture"

    def __init__(self, rates: tuple[FxRate, ...]) -> None:
        self.rates = rates
        self.calls: list[tuple[Currency, Currency, date, date]] = []

    def fetch(self, base: Currency, quote: Currency, report_date: date) -> FxRate:
        del base, quote, report_date
        raise AssertionError("backfill must not use the single-date provider method")

    def fetch_between(
        self,
        base: Currency,
        quote: Currency,
        start_date: date,
        end_date: date,
    ) -> tuple[FxRate, ...]:
        self.calls.append((base, quote, start_date, end_date))
        return self.rates


def fx_rate(day: date, value: str, *, fetched_day: int = 1) -> FxRate:
    return FxRate(
        base_currency=Currency.USD,
        quote_currency=Currency.TWD,
        rate_date=day,
        rate=Decimal(value),
        source="Alpha Vantage FX_DAILY",
        fetched_at=datetime(2026, 8, fetched_day, tzinfo=UTC),
    )


def series(rows: dict[str, str]) -> dict[str, object]:
    return {
        "Time Series FX (Daily)": {
            day: {"4. close": value} for day, value in rows.items()
        }
    }


def test_provider_filters_inclusive_bounded_compact_range_without_future() -> None:
    transport = SizeDocumentTransport(
        {
            "compact": series(
                {
                    "2026-06-25": "32.10000",
                    "2026-06-26": "32.12345",
                    "2026-06-29": "32.20000",
                    "2026-07-01": "32.30000",
                    "2026-07-02": "32.40000",
                }
            )
        }
    )
    provider = AlphaVantageFXRateProvider(
        "secret", transport, request_interval_seconds=0
    )

    result = provider.fetch_between(
        Currency.USD,
        Currency.TWD,
        date(2026, 6, 26),
        date(2026, 7, 1),
    )

    assert transport.output_sizes == ["compact"]
    assert [item.rate_date for item in result] == [
        date(2026, 6, 26),
        date(2026, 6, 29),
        date(2026, 7, 1),
    ]
    assert result[0].rate == Decimal("32.12345")


def test_provider_uses_one_bounded_full_fallback_when_compact_is_too_recent() -> None:
    transport = SizeDocumentTransport(
        {
            "compact": series(
                {
                    "2026-08-03": "32.30",
                    "2026-08-04": "32.31",
                }
            ),
            "full": series(
                {
                    "2026-06-26": "32.12345",
                    "2026-06-29": "32.20",
                    "2026-08-04": "32.31",
                }
            ),
        }
    )
    provider = AlphaVantageFXRateProvider(
        "secret", transport, request_interval_seconds=0
    )

    result = provider.fetch_between(
        Currency.USD,
        Currency.TWD,
        date(2026, 6, 26),
        date(2026, 6, 29),
    )

    assert transport.output_sizes == ["compact", "full"]
    assert [item.rate_date for item in result] == [
        date(2026, 6, 26),
        date(2026, 6, 29),
    ]


def test_provider_preserves_weekend_and_holiday_gaps() -> None:
    transport = SizeDocumentTransport(
        {
            "compact": series(
                {
                    "2026-07-02": "32.10",
                    "2026-07-03": "32.20",
                    "2026-07-06": "32.30",
                }
            )
        }
    )
    provider = AlphaVantageFXRateProvider(
        "secret", transport, request_interval_seconds=0
    )

    result = provider.fetch_between(
        Currency.USD,
        Currency.TWD,
        date(2026, 7, 3),
        date(2026, 7, 6),
    )

    assert [item.rate_date for item in result] == [
        date(2026, 7, 3),
        date(2026, 7, 6),
    ]


def test_repository_insert_if_absent_preserves_existing_value_and_timestamp(
    connection: sqlite3.Connection,
) -> None:
    repository = SQLiteFxRateRepository(connection)
    original = fx_rate(date(2026, 6, 26), "32.10", fetched_day=1)
    replacement = fx_rate(date(2026, 6, 26), "99.99", fetched_day=2)

    assert repository.insert_if_absent(original) is True
    assert repository.insert_if_absent(replacement) is False

    persisted = repository.list_between(
        "USD", "TWD", date(2026, 6, 26), date(2026, 6, 26)
    )
    assert len(persisted) == 1
    assert persisted[0].rate == Decimal("32.10")
    assert persisted[0].fetched_at == original.fetched_at


def test_backfill_dry_run_has_no_writes(connection: sqlite3.Connection) -> None:
    repository = SQLiteFxRateRepository(connection)
    provider = FixedProvider((fx_rate(date(2026, 6, 26), "32.12345"),))
    use_case = BackfillFxRatesUseCase(
        provider,
        repository,
        SQLiteMarketDataUnitOfWork(connection),
        "test",
    )

    result = use_case.execute(
        Currency.USD,
        Currency.TWD,
        date(2026, 6, 26),
        date(2026, 8, 4),
        apply=False,
    )

    assert result.applied is False
    assert result.inserted == 0
    assert result.missing_dates == (date(2026, 6, 26),)
    assert (
        repository.list_between("USD", "TWD", date(2026, 6, 26), date(2026, 8, 4)) == []
    )


def test_backfill_repeated_apply_is_idempotent(connection: sqlite3.Connection) -> None:
    repository = SQLiteFxRateRepository(connection)
    provider = FixedProvider(
        (
            fx_rate(date(2026, 6, 26), "32.12345"),
            fx_rate(date(2026, 6, 29), "32.20"),
        )
    )
    use_case = BackfillFxRatesUseCase(
        provider,
        repository,
        SQLiteMarketDataUnitOfWork(connection),
        "test",
    )

    first = use_case.execute(
        Currency.USD,
        Currency.TWD,
        date(2026, 6, 26),
        date(2026, 8, 4),
        apply=True,
    )
    second = use_case.execute(
        Currency.USD,
        Currency.TWD,
        date(2026, 6, 26),
        date(2026, 8, 4),
        apply=True,
    )

    assert first.inserted == 2
    assert second.inserted == 0
    assert second.missing_dates == ()
    assert (
        len(repository.list_between("USD", "TWD", date(2026, 6, 26), date(2026, 8, 4)))
        == 2
    )


def test_backfill_rejects_invalid_range_before_provider_call(
    connection: sqlite3.Connection,
) -> None:
    provider = FixedProvider(())
    use_case = BackfillFxRatesUseCase(
        provider,
        SQLiteFxRateRepository(connection),
        SQLiteMarketDataUnitOfWork(connection),
        "test",
    )

    with pytest.raises(ValueError, match="start date follows end date"):
        use_case.execute(
            Currency.USD,
            Currency.TWD,
            date(2026, 8, 4),
            date(2026, 6, 26),
            apply=False,
        )
    assert provider.calls == []


def test_cli_requires_exactly_one_backfill_mode() -> None:
    parser = build_parser()
    base = [
        "fx",
        "backfill",
        "--pair",
        "USD/TWD",
        "--from",
        "2026-06-26",
        "--to",
        "2026-08-04",
    ]

    with pytest.raises(SystemExit):
        parser.parse_args(base)
    with pytest.raises(SystemExit):
        parser.parse_args([*base, "--dry-run", "--apply"])
    assert parser.parse_args([*base, "--dry-run"]).dry_run is True
    assert parser.parse_args([*base, "--apply"]).apply is True

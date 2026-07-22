"""Market-data provider, normalization, persistence, and engine tests."""

import sqlite3
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from domain import Currency, Holding, Market, PriceQuote
from market_data import (
    MarketDataEngine,
    ProviderDataError,
    QuoteNormalizationError,
    QuoteNormalizer,
    SourceDateMismatchError,
    SuspendedSecurityError,
    SymbolNotFoundError,
    TPExProvider,
    TWSEProvider,
)
from market_data.transport import JSONRecord
from repositories import (
    SQLiteHoldingRepository,
    SQLiteLiabilityRepository,
    SQLiteMarketDataUnitOfWork,
    SQLitePriceQuoteRepository,
    SQLiteSnapshotRepository,
)
from services import DuplicateSnapshotError


class StaticTransport:
    """Deterministic transport for provider contract tests."""

    def __init__(self, records: Sequence[JSONRecord]) -> None:
        self.records = records
        self.requested_url: str | None = None

    def get_records(self, url: str) -> Sequence[JSONRecord]:
        self.requested_url = url
        return self.records


class StaticProvider:
    """Deterministic market provider for engine integration tests."""

    def __init__(
        self, market: Market, source: str, records: Sequence[JSONRecord]
    ) -> None:
        self.market = market
        self.source = source
        self.records = records

    def fetch(self) -> Sequence[JSONRecord]:
        return self.records


class FailingProvider(StaticProvider):
    """Provider that simulates an upstream transport failure."""

    def fetch(self) -> Sequence[JSONRecord]:
        raise OSError("upstream unavailable")


def test_twse_provider_uses_official_endpoint() -> None:
    transport = StaticTransport([{"Code": "2330"}])
    assert TWSEProvider(transport).fetch() == [{"Code": "2330"}]
    assert transport.requested_url == TWSEProvider.endpoint


def test_tpex_provider_uses_official_endpoint() -> None:
    transport = StaticTransport([{"SecuritiesCompanyCode": "8299"}])
    assert TPExProvider(transport).fetch() == [{"SecuritiesCompanyCode": "8299"}]
    assert transport.requested_url == TPExProvider.endpoint


@pytest.mark.parametrize(
    ("market", "record", "expected"),
    [
        (
            Market.TWSE,
            {"Code": " 2330 ", "ClosingPrice": "1,820.50"},
            Decimal("1820.50"),
        ),
        (
            Market.TPEX,
            {"SecuritiesCompanyCode": "8299", "Close": "2,499.00"},
            Decimal("2499.00"),
        ),
    ],
)
def test_normalizer_handles_exchange_field_shapes(
    market: Market, record: Mapping[str, object], expected: Decimal
) -> None:
    quote = QuoteNormalizer().normalize(
        record,
        market,
        date(2026, 7, 22),
        "test",
        datetime(2026, 7, 22, tzinfo=UTC),
    )
    assert quote.close_price == expected
    assert quote.currency is Currency.TWD


def test_normalizer_parses_official_roc_trade_date() -> None:
    assert QuoteNormalizer().extract_trade_date({"Date": "115/07/22"}) == date(
        2026, 7, 22
    )


def test_normalizer_rejects_missing_close() -> None:
    with pytest.raises(SuspendedSecurityError, match="no closing trade"):
        QuoteNormalizer().normalize(
            {"Code": "2330", "ClosingPrice": "--"},
            Market.TWSE,
            date(2026, 7, 22),
            "test",
            datetime(2026, 7, 22, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ("+2", Decimal("100")),
        ("2", Decimal("100")),
        ("-2", Decimal("104")),
        ("0.0000", Decimal("102")),
        ("+1,000.00", Decimal("-898")),
    ],
)
def test_normalizer_derives_previous_close_from_signed_change(
    change: str, expected: Decimal
) -> None:
    if expected < 0:
        with pytest.raises(QuoteNormalizationError, match="negative"):
            QuoteNormalizer().normalize(
                {"Code": "2330", "ClosingPrice": "102", "Change": change},
                Market.TWSE,
                date(2026, 7, 22),
                "test",
                datetime(2026, 7, 22, tzinfo=UTC),
            )
        return
    quote = QuoteNormalizer().normalize(
        {"Code": "2330", "ClosingPrice": "102", "Change": change},
        Market.TWSE,
        date(2026, 7, 22),
        "test",
        datetime(2026, 7, 22, tzinfo=UTC),
    )
    assert quote.previous_close == expected


@pytest.mark.parametrize("change", ["---", "－", ""])
def test_special_change_leaves_previous_close_unknown(change: str) -> None:
    quote = QuoteNormalizer().normalize(
        {"Code": "2330", "ClosingPrice": "102", "Change": change},
        Market.TWSE,
        date(2026, 7, 22),
        "test",
        datetime(2026, 7, 22, tzinfo=UTC),
    )
    assert quote.previous_close is None


def test_tpex_negative_comma_formatted_change_derives_previous_close() -> None:
    quote = QuoteNormalizer().normalize(
        {
            "SecuritiesCompanyCode": "8299",
            "Close": "1,102.00",
            "Change": "-1,000.00 ",
        },
        Market.TPEX,
        date(2026, 7, 22),
        "test",
        datetime(2026, 7, 22, tzinfo=UTC),
    )
    assert quote.previous_close == Decimal("2102.00")


def test_price_quote_repository_upserts_exact_decimals(
    connection: sqlite3.Connection,
) -> None:
    repository = SQLitePriceQuoteRepository(connection)
    quote = PriceQuote(
        symbol="2330",
        market=Market.TWSE,
        trade_date=date(2026, 7, 22),
        close_price=Decimal("1820.1234"),
        previous_close=Decimal("1810.5"),
        currency=Currency.TWD,
        source="test",
    )
    repository.upsert_many([quote])
    repository.upsert_many([quote.model_copy(update={"close_price": Decimal("1821")})])
    loaded = repository.list_by_date(date(2026, 7, 22))
    assert len(loaded) == 1
    assert loaded[0].close_price == Decimal("1821")


def _holding(identifier: str, symbol: str, market: Market) -> Holding:
    return Holding(
        id=identifier,
        symbol=symbol,
        name=symbol,
        market=market,
        currency=Currency.TWD,
        quantity=Decimal("10"),
        average_cost=Decimal("50"),
    )


def test_engine_persists_quotes_and_portfolio_snapshot(
    connection: sqlite3.Connection,
) -> None:
    holdings = SQLiteHoldingRepository(connection)
    holdings.upsert(_holding("h1", "2330", Market.TWSE))
    holdings.upsert(_holding("h2", "8299", Market.TPEX))
    quote_repository = SQLitePriceQuoteRepository(connection)
    snapshot_repository = SQLiteSnapshotRepository(connection)
    engine = MarketDataEngine(
        (
            StaticProvider(
                Market.TWSE,
                "twse-test",
                [{"Date": "1150722", "Code": "2330", "ClosingPrice": "100"}],
            ),
            StaticProvider(
                Market.TPEX,
                "tpex-test",
                [
                    {
                        "Date": "1150722",
                        "SecuritiesCompanyCode": "8299",
                        "Close": "200",
                    }
                ],
            ),
        ),
        holdings,
        SQLiteLiabilityRepository(connection),
        SQLiteMarketDataUnitOfWork(connection),
    )
    result = engine.refresh(date(2026, 7, 22))
    assert len(result.quotes) == 2
    assert len(quote_repository.list_by_date(date(2026, 7, 22))) == 2
    assert result.snapshot.total_market_value == Decimal("3000")
    assert snapshot_repository.get_by_date(date(2026, 7, 22)) == result.snapshot
    position_repository = SQLiteMarketDataUnitOfWork(connection).position_snapshots
    positions = position_repository.list_by_date(date(2026, 7, 22))
    assert len(positions) == 2
    with pytest.raises(sqlite3.IntegrityError):
        position_repository.add_many([positions[0]])


def test_engine_fails_before_persistence_when_requested_symbol_is_missing(
    connection: sqlite3.Connection,
) -> None:
    holdings = SQLiteHoldingRepository(connection)
    holdings.upsert(_holding("h1", "2330", Market.TWSE))
    quotes = SQLitePriceQuoteRepository(connection)
    engine = MarketDataEngine(
        (
            StaticProvider(
                Market.TWSE,
                "twse-test",
                [{"Date": "1150722", "Code": "9999", "ClosingPrice": "1"}],
            ),
        ),
        holdings,
        SQLiteLiabilityRepository(connection),
        SQLiteMarketDataUnitOfWork(connection),
    )
    with pytest.raises(SymbolNotFoundError, match="2330"):
        engine.refresh(date(2026, 7, 22))
    assert quotes.list_by_date(date(2026, 7, 22)) == []


@pytest.mark.parametrize(
    ("requested", "source"),
    [
        pytest.param(date(2026, 7, 22), "1150721", id="source-not-updated"),
        pytest.param(date(2026, 7, 19), "1150717", id="holiday"),
        pytest.param(date(2026, 7, 21), "1150722", id="requested-date-differs"),
    ],
)
def test_engine_rejects_source_date_mismatch_before_writes(
    connection: sqlite3.Connection, requested: date, source: str
) -> None:
    holdings = SQLiteHoldingRepository(connection)
    holdings.upsert(_holding("h1", "2330", Market.TWSE))
    engine = MarketDataEngine(
        (
            StaticProvider(
                Market.TWSE,
                "twse-test",
                [{"Date": source, "Code": "2330", "ClosingPrice": "100"}],
            ),
        ),
        holdings,
        SQLiteLiabilityRepository(connection),
        SQLiteMarketDataUnitOfWork(connection),
    )
    with pytest.raises(SourceDateMismatchError):
        engine.refresh(requested)
    assert SQLitePriceQuoteRepository(connection).list_by_date(requested) == []


def test_engine_distinguishes_provider_failure(connection: sqlite3.Connection) -> None:
    holdings = SQLiteHoldingRepository(connection)
    holdings.upsert(_holding("h1", "2330", Market.TWSE))
    engine = MarketDataEngine(
        (FailingProvider(Market.TWSE, "twse-test", []),),
        holdings,
        SQLiteLiabilityRepository(connection),
        SQLiteMarketDataUnitOfWork(connection),
    )
    with pytest.raises(ProviderDataError, match="request failed"):
        engine.refresh(date(2026, 7, 22))


def test_engine_distinguishes_suspended_security(
    connection: sqlite3.Connection,
) -> None:
    holdings = SQLiteHoldingRepository(connection)
    holdings.upsert(_holding("h1", "2330", Market.TWSE))
    engine = MarketDataEngine(
        (
            StaticProvider(
                Market.TWSE,
                "twse-test",
                [{"Date": "1150722", "Code": "2330", "ClosingPrice": "---"}],
            ),
        ),
        holdings,
        SQLiteLiabilityRepository(connection),
        SQLiteMarketDataUnitOfWork(connection),
    )
    with pytest.raises(SuspendedSecurityError):
        engine.refresh(date(2026, 7, 22))


def test_repeated_same_day_ingestion_is_rejected(
    connection: sqlite3.Connection,
) -> None:
    holdings = SQLiteHoldingRepository(connection)
    holdings.upsert(_holding("h1", "2330", Market.TWSE))
    engine = MarketDataEngine(
        (
            StaticProvider(
                Market.TWSE,
                "twse-test",
                [{"Date": "1150722", "Code": "2330", "ClosingPrice": "100"}],
            ),
        ),
        holdings,
        SQLiteLiabilityRepository(connection),
        SQLiteMarketDataUnitOfWork(connection),
    )
    engine.refresh(date(2026, 7, 22))
    with pytest.raises(DuplicateSnapshotError):
        engine.refresh(date(2026, 7, 22))


def test_ingestion_rolls_back_quotes_and_aggregate_when_positions_fail(
    connection: sqlite3.Connection,
) -> None:
    holdings = SQLiteHoldingRepository(connection)
    holdings.upsert(_holding("h1", "2330", Market.TWSE))
    connection.execute(
        """CREATE TRIGGER fail_position_insert
        BEFORE INSERT ON position_snapshots
        BEGIN SELECT RAISE(FAIL, 'forced position failure'); END"""
    )
    engine = MarketDataEngine(
        (
            StaticProvider(
                Market.TWSE,
                "twse-test",
                [{"Date": "1150722", "Code": "2330", "ClosingPrice": "100"}],
            ),
        ),
        holdings,
        SQLiteLiabilityRepository(connection),
        SQLiteMarketDataUnitOfWork(connection),
    )
    with pytest.raises(sqlite3.IntegrityError, match="forced position failure"):
        engine.refresh(date(2026, 7, 22))
    assert SQLitePriceQuoteRepository(connection).list_by_date(date(2026, 7, 22)) == []
    assert SQLiteSnapshotRepository(connection).get_by_date(date(2026, 7, 22)) is None

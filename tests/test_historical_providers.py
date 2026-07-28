"""Offline tests for official explicit-date provider adapters."""

import json
import sqlite3
from datetime import date
from http.client import IncompleteRead
from pathlib import Path

import pytest

from domain import Market
from market_data import (
    MarketDataEngine,
    ProviderDataError,
    SourceDateMismatchError,
    SymbolNotFoundError,
)
from market_data.providers import (
    HistoricalTPExProvider,
    HistoricalTWSEProvider,
    MarketDataProvider,
)
from market_data.transport import JSONRecord, UrllibJSONDocumentTransport
from pams.application import UpdateMode, UpdatePortfolioUseCase
from repositories import (
    SQLiteHoldingRepository,
    SQLiteLiabilityRepository,
    SQLiteMarketDataUnitOfWork,
)
from services.bootstrap import SEED_HOLDINGS


class DocumentTransport:
    def __init__(self, document: JSONRecord | Exception) -> None:
        self.document = document
        self.requested_url: str | None = None

    def get_document(self, url: str) -> JSONRecord:
        self.requested_url = url
        if isinstance(self.document, Exception):
            raise self.document
        return self.document


class BytesResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def __enter__(self) -> "BytesResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.content


class IncompleteThenCompleteOpener:
    def __init__(self, document: JSONRecord) -> None:
        self.content = json.dumps(document).encode()
        self.calls = 0

    def __call__(self, _request: object, *, timeout: float) -> BytesResponse:
        assert timeout == 15
        self.calls += 1
        if self.calls == 1:
            raise IncompleteRead(self.content[:100])
        return BytesResponse(self.content)


def twse_document(source_date: str = "20260721") -> JSONRecord:
    return {
        "stat": "OK",
        "date": source_date,
        "tables": [
            {
                "fields": [
                    "證券代號",
                    "證券名稱",
                    "收盤價",
                    "漲跌(+/-)",
                    "漲跌價差",
                ],
                "data": [
                    ["0050", "元大台灣50", "100.00", "<p>X</p>", "0.00"],
                    ["2027", "大成鋼", "41.15", "<p>+</p>", "0.70"],
                    ["2330", "台積電", "2,410.00", "<p>-</p>", "90.00"],
                ],
            }
        ],
    }


def tpex_document(source_date: str = "20260721") -> JSONRecord:
    return {
        "date": source_date,
        "tables": [
            {
                "fields": ["代號", "名稱", "收盤", "漲跌"],
                "data": [
                    ["3293", "鈊象", "718.00", "+12.00"],
                    ["8299", "群聯", "1,855.00", "-120.00"],
                ],
            }
        ],
    }


def test_historical_twse_parses_official_table_and_signs() -> None:
    transport = DocumentTransport(twse_document())
    provider = HistoricalTWSEProvider(date(2026, 7, 21), transport)
    records = provider.fetch()
    assert records == [
        {
            "Date": "20260721",
            "Code": "0050",
            "Name": "元大台灣50",
            "ClosingPrice": "100.00",
            "Change": "-",
        },
        {
            "Date": "20260721",
            "Code": "2027",
            "Name": "大成鋼",
            "ClosingPrice": "41.15",
            "Change": "+0.70",
        },
        {
            "Date": "20260721",
            "Code": "2330",
            "Name": "台積電",
            "ClosingPrice": "2,410.00",
            "Change": "-90.00",
        },
    ]
    assert "date=20260721" in (transport.requested_url or "")


def test_historical_tpex_parses_official_table() -> None:
    transport = DocumentTransport(tpex_document())
    provider = HistoricalTPExProvider(date(2026, 7, 21), transport)
    records = provider.fetch()
    assert records[0] == {
        "Date": "20260721",
        "Code": "3293",
        "Name": "鈊象",
        "ClosingPrice": "718.00",
        "Change": "+12.00",
    }
    assert "date=115%2F07%2F21" in (transport.requested_url or "")


@pytest.mark.parametrize(
    "provider",
    [
        HistoricalTWSEProvider(date(2026, 7, 21), DocumentTransport(OSError())),
        HistoricalTPExProvider(date(2026, 7, 21), DocumentTransport({"tables": []})),
    ],
)
def test_historical_provider_errors_are_typed(provider: MarketDataProvider) -> None:
    with pytest.raises(ProviderDataError):
        provider.fetch()


def test_historical_providers_satisfy_common_protocol() -> None:
    providers: tuple[MarketDataProvider, ...] = (
        HistoricalTWSEProvider(date(2026, 7, 21), DocumentTransport(twse_document())),
        HistoricalTPExProvider(date(2026, 7, 21), DocumentTransport(tpex_document())),
    )
    assert {provider.market for provider in providers} == {Market.TWSE, Market.TPEX}
    assert all(provider.source for provider in providers)


def historical_engine(
    connection: sqlite3.Connection,
    *,
    twse_date: str = "20260721",
    include_requested_symbol: bool = True,
) -> MarketDataEngine:
    holdings = SQLiteHoldingRepository(connection)
    for holding in SEED_HOLDINGS:
        holdings.upsert(holding)
    twse = twse_document(twse_date)
    if not include_requested_symbol:
        twse["tables"][0]["data"] = [  # type: ignore[index]
            ["9999", "Not Held", "10.00", "<p>+</p>", "0.10"]
        ]
    return MarketDataEngine(
        (
            HistoricalTWSEProvider(date(2026, 7, 21), DocumentTransport(twse)),
            HistoricalTPExProvider(
                date(2026, 7, 21), DocumentTransport(tpex_document())
            ),
        ),
        holdings,
        SQLiteLiabilityRepository(connection),
        SQLiteMarketDataUnitOfWork(connection),
    )


def test_manual_historical_update_succeeds_and_persists(
    connection: sqlite3.Connection,
) -> None:
    engine = historical_engine(connection)
    use_case = UpdatePortfolioUseCase(
        calendar=object(),  # type: ignore[arg-type]
        engine=object(),  # type: ignore[arg-type]
        database_path=Path("historical.db"),
        historical_engine_factory=lambda requested: engine,
    )
    result = use_case.execute(date(2026, 7, 21))
    assert result.mode is UpdateMode.UPDATED
    assert result.verified_source_date == date(2026, 7, 21)
    assert len(result.positions) == 5


def test_historical_source_date_mismatch_remains_strict(
    connection: sqlite3.Connection,
) -> None:
    with pytest.raises(SourceDateMismatchError):
        historical_engine(connection, twse_date="20260720").preview(date(2026, 7, 21))


def test_historical_missing_symbol_remains_strict(
    connection: sqlite3.Connection,
) -> None:
    with pytest.raises(SymbolNotFoundError):
        historical_engine(connection, include_requested_symbol=False).preview(
            date(2026, 7, 21)
        )


def test_transport_retry_precedes_one_atomic_database_write(
    connection: sqlite3.Connection,
) -> None:
    holdings = SQLiteHoldingRepository(connection)
    for holding in SEED_HOLDINGS:
        holdings.upsert(holding)
    opener = IncompleteThenCompleteOpener(tpex_document())
    tpex_transport = UrllibJSONDocumentTransport(
        provider_name="TPEx",
        opener=opener,
        sleeper=lambda _delay: None,
        jitter=lambda: 0,
    )
    engine = MarketDataEngine(
        (
            HistoricalTWSEProvider(
                date(2026, 7, 21), DocumentTransport(twse_document())
            ),
            HistoricalTPExProvider(date(2026, 7, 21), tpex_transport),
        ),
        holdings,
        SQLiteLiabilityRepository(connection),
        SQLiteMarketDataUnitOfWork(connection),
    )

    result = engine.refresh(date(2026, 7, 21))

    assert opener.calls == 2
    assert len(result.summary.positions) == 5
    assert connection.execute("SELECT COUNT(*) FROM price_quotes").fetchone()[0] == 5
    assert connection.execute("SELECT COUNT(*) FROM daily_snapshots").fetchone()[0] == 1
    assert (
        connection.execute("SELECT COUNT(*) FROM position_snapshots").fetchone()[0] == 5
    )

"""Smoke tests for local database initialization."""

import sqlite3
from pathlib import Path

import pytest

from database import initialize_database, initialize_schema
from database.schema import SCHEMA_VERSION

SCHEMA_6_FIXTURE = """
CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
INSERT INTO schema_version VALUES (6, '2026-08-01T00:00:00+00:00');
CREATE TABLE holdings (
 id TEXT PRIMARY KEY, symbol TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
 market TEXT NOT NULL, currency TEXT NOT NULL, quantity TEXT NOT NULL,
 average_cost TEXT NOT NULL, holding_type TEXT NOT NULL, is_pledged INTEGER NOT NULL,
 notes TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE position_snapshots (
 snapshot_date TEXT NOT NULL, holding_id TEXT NOT NULL, symbol TEXT NOT NULL,
 quantity TEXT NOT NULL, average_cost TEXT NOT NULL, close_price TEXT NOT NULL,
 cost_basis TEXT NOT NULL, market_value TEXT NOT NULL, unrealized_pnl TEXT NOT NULL,
 unrealized_return TEXT NOT NULL, portfolio_weight TEXT NOT NULL,
 daily_value_change TEXT NOT NULL, daily_return TEXT,
 PRIMARY KEY(snapshot_date, holding_id));
INSERT INTO holdings VALUES
 ('tw','2330','TSMC','TWSE','TWD','10','100','stock',0,NULL,'t','t'),
 ('otc','8299','Phison','TPEX','TWD','20','200','stock',0,NULL,'t','t');
INSERT INTO position_snapshots VALUES
 ('2026-08-01','tw','2330','10','100','110','1000','1100','100','0.1','0.5','20','0.0185'),
 ('2026-08-01','otc','8299','20','200','190','4000','3800','-200','-0.05','0.5','-40','-0.0104');
CREATE TABLE transactions (
 id TEXT PRIMARY KEY, symbol TEXT NOT NULL, market TEXT NOT NULL,
 transaction_type TEXT NOT NULL, trade_date TEXT NOT NULL,
 settlement_date TEXT NOT NULL, quantity TEXT NOT NULL, price TEXT NOT NULL,
 fees TEXT NOT NULL, taxes TEXT NOT NULL, currency TEXT NOT NULL, notes TEXT,
 created_at TEXT NOT NULL);
INSERT INTO transactions VALUES
 ('tx','2330','TWSE','buy','2026-01-01','2026-01-01','10','100','1','0','TWD',NULL,'t');
CREATE TABLE price_quotes (
 symbol TEXT NOT NULL, market TEXT NOT NULL, trade_date TEXT NOT NULL,
 close_price TEXT NOT NULL, previous_close TEXT, currency TEXT NOT NULL,
 source TEXT NOT NULL, fetched_at TEXT NOT NULL,
 PRIMARY KEY(symbol, market, trade_date));
INSERT INTO price_quotes VALUES ('2330','TWSE','2026-08-01','110','108','TWD','official','t');
CREATE TABLE daily_snapshots (
 snapshot_date TEXT PRIMARY KEY, total_market_value TEXT NOT NULL,
 total_cost_basis TEXT NOT NULL, total_unrealized_pnl TEXT NOT NULL,
 total_liabilities TEXT NOT NULL, net_asset_value TEXT NOT NULL,
 leverage_ratio TEXT NOT NULL, high_water_mark TEXT NOT NULL,
 drawdown TEXT NOT NULL, created_at TEXT NOT NULL);
INSERT INTO daily_snapshots VALUES ('2026-08-01','4900','5000','-100','100','4800','0.0204','5000','-0.04','t');
CREATE TABLE liabilities (
 id TEXT PRIMARY KEY, liability_type TEXT NOT NULL, principal TEXT NOT NULL,
 annual_interest_rate TEXT, currency TEXT NOT NULL, start_date TEXT,
 maturity_date TEXT, collateral_description TEXT, notes TEXT, created_at TEXT NOT NULL);
INSERT INTO liabilities VALUES ('loan','margin_financing','100',NULL,'TWD',NULL,NULL,NULL,NULL,'t');
CREATE TABLE dividend_events (
 source_event_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, market TEXT NOT NULL,
 name TEXT NOT NULL, dividend_year INTEGER NOT NULL, ex_dividend_date TEXT NOT NULL,
 record_date TEXT, payment_date TEXT, cash_dividend_per_share TEXT,
 stock_dividend_per_share TEXT, source TEXT NOT NULL, source_updated_at TEXT,
 fetched_at TEXT NOT NULL);
INSERT INTO dividend_events VALUES
 ('event','2330','TWSE','TSMC',2026,'2026-07-01',NULL,NULL,'5',NULL,'official',NULL,'t');
"""


def test_database_initialization_creates_usable_sqlite_database(tmp_path: Path) -> None:
    """Initialization creates a database with foreign keys enabled."""
    database_path = tmp_path / "nested" / "pams.db"

    with initialize_database(f"sqlite:///{database_path.as_posix()}") as connection:
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()
        connection.execute("CREATE TABLE smoke_test (id INTEGER PRIMARY KEY)")

    assert database_path.is_file()
    assert foreign_keys[0] == 1
    with sqlite3.connect(database_path) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    assert ("smoke_test",) in tables


def test_schema_initialization_creates_market_data_version(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "schema.db"
    with initialize_database(f"sqlite:///{database_path.as_posix()}") as connection:
        initialize_schema(connection)
        version = connection.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        transaction_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(transactions)")
        }
        liability_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(liabilities)")
        }
    assert version[0] == SCHEMA_VERSION
    assert "watchlist" in tables
    assert "dividend_events" in tables
    assert "financing_type" in transaction_columns
    assert {"financed_symbol", "financed_quantity"} <= liability_columns
    assert "liability_principal_events" in tables
    assert {
        "price_quotes",
        "daily_snapshots",
        "position_snapshots",
        "report_deliveries",
    } <= tables


def test_schema_6_to_7_preserves_realistic_taiwan_data() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA_6_FIXTURE)
    initialize_schema(connection)
    assert (
        connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        == SCHEMA_VERSION
    )
    holdings = connection.execute(
        "SELECT symbol, market, currency, quantity, average_cost FROM holdings ORDER BY symbol"
    ).fetchall()
    assert [tuple(row) for row in holdings] == [
        ("2330", "TWSE", "TWD", "10", "100"),
        ("8299", "TPEX", "TWD", "20", "200"),
    ]
    positions = connection.execute(
        """SELECT symbol, market, native_currency, quote_date, fx_rate,
        quantity, average_cost, market_value, daily_value_change
        FROM position_snapshots ORDER BY symbol"""
    ).fetchall()
    assert [tuple(row) for row in positions] == [
        ("2330", "TWSE", "TWD", "2026-08-01", "1", "10", "100", "1100", "20"),
        ("8299", "TPEX", "TWD", "2026-08-01", "1", "20", "200", "3800", "-40"),
    ]
    assert connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM price_quotes").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM daily_snapshots").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM liabilities").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM dividend_events").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM fx_rates").fetchone()[0] == 0
    fx_indexes = {
        row[1] for row in connection.execute("PRAGMA index_list(fx_rates)").fetchall()
    }
    assert "ix_fx_rates_pair_date" in fx_indexes
    holdings_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='holdings'"
    ).fetchone()[0]
    assert "UNIQUE(symbol, market)" in holdings_sql
    initialize_schema(connection)
    assert connection.execute("SELECT COUNT(*) FROM holdings").fetchone()[0] == 2
    connection.close()


def test_failed_schema_7_rebuild_rolls_back_without_creating_fx_table() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE holdings (
          id TEXT PRIMARY KEY, symbol TEXT NOT NULL, name TEXT NOT NULL,
          market TEXT NOT NULL, currency TEXT NOT NULL, quantity TEXT NOT NULL,
          average_cost TEXT NOT NULL, holding_type TEXT NOT NULL,
          is_pledged INTEGER NOT NULL, notes TEXT, created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL);
        INSERT INTO holdings VALUES
          ('one','ABC','One','TWSE','TWD','1','1','stock',0,NULL,'t','t'),
          ('two','ABC','Two','TWSE','TWD','1','1','stock',0,NULL,'t','t');
        CREATE TABLE position_snapshots (
          snapshot_date TEXT NOT NULL, holding_id TEXT NOT NULL, symbol TEXT NOT NULL,
          quantity TEXT NOT NULL, average_cost TEXT NOT NULL, close_price TEXT NOT NULL,
          cost_basis TEXT NOT NULL, market_value TEXT NOT NULL,
          unrealized_pnl TEXT NOT NULL, unrealized_return TEXT NOT NULL,
          portfolio_weight TEXT NOT NULL, daily_value_change TEXT NOT NULL,
          daily_return TEXT, PRIMARY KEY(snapshot_date, holding_id));
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        initialize_schema(connection)
    assert connection.execute("SELECT COUNT(*) FROM holdings").fetchone()[0] == 2
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='fx_rates'"
        ).fetchone()[0]
        == 0
    )
    connection.close()


def test_sqlite_schema_10_to_11_preserves_existing_rows() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
        );
        INSERT INTO schema_version VALUES (10, 'before');
        CREATE TABLE preserved (id TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO preserved VALUES ('one', 'unchanged');
        """
    )

    initialize_schema(connection)

    assert (
        connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        == 12
    )
    assert tuple(connection.execute("SELECT * FROM preserved").fetchone()) == (
        "one",
        "unchanged",
    )
    columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(liability_principal_events)"
        ).fetchall()
    }
    assert {"liability_id", "effective_date", "sequence", "principal_delta"} <= columns
    connection.close()


def test_sqlite_schema_11_to_12_backfills_valuation_provenance() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
        INSERT INTO schema_version VALUES (11, 'before');
        CREATE TABLE daily_snapshots (
            snapshot_date TEXT PRIMARY KEY, total_market_value TEXT NOT NULL,
            total_cost_basis TEXT NOT NULL, total_unrealized_pnl TEXT NOT NULL,
            total_liabilities TEXT NOT NULL, net_asset_value TEXT NOT NULL,
            leverage_ratio TEXT NOT NULL, high_water_mark TEXT NOT NULL,
            drawdown TEXT NOT NULL, created_at TEXT NOT NULL
        );
        INSERT INTO daily_snapshots VALUES
            ('2026-08-28','1','1','123','0','1','0','1','0','before');
        CREATE TABLE annual_pnl_snapshots (
            snapshot_date TEXT PRIMARY KEY, year INTEGER NOT NULL,
            reporting_currency TEXT NOT NULL, realized_pnl_ytd TEXT NOT NULL,
            unrealized_pnl TEXT NOT NULL, dividend_income_ytd TEXT NOT NULL,
            financing_cost_ytd TEXT NOT NULL, other_cost_ytd TEXT NOT NULL,
            total_pnl_ytd TEXT NOT NULL, created_at TEXT NOT NULL
        );
        INSERT INTO annual_pnl_snapshots VALUES
            ('2026-08-28',2026,'TWD','10','123','5','2','1','135','before');
        """
    )

    initialize_schema(connection)

    row = connection.execute(
        "SELECT * FROM annual_pnl_snapshots WHERE snapshot_date='2026-08-28'"
    ).fetchone()
    assert row["valuation_date"] == "2026-08-28"
    assert row["unrealized_pnl"] == "123"
    assert (
        connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        == 12
    )
    columns = {
        item[1]: item[3]
        for item in connection.execute("PRAGMA table_info(annual_pnl_snapshots)")
    }
    assert columns["valuation_date"] == 1
    connection.close()


def test_sqlite_v12_migration_stops_when_provenance_is_unresolvable() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE daily_snapshots (
            snapshot_date TEXT PRIMARY KEY, total_unrealized_pnl TEXT NOT NULL
        );
        CREATE TABLE annual_pnl_snapshots (
            snapshot_date TEXT PRIMARY KEY, unrealized_pnl TEXT NOT NULL
        );
        INSERT INTO annual_pnl_snapshots VALUES ('2026-08-28','123');
        """
    )
    with pytest.raises(RuntimeError, match="cannot be resolved"):
        initialize_schema(connection)
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(annual_pnl_snapshots)")
    }
    assert "valuation_date" not in columns
    connection.close()

"""Initial SQLite schema and schema-version management."""

import sqlite3

SCHEMA_VERSION = 8

INITIAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS holdings (
    id TEXT PRIMARY KEY, symbol TEXT NOT NULL, name TEXT NOT NULL,
    market TEXT NOT NULL, currency TEXT NOT NULL, quantity TEXT NOT NULL,
    average_cost TEXT NOT NULL, holding_type TEXT NOT NULL,
    is_pledged INTEGER NOT NULL CHECK (is_pledged IN (0, 1)), notes TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(symbol, market)
);
CREATE INDEX IF NOT EXISTS ix_holdings_symbol ON holdings(symbol);
CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY, symbol TEXT NOT NULL, market TEXT NOT NULL,
    transaction_type TEXT NOT NULL, trade_date TEXT NOT NULL,
    settlement_date TEXT NOT NULL, quantity TEXT NOT NULL, price TEXT NOT NULL,
    fees TEXT NOT NULL, taxes TEXT NOT NULL, currency TEXT NOT NULL,
    financing_type TEXT CHECK (financing_type IS NULL OR financing_type = 'margin'),
    notes TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_transactions_symbol_date ON transactions(symbol, trade_date);
CREATE TABLE IF NOT EXISTS dividends (
    id TEXT PRIMARY KEY, symbol TEXT NOT NULL, market TEXT NOT NULL,
    ex_dividend_date TEXT NOT NULL, payment_date TEXT, amount_per_share TEXT NOT NULL,
    currency TEXT NOT NULL, shares_eligible TEXT NOT NULL, gross_amount TEXT NOT NULL,
    withholding_tax TEXT NOT NULL, net_amount TEXT NOT NULL, status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(symbol, market, ex_dividend_date)
);
CREATE INDEX IF NOT EXISTS ix_dividends_symbol_date ON dividends(symbol, ex_dividend_date);
CREATE TABLE IF NOT EXISTS dividend_events (
    source_event_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, market TEXT NOT NULL,
    name TEXT NOT NULL, dividend_year INTEGER NOT NULL,
    ex_dividend_date TEXT NOT NULL, record_date TEXT, payment_date TEXT,
    cash_dividend_per_share TEXT, stock_dividend_per_share TEXT,
    source TEXT NOT NULL, source_updated_at TEXT, fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_dividend_events_symbol_market
    ON dividend_events(symbol, market);
CREATE INDEX IF NOT EXISTS ix_dividend_events_ex_date
    ON dividend_events(ex_dividend_date);
CREATE INDEX IF NOT EXISTS ix_dividend_events_payment_date
    ON dividend_events(payment_date);
CREATE TABLE IF NOT EXISTS liabilities (
    id TEXT PRIMARY KEY, liability_type TEXT NOT NULL, principal TEXT NOT NULL,
    annual_interest_rate TEXT, currency TEXT NOT NULL, start_date TEXT,
    maturity_date TEXT, collateral_description TEXT, financed_symbol TEXT,
    financed_quantity TEXT, notes TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS price_quotes (
    symbol TEXT NOT NULL, market TEXT NOT NULL, trade_date TEXT NOT NULL,
    close_price TEXT NOT NULL, previous_close TEXT, currency TEXT NOT NULL,
    source TEXT NOT NULL, fetched_at TEXT NOT NULL,
    PRIMARY KEY (symbol, market, trade_date)
);
CREATE INDEX IF NOT EXISTS ix_price_quotes_date ON price_quotes(trade_date);
CREATE INDEX IF NOT EXISTS ix_price_quotes_symbol_date
    ON price_quotes(symbol, market, trade_date DESC);
CREATE TABLE IF NOT EXISTS fx_rates (
    base_currency TEXT NOT NULL, quote_currency TEXT NOT NULL,
    rate_date TEXT NOT NULL, rate TEXT NOT NULL, source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (base_currency, quote_currency, rate_date, source)
);
CREATE INDEX IF NOT EXISTS ix_fx_rates_pair_date
    ON fx_rates(base_currency, quote_currency, rate_date DESC);
CREATE TABLE IF NOT EXISTS daily_snapshots (
    snapshot_date TEXT PRIMARY KEY, total_market_value TEXT NOT NULL,
    total_cost_basis TEXT NOT NULL, total_unrealized_pnl TEXT NOT NULL,
    total_liabilities TEXT NOT NULL, net_asset_value TEXT NOT NULL,
    leverage_ratio TEXT NOT NULL, high_water_mark TEXT NOT NULL,
    drawdown TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_daily_snapshots_date
    ON daily_snapshots(snapshot_date);
CREATE TABLE IF NOT EXISTS position_snapshots (
    snapshot_date TEXT NOT NULL, holding_id TEXT NOT NULL, symbol TEXT NOT NULL,
    market TEXT NOT NULL, native_currency TEXT NOT NULL, quote_date TEXT,
    fx_rate TEXT NOT NULL, fx_rate_date TEXT,
    quantity TEXT NOT NULL, average_cost TEXT NOT NULL, close_price TEXT NOT NULL,
    cost_basis TEXT NOT NULL, market_value TEXT NOT NULL,
    unrealized_pnl TEXT NOT NULL, unrealized_return TEXT NOT NULL,
    portfolio_weight TEXT NOT NULL, daily_value_change TEXT NOT NULL,
    daily_return TEXT,
    PRIMARY KEY (snapshot_date, holding_id),
    FOREIGN KEY (holding_id) REFERENCES holdings(id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS ix_position_snapshots_symbol_date
    ON position_snapshots(symbol, snapshot_date DESC);
CREATE TABLE IF NOT EXISTS report_deliveries (
    id TEXT PRIMARY KEY,
    report_type TEXT NOT NULL,
    report_date TEXT NOT NULL,
    recipient TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('SENDING', 'SENT', 'FAILED')),
    sent_at TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (report_type, report_date, recipient)
);
CREATE INDEX IF NOT EXISTS ix_report_deliveries_date
    ON report_deliveries(report_type, report_date);
CREATE TABLE IF NOT EXISTS watchlist (
    symbol TEXT NOT NULL, market TEXT NOT NULL, display_name TEXT,
    target_price TEXT, buy_below_price TEXT, notes TEXT,
    PRIMARY KEY (symbol, market)
);
"""


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Create the initial schema and record its version idempotently."""
    existing_holdings = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'holdings'"
    ).fetchone()
    if existing_holdings is not None:
        holdings_sql = existing_holdings[0]
        position_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(position_snapshots)")
        }
        if "daily_return" not in position_columns:
            connection.execute(
                "ALTER TABLE position_snapshots ADD COLUMN daily_return TEXT"
            )
            position_columns.add("daily_return")
        if (
            "symbol TEXT NOT NULL UNIQUE" in holdings_sql
            or "market" not in position_columns
        ):
            _migrate_multi_currency_schema(connection)
    connection.executescript(INITIAL_SCHEMA)
    _migrate_margin_financing_schema(connection)
    for version in range(1, SCHEMA_VERSION + 1):
        connection.execute(
            """INSERT OR IGNORE INTO schema_version(version, applied_at)
            VALUES (?, datetime('now'))""",
            (version,),
        )
    connection.commit()


def _migrate_margin_financing_schema(connection: sqlite3.Connection) -> None:
    """Add nullable structured margin fields without rewriting existing records."""
    transaction_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(transactions)")
    }
    if "financing_type" not in transaction_columns:
        connection.execute("ALTER TABLE transactions ADD COLUMN financing_type TEXT")
    liability_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(liabilities)")
    }
    if "financed_symbol" not in liability_columns:
        connection.execute("ALTER TABLE liabilities ADD COLUMN financed_symbol TEXT")
    if "financed_quantity" not in liability_columns:
        connection.execute("ALTER TABLE liabilities ADD COLUMN financed_quantity TEXT")


def _migrate_multi_currency_schema(connection: sqlite3.Connection) -> None:
    """Rebuild legacy holding/snapshot tables for schema version 7."""
    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.executescript(
            """
            BEGIN;
            CREATE TABLE holdings_v7 (
                id TEXT PRIMARY KEY, symbol TEXT NOT NULL, name TEXT NOT NULL,
                market TEXT NOT NULL, currency TEXT NOT NULL, quantity TEXT NOT NULL,
                average_cost TEXT NOT NULL, holding_type TEXT NOT NULL,
                is_pledged INTEGER NOT NULL CHECK (is_pledged IN (0, 1)), notes TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(symbol, market)
            );
            INSERT INTO holdings_v7 SELECT * FROM holdings;
            CREATE TABLE position_snapshots_v7 (
                snapshot_date TEXT NOT NULL, holding_id TEXT NOT NULL,
                symbol TEXT NOT NULL, market TEXT NOT NULL,
                native_currency TEXT NOT NULL, quote_date TEXT,
                fx_rate TEXT NOT NULL, fx_rate_date TEXT,
                quantity TEXT NOT NULL, average_cost TEXT NOT NULL,
                close_price TEXT NOT NULL, cost_basis TEXT NOT NULL,
                market_value TEXT NOT NULL, unrealized_pnl TEXT NOT NULL,
                unrealized_return TEXT NOT NULL, portfolio_weight TEXT NOT NULL,
                daily_value_change TEXT NOT NULL, daily_return TEXT,
                PRIMARY KEY (snapshot_date, holding_id),
                FOREIGN KEY (holding_id) REFERENCES holdings_v7(id) ON DELETE RESTRICT
            );
            INSERT INTO position_snapshots_v7 (
                snapshot_date, holding_id, symbol, market, native_currency,
                quote_date, fx_rate, fx_rate_date, quantity, average_cost,
                close_price, cost_basis, market_value, unrealized_pnl,
                unrealized_return, portfolio_weight, daily_value_change, daily_return
            )
            SELECT p.snapshot_date, p.holding_id, p.symbol, h.market, h.currency,
                   p.snapshot_date, '1', NULL, p.quantity, p.average_cost,
                   p.close_price, p.cost_basis, p.market_value, p.unrealized_pnl,
                   p.unrealized_return, p.portfolio_weight, p.daily_value_change,
                   p.daily_return
            FROM position_snapshots p JOIN holdings h ON h.id = p.holding_id;
            DROP TABLE position_snapshots;
            DROP TABLE holdings;
            ALTER TABLE holdings_v7 RENAME TO holdings;
            ALTER TABLE position_snapshots_v7 RENAME TO position_snapshots;
            CREATE INDEX ix_holdings_symbol ON holdings(symbol);
            CREATE INDEX ix_position_snapshots_symbol_date
                ON position_snapshots(symbol, snapshot_date DESC);
            COMMIT;
            """
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")

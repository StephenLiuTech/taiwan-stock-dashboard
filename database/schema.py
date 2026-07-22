"""Initial SQLite schema and schema-version management."""

import sqlite3

SCHEMA_VERSION = 3

INITIAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS holdings (
    id TEXT PRIMARY KEY, symbol TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
    market TEXT NOT NULL, currency TEXT NOT NULL, quantity TEXT NOT NULL,
    average_cost TEXT NOT NULL, holding_type TEXT NOT NULL,
    is_pledged INTEGER NOT NULL CHECK (is_pledged IN (0, 1)), notes TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_holdings_symbol ON holdings(symbol);
CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY, symbol TEXT NOT NULL, market TEXT NOT NULL,
    transaction_type TEXT NOT NULL, trade_date TEXT NOT NULL,
    settlement_date TEXT NOT NULL, quantity TEXT NOT NULL, price TEXT NOT NULL,
    fees TEXT NOT NULL, taxes TEXT NOT NULL, currency TEXT NOT NULL, notes TEXT,
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
CREATE TABLE IF NOT EXISTS liabilities (
    id TEXT PRIMARY KEY, liability_type TEXT NOT NULL, principal TEXT NOT NULL,
    annual_interest_rate TEXT, currency TEXT NOT NULL, start_date TEXT,
    maturity_date TEXT, collateral_description TEXT, notes TEXT, created_at TEXT NOT NULL
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
"""


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Create the initial schema and record its version idempotently."""
    connection.executescript(INITIAL_SCHEMA)
    position_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(position_snapshots)")
    }
    if "daily_return" not in position_columns:
        connection.execute(
            "ALTER TABLE position_snapshots ADD COLUMN daily_return TEXT"
        )
    for version in range(1, SCHEMA_VERSION + 1):
        connection.execute(
            """INSERT OR IGNORE INTO schema_version(version, applied_at)
            VALUES (?, datetime('now'))""",
            (version,),
        )
    connection.commit()

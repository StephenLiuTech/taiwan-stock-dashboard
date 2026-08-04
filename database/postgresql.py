"""PostgreSQL connection and schema initialization."""

from collections.abc import Iterator, Mapping, Sequence

from database.schema import SCHEMA_VERSION

POSTGRESQL_URL_PREFIXES = ("postgresql://", "postgres://")


class DatabaseDependencyError(RuntimeError):
    """Raised when an optional database driver is unavailable."""


class PostgreSQLRow(Mapping[str, object]):
    """Row compatible with both sqlite3.Row mapping and index access."""

    def __init__(self, columns: Sequence[str], values: Sequence[object]) -> None:
        self._columns = tuple(columns)
        self._values = tuple(values)
        self._mapping = dict(zip(self._columns, self._values, strict=True))

    def __getitem__(self, key: str | int) -> object:
        if isinstance(key, int):
            return self._values[key]
        return self._mapping[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._columns)

    def __len__(self) -> int:
        return len(self._columns)


class PostgreSQLCursor:
    """Small cursor adapter exposing sqlite-compatible fetched rows."""

    def __init__(self, cursor: object) -> None:
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def fetchone(self) -> PostgreSQLRow | None:
        row = self._cursor.fetchone()
        return self._adapt(row) if row is not None else None

    def fetchall(self) -> list[PostgreSQLRow]:
        return [self._adapt(row) for row in self._cursor.fetchall()]

    def _adapt(self, row: Sequence[object]) -> PostgreSQLRow:
        columns = [item.name for item in self._cursor.description]
        return PostgreSQLRow(columns, row)


class PostgreSQLConnection:
    """DB-API adapter used by the repository implementations."""

    backend = "postgresql"

    def __init__(self, connection: object) -> None:
        self.raw_connection = connection

    def execute(
        self, statement: str, parameters: Sequence[object] | None = None
    ) -> PostgreSQLCursor:
        cursor = self.raw_connection.cursor()
        cursor.execute(_postgres_sql(statement), parameters or ())
        return PostgreSQLCursor(cursor)

    def executemany(
        self, statement: str, parameters: Sequence[Sequence[object]]
    ) -> PostgreSQLCursor:
        cursor = self.raw_connection.cursor()
        cursor.executemany(_postgres_sql(statement), parameters)
        return PostgreSQLCursor(cursor)

    def commit(self) -> None:
        self.raw_connection.commit()

    def rollback(self) -> None:
        self.raw_connection.rollback()

    def close(self) -> None:
        self.raw_connection.close()


def _postgres_sql(statement: str) -> str:
    return statement.replace("?", "%s").replace("datetime('now')", "CURRENT_TIMESTAMP")


def initialize_postgresql_database(database_url: str) -> PostgreSQLConnection:
    """Connect to PostgreSQL without exposing credentials."""
    if not database_url.startswith(POSTGRESQL_URL_PREFIXES):
        raise ValueError("PostgreSQL URL must start with postgresql://")
    try:
        import psycopg
    except ImportError as error:
        raise DatabaseDependencyError(
            "PostgreSQL support requires the psycopg package"
        ) from error
    return PostgreSQLConnection(psycopg.connect(database_url))


POSTGRESQL_SCHEMA = """
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
    created_at TEXT NOT NULL, UNIQUE(symbol, market, ex_dividend_date)
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
CREATE INDEX IF NOT EXISTS ix_daily_snapshots_date ON daily_snapshots(snapshot_date);
CREATE TABLE IF NOT EXISTS position_snapshots (
    snapshot_date TEXT NOT NULL, holding_id TEXT NOT NULL, symbol TEXT NOT NULL,
    quantity TEXT NOT NULL, average_cost TEXT NOT NULL, close_price TEXT NOT NULL,
    cost_basis TEXT NOT NULL, market_value TEXT NOT NULL,
    unrealized_pnl TEXT NOT NULL, unrealized_return TEXT NOT NULL,
    portfolio_weight TEXT NOT NULL, daily_value_change TEXT NOT NULL,
    daily_return TEXT, PRIMARY KEY (snapshot_date, holding_id),
    FOREIGN KEY (holding_id) REFERENCES holdings(id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS ix_position_snapshots_symbol_date
    ON position_snapshots(symbol, snapshot_date DESC);
CREATE TABLE IF NOT EXISTS report_deliveries (
    id TEXT PRIMARY KEY, report_type TEXT NOT NULL, report_date TEXT NOT NULL,
    recipient TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('SENDING', 'SENT', 'FAILED')),
    sent_at TEXT, error_message TEXT, created_at TEXT NOT NULL,
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


def initialize_postgresql_schema(connection: PostgreSQLConnection) -> None:
    """Create all PostgreSQL tables and record every applied schema version."""
    for statement in POSTGRESQL_SCHEMA.split(";"):
        if statement.strip():
            connection.execute(statement)
    for version in range(1, SCHEMA_VERSION + 1):
        connection.execute(
            """INSERT INTO schema_version(version, applied_at)
            VALUES (?, CURRENT_TIMESTAMP)
            ON CONFLICT(version) DO NOTHING""",
            (version,),
        )
    connection.commit()

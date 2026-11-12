"""PostgreSQL connection and schema initialization."""

from collections.abc import Iterator, Mapping, Sequence

from database.schema import LEGACY_MARGIN_TRANSACTION_IDS, SCHEMA_VERSION

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
        sql = _postgres_sql(statement)
        if parameters is None:
            cursor.execute(sql)
        else:
            cursor.execute(sql, parameters)
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
    broker_order TEXT, notes TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_transactions_symbol_date ON transactions(symbol, trade_date);
CREATE UNIQUE INDEX IF NOT EXISTS ux_transactions_broker_order
    ON transactions(market, trade_date, broker_order)
    WHERE broker_order IS NOT NULL;
CREATE TABLE IF NOT EXISTS lot_allocations (
    sell_transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT,
    buy_transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT,
    matched_quantity TEXT NOT NULL CHECK (matched_quantity::NUMERIC > 0),
    matched_trade_cost TEXT NOT NULL CHECK (matched_trade_cost::NUMERIC >= 0),
    allocated_buy_fee TEXT NOT NULL CHECK (allocated_buy_fee::NUMERIC >= 0),
    source TEXT NOT NULL, source_reference TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (sell_transaction_id, buy_transaction_id)
);
CREATE INDEX IF NOT EXISTS ix_lot_allocations_buy ON lot_allocations(buy_transaction_id);
CREATE TABLE IF NOT EXISTS corporate_actions (
    id TEXT PRIMARY KEY, symbol TEXT NOT NULL, market TEXT NOT NULL,
    effective_date TEXT NOT NULL, quantity_multiplier TEXT NOT NULL,
    source TEXT NOT NULL, reference TEXT, notes TEXT, created_at TEXT NOT NULL,
    UNIQUE(symbol, market, effective_date, source, reference)
);
CREATE INDEX IF NOT EXISTS ix_corporate_actions_symbol_date
    ON corporate_actions(symbol, market, effective_date);
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
    maturity_date TEXT, collateral_description TEXT, financed_symbol TEXT,
    financed_quantity TEXT, notes TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS margin_financing_positions (
    id TEXT PRIMARY KEY,
    liability_id TEXT NOT NULL REFERENCES liabilities(id) ON DELETE RESTRICT,
    market TEXT NOT NULL CHECK (market IN ('TWSE', 'TPEx')),
    symbol TEXT NOT NULL, currency TEXT NOT NULL CHECK (currency = 'TWD'),
    principal TEXT NOT NULL CHECK (principal::NUMERIC >= 0),
    financed_quantity TEXT NOT NULL CHECK (financed_quantity::NUMERIC >= 0),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(liability_id, market, symbol)
);
CREATE INDEX IF NOT EXISTS ix_margin_financing_positions_liability
    ON margin_financing_positions(liability_id, market, symbol);
CREATE TABLE IF NOT EXISTS transaction_cost_pool_openings (
    symbol TEXT NOT NULL, market TEXT NOT NULL, currency TEXT NOT NULL,
    effective_date TEXT NOT NULL, cash_quantity TEXT NOT NULL,
    margin_quantity TEXT NOT NULL, source TEXT NOT NULL,
    source_reference TEXT NOT NULL,
    PRIMARY KEY(symbol, market, effective_date)
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
CREATE INDEX IF NOT EXISTS ix_daily_snapshots_date ON daily_snapshots(snapshot_date);
CREATE TABLE IF NOT EXISTS position_snapshots (
    snapshot_date TEXT NOT NULL, holding_id TEXT NOT NULL, symbol TEXT NOT NULL,
    market TEXT NOT NULL, native_currency TEXT NOT NULL, quote_date TEXT,
    fx_rate TEXT NOT NULL, fx_rate_date TEXT,
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
CREATE TABLE IF NOT EXISTS investment_cost_events (
    id TEXT PRIMARY KEY, event_date TEXT NOT NULL,
    cost_type TEXT NOT NULL CHECK (cost_type IN ('financing', 'other')),
    amount TEXT NOT NULL, currency TEXT NOT NULL, description TEXT,
    source TEXT, margin_position_id TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_investment_cost_events_date_type
    ON investment_cost_events(event_date, cost_type);
CREATE TABLE IF NOT EXISTS annual_pnl_snapshots (
    snapshot_date TEXT PRIMARY KEY, valuation_date TEXT NOT NULL,
    year INTEGER NOT NULL,
    reporting_currency TEXT NOT NULL, realized_pnl_ytd TEXT NOT NULL,
    unrealized_pnl TEXT NOT NULL, dividend_income_ytd TEXT NOT NULL,
    financing_cost_ytd TEXT NOT NULL, other_cost_ytd TEXT NOT NULL,
    total_pnl_ytd TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_annual_pnl_snapshots_year_date
    ON annual_pnl_snapshots(year, snapshot_date);
CREATE TABLE IF NOT EXISTS liability_principal_events (
    id TEXT PRIMARY KEY, liability_id TEXT NOT NULL, effective_date TEXT NOT NULL,
    sequence INTEGER NOT NULL, event_type TEXT NOT NULL
        CHECK (event_type IN ('opening', 'increase', 'repayment', 'correction')),
    principal_delta TEXT NOT NULL, resulting_principal TEXT,
    source TEXT NOT NULL, reference TEXT, margin_position_id TEXT,
    financed_quantity_delta TEXT, notes TEXT, created_at TEXT NOT NULL,
    UNIQUE(liability_id, effective_date, sequence)
);
CREATE INDEX IF NOT EXISTS ix_liability_principal_events_replay
    ON liability_principal_events(liability_id, effective_date, sequence);
CREATE TABLE IF NOT EXISTS broker_import_records (
    id TEXT PRIMARY KEY, broker TEXT NOT NULL, source_fingerprint TEXT NOT NULL,
    source_row_reference TEXT NOT NULL, record_type TEXT NOT NULL,
    domain_entity_type TEXT NOT NULL, domain_entity_id TEXT NOT NULL,
    normalized_identity TEXT, imported_at TEXT NOT NULL, notes TEXT,
    UNIQUE(broker, source_fingerprint, source_row_reference, domain_entity_type)
);
CREATE INDEX IF NOT EXISTS ix_broker_import_domain_entity
    ON broker_import_records(domain_entity_type, domain_entity_id);
CREATE TABLE IF NOT EXISTS stock_net_equity_history (
    snapshot_date TEXT PRIMARY KEY, total_market_value TEXT,
    pledge_debt TEXT, margin_debt TEXT, total_liabilities TEXT,
    stock_net_equity TEXT,
    quality_status TEXT NOT NULL CHECK (
        quality_status IN ('VERIFIED', 'ESTIMATED_LIABILITY', 'UNKNOWN')
    ), source TEXT NOT NULL, source_reference TEXT NOT NULL,
    imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_stock_net_equity_history_quality_date
    ON stock_net_equity_history(quality_status, snapshot_date);
"""


def initialize_postgresql_schema(connection: PostgreSQLConnection) -> None:
    """Create or transactionally upgrade PostgreSQL to the current schema."""
    try:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"""
        )
        row = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
        current_version = int(row[0]) if row and row[0] is not None else 0
        if current_version > SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema version {current_version} is newer than "
                f"supported version {SCHEMA_VERSION}"
            )

        if current_version == 0 or current_version < 7:
            for statement in POSTGRESQL_SCHEMA.split(";"):
                if statement.strip():
                    connection.execute(statement)
        if 0 < current_version < 7:
            _migrate_postgresql_to_v7(connection)
        if 0 < current_version <= 7:
            _migrate_postgresql_v7_to_v8(connection)
        if 0 < current_version <= 8:
            _migrate_postgresql_v8_to_v9(connection)
        if 0 < current_version <= 9:
            _migrate_postgresql_v9_to_v10(connection)
        if 0 < current_version <= 10:
            _migrate_postgresql_v10_to_v11(connection)
        if 0 < current_version <= 11:
            _migrate_postgresql_v11_to_v12(connection)
        if 0 < current_version <= 12:
            _migrate_postgresql_v12_to_v13(connection)
        if 0 < current_version <= 13:
            _migrate_postgresql_v13_to_v14(connection)
        if 0 < current_version <= 14:
            _migrate_postgresql_v14_to_v15(connection)
        if 0 < current_version <= 15:
            _migrate_postgresql_v15_to_v16(connection)

        for version in range(current_version + 1, SCHEMA_VERSION + 1):
            connection.execute(
                """INSERT INTO schema_version(version, applied_at)
                VALUES (?, CURRENT_TIMESTAMP)
                ON CONFLICT(version) DO NOTHING""",
                (version,),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _migrate_postgresql_to_v7(connection: PostgreSQLConnection) -> None:
    """Apply the legacy multi-market delta for databases older than v7."""
    connection.execute(
        "ALTER TABLE holdings DROP CONSTRAINT IF EXISTS holdings_symbol_key"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_holdings_symbol_market "
        "ON holdings(symbol, market)"
    )
    for column, definition in (
        ("market", "TEXT"),
        ("native_currency", "TEXT"),
        ("quote_date", "TEXT"),
        ("fx_rate", "TEXT"),
        ("fx_rate_date", "TEXT"),
    ):
        connection.execute(
            f"ALTER TABLE position_snapshots ADD COLUMN IF NOT EXISTS {column} {definition}"
        )
    connection.execute(
        """UPDATE position_snapshots p SET
        market = COALESCE(p.market, h.market),
        native_currency = COALESCE(p.native_currency, h.currency),
        quote_date = COALESCE(p.quote_date, p.snapshot_date),
        fx_rate = COALESCE(p.fx_rate, '1')
        FROM holdings h WHERE h.id = p.holding_id"""
    )


def _migrate_postgresql_v7_to_v8(connection: PostgreSQLConnection) -> None:
    """Add nullable margin provenance fields without rewriting business rows."""
    connection.execute(
        """ALTER TABLE transactions ADD COLUMN IF NOT EXISTS financing_type TEXT
        CHECK (financing_type IS NULL OR financing_type = 'margin')"""
    )
    connection.execute(
        "ALTER TABLE liabilities ADD COLUMN IF NOT EXISTS financed_symbol TEXT"
    )
    connection.execute(
        "ALTER TABLE liabilities ADD COLUMN IF NOT EXISTS financed_quantity TEXT"
    )


def _migrate_postgresql_v8_to_v9(connection: PostgreSQLConnection) -> None:
    """Add immutable annual P/L snapshots and dated investment costs."""
    for statement in POSTGRESQL_SCHEMA.split(";"):
        if "investment_cost_events" in statement or "annual_pnl_snapshots" in statement:
            connection.execute(statement)


def _migrate_postgresql_v9_to_v10(connection: PostgreSQLConnection) -> None:
    """Add replayable non-cash quantity-conversion events."""
    for statement in POSTGRESQL_SCHEMA.split(";"):
        if "corporate_actions" in statement:
            connection.execute(statement)


def _migrate_postgresql_v10_to_v11(connection: PostgreSQLConnection) -> None:
    """Add the replayable liability-principal event ledger."""
    for statement in POSTGRESQL_SCHEMA.split(";"):
        if "liability_principal_events" in statement:
            connection.execute(statement)


def _migrate_postgresql_v11_to_v12(connection: PostgreSQLConnection) -> None:
    """Add deterministic market-valuation provenance to annual P/L rows."""
    connection.execute(
        "ALTER TABLE annual_pnl_snapshots ADD COLUMN IF NOT EXISTS valuation_date TEXT"
    )
    connection.execute(
        """UPDATE annual_pnl_snapshots a SET valuation_date = (
        SELECT MAX(d.snapshot_date) FROM daily_snapshots d
        WHERE d.snapshot_date <= a.snapshot_date
          AND d.total_unrealized_pnl = a.unrealized_pnl)
        WHERE a.valuation_date IS NULL"""
    )
    unresolved = connection.execute(
        "SELECT COUNT(*) FROM annual_pnl_snapshots WHERE valuation_date IS NULL"
    ).fetchone()
    if unresolved and int(unresolved[0]) != 0:
        raise RuntimeError("annual P/L valuation provenance cannot be resolved")
    connection.execute(
        "ALTER TABLE annual_pnl_snapshots ALTER COLUMN valuation_date SET NOT NULL"
    )


def _migrate_postgresql_v12_to_v13(connection: PostgreSQLConnection) -> None:
    """Add structured broker-source provenance without touching accounting rows."""
    for statement in POSTGRESQL_SCHEMA.split(";"):
        if "broker_import_records" in statement:
            connection.execute(statement)


def _migrate_postgresql_v13_to_v14(connection: PostgreSQLConnection) -> None:
    """Add isolated historical Stock Net Equity facts."""
    for statement in POSTGRESQL_SCHEMA.split(";"):
        if "stock_net_equity_history" in statement:
            connection.execute(statement)


def _migrate_postgresql_v14_to_v15(connection: PostgreSQLConnection) -> None:
    """Add explicit sale-to-buy matching without rewriting financial history."""
    for statement in POSTGRESQL_SCHEMA.split(";"):
        if "lot_allocations" in statement:
            connection.execute(statement)


def _migrate_postgresql_v15_to_v16(connection: PostgreSQLConnection) -> None:
    """Add per-symbol projections beneath the aggregate margin liability."""
    connection.execute(
        "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS broker_order TEXT"
    )
    connection.execute(
        """UPDATE transactions SET broker_order = upper(trim(substring(notes from 14)))
        WHERE broker_order IS NULL AND notes LIKE ?""",
        ("Broker order %",),
    )
    connection.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS ux_transactions_broker_order
        ON transactions(market, trade_date, broker_order)
        WHERE broker_order IS NOT NULL"""
    )
    for statement in POSTGRESQL_SCHEMA.split(";"):
        if (
            "margin_financing_positions" in statement
            or "transaction_cost_pool_openings" in statement
        ):
            connection.execute(statement)
    connection.execute(
        "ALTER TABLE liability_principal_events "
        "ADD COLUMN IF NOT EXISTS margin_position_id TEXT"
    )
    connection.execute(
        "ALTER TABLE liability_principal_events "
        "ADD COLUMN IF NOT EXISTS financed_quantity_delta TEXT"
    )
    connection.execute(
        "ALTER TABLE investment_cost_events "
        "ADD COLUMN IF NOT EXISTS margin_position_id TEXT"
    )
    connection.execute(
        """INSERT INTO margin_financing_positions (
        id, liability_id, market, symbol, currency, principal,
        financed_quantity, created_at, updated_at)
        SELECT 'margin-position-' || l.id || '-' || lower(h.market) || '-' ||
               lower(l.financed_symbol),
               l.id, h.market, l.financed_symbol, l.currency, l.principal,
               l.financed_quantity, l.created_at, l.created_at
        FROM liabilities l
        JOIN holdings h ON h.symbol = l.financed_symbol
        WHERE l.liability_type = 'margin_financing'
          AND l.financed_symbol IS NOT NULL
          AND l.financed_quantity IS NOT NULL
        ON CONFLICT (liability_id, market, symbol) DO NOTHING"""
    )
    connection.execute(
        """UPDATE liability_principal_events e
        SET margin_position_id = p.id
        FROM margin_financing_positions p
        WHERE e.margin_position_id IS NULL
          AND e.liability_id = p.liability_id"""
    )
    connection.execute(
        """UPDATE liability_principal_events e
        SET financed_quantity_delta = t.quantity
        FROM transactions t
        WHERE e.financed_quantity_delta IS NULL
          AND e.event_type = 'increase'
          AND e.reference = t.id
          AND t.financing_type = 'margin'
          AND t.transaction_type = 'buy'"""
    )
    connection.execute(
        """UPDATE investment_cost_events c
        SET margin_position_id = p.id
        FROM margin_financing_positions p
        WHERE c.margin_position_id IS NULL
          AND c.cost_type = 'financing'
          AND position(p.liability_id in c.description) > 0"""
    )
    connection.executemany(
        "UPDATE transactions SET financing_type = 'margin' WHERE id = ?",
        [(transaction_id,) for transaction_id in LEGACY_MARGIN_TRANSACTION_IDS],
    )
    connection.executemany(
        """INSERT INTO transaction_cost_pool_openings VALUES
        (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING""",
        [
            (
                "8131",
                "TWSE",
                "TWD",
                "2026-07-02",
                "0",
                "0",
                "legacy migration opening",
                "8131 full margin lifecycle closed",
            ),
            (
                "2027",
                "TWSE",
                "TWD",
                "2026-10-30",
                "8000",
                "55000",
                "legacy migration opening",
                "approved cash/margin quantity split",
            ),
            (
                "00406A",
                "TWSE",
                "TWD",
                "2026-11-11",
                "10000",
                "0",
                "legacy migration opening",
                "pre-AW854 cash position",
            ),
        ],
    )

"""Initial SQLite schema and schema-version management."""

import sqlite3

SCHEMA_VERSION = 16

# Audited SELL recorded after the legacy 2027 cash/margin opening. Earlier
# positions are split by explicit legacy-migration openings instead of by
# rewriting their historical transactions.
LEGACY_MARGIN_TRANSACTION_IDS = ("broker-f6fc175f-244f-55da-bb27-b1a6d29eb755",)

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
    broker_order TEXT, notes TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_transactions_symbol_date ON transactions(symbol, trade_date);
CREATE TABLE IF NOT EXISTS lot_allocations (
    sell_transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT,
    buy_transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT,
    matched_quantity TEXT NOT NULL CHECK (CAST(matched_quantity AS NUMERIC) > 0),
    matched_trade_cost TEXT NOT NULL CHECK (CAST(matched_trade_cost AS NUMERIC) >= 0),
    allocated_buy_fee TEXT NOT NULL CHECK (CAST(allocated_buy_fee AS NUMERIC) >= 0),
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
CREATE TABLE IF NOT EXISTS margin_financing_positions (
    id TEXT PRIMARY KEY,
    liability_id TEXT NOT NULL REFERENCES liabilities(id) ON DELETE RESTRICT,
    market TEXT NOT NULL CHECK (market IN ('TWSE', 'TPEx')),
    symbol TEXT NOT NULL, currency TEXT NOT NULL CHECK (currency = 'TWD'),
    principal TEXT NOT NULL CHECK (CAST(principal AS NUMERIC) >= 0),
    financed_quantity TEXT NOT NULL
        CHECK (CAST(financed_quantity AS NUMERIC) >= 0),
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
    ),
    source TEXT NOT NULL, source_reference TEXT NOT NULL,
    imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_stock_net_equity_history_quality_date
    ON stock_net_equity_history(quality_status, snapshot_date);
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
    annual_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='annual_pnl_snapshots'"
    ).fetchone()
    if annual_table is not None:
        annual_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(annual_pnl_snapshots)")
        }
        if "valuation_date" not in annual_columns:
            _migrate_annual_pnl_valuation_date(connection)
    connection.executescript(INITIAL_SCHEMA)
    _migrate_margin_financing_schema(connection)
    _migrate_multi_symbol_margin_schema(connection)
    for version in range(1, SCHEMA_VERSION + 1):
        connection.execute(
            """INSERT OR IGNORE INTO schema_version(version, applied_at)
            VALUES (?, datetime('now'))""",
            (version,),
        )
    connection.commit()


def _migrate_annual_pnl_valuation_date(connection: sqlite3.Connection) -> None:
    """Rebuild annual snapshots with deterministic market-valuation provenance."""
    rows = connection.execute(
        """SELECT a.snapshot_date,
        (SELECT MAX(d.snapshot_date) FROM daily_snapshots d
         WHERE d.snapshot_date <= a.snapshot_date
           AND d.total_unrealized_pnl = a.unrealized_pnl) AS valuation_date
        FROM annual_pnl_snapshots a"""
    ).fetchall()
    unresolved = [row[0] for row in rows if row[1] is None]
    if unresolved:
        raise RuntimeError(
            "annual P/L valuation provenance cannot be resolved for "
            + ", ".join(unresolved)
        )
    connection.executescript(
        """
        BEGIN;
        CREATE TABLE annual_pnl_snapshots_v12 (
            snapshot_date TEXT PRIMARY KEY, valuation_date TEXT NOT NULL,
            year INTEGER NOT NULL, reporting_currency TEXT NOT NULL,
            realized_pnl_ytd TEXT NOT NULL, unrealized_pnl TEXT NOT NULL,
            dividend_income_ytd TEXT NOT NULL, financing_cost_ytd TEXT NOT NULL,
            other_cost_ytd TEXT NOT NULL, total_pnl_ytd TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        INSERT INTO annual_pnl_snapshots_v12
        SELECT a.snapshot_date,
               (SELECT MAX(d.snapshot_date) FROM daily_snapshots d
                WHERE d.snapshot_date <= a.snapshot_date
                  AND d.total_unrealized_pnl = a.unrealized_pnl),
               a.year, a.reporting_currency, a.realized_pnl_ytd,
               a.unrealized_pnl, a.dividend_income_ytd,
               a.financing_cost_ytd, a.other_cost_ytd, a.total_pnl_ytd,
               a.created_at
        FROM annual_pnl_snapshots a;
        DROP TABLE annual_pnl_snapshots;
        ALTER TABLE annual_pnl_snapshots_v12 RENAME TO annual_pnl_snapshots;
        CREATE INDEX ix_annual_pnl_snapshots_year_date
            ON annual_pnl_snapshots(year, snapshot_date);
        COMMIT;
        """
    )


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


def _migrate_multi_symbol_margin_schema(connection: sqlite3.Connection) -> None:
    """Add per-symbol margin projections while preserving the legacy master."""
    transaction_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(transactions)")
    }
    if "broker_order" not in transaction_columns:
        connection.execute("ALTER TABLE transactions ADD COLUMN broker_order TEXT")
        connection.execute(
            """UPDATE transactions SET broker_order = upper(trim(substr(notes, 14)))
            WHERE notes LIKE ?""",
            ("Broker order %",),
        )
    connection.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS ux_transactions_broker_order
        ON transactions(market, trade_date, broker_order)
        WHERE broker_order IS NOT NULL"""
    )
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS margin_financing_positions (
            id TEXT PRIMARY KEY,
            liability_id TEXT NOT NULL REFERENCES liabilities(id) ON DELETE RESTRICT,
            market TEXT NOT NULL CHECK (market IN ('TWSE', 'TPEx')),
            symbol TEXT NOT NULL, currency TEXT NOT NULL CHECK (currency = 'TWD'),
            principal TEXT NOT NULL CHECK (CAST(principal AS NUMERIC) >= 0),
            financed_quantity TEXT NOT NULL
                CHECK (CAST(financed_quantity AS NUMERIC) >= 0),
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
        """
    )
    principal_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(liability_principal_events)")
    }
    if "margin_position_id" not in principal_columns:
        connection.execute(
            "ALTER TABLE liability_principal_events "
            "ADD COLUMN margin_position_id TEXT"
        )
    if "financed_quantity_delta" not in principal_columns:
        connection.execute(
            "ALTER TABLE liability_principal_events "
            "ADD COLUMN financed_quantity_delta TEXT"
        )
    cost_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(investment_cost_events)")
    }
    if "margin_position_id" not in cost_columns:
        connection.execute(
            "ALTER TABLE investment_cost_events ADD COLUMN margin_position_id TEXT"
        )
    connection.execute(
        """INSERT OR IGNORE INTO margin_financing_positions (
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
          AND l.financed_quantity IS NOT NULL"""
    )
    connection.execute(
        """UPDATE liability_principal_events
        SET margin_position_id = (
            SELECT p.id FROM margin_financing_positions p
            WHERE p.liability_id = liability_principal_events.liability_id
            ORDER BY p.id LIMIT 1)
        WHERE margin_position_id IS NULL
          AND liability_id IN (
              SELECT id FROM liabilities WHERE liability_type='margin_financing')"""
    )
    connection.execute(
        """UPDATE liability_principal_events
        SET financed_quantity_delta = (
            SELECT t.quantity FROM transactions t
            WHERE t.id = liability_principal_events.reference
              AND t.financing_type = 'margin'
              AND t.transaction_type = 'buy')
        WHERE financed_quantity_delta IS NULL AND event_type = 'increase'"""
    )
    connection.execute(
        """UPDATE investment_cost_events
        SET margin_position_id = (
            SELECT p.id FROM margin_financing_positions p
            WHERE instr(description, p.liability_id) > 0
            ORDER BY p.id LIMIT 1)
        WHERE margin_position_id IS NULL AND cost_type = 'financing'"""
    )
    connection.executemany(
        "UPDATE transactions SET financing_type = 'margin' WHERE id = ?",
        [(transaction_id,) for transaction_id in LEGACY_MARGIN_TRANSACTION_IDS],
    )
    connection.executemany(
        """INSERT OR IGNORE INTO transaction_cost_pool_openings VALUES
        (?, ?, ?, ?, ?, ?, ?, ?)""",
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

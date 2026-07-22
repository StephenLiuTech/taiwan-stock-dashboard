"""SQLite implementations of the PAMS repository contracts."""

import sqlite3
from datetime import date, datetime
from decimal import Decimal

from domain import (
    DailySnapshot,
    Dividend,
    Holding,
    Liability,
    PositionSnapshot,
    PriceQuote,
    Transaction,
)


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


class SQLiteHoldingRepository:
    """Persist holdings in SQLite."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def list_all(self) -> list[Holding]:
        rows = self.connection.execute(
            "SELECT * FROM holdings ORDER BY symbol"
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def get_by_id(self, holding_id: str) -> Holding | None:
        row = self.connection.execute(
            "SELECT * FROM holdings WHERE id = ?", (holding_id,)
        ).fetchone()
        return self._from_row(row) if row else None

    def upsert(self, holding: Holding) -> None:
        self.connection.execute(
            """INSERT INTO holdings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET symbol=excluded.symbol, name=excluded.name,
            market=excluded.market, currency=excluded.currency, quantity=excluded.quantity,
            average_cost=excluded.average_cost, holding_type=excluded.holding_type,
            is_pledged=excluded.is_pledged, notes=excluded.notes,
            updated_at=excluded.updated_at""",
            (
                holding.id,
                holding.symbol,
                holding.name,
                holding.market.value,
                holding.currency.value,
                str(holding.quantity),
                str(holding.average_cost),
                holding.holding_type.value,
                int(holding.is_pledged),
                holding.notes,
                holding.created_at.isoformat(),
                holding.updated_at.isoformat(),
            ),
        )
        self.connection.commit()

    def delete(self, holding_id: str) -> None:
        self.connection.execute("DELETE FROM holdings WHERE id = ?", (holding_id,))
        self.connection.commit()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Holding:
        values = dict(row)
        values["quantity"] = _decimal(values["quantity"])
        values["average_cost"] = _decimal(values["average_cost"])
        values["is_pledged"] = bool(values["is_pledged"])
        values["created_at"] = datetime.fromisoformat(values["created_at"])
        values["updated_at"] = datetime.fromisoformat(values["updated_at"])
        return Holding.model_validate(values)


class SQLiteTransactionRepository:
    """Persist transactions in SQLite."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def list_all(self) -> list[Transaction]:
        rows = self.connection.execute(
            "SELECT * FROM transactions ORDER BY trade_date, id"
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def get_by_id(self, transaction_id: str) -> Transaction | None:
        row = self.connection.execute(
            "SELECT * FROM transactions WHERE id = ?", (transaction_id,)
        ).fetchone()
        return self._from_row(row) if row else None

    def list_by_symbol(self, symbol: str) -> list[Transaction]:
        rows = self.connection.execute(
            "SELECT * FROM transactions WHERE symbol = ? ORDER BY trade_date, id",
            (symbol.strip().upper(),),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def upsert(self, transaction: Transaction) -> None:
        self.connection.execute(
            """INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET symbol=excluded.symbol, market=excluded.market,
            transaction_type=excluded.transaction_type, trade_date=excluded.trade_date,
            settlement_date=excluded.settlement_date, quantity=excluded.quantity,
            price=excluded.price, fees=excluded.fees, taxes=excluded.taxes,
            currency=excluded.currency, notes=excluded.notes""",
            (
                transaction.id,
                transaction.symbol,
                transaction.market.value,
                transaction.transaction_type.value,
                transaction.trade_date.isoformat(),
                transaction.settlement_date.isoformat(),
                str(transaction.quantity),
                str(transaction.price),
                str(transaction.fees),
                str(transaction.taxes),
                transaction.currency.value,
                transaction.notes,
                datetime.now().isoformat(),
            ),
        )
        self.connection.commit()

    def delete(self, transaction_id: str) -> None:
        self.connection.execute(
            "DELETE FROM transactions WHERE id = ?", (transaction_id,)
        )
        self.connection.commit()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Transaction:
        values = dict(row)
        values.pop("created_at")
        for key in ("quantity", "price", "fees", "taxes"):
            values[key] = _decimal(values[key])
        values["trade_date"] = date.fromisoformat(values["trade_date"])
        values["settlement_date"] = date.fromisoformat(values["settlement_date"])
        return Transaction.model_validate(values)


class SQLiteDividendRepository:
    """Persist dividends in SQLite."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def list_all(self) -> list[Dividend]:
        rows = self.connection.execute(
            "SELECT * FROM dividends ORDER BY ex_dividend_date, id"
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def get_by_id(self, dividend_id: str) -> Dividend | None:
        row = self.connection.execute(
            "SELECT * FROM dividends WHERE id = ?", (dividend_id,)
        ).fetchone()
        return self._from_row(row) if row else None

    def list_by_symbol(self, symbol: str) -> list[Dividend]:
        rows = self.connection.execute(
            "SELECT * FROM dividends WHERE symbol = ? ORDER BY ex_dividend_date, id",
            (symbol.strip().upper(),),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def upsert(self, dividend: Dividend) -> None:
        self.connection.execute(
            """INSERT INTO dividends VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET payment_date=excluded.payment_date,
            amount_per_share=excluded.amount_per_share,
            shares_eligible=excluded.shares_eligible, gross_amount=excluded.gross_amount,
            withholding_tax=excluded.withholding_tax, net_amount=excluded.net_amount,
            status=excluded.status""",
            (
                dividend.id,
                dividend.symbol,
                dividend.market.value,
                dividend.ex_dividend_date.isoformat(),
                dividend.payment_date.isoformat() if dividend.payment_date else None,
                str(dividend.amount_per_share),
                dividend.currency.value,
                str(dividend.shares_eligible),
                str(dividend.gross_amount),
                str(dividend.withholding_tax),
                str(dividend.net_amount),
                dividend.status.value,
                datetime.now().isoformat(),
            ),
        )
        self.connection.commit()

    def delete(self, dividend_id: str) -> None:
        self.connection.execute("DELETE FROM dividends WHERE id = ?", (dividend_id,))
        self.connection.commit()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Dividend:
        values = dict(row)
        values.pop("created_at")
        for key in (
            "amount_per_share",
            "shares_eligible",
            "gross_amount",
            "withholding_tax",
            "net_amount",
        ):
            values[key] = _decimal(values[key])
        values["ex_dividend_date"] = date.fromisoformat(values["ex_dividend_date"])
        if values["payment_date"]:
            values["payment_date"] = date.fromisoformat(values["payment_date"])
        return Dividend.model_validate(values)


class SQLiteLiabilityRepository:
    """Persist liabilities in SQLite."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def list_all(self) -> list[Liability]:
        rows = self.connection.execute(
            "SELECT * FROM liabilities ORDER BY liability_type, id"
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def get_by_id(self, liability_id: str) -> Liability | None:
        row = self.connection.execute(
            "SELECT * FROM liabilities WHERE id = ?", (liability_id,)
        ).fetchone()
        return self._from_row(row) if row else None

    def upsert(self, liability: Liability) -> None:
        self.connection.execute(
            """INSERT INTO liabilities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET liability_type=excluded.liability_type,
            principal=excluded.principal, annual_interest_rate=excluded.annual_interest_rate,
            currency=excluded.currency, start_date=excluded.start_date,
            maturity_date=excluded.maturity_date,
            collateral_description=excluded.collateral_description, notes=excluded.notes""",
            (
                liability.id,
                liability.liability_type.value,
                str(liability.principal),
                (
                    str(liability.annual_interest_rate)
                    if liability.annual_interest_rate is not None
                    else None
                ),
                liability.currency.value,
                liability.start_date.isoformat() if liability.start_date else None,
                (
                    liability.maturity_date.isoformat()
                    if liability.maturity_date
                    else None
                ),
                liability.collateral_description,
                liability.notes,
                datetime.now().isoformat(),
            ),
        )
        self.connection.commit()

    def delete(self, liability_id: str) -> None:
        self.connection.execute("DELETE FROM liabilities WHERE id = ?", (liability_id,))
        self.connection.commit()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Liability:
        values = dict(row)
        values.pop("created_at")
        values["principal"] = _decimal(values["principal"])
        if values["annual_interest_rate"] is not None:
            values["annual_interest_rate"] = _decimal(values["annual_interest_rate"])
        if values["start_date"]:
            values["start_date"] = date.fromisoformat(values["start_date"])
        if values["maturity_date"]:
            values["maturity_date"] = date.fromisoformat(values["maturity_date"])
        return Liability.model_validate(values)


class SQLiteSnapshotRepository:
    """Persist immutable daily snapshots in SQLite."""

    def __init__(
        self, connection: sqlite3.Connection, *, auto_commit: bool = True
    ) -> None:
        self.connection = connection
        self.auto_commit = auto_commit

    def get_by_date(self, snapshot_date: date) -> DailySnapshot | None:
        row = self.connection.execute(
            "SELECT * FROM daily_snapshots WHERE snapshot_date = ?",
            (snapshot_date.isoformat(),),
        ).fetchone()
        return self._from_row(row) if row else None

    def get_latest(self) -> DailySnapshot | None:
        row = self.connection.execute(
            "SELECT * FROM daily_snapshots ORDER BY snapshot_date DESC LIMIT 1"
        ).fetchone()
        return self._from_row(row) if row else None

    def get_highest(self) -> DailySnapshot | None:
        row = self.connection.execute(
            """SELECT * FROM daily_snapshots
            ORDER BY CAST(high_water_mark AS NUMERIC) DESC LIMIT 1"""
        ).fetchone()
        return self._from_row(row) if row else None

    def list_between_dates(self, start: date, end: date) -> list[DailySnapshot]:
        rows = self.connection.execute(
            """SELECT * FROM daily_snapshots
            WHERE snapshot_date BETWEEN ? AND ? ORDER BY snapshot_date""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def add(self, snapshot: DailySnapshot) -> None:
        self.connection.execute(
            "INSERT INTO daily_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                snapshot.snapshot_date.isoformat(),
                str(snapshot.total_market_value),
                str(snapshot.total_cost_basis),
                str(snapshot.total_unrealized_pnl),
                str(snapshot.total_liabilities),
                str(snapshot.net_asset_value),
                str(snapshot.leverage_ratio),
                str(snapshot.high_water_mark),
                str(snapshot.drawdown),
                snapshot.created_at.isoformat(),
            ),
        )
        if self.auto_commit:
            self.connection.commit()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> DailySnapshot:
        values = dict(row)
        values["snapshot_date"] = date.fromisoformat(values["snapshot_date"])
        values["created_at"] = datetime.fromisoformat(values["created_at"])
        for key in (
            "total_market_value",
            "total_cost_basis",
            "total_unrealized_pnl",
            "total_liabilities",
            "net_asset_value",
            "leverage_ratio",
            "high_water_mark",
            "drawdown",
        ):
            values[key] = _decimal(values[key])
        return DailySnapshot.model_validate(values)


class SQLitePriceQuoteRepository:
    """Persist normalized end-of-day price quotes in SQLite."""

    def __init__(
        self, connection: sqlite3.Connection, *, auto_commit: bool = True
    ) -> None:
        self.connection = connection
        self.auto_commit = auto_commit

    def upsert_many(self, quotes: list[PriceQuote]) -> None:
        self.connection.executemany(
            """INSERT INTO price_quotes VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, market, trade_date) DO UPDATE SET
            close_price=excluded.close_price,
            previous_close=excluded.previous_close,
            currency=excluded.currency,
            source=excluded.source,
            fetched_at=excluded.fetched_at""",
            [
                (
                    quote.symbol,
                    quote.market.value,
                    quote.trade_date.isoformat(),
                    str(quote.close_price),
                    (
                        str(quote.previous_close)
                        if quote.previous_close is not None
                        else None
                    ),
                    quote.currency.value,
                    quote.source,
                    quote.fetched_at.isoformat(),
                )
                for quote in quotes
            ],
        )
        if self.auto_commit:
            self.connection.commit()

    def list_by_date(self, trade_date: date) -> list[PriceQuote]:
        rows = self.connection.execute(
            """SELECT * FROM price_quotes WHERE trade_date = ?
            ORDER BY market, symbol""",
            (trade_date.isoformat(),),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def get_latest(self, symbol: str, market: str) -> PriceQuote | None:
        row = self.connection.execute(
            """SELECT * FROM price_quotes WHERE symbol = ? AND market = ?
            ORDER BY trade_date DESC LIMIT 1""",
            (symbol.strip().upper(), market),
        ).fetchone()
        return self._from_row(row) if row else None

    def get_latest_date(self) -> date | None:
        """Return the newest persisted quote date across all markets."""
        row = self.connection.execute(
            "SELECT MAX(trade_date) FROM price_quotes"
        ).fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None

    @staticmethod
    def _from_row(row: sqlite3.Row) -> PriceQuote:
        values = dict(row)
        values["trade_date"] = date.fromisoformat(values["trade_date"])
        values["fetched_at"] = datetime.fromisoformat(values["fetched_at"])
        values["close_price"] = _decimal(values["close_price"])
        if values["previous_close"] is not None:
            values["previous_close"] = _decimal(values["previous_close"])
        return PriceQuote.model_validate(values)


class SQLitePositionSnapshotRepository:
    """Persist one position valuation per holding and snapshot date."""

    def __init__(
        self, connection: sqlite3.Connection, *, auto_commit: bool = True
    ) -> None:
        self.connection = connection
        self.auto_commit = auto_commit

    def add_many(self, snapshots: list[PositionSnapshot]) -> None:
        self.connection.executemany(
            """INSERT INTO position_snapshots VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    item.snapshot_date.isoformat(),
                    item.holding_id,
                    item.symbol,
                    str(item.quantity),
                    str(item.average_cost),
                    str(item.close_price),
                    str(item.cost_basis),
                    str(item.market_value),
                    str(item.unrealized_pnl),
                    str(item.unrealized_return),
                    str(item.portfolio_weight),
                    str(item.daily_value_change),
                    str(item.daily_return) if item.daily_return is not None else None,
                )
                for item in snapshots
            ],
        )
        if self.auto_commit:
            self.connection.commit()

    def list_by_date(self, snapshot_date: date) -> list[PositionSnapshot]:
        rows = self.connection.execute(
            """SELECT * FROM position_snapshots
            WHERE snapshot_date = ? ORDER BY symbol, holding_id""",
            (snapshot_date.isoformat(),),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def get_latest_date(self) -> date | None:
        """Return the newest holding-level snapshot date."""
        row = self.connection.execute(
            "SELECT MAX(snapshot_date) FROM position_snapshots"
        ).fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None

    @staticmethod
    def _from_row(row: sqlite3.Row) -> PositionSnapshot:
        values = dict(row)
        values["snapshot_date"] = date.fromisoformat(values["snapshot_date"])
        for key in (
            "quantity",
            "average_cost",
            "close_price",
            "cost_basis",
            "market_value",
            "unrealized_pnl",
            "unrealized_return",
            "portfolio_weight",
            "daily_value_change",
        ):
            values[key] = _decimal(values[key])
        if values["daily_return"] is not None:
            values["daily_return"] = _decimal(values["daily_return"])
        return PositionSnapshot.model_validate(values)

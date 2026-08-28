"""Application orchestration for immutable annual investment P/L facts."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from domain import (
    AnnualPnlSnapshot,
    Currency,
    DividendEvent,
    InvestmentCostEvent,
    RealizedSale,
    Transaction,
)
from repositories import (
    AnnualPnlSnapshotRepository,
    DividendEventRepository,
    FxRateRepository,
    InvestmentCostEventRepository,
    SnapshotRepository,
    TransactionRepository,
)
from repositories.interfaces import CorporateActionRepository
from services import AnnualPnlEngine, TransactionEngine


class AnnualPnlApplicationError(RuntimeError):
    """An annual P/L workflow could not produce a reliable result."""


@dataclass(frozen=True)
class AnnualPnlHistory:
    """Immutable annual snapshot query result."""

    year: int
    snapshots: tuple[AnnualPnlSnapshot, ...]


class AnnualPnlUseCase:
    """Load facts, invoke the pure engine, and preserve daily results."""

    def __init__(
        self,
        transactions: TransactionRepository,
        daily_snapshots: SnapshotRepository,
        annual_snapshots: AnnualPnlSnapshotRepository,
        dividends: DividendEventRepository,
        costs: InvestmentCostEventRepository,
        fx_rates: FxRateRepository,
        engine: AnnualPnlEngine | None = None,
        transaction_engine: TransactionEngine | None = None,
        corporate_actions: CorporateActionRepository | None = None,
    ) -> None:
        self._transactions = transactions
        self._daily_snapshots = daily_snapshots
        self._annual_snapshots = annual_snapshots
        self._dividends = dividends
        self._costs = costs
        self._fx_rates = fx_rates
        self._engine = engine or AnnualPnlEngine()
        self._transaction_engine = transaction_engine or TransactionEngine()
        self._corporate_actions = corporate_actions

    def ensure(
        self,
        snapshot_date: date,
        *,
        unrealized_pnl: Decimal | None = None,
        persist: bool = True,
    ) -> AnnualPnlSnapshot:
        """Return an existing immutable row or create the missing daily fact."""
        existing = self._annual_snapshots.get_by_date(snapshot_date)
        if existing is not None:
            return existing
        daily = self._daily_snapshots.get_by_date(snapshot_date)
        if unrealized_pnl is None:
            if daily is None:
                raise AnnualPnlApplicationError(
                    f"portfolio snapshot does not exist for {snapshot_date}"
                )
            unrealized_pnl = daily.total_unrealized_pnl
        transactions = self._transactions.list_filtered(end_date=snapshot_date)
        dividends = self._dividends.list_filtered()
        start = date(snapshot_date.year, 1, 1)
        costs = self._costs.list_between_dates(start, snapshot_date)
        rates = self._historical_rates(snapshot_date, transactions, dividends, costs)
        actions = (
            self._corporate_actions.list_filtered(end_date=snapshot_date)
            if self._corporate_actions is not None
            else None
        )
        result = self._engine.calculate(
            snapshot_date,
            transactions,
            unrealized_pnl,
            dividends,
            costs,
            rates,
            actions,
        )
        if persist:
            self._annual_snapshots.add(result)
        return result

    def realized_sales(
        self, *, year: int | None = None, symbol: str | None = None
    ) -> tuple[RealizedSale, ...]:
        """Derive durable sale history from the complete transaction ledger."""
        actions = (
            self._corporate_actions.list_all()
            if self._corporate_actions is not None
            else None
        )
        ledger = self._transaction_engine.build_ledger(
            self._transactions.list_all(), actions
        )
        normalized = symbol.strip().upper() if symbol else None
        return tuple(
            sale
            for sale in ledger.realized_sales
            if (year is None or sale.trade_date.year == year)
            and (normalized is None or sale.symbol == normalized)
        )

    def summary(
        self, *, year: int, as_of: date | None = None
    ) -> AnnualPnlSnapshot | None:
        """Return the latest immutable snapshot in the requested period."""
        limit = as_of or date(year, 12, 31)
        rows = [
            row
            for row in self._annual_snapshots.list_for_year(year)
            if row.snapshot_date <= limit
        ]
        return rows[-1] if rows else None

    def history(
        self,
        *,
        year: int,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> AnnualPnlHistory:
        """Return the inclusive persisted daily time series for one year."""
        start = max(start_date or date(year, 1, 1), date(year, 1, 1))
        end = min(end_date or date(year, 12, 31), date(year, 12, 31))
        if start > end:
            raise AnnualPnlApplicationError("annual P/L start date follows end date")
        return AnnualPnlHistory(
            year,
            tuple(self._annual_snapshots.list_between_dates(start, end)),
        )

    def record_cost(self, event: InvestmentCostEvent) -> None:
        """Persist an explicit non-transaction investment cost event."""
        self._costs.add(event)

    def _historical_rates(
        self,
        snapshot_date: date,
        transactions: list[Transaction],
        dividends: list[DividendEvent],
        costs: list[InvestmentCostEvent],
    ) -> dict[tuple[Currency, date], Decimal]:
        dates: set[date] = {
            item.trade_date
            for item in transactions
            if item.currency is Currency.USD
            and item.trade_date.year == snapshot_date.year
        }
        dates.update(
            event.event_date for event in costs if event.currency is Currency.USD
        )
        dates.update(
            event.payment_date
            for event in dividends
            if event.market.value == "US"
            and event.payment_date is not None
            and event.payment_date <= snapshot_date
        )
        result: dict[tuple[Currency, date], Decimal] = {}
        for effective_date in dates:
            rate = self._fx_rates.get_latest_on_or_before(
                Currency.USD.value, Currency.TWD.value, effective_date
            )
            if rate is not None:
                result[(Currency.USD, effective_date)] = rate.rate
        return result

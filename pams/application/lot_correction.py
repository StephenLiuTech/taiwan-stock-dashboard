"""Auditable, atomic restatement of one explicitly allocated sale."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256

from domain import (
    AnnualPnlSnapshot,
    DailySnapshot,
    LotAllocation,
    PositionSnapshot,
    Transaction,
    TransactionType,
)
from pams.application.annual_pnl import AnnualPnlUseCase
from repositories.postgresql import (
    PostgreSQLAnnualPnlSnapshotRepository,
    PostgreSQLHoldingRepository,
    PostgreSQLLotAllocationRepository,
    PostgreSQLPositionSnapshotRepository,
    PostgreSQLSnapshotRepository,
    PostgreSQLTransactionRepository,
)
from repositories.sqlite import (
    SQLiteAnnualPnlSnapshotRepository,
    SQLiteHoldingRepository,
    SQLiteLotAllocationRepository,
    SQLitePositionSnapshotRepository,
    SQLiteSnapshotRepository,
    SQLiteTransactionRepository,
)
from services import TransactionEngine
from services.multi_currency_valuation import MultiCurrencyValuationEngine


class LotCorrectionError(ValueError):
    """Correction is incomplete, stale, or cannot be proven from stored facts."""


@dataclass(frozen=True)
class BuyFeeCorrection:
    transaction_id: str
    expected_fee: Decimal
    corrected_fee: Decimal


@dataclass(frozen=True)
class LotCorrectionRequest:
    sell_transaction_id: str
    buy_fees: tuple[BuyFeeCorrection, ...]
    allocations: tuple[LotAllocation, ...]


@dataclass(frozen=True)
class LotCorrectionPlan:
    """Every before/after value is immutable and included in the review hash."""

    fingerprint: str
    transactions: tuple[tuple[Transaction, Transaction], ...]
    allocations: tuple[LotAllocation, ...]
    holding: tuple[object, object]
    positions: tuple[
        tuple[date, tuple[PositionSnapshot, ...], tuple[PositionSnapshot, ...]], ...
    ]
    daily: tuple[tuple[DailySnapshot, DailySnapshot], ...]
    annual: tuple[tuple[AnnualPnlSnapshot, AnnualPnlSnapshot], ...]
    already_applied: bool = False


class LotCorrectionUseCase:
    """Preview source-derived restatement, then commit only a reviewed fingerprint."""

    def __init__(self, connection: object, repositories: object) -> None:
        self.connection = connection
        self.repos = repositories
        postgres = getattr(connection, "backend", None) == "postgresql"
        namespace = (
            (
                PostgreSQLTransactionRepository,
                PostgreSQLLotAllocationRepository,
                PostgreSQLHoldingRepository,
                PostgreSQLPositionSnapshotRepository,
                PostgreSQLSnapshotRepository,
                PostgreSQLAnnualPnlSnapshotRepository,
            )
            if postgres
            else (
                SQLiteTransactionRepository,
                SQLiteLotAllocationRepository,
                SQLiteHoldingRepository,
                SQLitePositionSnapshotRepository,
                SQLiteSnapshotRepository,
                SQLiteAnnualPnlSnapshotRepository,
            )
        )
        self.transactions = namespace[0](connection, auto_commit=False)
        self.allocations = namespace[1](connection, auto_commit=False)
        self.holdings = namespace[2](connection, auto_commit=False)
        self.positions = namespace[3](connection, auto_commit=False)
        self.daily = namespace[4](connection, auto_commit=False)
        self.annual = namespace[5](connection, auto_commit=False)

    def preview(self, request: LotCorrectionRequest) -> LotCorrectionPlan:
        """Never write. Schema v15 must exist in the target database."""
        return self._build_plan(request)

    def apply(
        self, request: LotCorrectionRequest, *, reviewed_fingerprint: str
    ) -> LotCorrectionPlan:
        """One transaction; any changed precondition or assertion rolls back all writes."""
        with self._transaction():
            plan = self._build_plan(request)
            if plan.fingerprint != reviewed_fingerprint:
                raise LotCorrectionError("Production changed since reviewed dry-run")
            if plan.already_applied:
                return plan
            for _, corrected in plan.transactions:
                self.transactions.upsert(corrected)
            self.allocations.add_many(list(plan.allocations))
            self.holdings.upsert(plan.holding[1])
            for when, _, corrected in plan.positions:
                self.positions.replace_many(when, list(corrected))
            for _, corrected in plan.daily:
                self.daily.replace(corrected)
            for _, corrected in plan.annual:
                self.annual.replace(corrected)
            if self.holdings.get_by_id(plan.holding[1].id) != plan.holding[1]:
                raise LotCorrectionError("Post-write holding assertion failed")
            if any(
                self.daily.get_by_date(after.snapshot_date) != after
                for _, after in plan.daily
            ):
                raise LotCorrectionError("Post-write daily snapshot assertion failed")
            if any(
                self.annual.get_by_date(after.snapshot_date) != after
                for _, after in plan.annual
            ):
                raise LotCorrectionError("Post-write annual snapshot assertion failed")
            return plan

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        if getattr(self.connection, "backend", None) == "postgresql":
            self.connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        else:
            self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def _build_plan(self, request: LotCorrectionRequest) -> LotCorrectionPlan:
        all_transactions = self.transactions.list_all()
        by_id = {item.id: item for item in all_transactions}
        sell = by_id.get(request.sell_transaction_id)
        if sell is None or sell.transaction_type is not TransactionType.SELL:
            raise LotCorrectionError("Referenced SELL is missing")
        if not request.allocations or any(
            item.sell_transaction_id != sell.id for item in request.allocations
        ):
            raise LotCorrectionError("Allocations must identify exactly one SELL")
        existing = self.allocations.list_by_sell(sell.id)
        if existing and not self._same_allocations(existing, request.allocations):
            raise LotCorrectionError("Existing allocation differs from reviewed set")
        fee_changes: list[tuple[Transaction, Transaction]] = []
        replacements = {}
        for fee in request.buy_fees:
            buy = by_id.get(fee.transaction_id)
            if buy is None or buy.transaction_type is not TransactionType.BUY:
                raise LotCorrectionError(
                    f"Referenced BUY is missing: {fee.transaction_id}"
                )
            if buy.fees not in (fee.expected_fee, fee.corrected_fee):
                raise LotCorrectionError(f"BUY fee changed: {fee.transaction_id}")
            corrected = buy.model_copy(update={"fees": fee.corrected_fee})
            replacements[buy.id] = corrected
            if buy.fees != corrected.fees:
                fee_changes.append((buy, corrected))
        proposed_transactions = [
            replacements.get(item.id, item) for item in all_transactions
        ]
        all_allocations = self.allocations.list_all()
        proposed_allocations = (
            all_allocations if existing else [*all_allocations, *request.allocations]
        )
        engine = TransactionEngine(
            corporate_actions=self.repos.corporate_actions.list_all(),
            lot_allocations=proposed_allocations,
        )
        full_ledger = engine.build_ledger(proposed_transactions)
        position = next(
            (
                item
                for item in full_ledger.positions
                if (item.symbol, item.market, item.currency)
                == (sell.symbol, sell.market, sell.currency)
            ),
            None,
        )
        if position is None:
            raise LotCorrectionError(
                "Corrected position is closed; separate closure plan required"
            )
        old_holding = next(
            (
                item
                for item in self.holdings.list_all()
                if item.symbol == sell.symbol and item.market == sell.market
            ),
            None,
        )
        if old_holding is None or old_holding.market != sell.market:
            raise LotCorrectionError(
                "Persisted holding is missing or belongs to another market"
            )
        new_holding = old_holding.model_copy(
            update={
                "quantity": position.quantity,
                "average_cost": position.average_cost,
            }
        )
        latest = self.daily.get_latest()
        if latest is None or latest.snapshot_date < sell.trade_date:
            raise LotCorrectionError("Affected daily snapshot is missing")
        affected = self.daily.list_between_dates(sell.trade_date, latest.snapshot_date)
        history = self.repos.stock_net_equity_history.list_between_dates(
            sell.trade_date, latest.snapshot_date
        )
        if history:
            raise LotCorrectionError(
                "Affected stock_net_equity_history rows require a separate verified correction"
            )
        position_changes = []
        daily_changes = []
        prior = self.daily.get_highest_before(sell.trade_date)
        high = prior.high_water_mark if prior else Decimal("0")
        valuation = MultiCurrencyValuationEngine()
        for old_daily in affected:
            when = old_daily.snapshot_date
            old_rows = tuple(self.positions.list_by_date(when))
            old_target = next(
                (
                    item
                    for item in old_rows
                    if item.symbol == sell.symbol and item.market == sell.market
                ),
                None,
            )
            if old_target is None:
                raise LotCorrectionError(
                    f"Missing {sell.symbol} position snapshot for {when}"
                )
            as_of = engine.build_ledger(
                [item for item in proposed_transactions if item.trade_date <= when],
                [
                    item
                    for item in self.repos.corporate_actions.list_all()
                    if item.effective_date <= when
                ],
            )
            current = next(
                item
                for item in as_of.positions
                if (item.symbol, item.market, item.currency)
                == (sell.symbol, sell.market, sell.currency)
            )
            quote = self.repos.price_quotes.get_latest_on_or_before(
                sell.symbol, sell.market.value, when
            )
            if quote is None or quote.close_price != old_target.close_price:
                raise LotCorrectionError(
                    f"Missing or mismatched persisted quote for {when}"
                )
            native = old_holding.model_copy(
                update={
                    "quantity": current.quantity,
                    "average_cost": current.average_cost,
                }
            )
            valued = valuation.valuate(when, [native], [quote], None).holdings[0]
            amended = old_target.model_copy(
                update={
                    "quantity": current.quantity,
                    "average_cost": current.average_cost,
                    "cost_basis": valued.cost_basis_twd,
                    "market_value": valued.market_value_twd,
                    "unrealized_pnl": valued.unrealized_pnl_twd,
                    "unrealized_return": valued.unrealized_return_pct,
                    "daily_value_change": valued.daily_pnl_twd,
                    "daily_return": (
                        valued.daily_return_pct
                        if quote.previous_close is not None
                        else None
                    ),
                }
            )
            rows = [
                amended if item.holding_id == old_target.holding_id else item
                for item in old_rows
            ]
            total_market = sum((item.market_value for item in rows), Decimal("0"))
            rows = [
                item.model_copy(
                    update={
                        "portfolio_weight": (
                            item.market_value / total_market
                            if total_market
                            else Decimal("0")
                        )
                    }
                )
                for item in rows
            ]
            total_cost = sum((item.cost_basis for item in rows), Decimal("0"))
            total_unrealized = sum((item.unrealized_pnl for item in rows), Decimal("0"))
            nav = total_market - old_daily.total_liabilities
            high = max(high, nav)
            amended_daily = old_daily.model_copy(
                update={
                    "total_market_value": total_market,
                    "total_cost_basis": total_cost,
                    "total_unrealized_pnl": total_unrealized,
                    "net_asset_value": nav,
                    "leverage_ratio": (
                        old_daily.total_liabilities / total_market
                        if total_market
                        else Decimal("0")
                    ),
                    "high_water_mark": high,
                    "drawdown": (nav - high) / high if high > 0 else Decimal("0"),
                }
            )
            position_changes.append((when, old_rows, tuple(rows)))
            daily_changes.append((old_daily, amended_daily))
        first_changed = (
            min(item.trade_date for item, _ in fee_changes)
            if fee_changes
            else sell.trade_date
        )
        annual_changes = []
        annual_use_case = AnnualPnlUseCase(
            self.transactions,
            self.daily,
            self.annual,
            self.repos.dividend_events,
            self.repos.investment_cost_events,
            self.repos.fx_rates,
            transaction_engine=engine,
            corporate_actions=self.repos.corporate_actions,
        )
        changed_daily = {after.snapshot_date: after for _, after in daily_changes}
        for old_annual in self.annual.list_between_dates(
            first_changed, latest.snapshot_date
        ):
            valuation_date = old_annual.valuation_date
            valuation_snapshot = changed_daily.get(
                valuation_date
            ) or self.daily.get_by_date(valuation_date)
            if valuation_snapshot is None:
                raise LotCorrectionError(
                    f"Missing valuation snapshot for {valuation_date}"
                )
            calculated = annual_use_case.recalculate(
                old_annual.snapshot_date,
                unrealized_pnl=valuation_snapshot.total_unrealized_pnl,
                valuation_date=valuation_date,
                transactions_override=proposed_transactions,
            )
            annual_changes.append(
                (
                    old_annual,
                    calculated.model_copy(update={"created_at": old_annual.created_at}),
                )
            )
        already_applied = (
            bool(existing)
            and not fee_changes
            and old_holding == new_holding
            and all(before == after for before, after in daily_changes)
            and all(before == after for before, after in annual_changes)
        )
        allocation_facts = tuple(
            (
                item.sell_transaction_id,
                item.buy_transaction_id,
                item.matched_quantity,
                item.matched_trade_cost,
                item.allocated_buy_fee,
                item.source,
                item.source_reference,
            )
            for item in request.allocations
        )
        payload = repr(
            (
                fee_changes,
                allocation_facts,
                old_holding,
                new_holding,
                position_changes,
                daily_changes,
                annual_changes,
                existing,
            )
        )
        return LotCorrectionPlan(
            sha256(payload.encode("utf-8")).hexdigest(),
            tuple(fee_changes),
            () if existing else request.allocations,
            (old_holding, new_holding),
            tuple(position_changes),
            tuple(daily_changes),
            tuple(annual_changes),
            already_applied,
        )

    @staticmethod
    def _same_allocations(
        existing: list[LotAllocation], proposed: tuple[LotAllocation, ...]
    ) -> bool:
        fields = (
            "buy_transaction_id",
            "matched_quantity",
            "matched_trade_cost",
            "allocated_buy_fee",
            "source",
            "source_reference",
        )
        return sorted(
            tuple(getattr(item, field) for field in fields) for item in existing
        ) == sorted(
            tuple(getattr(item, field) for field in fields) for item in proposed
        )

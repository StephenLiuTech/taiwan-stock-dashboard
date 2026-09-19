"""A reviewed explicit-lot restatement is atomic and replayable."""

import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from database.schema import initialize_schema
from domain import (
    AnnualPnlSnapshot,
    Currency,
    DailySnapshot,
    Holding,
    HoldingType,
    Market,
    PositionSnapshot,
    PriceQuote,
)
from pams.application.lot_correction import (
    BuyFeeCorrection,
    LotCorrectionError,
    LotCorrectionRequest,
    LotCorrectionUseCase,
)
from repositories.provider import create_repositories
from services import TransactionEngine
from tests.test_lot_allocation import case


def _fixture() -> tuple[sqlite3.Connection, object, LotCorrectionRequest]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    repos = create_repositories("sqlite", connection)
    transactions, allocations = case()
    for transaction in transactions:
        if transaction.id.startswith("buy-") and transaction.id != "buy-707":
            transaction = transaction.model_copy(update={"fees": Decimal("0")})
        repos.transactions.add(transaction)
    ledger = TransactionEngine().build_ledger(repos.transactions.list_all())
    old = ledger.positions[0]
    repos.holdings.upsert(
        Holding(
            id="holding-3293",
            symbol="3293",
            name="3293",
            market=Market.TPEX,
            currency=Currency.TWD,
            quantity=old.quantity,
            average_cost=old.average_cost,
            holding_type=HoldingType.STOCK,
        )
    )
    when = date(2026, 9, 18)
    repos.price_quotes.upsert_many(
        [
            PriceQuote(
                symbol="3293",
                market=Market.TPEX,
                trade_date=when,
                close_price=Decimal("729"),
                previous_close=Decimal("725"),
                currency=Currency.TWD,
                source="test",
            )
        ]
    )
    market = old.quantity * Decimal("729")
    cost = old.cost_basis
    repos.position_snapshots.add_many(
        [
            PositionSnapshot(
                snapshot_date=when,
                holding_id="holding-3293",
                symbol="3293",
                market=Market.TPEX,
                native_currency=Currency.TWD,
                quote_date=when,
                quantity=old.quantity,
                average_cost=old.average_cost,
                close_price=Decimal("729"),
                cost_basis=cost,
                market_value=market,
                unrealized_pnl=market - cost,
                unrealized_return=(market - cost) / cost,
                portfolio_weight=Decimal("1"),
                daily_value_change=old.quantity * Decimal("4"),
                daily_return=Decimal("4") / Decimal("725"),
            )
        ]
    )
    for snapshot_date in (date(2026, 9, 1), date(2026, 9, 2), when):
        repos.daily_snapshots.add(
            DailySnapshot(
                snapshot_date=snapshot_date,
                total_market_value=market,
                total_cost_basis=cost,
                total_unrealized_pnl=market - cost,
                total_liabilities=Decimal("0"),
                net_asset_value=market,
                leverage_ratio=Decimal("0"),
                high_water_mark=market,
                drawdown=Decimal("0"),
            )
        )
    for day in (date(2026, 9, 1), date(2026, 9, 2), when):
        repos.annual_pnl_snapshots.add(
            AnnualPnlSnapshot(
                snapshot_date=day,
                valuation_date=day,
                year=2026,
                realized_pnl_ytd=Decimal("0"),
                unrealized_pnl=market - cost,
                dividend_income_ytd=Decimal("0"),
                financing_cost_ytd=Decimal("0"),
                other_cost_ytd=Decimal("22"),
                total_pnl_ytd=market - cost - Decimal("22"),
            )
        )
    request = LotCorrectionRequest(
        "sell",
        (
            BuyFeeCorrection("buy-711-80", Decimal("0"), Decimal("22")),
            BuyFeeCorrection("buy-706", Decimal("0"), Decimal("22")),
            BuyFeeCorrection("buy-711-60", Decimal("0"), Decimal("17")),
        ),
        tuple(allocations),
    )
    return connection, repos, request


def test_preview_apply_and_repeat_are_idempotent() -> None:
    connection, repos, request = _fixture()
    use_case = LotCorrectionUseCase(connection, repos)
    plan = use_case.preview(request)
    assert len(plan.transactions) == 3
    assert len(plan.allocations) == 4
    assert plan.holding[1].quantity == Decimal("2000")
    assert plan.holding[1].average_cost == Decimal("838.559")
    assert plan.daily[-1][1].total_market_value == Decimal("1458000")
    assert plan.annual[-1][1].realized_pnl_ytd == Decimal("2089")
    assert plan.annual[-1][1].other_cost_ytd == Decimal("83")
    with pytest.raises(LotCorrectionError, match="changed"):
        use_case.apply(request, reviewed_fingerprint="invalid")
    assert repos.lot_allocations.list_all() == []
    use_case.apply(request, reviewed_fingerprint=plan.fingerprint)
    repeat = use_case.preview(request)
    assert repeat.already_applied
    use_case.apply(request, reviewed_fingerprint=repeat.fingerprint)
    assert len(repos.lot_allocations.list_all()) == 4
    connection.close()


def test_apply_failure_rolls_back_every_write(monkeypatch: pytest.MonkeyPatch) -> None:
    connection, repos, request = _fixture()
    use_case = LotCorrectionUseCase(connection, repos)
    plan = use_case.preview(request)
    before_holding = repos.holdings.get_by_id("holding-3293")
    before_daily = repos.daily_snapshots.get_by_date(date(2026, 9, 18))
    before_positions = repos.position_snapshots.list_by_date(date(2026, 9, 18))

    def fail_after_prior_writes(_snapshot: AnnualPnlSnapshot) -> None:
        raise RuntimeError("simulated annual snapshot write failure")

    monkeypatch.setattr(use_case.annual, "replace", fail_after_prior_writes)
    with pytest.raises(RuntimeError, match="simulated annual"):
        use_case.apply(request, reviewed_fingerprint=plan.fingerprint)
    assert repos.transactions.get_by_id("buy-711-80").fees == Decimal("0")
    assert repos.lot_allocations.list_all() == []
    assert repos.holdings.get_by_id("holding-3293") == before_holding
    assert repos.daily_snapshots.get_by_date(date(2026, 9, 18)) == before_daily
    assert repos.position_snapshots.list_by_date(date(2026, 9, 18)) == before_positions
    connection.close()

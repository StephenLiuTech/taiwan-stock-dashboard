"""Focused tests for the reviewed 2027 margin-principal correction."""

import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from database.schema import initialize_schema
from domain import (
    AnnualPnlSnapshot,
    Currency,
    DailySnapshot,
    FinancingType,
    HistoricalStockNetEquity,
    InvestmentCostEvent,
    InvestmentCostType,
    Liability,
    LiabilityPrincipalEvent,
    LiabilityPrincipalEventType,
    LiabilityType,
    Market,
    StockNetEquityQuality,
    Transaction,
    TransactionType,
)
from pams.application.financing_interest import financing_interest_event_id
from pams.application.margin_principal_correction import (
    CATCHUP_ID,
    MARGIN_ID,
    MarginPrincipalCorrectionUseCase,
    approved_2027_request,
)
from repositories.provider import create_repositories
from repositories.sqlite import (
    SQLiteAnnualPnlSnapshotRepository,
    SQLiteInvestmentCostEventRepository,
    SQLiteLiabilityPrincipalEventRepository,
    SQLiteLiabilityRepository,
    SQLiteSnapshotRepository,
    SQLiteStockNetEquityHistoryRepository,
    SQLiteTransactionRepository,
)

DAILY_DATES = tuple(
    date.fromisoformat(value)
    for value in (
        "2026-08-25 2026-08-26 2026-08-27 2026-08-28 2026-08-31 "
        "2026-09-01 2026-09-02 2026-09-03 2026-09-04 2026-09-07 "
        "2026-09-08 2026-09-09 2026-09-10 2026-09-11 2026-09-15 "
        "2026-09-16 2026-09-17 2026-09-18 2026-09-21 2026-09-22 "
        "2026-09-23 2026-09-24 2026-09-29 2026-09-30"
    ).split()
)
HISTORY_DATES = DAILY_DATES[:9]
ANNUAL_DATES = tuple(
    sorted(
        {
            date(2026, 8, 29),
            *(item for item in DAILY_DATES if item >= date(2026, 8, 28)),
        }
    )
)


def _event(
    event_id: str,
    when: date,
    delta: Decimal,
    resulting: Decimal,
    liability_id: str = MARGIN_ID,
) -> LiabilityPrincipalEvent:
    return LiabilityPrincipalEvent(
        id=event_id,
        liability_id=liability_id,
        effective_date=when,
        sequence=10,
        event_type=LiabilityPrincipalEventType.INCREASE,
        principal_delta=delta,
        resulting_principal=resulting,
        source="fixture",
        created_at=datetime.combine(when, datetime.min.time(), UTC),
    )


def _database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    liabilities = SQLiteLiabilityRepository(connection)
    liabilities.upsert(
        Liability(
            id=MARGIN_ID,
            liability_type=LiabilityType.MARGIN_FINANCING,
            principal=Decimal("1404650"),
            currency=Currency.TWD,
            financed_symbol="2027",
            financed_quantity=Decimal("52000"),
        )
    )
    liabilities.upsert(
        Liability(
            id="liability-stock-pledge",
            liability_type=LiabilityType.STOCK_PLEDGE,
            principal=Decimal("998000"),
            currency=Currency.TWD,
        )
    )
    events = SQLiteLiabilityPrincipalEventRepository(connection)
    events.insert_many_if_absent(
        [
            _event(
                "margin-opening",
                date(2026, 8, 21),
                Decimal("767000"),
                Decimal("767000"),
            ),
            _event(
                "margin-825", date(2026, 8, 25), Decimal("60960"), Decimal("827960")
            ),
            _event(
                "margin-827", date(2026, 8, 27), Decimal("58560"), Decimal("886520")
            ),
            _event(
                "margin-828", date(2026, 8, 28), Decimal("87480"), Decimal("974000")
            ),
            _event(
                "margin-831", date(2026, 8, 31), Decimal("57720"), Decimal("1031720")
            ),
            _event(
                "margin-904", date(2026, 9, 4), Decimal("59160"), Decimal("1090880")
            ),
            _event(
                "pledge-opening",
                date(2026, 1, 1),
                Decimal("998000"),
                Decimal("998000"),
                "liability-stock-pledge",
            ),
        ]
    )
    transactions = SQLiteTransactionRepository(connection)
    for spec, price, quantity in zip(
        approved_2027_request().event_inserts,
        ("48.4", "48.9", "46.8", "46.35", "47.35"),
        ("2000", "2000", "2000", "2000", "3000"),
        strict=True,
    ):
        transactions.add(
            Transaction(
                id=spec.transaction_id,
                symbol="2027",
                market=Market.TWSE,
                transaction_type=TransactionType.BUY,
                trade_date=spec.effective_date,
                settlement_date=spec.effective_date,
                quantity=Decimal(quantity),
                price=Decimal(price),
                currency=Currency.TWD,
                financing_type=FinancingType.MARGIN,
            )
        )
    snapshots = SQLiteSnapshotRepository(connection)
    snapshots.add(
        DailySnapshot(
            snapshot_date=date(2026, 8, 24),
            total_market_value=Decimal("4000000"),
            total_cost_basis=Decimal("2500000"),
            total_unrealized_pnl=Decimal("1500000"),
            total_liabilities=Decimal("1765000"),
            net_asset_value=Decimal("2235000"),
            leverage_ratio=Decimal("0.44125"),
            high_water_mark=Decimal("2235000"),
            drawdown=Decimal("0"),
        )
    )
    for when in DAILY_DATES:
        snapshots.add(
            DailySnapshot(
                snapshot_date=when,
                total_market_value=Decimal("5000000"),
                total_cost_basis=Decimal("3000000"),
                total_unrealized_pnl=Decimal("2000000"),
                total_liabilities=Decimal("2088880"),
                net_asset_value=Decimal("2911120"),
                leverage_ratio=Decimal("0.417776"),
                high_water_mark=Decimal("2911120"),
                drawdown=Decimal("0"),
            )
        )
    history = SQLiteStockNetEquityHistoryRepository(connection)
    history.insert_many_if_absent(
        [
            HistoricalStockNetEquity(
                snapshot_date=when,
                total_market_value=Decimal("5000000"),
                pledge_debt=Decimal("998000"),
                margin_debt=Decimal("1090880"),
                total_liabilities=Decimal("2088880"),
                stock_net_equity=Decimal("2911120"),
                quality_status=StockNetEquityQuality.VERIFIED,
                source="fixture",
                source_reference="fixture",
            )
            for when in HISTORY_DATES
        ]
    )
    costs = SQLiteInvestmentCostEventRepository(connection)
    values = [
        InvestmentCostEvent(
            id=CATCHUP_ID,
            event_date=date(2026, 8, 28),
            cost_type=InvestmentCostType.FINANCING,
            amount=Decimal("7975.667397260273972602739726"),
            currency=Currency.TWD,
            source="approved catch-up",
        )
    ]
    current = date(2026, 8, 29)
    while current <= date(2026, 9, 30):
        values.append(
            InvestmentCostEvent(
                id=financing_interest_event_id(MARGIN_ID, current),
                event_date=current,
                cost_type=InvestmentCostType.FINANCING,
                amount=Decimal("100"),
                currency=Currency.TWD,
                source="automatic financing accrual",
            )
        )
        current += timedelta(days=1)
    costs.insert_many_if_absent(values)
    annual = SQLiteAnnualPnlSnapshotRepository(connection)
    for when in ANNUAL_DATES:
        annual.add(
            AnnualPnlSnapshot(
                snapshot_date=when,
                valuation_date=max(item for item in DAILY_DATES if item <= when),
                year=2026,
                realized_pnl_ytd=Decimal("1000"),
                unrealized_pnl=Decimal("2000"),
                dividend_income_ytd=Decimal("500"),
                financing_cost_ytd=Decimal("100"),
                other_cost_ytd=Decimal("50"),
                total_pnl_ytd=Decimal("3350"),
            )
        )
    return connection


def test_preview_has_exact_reviewed_manifest_and_never_changes_positions() -> None:
    connection = _database()
    case = MarginPrincipalCorrectionUseCase(
        connection, create_repositories("sqlite", connection)
    )
    plan = case.preview(approved_2027_request())
    assert len(plan.event_inserts) == 5
    assert len(plan.event_updates) == 5
    assert plan.master[0].principal == Decimal("1404650")
    assert plan.master[1].principal == Decimal("1400000")
    assert len(plan.daily) == 24
    assert len(plan.history) == 9
    assert len(plan.interest) == 34
    assert len(plan.annual) == 22
    assert plan.event_updates[0][1].principal_delta == Decimal("60000")
    assert plan.daily[-1][1].total_liabilities == Decimal("2398000")
    assert plan.history[-1][1].margin_debt == Decimal("1088000")
    assert plan.fingerprint
    connection.close()


def test_atomic_apply_is_idempotent_and_reconciles_every_projection() -> None:
    connection = _database()
    case = MarginPrincipalCorrectionUseCase(
        connection, create_repositories("sqlite", connection)
    )
    preview = case.preview(approved_2027_request())
    applied = case.apply(
        approved_2027_request(), reviewed_fingerprint=preview.fingerprint
    )
    assert applied.fingerprint == preview.fingerprint
    assert SQLiteLiabilityRepository(connection).get_by_id(
        MARGIN_ID
    ).principal == Decimal("1400000")
    rerun = case.preview(approved_2027_request())
    assert rerun.already_applied is True
    case.apply(approved_2027_request(), reviewed_fingerprint=rerun.fingerprint)
    assert (
        len(
            SQLiteLiabilityPrincipalEventRepository(connection).list_by_liability(
                MARGIN_ID
            )
        )
        == 11
    )
    connection.close()

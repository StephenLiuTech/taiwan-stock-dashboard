from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from database import initialize_database, initialize_schema
from domain import (
    Currency,
    DailySnapshot,
    InvestmentCostEvent,
    InvestmentCostType,
    Liability,
    LiabilityPrincipalEvent,
    LiabilityPrincipalEventType,
    LiabilityType,
)
from pams.application import (
    AUTOMATIC_FINANCING_ACCRUAL_START,
    AnnualPnlUseCase,
    FinancingInterestUseCase,
    financing_interest_event_id,
)
from repositories.provider import create_repositories
from repositories.sqlite import (
    SQLiteInvestmentCostEventRepository,
    SQLiteLiabilityPrincipalEventRepository,
    SQLiteLiabilityRepository,
)
from services import FinancingInterestEngine

NOW = datetime(2026, 8, 29, tzinfo=UTC)
RATE = Decimal("0.065")


def principal_event(
    event_id: str,
    liability_id: str,
    effective_date: date,
    sequence: int,
    delta: str,
    resulting: str,
    event_type: LiabilityPrincipalEventType = LiabilityPrincipalEventType.INCREASE,
) -> LiabilityPrincipalEvent:
    return LiabilityPrincipalEvent(
        id=event_id,
        liability_id=liability_id,
        effective_date=effective_date,
        sequence=sequence,
        event_type=event_type,
        principal_delta=Decimal(delta),
        resulting_principal=Decimal(resulting),
        source="test",
        created_at=NOW,
    )


def build_use_case(tmp_path: Path) -> tuple[FinancingInterestUseCase, object]:
    connection = initialize_database(f"sqlite:///{(tmp_path / 'pams.db').as_posix()}")
    initialize_schema(connection)
    liabilities = SQLiteLiabilityRepository(connection)
    liabilities.upsert(
        Liability(
            id="margin",
            liability_type=LiabilityType.MARGIN_FINANCING,
            principal=Decimal("974000"),
            currency=Currency.TWD,
        )
    )
    liabilities.upsert(
        Liability(
            id="pledge",
            liability_type=LiabilityType.STOCK_PLEDGE,
            principal=Decimal("998000"),
            currency=Currency.TWD,
        )
    )
    liabilities.upsert(
        Liability(
            id="other",
            liability_type=LiabilityType.OTHER,
            principal=Decimal("100"),
            currency=Currency.TWD,
        )
    )
    principal = SQLiteLiabilityPrincipalEventRepository(connection)
    principal.insert_many_if_absent(
        [
            principal_event(
                "m-open", "margin", date(2026, 1, 1), 10, "974000", "974000"
            ),
            principal_event(
                "p-open", "pledge", date(2026, 1, 1), 10, "998000", "998000"
            ),
        ]
    )
    return (
        FinancingInterestUseCase(
            liabilities,
            principal,
            SQLiteInvestmentCostEventRepository(connection),
            {
                LiabilityType.MARGIN_FINANCING: RATE,
                LiabilityType.STOCK_PLEDGE: RATE,
            },
        ),
        connection,
    )


def test_exact_first_daily_interest_two_liabilities_and_deterministic_ids(
    tmp_path: Path,
) -> None:
    use_case, connection = build_use_case(tmp_path)
    result = use_case.ensure_through(date(2026, 8, 29))

    assert result.inserted == 2
    assert [item.liability_id for item in result.items] == ["margin", "pledge"]
    assert result.items[0].daily_interest == Decimal("974000") * RATE / Decimal("365")
    assert result.items[1].daily_interest == Decimal("998000") * RATE / Decimal("365")
    assert sum(item.daily_interest for item in result.items) == (
        Decimal("1972000") * RATE / Decimal("365")
    )
    assert result.items[0].event_id == "financing-interest:margin:2026-08-29"
    assert result.items[1].event_id == "financing-interest:pledge:2026-08-29"
    assert use_case.ensure_through(date(2026, 8, 29)).inserted == 0
    assert len(use_case.inspect(date(2026, 8, 29))) == 2
    assert all(item.persisted for item in use_case.inspect(date(2026, 8, 29)))
    connection.close()


def test_boundary_zero_principal_and_catchup_are_never_overlapped(
    tmp_path: Path,
) -> None:
    use_case, connection = build_use_case(tmp_path)
    costs = SQLiteInvestmentCostEventRepository(connection)
    catchup = InvestmentCostEvent(
        id="approved-catchup",
        event_date=date(2026, 8, 28),
        cost_type=InvestmentCostType.FINANCING,
        amount=Decimal("38635.54410958904109589041097"),
        currency=Currency.TWD,
        source="approved catch-up",
    )
    costs.add(catchup)

    result = use_case.ensure_through(date(2026, 8, 28))

    assert result.items == ()
    assert result.inserted == 0
    rows = costs.list_between_dates(date(2026, 1, 1), date(2026, 8, 28))
    assert rows == [catchup]
    connection.close()


def test_missing_days_include_weekend_holiday_and_respect_requested_ceiling(
    tmp_path: Path,
) -> None:
    use_case, connection = build_use_case(tmp_path)

    first = use_case.ensure_through(date(2026, 8, 31))
    second = use_case.ensure_through(date(2026, 8, 31))

    assert first.inserted == 6
    assert {item.accrual_date for item in first.items} == {
        date(2026, 8, 29),
        date(2026, 8, 30),
        date(2026, 8, 31),
    }
    assert second.inserted == 0
    rows = SQLiteInvestmentCostEventRepository(connection).list_between_dates(
        date(2026, 8, 29), date(2026, 9, 1)
    )
    assert max(item.event_date for item in rows) == date(2026, 8, 31)
    connection.close()


def test_same_day_increase_and_repayment_change_that_days_accrual(
    tmp_path: Path,
) -> None:
    use_case, connection = build_use_case(tmp_path)
    principal = SQLiteLiabilityPrincipalEventRepository(connection)
    principal.insert_many_if_absent(
        [
            principal_event(
                "m-inc", "margin", date(2026, 9, 10), 10, "50000", "1024000"
            ),
            principal_event(
                "p-repay",
                "pledge",
                date(2026, 9, 10),
                10,
                "-40000",
                "958000",
                LiabilityPrincipalEventType.REPAYMENT,
            ),
        ]
    )

    before = {item.liability_id: item for item in use_case.inspect(date(2026, 9, 9))}
    effective = {
        item.liability_id: item for item in use_case.inspect(date(2026, 9, 10))
    }

    assert before["margin"].principal == 974000
    assert effective["margin"].principal == 1024000
    assert before["pledge"].principal == 998000
    assert effective["pledge"].principal == 958000
    connection.close()


def test_zero_principal_creates_no_event(tmp_path: Path) -> None:
    use_case, connection = build_use_case(tmp_path)
    principal = SQLiteLiabilityPrincipalEventRepository(connection)
    principal.insert_many_if_absent(
        [
            principal_event(
                "m-zero",
                "margin",
                AUTOMATIC_FINANCING_ACCRUAL_START,
                10,
                "-974000",
                "0",
                LiabilityPrincipalEventType.REPAYMENT,
            )
        ]
    )

    result = use_case.ensure_through(AUTOMATIC_FINANCING_ACCRUAL_START)

    assert result.inserted == 1
    assert {item.liability_id for item in result.items if item.daily_interest == 0} == {
        "margin"
    }
    ids = {
        item.id
        for item in SQLiteInvestmentCostEventRepository(connection).list_between_dates(
            AUTOMATIC_FINANCING_ACCRUAL_START, AUTOMATIC_FINANCING_ACCRUAL_START
        )
    }
    assert (
        financing_interest_event_id("margin", AUTOMATIC_FINANCING_ACCRUAL_START)
        not in ids
    )
    connection.close()


def test_pure_engine_rejects_invalid_values_and_preserves_decimal() -> None:
    engine = FinancingInterestEngine()
    value = engine.calculate(Decimal("100000"), RATE)
    assert value.amount == Decimal("100000") * RATE / Decimal("365")


def test_829_annual_pnl_includes_catchup_and_daily_interest_exactly_once(
    tmp_path: Path,
) -> None:
    financing, connection = build_use_case(tmp_path)
    repositories = create_repositories("sqlite", connection)
    repositories.daily_snapshots.add(
        DailySnapshot(
            snapshot_date=date(2026, 8, 28),
            total_market_value=Decimal("1000"),
            total_cost_basis=Decimal("900"),
            total_unrealized_pnl=Decimal("100"),
            total_liabilities=Decimal("1972000"),
            net_asset_value=Decimal("-1971000"),
            leverage_ratio=Decimal("1972"),
            high_water_mark=Decimal("1000"),
            drawdown=Decimal("0"),
        )
    )
    catchup_margin = Decimal("7975.667397260273972602739726")
    catchup_pledge = Decimal("30659.87671232876712328767124")
    repositories.investment_cost_events.add(
        InvestmentCostEvent(
            id="margin-catchup",
            event_date=date(2026, 8, 28),
            cost_type=InvestmentCostType.FINANCING,
            amount=catchup_margin,
            currency=Currency.TWD,
        )
    )
    repositories.investment_cost_events.add(
        InvestmentCostEvent(
            id="pledge-catchup",
            event_date=date(2026, 8, 28),
            cost_type=InvestmentCostType.FINANCING,
            amount=catchup_pledge,
            currency=Currency.TWD,
        )
    )
    assert financing.ensure_through(date(2026, 8, 29)).inserted == 2
    assert financing.ensure_through(date(2026, 8, 29)).inserted == 0
    annual = AnnualPnlUseCase(
        repositories.transactions,
        repositories.daily_snapshots,
        repositories.annual_pnl_snapshots,
        repositories.dividend_events,
        repositories.investment_cost_events,
        repositories.fx_rates,
    ).ensure(date(2026, 8, 29))
    expected_daily = Decimal("1972000") * RATE / Decimal("365")

    assert annual.valuation_date == date(2026, 8, 28)
    assert annual.financing_cost_ytd == catchup_margin + catchup_pledge + expected_daily
    assert repositories.daily_snapshots.get_by_date(date(2026, 8, 29)) is None
    assert repositories.position_snapshots.list_by_date(date(2026, 8, 29)) == []
    connection.close()

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from database import initialize_database, initialize_schema
from domain import (
    Currency,
    InvestmentCostEvent,
    InvestmentCostType,
    Liability,
    LiabilityType,
    Market,
)
from pams.application import (
    BrokerActualInterestError,
    FinancingInterestUseCase,
    RecordBrokerActualInterestCommand,
    RecordBrokerActualInterestUseCase,
    financing_interest_event_id,
)
from pams.cli import build_parser
from repositories.sqlite import (
    SQLiteInvestmentCostEventRepository,
    SQLiteLiabilityPrincipalEventRepository,
    SQLiteLiabilityRepository,
)
from services import AnnualPnlEngine

EVENT_DATE = date(2026, 10, 30)
LIABILITY_ID = "liability-margin-financing"


def command(amount: str = "3378") -> RecordBrokerActualInterestCommand:
    return RecordBrokerActualInterestCommand(
        event_date=EVENT_DATE,
        amount=Decimal(amount),
        liability_id=LIABILITY_ID,
        market=Market.TWSE,
        symbol="2027",
        sell_order="AG716",
        source="broker actual margin interest",
    )


def build_use_case(
    tmp_path: Path,
) -> tuple[RecordBrokerActualInterestUseCase, object]:
    connection = initialize_database(f"sqlite:///{(tmp_path / 'pams.db').as_posix()}")
    initialize_schema(connection)
    liabilities = SQLiteLiabilityRepository(connection)
    liabilities.upsert(
        Liability(
            id=LIABILITY_ID,
            liability_type=LiabilityType.MARGIN_FINANCING,
            principal=Decimal("1486000"),
            currency=Currency.TWD,
        )
    )
    return (
        RecordBrokerActualInterestUseCase(
            SQLiteInvestmentCostEventRepository(connection), liabilities
        ),
        connection,
    )


def automatic_event() -> InvestmentCostEvent:
    return InvestmentCostEvent(
        id=financing_interest_event_id(LIABILITY_ID, EVENT_DATE),
        event_date=EVENT_DATE,
        cost_type=InvestmentCostType.FINANCING,
        amount=Decimal("264.6301369863013698630136986"),
        currency=Currency.TWD,
        description="automatic estimate",
        source="automatic financing accrual",
        created_at=datetime(2026, 10, 30, tzinfo=UTC),
    )


def test_first_add_and_repeat_are_idempotent(tmp_path: Path) -> None:
    use_case, connection = build_use_case(tmp_path)
    costs = SQLiteInvestmentCostEventRepository(connection)

    preview = use_case.execute(command(), apply=False)
    first = use_case.execute(command(), apply=True)
    repeated = use_case.execute(command(), apply=True)

    assert preview.inserted == 0
    assert len(preview.events_to_insert) == 1
    assert first.inserted == 1
    assert repeated.inserted == 0
    assert repeated.already_recorded is True
    rows = costs.list_between_dates(EVENT_DATE, EVENT_DATE)
    assert len(rows) == 1
    assert rows[0].amount == Decimal("3378")
    assert "sell_order=AG716" in (rows[0].description or "")
    connection.close()


def test_cli_parses_broker_actual_interest_dry_run() -> None:
    arguments = build_parser().parse_args(
        [
            "liability",
            "interest",
            "add",
            "--date",
            "2026-10-30",
            "--amount",
            "3378",
            "--liability-id",
            LIABILITY_ID,
            "--market",
            "TWSE",
            "--symbol",
            "2027",
            "--sell-order",
            "AG716",
            "--source",
            "broker actual margin interest",
            "--dry-run",
        ]
    )

    assert arguments.interest_action == "add"
    assert arguments.amount == Decimal("3378")
    assert arguments.apply is False
    assert arguments.dry_run is True


def test_existing_identity_with_different_fields_is_rejected(tmp_path: Path) -> None:
    use_case, connection = build_use_case(tmp_path)
    use_case.execute(command(), apply=True)

    with pytest.raises(BrokerActualInterestError, match="different fields"):
        use_case.execute(command("3379"), apply=False)

    connection.close()


def test_actual_interest_reverses_same_day_accrual_without_double_count(
    tmp_path: Path,
) -> None:
    use_case, connection = build_use_case(tmp_path)
    costs = SQLiteInvestmentCostEventRepository(connection)
    costs.add(automatic_event())

    plan = use_case.execute(command(), apply=True)
    rows = costs.list_between_dates(EVENT_DATE, EVENT_DATE)
    annual = AnnualPnlEngine().calculate(
        EVENT_DATE,
        [],
        Decimal("0"),
        [],
        rows,
        {},
    )

    assert plan.inserted == 2
    assert plan.replaced_accrual_amount == automatic_event().amount
    assert plan.reversal_event is not None
    assert annual.financing_cost_ytd == Decimal("3378")
    connection.close()


def test_actual_interest_prevents_later_automatic_same_day_accrual(
    tmp_path: Path,
) -> None:
    use_case, connection = build_use_case(tmp_path)
    use_case.execute(command(), apply=True)
    financing = FinancingInterestUseCase(
        SQLiteLiabilityRepository(connection),
        SQLiteLiabilityPrincipalEventRepository(connection),
        SQLiteInvestmentCostEventRepository(connection),
        {LiabilityType.MARGIN_FINANCING: Decimal("0.065")},
    )

    result = financing.ensure_through(EVENT_DATE)

    assert financing_interest_event_id(LIABILITY_ID, EVENT_DATE) not in {
        event.id
        for event in SQLiteInvestmentCostEventRepository(connection).list_between_dates(
            EVENT_DATE, EVENT_DATE
        )
    }
    item = next(
        item
        for item in result.items
        if item.liability_id == LIABILITY_ID and item.accrual_date == EVENT_DATE
    )
    assert item.persisted is True
    connection.close()


def test_insert_failure_rolls_back_actual_and_reversal(tmp_path: Path) -> None:
    use_case, connection = build_use_case(tmp_path)
    costs = SQLiteInvestmentCostEventRepository(connection)
    costs.add(automatic_event())
    connection.execute(
        """CREATE TRIGGER reject_broker_reversal
        BEFORE INSERT ON investment_cost_events
        WHEN NEW.id LIKE 'broker-financing-interest-reversal:%'
        BEGIN SELECT RAISE(ABORT, 'forced rollback'); END"""
    )
    connection.commit()

    with pytest.raises(Exception, match="forced rollback"):
        use_case.execute(command(), apply=True)

    rows = costs.list_between_dates(EVENT_DATE, EVENT_DATE)
    assert rows == [automatic_event()]
    connection.close()

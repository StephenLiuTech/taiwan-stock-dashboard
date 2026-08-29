from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from database import initialize_database, initialize_schema
from domain import (
    Currency,
    Liability,
    LiabilityPrincipalEvent,
    LiabilityPrincipalEventType,
    LiabilityType,
)
from pams.application import LiabilityPrincipalUseCase
from pams.cli import main
from repositories.sqlite import (
    SQLiteLiabilityPrincipalEventRepository,
    SQLiteLiabilityRepository,
)
from services import LiabilityPrincipalEngine, LiabilityPrincipalReplayError

NOW = datetime(2026, 1, 1, tzinfo=UTC)
MARGIN_ID = "liability-margin-financing"
PLEDGE_ID = "liability-stock-pledge"


def event(
    event_id: str,
    day: str,
    sequence: int,
    delta: str,
    resulting: str | None,
    event_type: LiabilityPrincipalEventType = LiabilityPrincipalEventType.INCREASE,
) -> LiabilityPrincipalEvent:
    return LiabilityPrincipalEvent(
        id=event_id,
        liability_id="loan",
        effective_date=date.fromisoformat(day),
        sequence=sequence,
        event_type=event_type,
        principal_delta=Decimal(delta),
        resulting_principal=(Decimal(resulting) if resulting else None),
        source="test",
        created_at=NOW,
    )


def approved_events() -> list[LiabilityPrincipalEvent]:
    rows = [
        (MARGIN_ID, "2026-06-02", 10, "50000", "50000", "2027"),
        (MARGIN_ID, "2026-06-04", 10, "52000", "102000", "2027"),
        (MARGIN_ID, "2026-06-05", 10, "52000", "154000", "2027"),
        (MARGIN_ID, "2026-06-08", 10, "49000", "203000", "2027"),
        (MARGIN_ID, "2026-06-10", 10, "50000", "253000", "2027"),
        (MARGIN_ID, "2026-06-16", 10, "50000", "303000", "2027"),
        (MARGIN_ID, "2026-06-18", 10, "98000", "401000", "2027"),
        (MARGIN_ID, "2026-06-23", 10, "310000", "711000", "8131"),
        (MARGIN_ID, "2026-06-29", 10, "48000", "759000", "2027"),
        (MARGIN_ID, "2026-07-01", 10, "-310000", "449000", "8131 repayment"),
        (MARGIN_ID, "2026-07-09", 10, "48000", "497000", "2027"),
        (MARGIN_ID, "2026-07-14", 10, "46000", "543000", "2027"),
        (MARGIN_ID, "2026-08-05", 10, "52000", "595000", "2027"),
        (MARGIN_ID, "2026-08-11", 10, "55000", "650000", "2027"),
        (MARGIN_ID, "2026-08-14", 10, "57000", "707000", "2027"),
        (MARGIN_ID, "2026-08-21", 10, "60000", "767000", "2027"),
        (MARGIN_ID, "2026-08-25", 10, "60960", "827960", "2027"),
        (MARGIN_ID, "2026-08-27", 10, "58560", "886520", "2027"),
        (MARGIN_ID, "2026-08-28", 10, "87480", "974000", "2027"),
        (PLEDGE_ID, "2026-01-06", 10, "83000", "83000", "tranche 1"),
        (PLEDGE_ID, "2026-01-06", 20, "400000", "483000", "tranche 2"),
        (PLEDGE_ID, "2026-01-15", 10, "40000", "523000", "tranche 3"),
        (PLEDGE_ID, "2026-03-18", 10, "15000", "538000", "tranche 4"),
        (PLEDGE_ID, "2026-05-15", 10, "400000", "938000", "tranche 5"),
        (PLEDGE_ID, "2026-05-25", 10, "60000", "998000", "tranche 6"),
    ]
    return [
        LiabilityPrincipalEvent(
            id=f"fixture-{liability_id}-{day}-{sequence}",
            liability_id=liability_id,
            effective_date=date.fromisoformat(day),
            sequence=sequence,
            event_type=(
                LiabilityPrincipalEventType.REPAYMENT
                if Decimal(delta) < 0
                else LiabilityPrincipalEventType.INCREASE
            ),
            principal_delta=Decimal(delta),
            resulting_principal=Decimal(resulting),
            source="approved regression fixture",
            reference=reference,
            created_at=NOW,
        )
        for liability_id, day, sequence, delta, resulting, reference in rows
    ]


def test_replay_opening_increase_repayment_and_correction() -> None:
    values = [
        event(
            "d", "2026-01-04", 10, "-5", "110", LiabilityPrincipalEventType.CORRECTION
        ),
        event("b", "2026-01-02", 10, "25", "125"),
        event(
            "c", "2026-01-03", 10, "-10", "115", LiabilityPrincipalEventType.REPAYMENT
        ),
        event("a", "2026-01-01", 10, "100", "100", LiabilityPrincipalEventType.OPENING),
        event("e", "2026-01-04", 20, "10", "120"),
    ]
    engine = LiabilityPrincipalEngine()

    timeline = engine.timeline(values)

    assert [point.event.id for point in timeline] == ["a", "b", "c", "d", "e"]
    assert [point.principal for point in timeline] == [
        Decimal("100"),
        Decimal("125"),
        Decimal("115"),
        Decimal("110"),
        Decimal("120"),
    ]


def test_replay_rejects_resulting_invariant_and_negative_principal() -> None:
    engine = LiabilityPrincipalEngine()
    with pytest.raises(LiabilityPrincipalReplayError, match="resulting principal"):
        engine.timeline([event("bad", "2026-01-01", 10, "10", "11")])
    with pytest.raises(LiabilityPrincipalReplayError, match="negative"):
        engine.timeline(
            [
                event(
                    "bad",
                    "2026-01-01",
                    10,
                    "-1",
                    None,
                    LiabilityPrincipalEventType.REPAYMENT,
                )
            ]
        )


def test_replay_requires_unique_same_day_sequence() -> None:
    with pytest.raises(LiabilityPrincipalReplayError, match="duplicate"):
        LiabilityPrincipalEngine().timeline(
            [
                event("a", "2026-01-01", 10, "1", "1"),
                event("b", "2026-01-01", 10, "1", "2"),
            ]
        )


def test_principal_as_of_is_inclusive() -> None:
    engine = LiabilityPrincipalEngine()
    values = [
        event("a", "2026-01-01", 10, "100", "100"),
        event("b", "2026-01-02", 10, "50", "150"),
    ]
    assert engine.principal_as_of(values, date(2025, 12, 31)) == 0
    assert engine.principal_as_of(values, date(2026, 1, 1)) == 100
    assert engine.principal_as_of(values, date(2026, 1, 2)) == 150


def test_approved_backfill_replays_all_liabilities_and_8131_lifecycle(
    tmp_path: Path,
) -> None:
    connection = initialize_database(f"sqlite:///{(tmp_path / 'pams.db').as_posix()}")
    initialize_schema(connection)
    liabilities = SQLiteLiabilityRepository(connection)
    liabilities.upsert(
        Liability(
            id="liability-margin-financing",
            liability_type=LiabilityType.MARGIN_FINANCING,
            principal=Decimal("974000"),
            currency=Currency.TWD,
            financed_symbol="2027",
            financed_quantity=Decimal("37000"),
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
    repository = SQLiteLiabilityPrincipalEventRepository(connection)
    use_case = LiabilityPrincipalUseCase(repository, liabilities)

    values = approved_events()
    expected = {MARGIN_ID: Decimal("974000"), PLEDGE_ID: Decimal("998000")}
    first = use_case.backfill(
        values, as_of=date(2026, 8, 28), expected_principals=expected
    )
    second = use_case.backfill(
        values, as_of=date(2026, 8, 28), expected_principals=expected
    )

    assert first.inserted == first.attempted == 25
    assert second.inserted == 0
    assert use_case.principal("liability-margin-financing", date(2026, 6, 22)) == 401000
    assert use_case.principal("liability-margin-financing", date(2026, 6, 23)) == 711000
    assert use_case.principal("liability-margin-financing", date(2026, 6, 30)) == 759000
    assert use_case.principal("liability-margin-financing", date(2026, 7, 1)) == 449000
    assert use_case.principal("liability-margin-financing", date(2026, 8, 28)) == 974000
    assert use_case.principal("liability-stock-pledge", date(2026, 8, 28)) == 998000
    assert (
        connection.execute("SELECT COUNT(*) FROM investment_cost_events").fetchone()[0]
        == 0
    )
    connection.close()


def test_approved_same_day_pledge_events_have_explicit_order() -> None:
    values = [
        item
        for item in approved_events()
        if item.liability_id == "liability-stock-pledge"
        and item.effective_date == date(2026, 1, 6)
    ]
    assert [item.sequence for item in values] == [10, 20]
    assert LiabilityPrincipalEngine().timeline(values)[-1].principal == 483000


def test_liability_history_and_principal_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database_path = tmp_path / "pams.db"
    connection = initialize_database(f"sqlite:///{database_path.as_posix()}")
    initialize_schema(connection)
    liabilities = SQLiteLiabilityRepository(connection)
    liabilities.upsert(
        Liability(
            id="liability-margin-financing",
            liability_type=LiabilityType.MARGIN_FINANCING,
            principal=Decimal("974000"),
            currency=Currency.TWD,
            financed_symbol="2027",
            financed_quantity=Decimal("37000"),
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
    LiabilityPrincipalUseCase(
        SQLiteLiabilityPrincipalEventRepository(connection), liabilities
    ).backfill(
        approved_events(),
        as_of=date(2026, 8, 28),
        expected_principals={
            MARGIN_ID: Decimal("974000"),
            PLEDGE_ID: Decimal("998000"),
        },
    )
    connection.close()

    assert (
        main(
            [
                "liability",
                "principal",
                "--liability-id",
                "liability-margin-financing",
                "--date",
                "2026-08-28",
                "--database",
                str(database_path),
            ]
        )
        == 0
    )
    assert "Principal: NT$974,000.00" in capsys.readouterr().out
    assert (
        main(
            [
                "liability",
                "history",
                "--liability-id",
                "liability-stock-pledge",
                "--database",
                str(database_path),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "pledge" in output
    assert "Count: 6" in output

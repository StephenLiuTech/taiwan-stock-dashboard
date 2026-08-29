from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from domain import Currency, FinancingType, Market, Transaction, TransactionType
from pams.application import ReconcileBrokerUseCase
from pams.brokerage import (
    ReconciliationStatus,
    TaiwanBrokerCsvParser,
)
from pams.brokerage.reporting import format_reconciliation
from pams.cli import build_parser
from services import BrokerReconciliationEngine

FIXTURE = Path(__file__).parent / "fixtures" / "broker_statement_sanitized.csv"
NON_TRADE_FIXTURE = (
    Path(__file__).parent / "fixtures" / "broker_non_trade_sanitized.csv"
)
SECURITIES = {"台積電": ("2330", Market.TWSE), "大成鋼": ("2027", Market.TWSE)}


def transaction(
    transaction_id: str,
    *,
    symbol: str = "2330",
    market: Market = Market.TWSE,
    trade_date: date = date(2026, 8, 1),
    side: TransactionType = TransactionType.BUY,
    quantity: str = "10",
    price: str = "1000",
    fees: str = "5",
    taxes: str = "0",
    financing: FinancingType | None = None,
    notes: str | None = None,
) -> Transaction:
    return Transaction(
        id=transaction_id,
        symbol=symbol,
        market=market,
        transaction_type=side,
        trade_date=trade_date,
        settlement_date=trade_date,
        quantity=Decimal(quantity),
        price=Decimal(price),
        fees=Decimal(fees),
        taxes=Decimal(taxes),
        currency=Currency.TWD,
        financing_type=financing,
        notes=notes,
    )


def parsed() -> tuple[int, tuple[object, ...]]:
    return TaiwanBrokerCsvParser().parse(FIXTURE, SECURITIES)


def test_parser_normalizes_decimal_dates_markets_and_trade_sides() -> None:
    source_rows, records = parsed()
    assert source_rows == 5
    assert records[0].trade_date == date(2026, 8, 1)
    assert records[0].market is Market.TWSE
    assert records[0].quantity == Decimal("10")
    assert records[0].transaction_type is TransactionType.BUY
    assert records[1].transaction_type is TransactionType.SELL
    assert records[1].tax == Decimal("24")
    assert records[2].financing_type is None
    assert records[2].warning is not None
    assert records[-1].warning is not None


def test_parser_supports_inclusive_partial_date_range() -> None:
    source_rows, records = TaiwanBrokerCsvParser().parse(
        FIXTURE,
        SECURITIES,
        start_date=date(2026, 8, 2),
        end_date=date(2026, 8, 3),
    )
    assert source_rows == 5
    assert [item.trade_date for item in records] == [date(2026, 8, 2), date(2026, 8, 3)]


def test_parser_classifies_non_trade_rows_without_forcing_buy_or_sell() -> None:
    _, records = TaiwanBrokerCsvParser().parse(NON_TRADE_FIXTURE, SECURITIES)
    assert [item.kind.value for item in records] == [
        "dividend",
        "corporate_action",
        "financing_settlement",
        "trade",
        "trade",
    ]
    assert all(item.transaction_type is None for item in records[:3])
    assert records[3].financing_type is FinancingType.MARGIN
    assert records[4].financing_type is FinancingType.MARGIN
    plan = BrokerReconciliationEngine().reconcile("abc", 5, (records[4],), [])
    assert plan.items[0].dependencies == (
        "BLOCKED: negative financing principal repayment evidence is required",
    )


def test_engine_classifies_matched_new_mismatch_and_unsupported() -> None:
    source_rows, records = parsed()
    ledger = [
        transaction("matched", notes="Broker order order-a"),
        transaction(
            "mismatch",
            trade_date=date(2026, 8, 2),
            side=TransactionType.SELL,
            quantity="5",
            price="1200",
            fees="0",
            taxes="24",
        ),
    ]
    plan = BrokerReconciliationEngine().reconcile("abc", source_rows, records, ledger)
    assert plan.count(ReconciliationStatus.MATCHED) == 1
    assert plan.count(ReconciliationStatus.MISMATCH) == 1
    assert plan.count(ReconciliationStatus.NEW) == 2
    assert plan.count(ReconciliationStatus.UNSUPPORTED) == 1
    mismatch = next(
        item for item in plan.items if item.status is ReconciliationStatus.MISMATCH
    )
    assert mismatch.field_differences == (("fee", "6", "0"),)


def test_same_day_identical_trades_use_source_reference_not_fuzzy_choice() -> None:
    _, records = parsed()
    candidates = [
        transaction("one", notes="Broker order other"),
        transaction("two", notes="Broker order order-a"),
    ]
    plan = BrokerReconciliationEngine().reconcile("abc", 1, (records[0],), candidates)
    assert plan.items[0].status is ReconciliationStatus.MATCHED
    assert plan.items[0].matched_transaction_id == "two"


def test_multiple_candidates_without_strong_reference_are_ambiguous() -> None:
    _, records = parsed()
    candidates = [transaction("one"), transaction("two")]
    plan = BrokerReconciliationEngine().reconcile("abc", 1, (records[0],), candidates)
    assert plan.items[0].status is ReconciliationStatus.AMBIGUOUS
    assert plan.items[0].candidate_transaction_ids == ("one", "two")


def test_same_reference_candidates_use_exact_fee_and_tax() -> None:
    _, records = parsed()
    candidates = [
        transaction("one", taxes="0", notes="Broker order order-a"),
        transaction("two", taxes="1", notes="Broker order order-a"),
    ]
    plan = BrokerReconciliationEngine().reconcile("abc", 1, (records[0],), candidates)
    assert plan.items[0].status is ReconciliationStatus.MATCHED
    assert plan.items[0].matched_transaction_id == "one"


def test_new_proposal_is_deterministic_and_margin_dependency_is_blocked() -> None:
    _, records = parsed()
    engine = BrokerReconciliationEngine()
    first = engine.reconcile("abc", 1, (records[2],), [])
    second = engine.reconcile("abc", 1, (records[2],), [])
    assert first == second
    assert first.items[0].proposed_transaction is not None
    assert first.items[0].dependencies == (
        "BLOCKED: explicit cash/margin classification is required",
    )


def test_explicit_margin_record_requires_principal_evidence() -> None:
    _, records = parsed()
    margin = replace(
        records[2],
        financing_type=FinancingType.MARGIN,
        financing_classification_known=True,
    )
    plan = BrokerReconciliationEngine().reconcile("abc", 1, (margin,), [])
    assert plan.items[0].dependencies == (
        "BLOCKED: positive financing principal evidence is required",
    )


def test_duplicate_rows_inside_source_are_reported_and_not_proposed_twice() -> None:
    _, records = parsed()
    plan = BrokerReconciliationEngine().reconcile(
        "abc", 2, (records[0], records[0]), []
    )
    assert plan.count(ReconciliationStatus.NEW) == 1
    assert plan.count(ReconciliationStatus.AMBIGUOUS) == 1
    assert plan.duplicate_source_rows == ((2, 2),)


def test_reporting_is_read_only_and_json_serializable() -> None:
    source_rows, records = parsed()
    plan = BrokerReconciliationEngine().reconcile("abc", source_rows, records, [])
    text = format_reconciliation(plan)
    output = format_reconciliation(plan, json_output=True)
    assert "READ-ONLY; no database writes" in text
    assert '"source_fingerprint": "abc"' in output


def test_application_fingerprint_is_stable_and_performs_no_writes() -> None:
    class Holdings:
        def list_all(self) -> list[object]:
            return []

    class Transactions:
        def list_all(self) -> list[object]:
            return []

    use_case = ReconcileBrokerUseCase(Holdings(), Transactions())  # type: ignore[arg-type]
    first = use_case.execute(FIXTURE)
    second = use_case.execute(FIXTURE)
    assert first.source_fingerprint == second.source_fingerprint
    assert len(first.source_fingerprint) == 64


def test_cli_exposes_only_read_only_broker_reconcile() -> None:
    arguments = build_parser().parse_args(
        ["broker", "reconcile", "statement.csv", "--from", "2026-01-01", "--json"]
    )
    assert arguments.broker_command == "reconcile"
    assert arguments.start_date == date(2026, 1, 1)
    assert arguments.json_output is True


def test_cli_requires_exactly_one_broker_import_mode() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["broker", "import", "statement.csv"])
    arguments = parser.parse_args(["broker", "import", "statement.csv", "--dry-run"])
    assert arguments.dry_run is True

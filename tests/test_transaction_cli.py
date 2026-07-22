"""Offline CLI tests for explicit holding rebuild and transaction commands."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

import pams.cli
from pams.application import (
    AddTransactionCommand,
    HoldingChangeAction,
    HoldingChangeItem,
    HoldingChangePlan,
    TransactionList,
    TransactionRecord,
)
from pams.cli import ExitCode, main


def plan(*, applied: bool = False) -> HoldingChangePlan:
    item = HoldingChangeItem(
        symbol="2330",
        action=HoldingChangeAction.UPDATE,
        old_quantity=Decimal("1"),
        new_quantity=Decimal("2"),
        old_average_cost=Decimal("10"),
        new_average_cost=Decimal("11"),
        old_cost_basis=Decimal("10"),
        new_cost_basis=Decimal("22"),
    )
    return HoldingChangePlan(
        created_holdings=(),
        updated_holdings=(item,),
        unchanged_holdings=(),
        closed_holdings=(),
        warnings=(),
        transaction_count=1,
        projected_total_cost_basis=Decimal("22"),
        applied=applied,
    )


class FakeApplyUseCase:
    def __init__(self) -> None:
        self.calls: list[tuple[bool, bool]] = []

    def execute(
        self, *, apply: bool = False, allow_unmatched_holdings: bool = False
    ) -> HoldingChangePlan:
        self.calls.append((apply, allow_unmatched_holdings))
        return plan(applied=apply)


class FakeAddUseCase:
    def __init__(self) -> None:
        self.command = None

    def execute(self, command: AddTransactionCommand) -> TransactionRecord:
        self.command = command
        return TransactionRecord(
            id=command.transaction_id or "generated",
            symbol=command.symbol,
            market=command.market,
            transaction_type=command.transaction_type,
            trade_date=command.trade_date,
            settlement_date=command.settlement_date,
            quantity=command.quantity,
            price=command.price,
            fees=command.fees,
            taxes=command.taxes,
            currency=command.currency,
            notes=command.notes,
        )


class FakeListUseCase:
    def execute(self, **filters: object) -> TransactionList:
        del filters
        return TransactionList(
            (
                TransactionRecord(
                    id="tx-1",
                    symbol="2330",
                    market="TWSE",
                    transaction_type="buy",
                    trade_date=date(2026, 7, 1),
                    settlement_date=date(2026, 7, 3),
                    quantity=Decimal("100.125"),
                    price=Decimal("1800.1234"),
                    fees=Decimal("20"),
                    taxes=Decimal("0"),
                    currency="TWD",
                    notes=None,
                ),
            )
        )


def install_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[FakeApplyUseCase, FakeAddUseCase]:
    apply_use_case = FakeApplyUseCase()
    add_use_case = FakeAddUseCase()

    @contextmanager
    def compose(
        database_override: Path | None = None, *, verbose: bool = False
    ) -> Iterator[SimpleNamespace]:
        del database_override, verbose
        yield SimpleNamespace(
            apply_rebuilt_holdings=apply_use_case,
            add_transaction=add_use_case,
            list_transactions=FakeListUseCase(),
        )

    monkeypatch.setattr(pams.cli, "compose_application", compose)
    monkeypatch.setattr(pams.cli, "compose_ledger_operations", compose)
    monkeypatch.setattr(pams.cli, "compose_operations", compose)
    return apply_use_case, add_use_case


def test_cli_holding_rebuild_defaults_to_dry_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apply_use_case, _ = install_composition(monkeypatch)
    assert main(["holdings", "rebuild"]) == ExitCode.SUCCESS
    assert apply_use_case.calls == [(False, False)]
    assert "Applied: No" in capsys.readouterr().out


def test_cli_holding_apply_is_explicit_and_supports_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apply_use_case, _ = install_composition(monkeypatch)
    assert (
        main(
            [
                "holdings",
                "rebuild",
                "--apply",
                "--allow-unmatched",
                "--json",
            ]
        )
        == 0
    )
    assert apply_use_case.calls == [(True, True)]
    payload = json.loads(capsys.readouterr().out)
    assert payload["applied"] is True
    assert payload["updated_holdings"][0]["new_cost_basis"] == "22"


def test_cli_transaction_add_preserves_decimal_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, add_use_case = install_composition(monkeypatch)
    assert (
        main(
            [
                "transaction",
                "add",
                "--id",
                "tx-1",
                "--symbol",
                "2330",
                "--market",
                "TWSE",
                "--type",
                "buy",
                "--trade-date",
                "2026-07-01",
                "--settlement-date",
                "2026-07-03",
                "--quantity",
                "100.125",
                "--price",
                "1800.1234",
                "--fees",
                "20.05",
            ]
        )
        == 0
    )
    assert add_use_case.command.quantity == Decimal("100.125")
    assert add_use_case.command.price == Decimal("1800.1234")


def test_cli_transaction_list_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install_composition(monkeypatch)
    assert main(["transaction", "list", "--symbol", "2330", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["transactions"][0]["quantity"] == "100.125"

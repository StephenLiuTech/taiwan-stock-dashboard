"""Offline CLI tests for explicit holding rebuild and transaction commands."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
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
    HoldingQueryItem,
    HoldingsQueryResult,
    InvalidHoldingHistoryError,
    TransactionList,
    TransactionRecord,
)
from pams.cli import ExitCode, build_parser, main


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


class FakeHoldingsQueryUseCase:
    def __init__(self) -> None:
        self.symbols: list[str | None] = []

    def execute(self, symbol: str | None = None) -> HoldingsQueryResult:
        self.symbols.append(symbol)
        return HoldingsQueryResult(
            valuation_date=date(2026, 7, 22),
            holdings=(
                HoldingQueryItem(
                    symbol="2330",
                    market="TWSE",
                    quantity=Decimal("2"),
                    average_cost=Decimal("80"),
                    total_cost=Decimal("160"),
                    latest_price=Decimal("100"),
                    market_value=Decimal("200"),
                    unrealized_pl=Decimal("40"),
                    unrealized_return=Decimal("0.25"),
                    transaction_count=2,
                    first_trade_date=date(2026, 7, 1),
                    latest_trade_date=date(2026, 7, 10),
                ),
            ),
        )


def install_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[FakeApplyUseCase, FakeAddUseCase, FakeHoldingsQueryUseCase]:
    apply_use_case = FakeApplyUseCase()
    add_use_case = FakeAddUseCase()
    query_use_case = FakeHoldingsQueryUseCase()

    @contextmanager
    def compose(
        database_override: Path | None = None, *, verbose: bool = False
    ) -> Iterator[SimpleNamespace]:
        del database_override, verbose
        yield SimpleNamespace(
            apply_rebuilt_holdings=apply_use_case,
            add_transaction=add_use_case,
            list_transactions=FakeListUseCase(),
            query_holdings=query_use_case,
        )

    monkeypatch.setattr(pams.cli, "compose_application", compose)
    monkeypatch.setattr(pams.cli, "compose_ledger_operations", compose)
    monkeypatch.setattr(pams.cli, "compose_operations", compose)
    return apply_use_case, add_use_case, query_use_case


def test_cli_holding_rebuild_defaults_to_dry_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apply_use_case, _, _ = install_composition(monkeypatch)
    assert main(["holdings", "rebuild"]) == ExitCode.SUCCESS
    assert apply_use_case.calls == [(False, False)]
    assert "Applied: No" in capsys.readouterr().out


def test_cli_holding_apply_is_explicit_and_supports_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apply_use_case, _, _ = install_composition(monkeypatch)
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
    _, add_use_case, _ = install_composition(monkeypatch)
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


def test_cli_transaction_add_defaults_settlement_to_trade_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, add_use_case, _ = install_composition(monkeypatch)

    assert (
        main(
            [
                "transaction",
                "add",
                "--symbol",
                "2330",
                "--market",
                "TWSE",
                "--type",
                "buy",
                "--trade-date",
                "2026-07-01",
                "--quantity",
                "1",
                "--price",
                "100",
            ]
        )
        == 0
    )
    assert add_use_case.command.settlement_date == date(2026, 7, 1)


def test_transaction_add_help_documents_settlement_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        build_parser().parse_args(["transaction", "add", "--help"])

    assert raised.value.code == 0
    assert "settlement date (defaults to trade date)" in capsys.readouterr().out


def test_cli_holdings_list_and_show_route_to_query_use_case(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, _, query = install_composition(monkeypatch)

    assert main(["holdings", "list"]) == 0
    listing = capsys.readouterr().out
    assert "PAMS Holdings" in listing
    assert "2330 | TWSE | 2.00" in listing

    assert main(["holdings", "show", "2330"]) == 0
    detail = capsys.readouterr().out
    assert "Transaction count: 2" in detail
    assert "First trade date: 2026-07-01" in detail
    assert query.symbols == [None, "2330"]


def test_cli_holding_without_quote_renders_unavailable_values(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, _, query = install_composition(monkeypatch)
    item = query.execute().holdings[0]
    query.execute = lambda symbol=None: HoldingsQueryResult(  # type: ignore[method-assign]
        None,
        (
            replace(
                item,
                latest_price=None,
                market_value=None,
                unrealized_pl=None,
                unrealized_return=None,
            ),
        ),
    )

    assert main(["holdings", "show", "2330"]) == 0
    output = capsys.readouterr().out
    assert "Latest available market price: N/A" in output
    assert "Market value: N/A" in output


def test_cli_oversell_error_is_clean_without_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, _, query = install_composition(monkeypatch)

    def reject(symbol: str | None = None) -> HoldingsQueryResult:
        del symbol
        raise InvalidHoldingHistoryError(
            "SELL exceeds held quantity for 2330 in transaction oversell"
        )

    query.execute = reject  # type: ignore[method-assign]
    assert main(["holdings", "list"]) == ExitCode.SECURITY_ERROR
    captured = capsys.readouterr()
    assert "SELL exceeds held quantity" in captured.err
    assert "Traceback" not in captured.err


def test_cli_transaction_list_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install_composition(monkeypatch)
    assert main(["transaction", "list", "--symbol", "2330", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["transactions"][0]["quantity"] == "100.125"

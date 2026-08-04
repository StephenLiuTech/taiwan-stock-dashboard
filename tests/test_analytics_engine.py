"""Pure Analytics Engine behavior and boundary tests."""

import ast
from dataclasses import FrozenInstanceError
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from domain import DailySnapshot, TransactionExpenseSummary
from services import (
    AnalyticsEngine,
    DuplicateSnapshotDateError,
    EmptySnapshotHistoryError,
)


def snapshot(day: int, value: str) -> DailySnapshot:
    snapshot_date = date(2026, 7, 1) + timedelta(days=day)
    net_value = Decimal(value)
    return DailySnapshot(
        snapshot_date=snapshot_date,
        total_market_value=max(net_value, Decimal("0")),
        total_cost_basis=Decimal("0"),
        total_unrealized_pnl=net_value,
        total_liabilities=Decimal("0"),
        net_asset_value=net_value,
        leverage_ratio=Decimal("0"),
        high_water_mark=max(net_value, Decimal("0")),
        drawdown=Decimal("0"),
    )


def test_basic_period_performance() -> None:
    result = AnalyticsEngine().analyze([snapshot(0, "100"), snapshot(2, "125")])
    assert result.starting_value == Decimal("100")
    assert result.ending_value == Decimal("125")
    assert result.absolute_profit_loss == Decimal("25")
    assert result.total_return == Decimal("0.25")
    assert result.start_date == date(2026, 7, 1)
    assert result.end_date == date(2026, 7, 3)
    assert result.snapshot_count == 2


def test_expenses_are_reported_separately_from_before_expense_return() -> None:
    result = AnalyticsEngine().analyze(
        [snapshot(0, "100"), snapshot(1, "125")],
        TransactionExpenseSummary(
            total_buy_fees=Decimal("1"),
            total_sell_fees=Decimal("2"),
            total_taxes=Decimal("3"),
        ),
    )

    assert result.total_buy_fees == Decimal("1")
    assert result.total_sell_fees == Decimal("2")
    assert result.total_taxes == Decimal("3")
    assert result.total_trading_expenses == Decimal("6")
    assert result.net_investment_return_before_expenses == Decimal("0.25")
    assert result.net_investment_return_after_expenses == Decimal("0.19")


def test_daily_returns_use_consecutive_chronological_snapshots() -> None:
    result = AnalyticsEngine().analyze(
        [snapshot(2, "90"), snapshot(0, "100"), snapshot(1, "120")]
    )
    assert [item.daily_return for item in result.daily_returns] == [
        Decimal("0.2"),
        Decimal("-0.25"),
    ]
    assert result.daily_returns[0].period_start == date(2026, 7, 1)
    assert result.daily_returns[0].period_end == date(2026, 7, 2)
    assert result.daily_returns[0].starting_value == Decimal("100")
    assert result.daily_returns[0].ending_value == Decimal("120")


def test_peak_trough_and_running_peak_max_drawdown() -> None:
    result = AnalyticsEngine().analyze(
        [
            snapshot(0, "100"),
            snapshot(1, "120"),
            snapshot(2, "90"),
            snapshot(3, "110"),
            snapshot(4, "80"),
        ]
    )
    assert result.peak_value == Decimal("120")
    assert result.trough_value == Decimal("80")
    assert result.max_drawdown == Decimal("-0.3333333333333333333333333333")


def test_single_snapshot_has_zero_returns_and_drawdown() -> None:
    result = AnalyticsEngine().analyze([snapshot(0, "100")])
    assert result.absolute_profit_loss == Decimal("0")
    assert result.total_return == Decimal("0")
    assert result.daily_returns == ()
    assert result.peak_value == result.trough_value == Decimal("100")
    assert result.max_drawdown == Decimal("0")


def test_zero_starting_value_returns_zero_instead_of_dividing() -> None:
    result = AnalyticsEngine().analyze([snapshot(0, "0"), snapshot(1, "10")])
    assert result.absolute_profit_loss == Decimal("10")
    assert result.total_return == Decimal("0")
    assert result.daily_returns[0].daily_return == Decimal("0")


def test_empty_history_is_rejected_with_typed_error() -> None:
    with pytest.raises(EmptySnapshotHistoryError):
        AnalyticsEngine().analyze([])


def test_duplicate_snapshot_dates_are_rejected() -> None:
    with pytest.raises(DuplicateSnapshotDateError):
        AnalyticsEngine().analyze([snapshot(0, "100"), snapshot(0, "110")])


def test_analytics_results_are_immutable() -> None:
    result = AnalyticsEngine().analyze([snapshot(0, "100")])
    with pytest.raises(FrozenInstanceError):
        result.ending_value = Decimal("0")  # type: ignore[misc]


def test_analytics_engine_does_not_import_infrastructure_or_ui() -> None:
    path = Path(__file__).parents[1] / "services" / "analytics_engine.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {
        "repositories",
        "sqlite3",
        "pams",
        "streamlit",
        "pandas",
        "plotly",
        "market_data",
    }
    imported = {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    imported.update(
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert imported.isdisjoint(forbidden)

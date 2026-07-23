"""Offline tests for Dashboard 2.0 presentation projections."""

import ast
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from domain import (
    DailyPortfolioReturn,
    HoldingValuation,
    Market,
    PortfolioAnalytics,
    PortfolioValuation,
)
from pams.analytics_reporting import (
    analytics_error_message,
    analytics_view_model,
)
from pams.application import (
    AnalyticsDataUnavailableError,
    AnalyticsProcessingError,
    AnalyticsRepositoryError,
    InvalidAnalyticsPeriodError,
)
from pams.dashboard.charts import allocation_chart, daily_returns_chart
from pams.dashboard.formatting import (
    MISSING_VALUE,
    format_percentage,
    format_twd,
    kpi_view_model,
)
from pams.dashboard.page import _load_analytics, _load_valuation
from pams.dashboard.tables import (
    allocation_rows,
    holdings_table_rows,
    largest_position_rows,
    performance_rows,
)


def item(
    symbol: str,
    *,
    market_value: str,
    unrealized: str,
    weight: str,
) -> HoldingValuation:
    return HoldingValuation(
        symbol=symbol,
        market=Market.TWSE,
        quantity=Decimal("10"),
        average_cost=Decimal("80"),
        last_price=Decimal("100"),
        cost_basis=Decimal("800"),
        market_value=Decimal(market_value),
        unrealized_pl=Decimal(unrealized),
        unrealized_return=Decimal(unrealized) / Decimal("800"),
        portfolio_weight=Decimal(weight),
    )


def valuation() -> PortfolioValuation:
    holdings = (
        item("2330", market_value="1000", unrealized="200", weight="0.625"),
        item("0050", market_value="600", unrealized="-200", weight="0.375"),
    )
    return PortfolioValuation(
        valuation_date=date(2026, 7, 22),
        total_cost=Decimal("1600"),
        total_market_value=Decimal("1600"),
        total_unrealized_pl=Decimal("0"),
        total_return=Decimal("0"),
        holdings=holdings,
    )


def analytics(total_return: str = "0.1") -> PortfolioAnalytics:
    return PortfolioAnalytics(
        start_date=date(2026, 7, 21),
        end_date=date(2026, 7, 22),
        starting_value=Decimal("1000.01"),
        ending_value=Decimal("1100.02"),
        absolute_profit_loss=Decimal("100.01"),
        total_return=Decimal(total_return),
        daily_returns=(
            DailyPortfolioReturn(
                period_start=date(2026, 7, 21),
                period_end=date(2026, 7, 22),
                starting_value=Decimal("1000.01"),
                ending_value=Decimal("1100.02"),
                daily_return=Decimal(total_return),
            ),
        ),
        peak_value=Decimal("1100.02"),
        trough_value=Decimal("1000.01"),
        max_drawdown=Decimal("-0.025"),
        snapshot_count=2,
    )


def test_currency_and_percentage_formatting_preserves_signs_and_missing() -> None:
    assert format_twd(Decimal("1234"), signed=True) == "+NT$ 1,234"
    assert format_twd(Decimal("-1234"), signed=True) == "NT$ -1,234"
    assert format_percentage(Decimal("0.125"), signed=True) == "+12.50%"
    assert format_twd(None) == MISSING_VALUE


def test_summary_uses_portfolio_valuation_totals() -> None:
    values = dict(kpi_view_model(valuation()))
    assert values == {
        "Market Value": "NT$ 1,600",
        "Cost": "NT$ 1,600",
        "Unrealized": "NT$ 0",
        "Return": "0.00%",
    }


def test_largest_positions_are_ranked_and_limited() -> None:
    base = valuation()
    many = tuple(
        item(
            str(index),
            market_value=str(index),
            unrealized=str(index),
            weight="0.01",
        )
        for index in range(12)
    )
    rows = largest_position_rows(
        PortfolioValuation(
            base.valuation_date,
            base.total_cost,
            base.total_market_value,
            base.total_unrealized_pl,
            base.total_return,
            many,
        )
    )
    assert len(rows) == 10
    assert rows[0]["Symbol"] == "11"
    assert rows[0]["Weight %"] == "1.00%"


def test_winners_losers_and_full_table_use_dto_values() -> None:
    current = valuation()
    assert performance_rows(current, winners=True)[0]["Symbol"] == "2330"
    assert performance_rows(current, winners=False)[0]["Symbol"] == "0050"
    rows = holdings_table_rows(current)
    assert rows[0]["Market Value"] == Decimal("1000")
    assert rows[0]["Return %"] == Decimal("0.25")


def test_allocation_chart_uses_application_weights_and_values() -> None:
    current = valuation()
    assert allocation_rows(current)[0] == {
        "Holding": "2330",
        "Market Value": Decimal("1000"),
        "Weight": Decimal("0.625"),
    }
    assert allocation_chart(current) is not None
    empty = PortfolioValuation(None, *(Decimal("0") for _ in range(4)), ())
    assert allocation_chart(empty) is None


def test_dashboard_modules_do_not_import_forbidden_layers() -> None:
    dashboard = Path(__file__).parents[1] / "pams" / "dashboard"
    forbidden = {"sqlite3", "repositories", "market_data", "domain", "services"}
    for path in dashboard.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert imported.isdisjoint(forbidden), path.name


def test_dashboard_load_executes_valuation_use_case_once() -> None:
    class FakeUseCase:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self) -> PortfolioValuation:
            self.calls += 1
            return valuation()

    use_case = FakeUseCase()
    _load_valuation.clear()
    first = _load_valuation(use_case)  # type: ignore[arg-type]
    second = _load_valuation(use_case)  # type: ignore[arg-type]
    assert first == second
    assert use_case.calls == 1


def test_dashboard_analytics_uses_application_result_once() -> None:
    class FakeUseCase:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self) -> PortfolioAnalytics:
            self.calls += 1
            return analytics()

    use_case = FakeUseCase()
    _load_analytics.clear()
    first = _load_analytics(use_case)  # type: ignore[arg-type]
    second = _load_analytics(use_case)  # type: ignore[arg-type]
    assert first == second == analytics()
    assert use_case.calls == 1


def test_analytics_view_model_preserves_dto_values_and_formats_signs() -> None:
    source = analytics()
    view = analytics_view_model(source)
    assert view.period == "2026-07-21 to 2026-07-22"
    assert view.starting_value == "NT$1,000.01"
    assert view.ending_value == "NT$1,100.02"
    assert view.absolute_profit_loss == "+NT$100.01"
    assert view.total_return == "+10.00%"
    assert view.max_drawdown == "-2.50%"
    assert view.daily_returns[0].value == Decimal("0.1")
    assert view.daily_returns[0].formatted_value == "+10.00%"
    assert source == analytics()


def test_daily_return_chart_converts_only_mapped_values() -> None:
    figure = daily_returns_chart(analytics_view_model(analytics()))
    assert figure is not None
    assert list(figure.data[0].y) == [0.1]
    assert list(figure.data[0].text) == ["+10.00%"]


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            AnalyticsDataUnavailableError("internal"),
            "Portfolio analytics are unavailable until snapshots exist.",
        ),
        (
            InvalidAnalyticsPeriodError("internal"),
            "The analytics start date must not be after the end date.",
        ),
        (
            AnalyticsRepositoryError("sqlite details"),
            "Portfolio analytics could not be loaded.",
        ),
        (
            AnalyticsProcessingError("engine details"),
            "Portfolio analytics could not be processed.",
        ),
    ],
)
def test_analytics_errors_are_mapped_without_internal_details(
    error: Exception, message: str
) -> None:
    assert analytics_error_message(error) == message

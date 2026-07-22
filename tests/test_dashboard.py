"""Offline tests for dashboard view models and architecture boundaries."""

import ast
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

from pams.application import (
    HoldingOverview,
    MarketAvailabilitySummary,
    PortfolioHistory,
    PortfolioHistoryPoint,
    PortfolioOverview,
)
from pams.dashboard.charts import allocation_chart, history_chart
from pams.dashboard.formatting import (
    MISSING_VALUE,
    format_percentage,
    format_twd,
    kpi_view_model,
)
from pams.dashboard.tables import allocation_rows, holdings_table_rows


def overview(*, synchronized: bool = True) -> PortfolioOverview:
    common = date(2026, 7, 22) if synchronized else None
    holding = HoldingOverview(
        symbol="2330",
        name="TSMC",
        market="TWSE",
        shares=Decimal("10"),
        average_cost=Decimal("80"),
        latest_price=Decimal("100"),
        market_value=Decimal("1000"),
        cost_basis=Decimal("800"),
        unrealized_pnl=Decimal("200"),
        unrealized_return=Decimal("0.25"),
        portfolio_weight=Decimal("1"),
        quote_date=date(2026, 7, 22),
    )
    return PortfolioOverview(
        database_path=Path("pams.db"),
        latest_quote_date=date(2026, 7, 22),
        latest_daily_snapshot=date(2026, 7, 22),
        latest_position_snapshot=date(2026, 7, 22),
        holdings_count=1,
        liabilities_count=1,
        schema_version=3,
        database_size_bytes=4096,
        market_availability=MarketAvailabilitySummary(
            date(2026, 7, 22), date(2026, 7, 22), common
        ),
        market_value=Decimal("1000"),
        net_equity=Decimal("700"),
        unrealized_pnl=Decimal("200"),
        todays_pnl=Decimal("-25"),
        total_liabilities=Decimal("300"),
        leverage_ratio=Decimal("0.3"),
        holdings=(holding,),
    )


def test_currency_and_percentage_formatting_preserves_signs_and_missing() -> None:
    assert format_twd(Decimal("1234"), signed=True) == "+NT$ 1,234"
    assert format_twd(Decimal("-1234"), signed=True) == "NT$ -1,234"
    assert format_percentage(Decimal("0.125"), signed=True) == "+12.50%"
    assert format_twd(None) == MISSING_VALUE


def test_kpi_view_model_uses_overview_values() -> None:
    values = dict(kpi_view_model(overview()))
    assert values["Market Value"] == "NT$ 1,000"
    assert values["Today's P/L"] == "NT$ -25"
    assert values["Leverage Ratio"] == "30.00%"


def test_empty_portfolio_and_missing_quotes_are_safe() -> None:
    empty = replace(
        overview(),
        latest_quote_date=None,
        holdings=(),
        market_value=None,
        net_equity=None,
        unrealized_pnl=None,
        todays_pnl=None,
    )
    assert holdings_table_rows(empty) == []
    assert allocation_rows(empty) == []
    assert allocation_chart(empty) is None
    assert dict(kpi_view_model(empty))["Market Value"] == MISSING_VALUE


def test_holdings_table_is_sorted_by_persisted_market_value() -> None:
    base = overview()
    smaller = replace(
        base.holdings[0], symbol="0050", name="ETF", market_value=Decimal("500")
    )
    rows = holdings_table_rows(replace(base, holdings=(smaller, base.holdings[0])))
    assert [row["Symbol"] for row in rows] == ["2330", "0050"]
    assert rows[0]["Cost Basis"] == "NT$ 800"
    assert rows[0]["Quote Date"] == "2026-07-22"


def test_allocation_and_history_charts_use_dto_data() -> None:
    current = overview()
    assert allocation_rows(current) == [
        {"Holding": "2330 TSMC", "Market Value": Decimal("1000")}
    ]
    allocation = allocation_chart(current)
    assert allocation is not None
    history = PortfolioHistory(
        (
            PortfolioHistoryPoint(
                date(2026, 7, 21), Decimal("900"), Decimal("650"), Decimal("250")
            ),
            PortfolioHistoryPoint(
                date(2026, 7, 22), Decimal("1000"), Decimal("700"), Decimal("300")
            ),
        )
    )
    chart = history_chart(history)
    assert chart is not None
    assert [trace.name for trace in chart.data] == [
        "Market Value",
        "Net Equity",
        "Total Liabilities",
    ]
    assert history_chart(PortfolioHistory(())) is None


def test_unsynchronized_availability_has_neutral_waiting_state() -> None:
    availability = overview(synchronized=False).market_availability
    assert availability.synchronized is False
    assert availability.commonly_ingestible_date is None


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

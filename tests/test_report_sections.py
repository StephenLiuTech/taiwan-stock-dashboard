"""Focused tests for modular daily-report section services and rendering."""

# ruff: noqa: ANN001, ANN202, ANN204

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from domain import (
    AllocationItem,
    Currency,
    DailyReportSections,
    DividendCalendarItem,
    DividendCalendarSection,
    FinancingLeverageSection,
    Holding,
    HoldingType,
    Liability,
    LiabilityType,
    Market,
    MarketSnapshotItem,
    MarketSnapshotSection,
    NewsItem,
    NewsSection,
    PortfolioAllocationSection,
    PositionSnapshot,
    Transaction,
    TransactionSummarySection,
    TransactionType,
    UpcomingEventItem,
    UpcomingEventsSection,
)
from pams.application.report_sections import (
    BuildReportSectionsUseCase,
    ReportSectionSettings,
)
from pams.delivery.html_styles import (
    NEUTRAL_COLOR,
    TAIWAN_GAIN_COLOR,
    TAIWAN_LOSS_COLOR,
    taiwan_performance_color,
    taiwan_performance_color_from_text,
)
from pams.delivery.sections import DailyReportSectionRenderer
from services import NewsService, ReportSectionService


def holding(identifier: str, symbol: str, market: Market, kind: HoldingType) -> Holding:
    return Holding(
        id=identifier,
        symbol=symbol,
        name=f"Name {symbol}",
        market=market,
        currency=Currency.TWD,
        quantity=Decimal("10"),
        average_cost=Decimal("50"),
        holding_type=kind,
    )


def position(identifier: str, symbol: str, value: str) -> PositionSnapshot:
    market_value = Decimal(value)
    return PositionSnapshot(
        snapshot_date=date(2026, 7, 22),
        holding_id=identifier,
        symbol=symbol,
        quantity=Decimal("10"),
        average_cost=Decimal("50"),
        close_price=market_value / 10,
        cost_basis=Decimal("500"),
        market_value=market_value,
        unrealized_pnl=market_value - 500,
        unrealized_return=(market_value - 500) / 500,
        portfolio_weight=Decimal("0"),
        daily_value_change=Decimal("10") if symbol == "A" else Decimal("-20"),
        daily_return=Decimal("0.01"),
    )


def test_allocation_weights_groups_missing_quotes_and_zero_value() -> None:
    holdings = [
        holding("h1", "A", Market.TWSE, HoldingType.ETF),
        holding("h2", "B", Market.TPEX, HoldingType.STOCK),
        holding("h3", "C", Market.TWSE, HoldingType.STOCK),
    ]
    result = ReportSectionService.allocation(
        [position("h1", "A", "600"), position("h2", "B", "400")], holdings
    )
    assert [item.weight for item in result.by_holding] == [
        Decimal("0.6"),
        Decimal("0.4"),
    ]
    assert sum(item.weight for item in result.by_market) == Decimal("1.0")
    assert result.unquoted_holdings == ("C (TWSE)",)
    zero = ReportSectionService.allocation([position("h1", "A", "0")], holdings[:1])
    assert zero.by_holding[0].weight == 0


def test_insights_and_risk_are_deterministic_and_threshold_based() -> None:
    holdings = [
        holding("h1", "A", Market.TWSE, HoldingType.ETF),
        holding("h2", "B", Market.TWSE, HoldingType.STOCK),
    ]
    positions = [position("h1", "A", "800"), position("h2", "B", "200")]
    allocation = ReportSectionService.allocation(positions, holdings)
    first = ReportSectionService.insights(allocation, positions, Decimal("-10"))
    assert first == ReportSectionService.insights(allocation, positions, Decimal("-10"))
    assert "A is the largest holding" in first.insights[0]
    risk = ReportSectionService.risk(
        allocation,
        positions,
        single_threshold=Decimal("0.30"),
        top3_threshold=Decimal("0.70"),
        market_threshold=Decimal("0.80"),
    )
    assert risk.items[0].warning is True
    assert risk.items[1].value == "100.00%"
    assert any(item.label == "Largest daily loss contributor" for item in risk.items)


def test_dividend_insights_use_derived_annual_summary() -> None:
    dividend = DividendCalendarSection(
        items=(
            DividendCalendarItem(
                "2330",
                "TSMC",
                date(2026, 3, 17),
                None,
                date(2026, 4, 9),
                "Cash dividend",
                Decimal("6"),
                Decimal("1500"),
                Decimal("9000"),
                Decimal("9000"),
                "Paid",
                "official",
            ),
        ),
        estimated_annual_dividend=Decimal("12000"),
        already_received=Decimal("9000"),
    )
    result = ReportSectionService.insights(
        PortfolioAllocationSection((), (), (), ()), [], Decimal("0"), dividend
    )
    assert result.insights == (
        "Largest expected dividend: 2330 NT$9,000",
        "Already received: NT$9,000",
        "Remaining expected this year: NT$3,000",
    )


def test_transaction_summary_filters_trade_date_and_cash_direction() -> None:
    common = {
        "symbol": "2330",
        "market": Market.TWSE,
        "settlement_date": date(2026, 7, 25),
        "quantity": Decimal("10"),
        "price": Decimal("100"),
        "fees": Decimal("5"),
        "taxes": Decimal("2"),
        "currency": Currency.TWD,
    }
    transactions = [
        Transaction(
            id="buy",
            transaction_type=TransactionType.BUY,
            trade_date=date(2026, 7, 22),
            **common,
        ),
        Transaction(
            id="sell",
            transaction_type=TransactionType.SELL,
            trade_date=date(2026, 7, 22),
            **common,
        ),
        Transaction(
            id="later",
            transaction_type=TransactionType.BUY,
            trade_date=date(2026, 7, 23),
            **{**common, "settlement_date": date(2026, 7, 25)},
        ),
    ]
    result = ReportSectionService.transaction_summary(
        date(2026, 7, 22), transactions, {"2330": "TSMC"}
    )
    assert [item.net_cash_impact for item in result.items] == [
        Decimal("-1007"),
        Decimal("993"),
    ]


def test_news_deduplicates_limits_and_orders() -> None:
    now = datetime(2026, 7, 22, tzinfo=UTC)
    items = tuple(
        NewsItem(
            f"Headline {index % 3}",
            "Publisher",
            now,
            "Summary",
            f"https://example.com/{index}",
            "Relevant",
        )
        for index in range(8)
    )
    selected = NewsService.select(items, 2)
    assert len(selected.items) == 2
    assert len({item.headline for item in selected.items}) == 2


def test_renderer_orders_sections_and_omits_empty_watchlist_transactions() -> None:
    allocation = PortfolioAllocationSection(
        (AllocationItem("2330", "TSMC", Decimal("100"), Decimal("1")),),
        (),
        (),
        (),
    )
    sections = DailyReportSections(
        allocation=allocation,
        ai_news=NewsSection(status="News provider not configured"),
        transactions=TransactionSummarySection(),
    )
    renderer = DailyReportSectionRenderer()
    html = renderer.html(sections)
    text = renderer.text(sections)
    assert html.index("Portfolio Allocation") < html.index("AI News")
    assert "Transaction Summary" not in html
    assert "Portfolio Allocation" in text
    assert "News provider not configured" in text


def financing_liabilities() -> list[Liability]:
    return [
        Liability(
            id="liability-margin-financing",
            liability_type=LiabilityType.MARGIN_FINANCING,
            principal=Decimal("595000"),
            currency=Currency.TWD,
            collateral_description="2027 margin quantity: 24,000 shares",
            notes=(
                "Source/update: broker screenshot, 2026-08-05. "
                "Accrued interest reference: NT$4,583 (not included in principal)."
            ),
        ),
        Liability(
            id="liability-stock-pledge",
            liability_type=LiabilityType.STOCK_PLEDGE,
            principal=Decimal("998000"),
            currency=Currency.TWD,
            collateral_description=(
                "Pledged collateral: 0050: 4,000 shares; 3293: 2,000 shares"
            ),
            notes=(
                "Source/update: broker screenshot, 2026-08-05. "
                "Accrued interest reference: NT$26,368 (not included in principal). "
                "Repayment total reference: NT$1,024,368. "
                "Collateral market value reference: NT$1,969,200. "
                "Maintenance ratio reference: 197.31%."
            ),
        ),
    ]


def test_financing_section_parses_metadata_without_changing_principal_totals() -> None:
    result = ReportSectionService.financing(
        financing_liabilities(),
        Decimal("4262330"),
        Decimal("2669330"),
        Decimal("0.373738"),
    )

    assert result is not None
    assert result.total_principal_debt == Decimal("1593000")
    assert result.total_accrued_interest == Decimal("30951")
    assert result.total_debt == Decimal("1623951")
    assert result.net_stock_equity == Decimal("2669330")
    assert result.margin_financing is not None
    assert result.margin_financing.position_symbol == "2027"
    assert result.margin_financing.position_quantity == Decimal("24000")
    assert result.margin_financing.updated == date(2026, 8, 5)
    assert result.stock_pledge is not None
    assert result.stock_pledge.repayment_total == Decimal("1024368")
    assert result.stock_pledge.collateral_market_value == Decimal("1969200")
    assert result.stock_pledge.maintenance_ratio == Decimal("1.9731")
    assert [item.symbol for item in result.stock_pledge.collateral_holdings] == [
        "0050",
        "3293",
    ]


def test_financing_renderer_cards_text_order_and_empty_state() -> None:
    financing = ReportSectionService.financing(
        financing_liabilities(),
        Decimal("4262330"),
        Decimal("2669330"),
        Decimal("0.3732"),
    )
    assert isinstance(financing, FinancingLeverageSection)
    sections = DailyReportSections(
        allocation=PortfolioAllocationSection((), (), (), ()),
        upcoming_events=UpcomingEventsSection(status="No upcoming events"),
        dividend_calendar=DividendCalendarSection(
            items=(
                DividendCalendarItem(
                    "2330",
                    "TSMC",
                    date(2026, 8, 5),
                    None,
                    None,
                    "Cash dividend",
                    Decimal("1"),
                    Decimal("1"),
                    Decimal("1"),
                    Decimal("0"),
                    "Unknown Payment Date",
                    "official",
                ),
            )
        ),
        financing_leverage=financing,
        market_snapshot=MarketSnapshotSection(status="Market data unavailable"),
    )
    renderer = DailyReportSectionRenderer()
    html = renderer.html(sections)
    text = renderer.text(sections)
    financing_html = renderer.financing_html(sections)

    assert html.index("Portfolio Allocation") < html.index("Upcoming Events")
    assert html.index("Dividend Calendar") < html.index("Financing &amp; Leverage")
    assert html.index("Financing &amp; Leverage") < html.index("Market Snapshot")
    assert "NT$1,593,000" in html
    assert "NT$30,951" in html
    assert "NT$1,623,951" in html
    assert "24,000 shares" in html
    assert "width:33.33%" not in financing_html
    assert "width:50%" not in financing_html
    assert financing_html.count("max-width:100%;table-layout:fixed") == 3
    assert financing_html.index("Overall Financing") < financing_html.index(
        "Margin Financing"
    )
    assert financing_html.index("Margin Financing") < financing_html.index(
        "Stock Pledge"
    )
    assert "0050 — 4,000 shares" in text
    assert "Maintenance Ratio: 197.31%" in text
    assert DailyReportSectionRenderer().financing_html(DailyReportSections()) == ""
    assert (
        ReportSectionService.financing([], Decimal("0"), Decimal("0"), Decimal("0"))
        is None
    )


def test_financing_renderer_shows_only_present_liability_subsection() -> None:
    financing = ReportSectionService.financing(
        financing_liabilities()[:1],
        Decimal("1000000"),
        Decimal("405000"),
        Decimal("0.595"),
    )
    html = DailyReportSectionRenderer.financing_html(
        DailyReportSections(financing_leverage=financing)
    )

    assert "Overall Financing" in html
    assert "Margin Financing" in html
    assert "Stock Pledge" not in html


def test_upcoming_events_are_sorted_cut_off_and_preserve_relevance() -> None:
    start = date(2026, 7, 22)
    external = (
        UpcomingEventItem(
            start + timedelta(days=10), "Earnings", "2330", "Results", True, "provider"
        ),
        UpcomingEventItem(
            start + timedelta(days=31),
            "Economic",
            "Taiwan",
            "Outside",
            False,
            "provider",
        ),
        UpcomingEventItem(
            start + timedelta(days=2), "Economic", "Taiwan", "GDP", False, "provider"
        ),
    )
    result = ReportSectionService.upcoming(
        start, 30, DividendCalendarSection(), {"2330"}, external
    )
    assert [item.title for item in result.items] == ["GDP", "Results"]
    assert result.items[1].relevant_to_holding is True


class _ListRepository:
    def __init__(self, values=()):
        self.values = list(values)

    def list_all(self):
        return self.values

    def list_filtered(self, **filters: object):
        del filters
        return self.values


class _Quotes:
    def get_latest(self, symbol, market):
        return None


class _FailingMarketProvider:
    def get_snapshot(self, report_date):
        raise TimeoutError("provider unavailable")


def test_optional_provider_failure_is_isolated(caplog) -> None:
    use_case = BuildReportSectionsUseCase(
        _ListRepository(),
        _ListRepository(),
        _ListRepository(),
        _Quotes(),
        _ListRepository(),
        ReportSectionSettings(),
        market_provider=_FailingMarketProvider(),
    )
    result = use_case.execute(date(2026, 7, 22), [], Decimal("0"))
    assert result.market_snapshot is not None
    assert result.market_snapshot.items == ()
    assert "provider failure" in result.market_snapshot.status
    assert "optional report section failed" in caplog.text


def test_dividend_scope_periods_cover_current_year_next_90_and_all() -> None:
    report_date = date(2026, 8, 3)

    def use_case(scope):
        return BuildReportSectionsUseCase(
            _ListRepository(),
            _ListRepository(),
            _ListRepository(),
            _Quotes(),
            _ListRepository(),
            ReportSectionSettings(dividend_scope=scope),
        )

    assert use_case("current_year")._dividend_period(report_date) == (
        date(2026, 1, 1),
        date(2026, 12, 31),
    )
    assert use_case("next_90_days")._dividend_period(report_date) == (
        report_date,
        report_date + timedelta(days=90),
    )
    assert use_case("all")._dividend_period(report_date) == (None, None)


def test_section_settings_can_disable_every_section() -> None:
    disabled = ReportSectionSettings(
        show_allocation=False,
        show_market_snapshot=False,
        show_upcoming_events=False,
        show_dividends=False,
        show_ai_news=False,
        show_semiconductor_news=False,
        show_insights=False,
        show_risk=False,
        show_watchlist=False,
        show_transactions=False,
    )
    result = BuildReportSectionsUseCase(
        _ListRepository(),
        _ListRepository(),
        _ListRepository(),
        _Quotes(),
        _ListRepository(),
        disabled,
    ).execute(date(2026, 7, 22), [], Decimal("0"))
    assert result == DailyReportSections()


def test_application_builder_loads_financing_with_snapshot_totals() -> None:
    result = BuildReportSectionsUseCase(
        _ListRepository(),
        _ListRepository(),
        _ListRepository(),
        _Quotes(),
        _ListRepository(),
        ReportSectionSettings(),
        liabilities=_ListRepository(financing_liabilities()),
    ).execute(
        date(2026, 8, 5),
        [],
        Decimal("0"),
        total_market_value=Decimal("4262330"),
        net_asset_value=Decimal("2669330"),
        liability_ratio=Decimal("0.3732"),
    )

    assert result.financing_leverage is not None
    assert result.financing_leverage.total_principal_debt == Decimal("1593000")


def test_taiwan_performance_color_helper_preserves_semantic_direction() -> None:
    assert taiwan_performance_color(Decimal("1")) == TAIWAN_GAIN_COLOR
    assert taiwan_performance_color(Decimal("-1")) == TAIWAN_LOSS_COLOR
    assert taiwan_performance_color(Decimal("0")) == NEUTRAL_COLOR
    assert taiwan_performance_color(None) == NEUTRAL_COLOR
    assert taiwan_performance_color_from_text("2330: NT$200") == TAIWAN_GAIN_COLOR
    assert taiwan_performance_color_from_text("2330: NT$-200") == TAIWAN_LOSS_COLOR
    assert taiwan_performance_color_from_text("N/A") == NEUTRAL_COLOR


def test_market_snapshot_uses_taiwan_colors_without_changing_values() -> None:
    quoted_at = datetime(2026, 7, 22, tzinfo=UTC)
    section = MarketSnapshotSection(
        (
            MarketSnapshotItem(
                "Gain",
                Decimal("100"),
                Decimal("2"),
                Decimal("0.02"),
                quoted_at,
                "available",
            ),
            MarketSnapshotItem(
                "Loss",
                Decimal("90"),
                Decimal("-3"),
                Decimal("-0.03"),
                quoted_at,
                "available",
            ),
            MarketSnapshotItem(
                "Neutral", Decimal("90"), Decimal("0"), None, quoted_at, "available"
            ),
        )
    )
    html = DailyReportSectionRenderer().market_snapshot_html(
        DailyReportSections(market_snapshot=section)
    )
    assert f'color:{TAIWAN_GAIN_COLOR};font-weight:600">2</td>' in html
    assert f'color:{TAIWAN_LOSS_COLOR};font-weight:600">-3</td>' in html
    assert f'color:{NEUTRAL_COLOR};font-weight:600">0</td>' in html
    assert f'color:{NEUTRAL_COLOR};font-weight:600">N/A</td>' in html

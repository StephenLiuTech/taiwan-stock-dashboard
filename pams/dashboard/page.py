"""Dashboard 2.0 rendering over one application valuation result."""

import streamlit as st

from pams.analytics_reporting import analytics_error_message, analytics_view_model
from pams.application import (
    AnalyticsDataUnavailableError,
    AnalyticsProcessingError,
    AnalyticsRepositoryError,
    AnalyzePortfolioUseCase,
    InvalidAnalyticsPeriodError,
    MissingQuoteError,
    PortfolioAnalytics,
    PortfolioValuation,
    ValuatePortfolioUseCase,
    ValuationRepositoryError,
)
from pams.dashboard.charts import allocation_chart, daily_returns_chart
from pams.dashboard.formatting import kpi_view_model
from pams.dashboard.tables import (
    holdings_table_rows,
    largest_position_rows,
    performance_rows,
)


@st.cache_data(ttl=60, show_spinner=False)
def _load_valuation(_use_case: ValuatePortfolioUseCase) -> PortfolioValuation:
    """Execute the application workflow once per cached dashboard load."""
    return _use_case.execute()


@st.cache_data(ttl=60, show_spinner=False)
def _load_analytics(_use_case: AnalyzePortfolioUseCase) -> PortfolioAnalytics:
    """Execute the analytics application workflow once per cached page load."""
    return _use_case.execute()


def render_dashboard(
    valuation_use_case: ValuatePortfolioUseCase,
    analytics_use_case: AnalyzePortfolioUseCase,
) -> None:
    """Render application-provided values without business calculations."""
    st.title("PAMS")
    st.caption("Personal Asset Management System")

    if st.button("Reload dashboard", width="stretch"):
        st.cache_data.clear()
        st.rerun()

    try:
        valuation = _load_valuation(valuation_use_case)
    except MissingQuoteError:
        st.info("Portfolio valuation requires a current quote for every holding.")
        return
    except ValuationRepositoryError:
        st.error("Portfolio valuation could not be loaded.")
        return

    st.caption(f"Valuation date: {valuation.valuation_date or '—'}")

    st.subheader("Portfolio Summary")
    columns = st.columns(4)
    for column, (label, value) in zip(columns, kpi_view_model(valuation), strict=True):
        column.metric(label, value)

    st.subheader("Largest Positions")
    largest = largest_position_rows(valuation)
    if largest:
        st.dataframe(largest, width="stretch", hide_index=True)
    else:
        st.info("No valued holdings are available yet.")

    st.subheader("Allocation")
    allocation = allocation_chart(valuation)
    if allocation is None:
        st.info("Allocation is unavailable until valued holdings exist.")
    else:
        st.plotly_chart(allocation, width="stretch")

    winners_column, losers_column = st.columns(2)
    with winners_column:
        st.subheader("Top Winners")
        winners = performance_rows(valuation, winners=True)
        if winners:
            st.dataframe(winners, width="stretch", hide_index=True)
        else:
            st.info("No holdings are available.")
    with losers_column:
        st.subheader("Top Losers")
        losers = performance_rows(valuation, winners=False)
        if losers:
            st.dataframe(losers, width="stretch", hide_index=True)
        else:
            st.info("No holdings are available.")

    st.subheader("Portfolio Table")
    holdings = holdings_table_rows(valuation)
    if holdings:
        st.dataframe(
            holdings,
            width="stretch",
            hide_index=True,
            column_config={
                "Average Cost": st.column_config.NumberColumn(format="NT$ %.2f"),
                "Last Price": st.column_config.NumberColumn(format="NT$ %.2f"),
                "Cost Basis": st.column_config.NumberColumn(format="NT$ %.0f"),
                "Market Value": st.column_config.NumberColumn(format="NT$ %.0f"),
                "Unrealized": st.column_config.NumberColumn(format="NT$ %.0f"),
                "Return %": st.column_config.NumberColumn(format="percent"),
            },
        )
    else:
        st.info("No holdings are available.")

    st.subheader("Portfolio Analytics")
    try:
        analytics = analytics_view_model(_load_analytics(analytics_use_case))
    except (
        AnalyticsDataUnavailableError,
        InvalidAnalyticsPeriodError,
        AnalyticsRepositoryError,
        AnalyticsProcessingError,
    ) as error:
        st.info(analytics_error_message(error))
        return

    st.caption(f"Period: {analytics.period} · {analytics.snapshot_count} snapshots")
    analytics_columns = st.columns(4)
    analytics_metrics = (
        ("Starting Value", analytics.starting_value),
        ("Ending Value", analytics.ending_value),
        ("Profit / Loss", analytics.absolute_profit_loss),
        ("Total Return", analytics.total_return),
    )
    for column, (label, value) in zip(
        analytics_columns, analytics_metrics, strict=True
    ):
        column.metric(label, value)

    risk_columns = st.columns(3)
    risk_metrics = (
        ("Peak Value", analytics.peak_value),
        ("Trough Value", analytics.trough_value),
        ("Maximum Drawdown", analytics.max_drawdown),
    )
    for column, (label, value) in zip(risk_columns, risk_metrics, strict=True):
        column.metric(label, value)

    daily_returns = daily_returns_chart(analytics)
    if daily_returns is None:
        st.info("Daily returns require at least two portfolio snapshots.")
    else:
        st.plotly_chart(daily_returns, width="stretch")

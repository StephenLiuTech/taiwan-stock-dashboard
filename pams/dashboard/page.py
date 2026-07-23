"""Dashboard 2.0 rendering over one application valuation result."""

import streamlit as st

from pams.application import (
    PortfolioValuation,
    ValuatePortfolioUseCase,
)
from pams.dashboard.charts import allocation_chart
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


def render_dashboard(valuation_use_case: ValuatePortfolioUseCase) -> None:
    """Render application-provided values without business calculations."""
    st.title("PAMS")
    st.caption("Personal Asset Management System")

    if st.button("Reload dashboard", width="stretch"):
        st.cache_data.clear()
        st.rerun()

    try:
        valuation = _load_valuation(valuation_use_case)
    except Exception as error:
        st.error(f"Portfolio valuation is unavailable: {error}")
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

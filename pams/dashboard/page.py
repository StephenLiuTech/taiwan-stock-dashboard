"""Single-page Streamlit rendering over application use cases."""

import streamlit as st

from pams.application import (
    PortfolioHistory,
    PortfolioHistoryUseCase,
    PortfolioOverview,
    PortfolioStatusUseCase,
    UpdateMode,
    UpdatePortfolioUseCase,
)
from pams.dashboard.charts import allocation_chart, history_chart
from pams.dashboard.formatting import kpi_view_model
from pams.dashboard.tables import holdings_table_rows


@st.cache_data(ttl=60, show_spinner=False)
def _load_overview(_use_case: PortfolioStatusUseCase) -> PortfolioOverview:
    return _use_case.execute()


@st.cache_data(ttl=60, show_spinner=False)
def _load_history(_use_case: PortfolioHistoryUseCase) -> PortfolioHistory:
    return _use_case.execute()


def render_dashboard(
    status_use_case: PortfolioStatusUseCase,
    history_use_case: PortfolioHistoryUseCase,
    update_use_case: UpdatePortfolioUseCase,
) -> None:
    """Render the complete dashboard without accessing infrastructure."""
    st.title("PAMS")
    st.caption("Personal Asset Management System")

    actions = st.columns(2)
    if actions[0].button("Refresh data", width="stretch"):
        try:
            result = update_use_case.execute(dry_run=True)
            if result.mode is UpdateMode.SOURCES_UNSYNCHRONIZED:
                st.warning(
                    "Official market sources are not yet synchronized. "
                    "No automatic update is available."
                )
            else:
                st.success("Dry-run completed. No data was persisted.")
        except Exception as error:
            st.error(f"Refresh unavailable: {error}")
    if actions[1].button("Reload dashboard", width="stretch"):
        st.cache_data.clear()
        st.rerun()

    try:
        overview = _load_overview(status_use_case)
    except Exception as error:
        st.error(f"Portfolio data is unavailable: {error}")
        return

    snapshot = overview.latest_daily_snapshot
    st.caption(
        f"Latest portfolio snapshot: {snapshot or '—'} · "
        f"Database schema: {overview.schema_version or '—'}"
    )

    columns = st.columns(6)
    for column, (label, value) in zip(columns, kpi_view_model(overview), strict=True):
        column.metric(label, value)

    st.subheader("Holdings")
    rows = holdings_table_rows(overview)
    if rows:
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.info("No valued holdings are available yet.")

    left, right = st.columns(2)
    with left:
        st.subheader("Asset Allocation")
        allocation = allocation_chart(overview)
        if allocation is None:
            st.info("Allocation is unavailable until quote data exists.")
        else:
            st.plotly_chart(allocation, width="stretch")
    with right:
        st.subheader("Market Availability")
        availability = overview.market_availability
        st.write(f"TWSE latest source date: {availability.twse_latest_date or '—'}")
        st.write(f"TPEx latest source date: {availability.tpex_latest_date or '—'}")
        st.write(
            "Commonly ingestible date: "
            f"{availability.commonly_ingestible_date or '—'}"
        )
        if not availability.source_dates_available:
            st.warning("Official market availability is temporarily unavailable.")
        elif availability.synchronized:
            st.success("Official market sources are synchronized.")
        else:
            st.warning(
                "Official market sources are not yet synchronized. "
                "No automatic update is available."
            )

    st.subheader("Portfolio History")
    try:
        history = _load_history(history_use_case)
    except Exception as error:
        st.warning(f"Portfolio history is unavailable: {error}")
    else:
        chart = history_chart(history)
        if chart is None:
            st.info("No portfolio history is available yet.")
        else:
            st.plotly_chart(chart, width="stretch")

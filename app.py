"""Streamlit entry point for the PAMS portfolio dashboard."""

import argparse
from collections.abc import Sequence
from pathlib import Path

import streamlit as st

from pams.composition import compose_operations
from pams.dashboard import render_dashboard


def parse_database_override(argv: Sequence[str] | None = None) -> Path | None:
    """Parse only dashboard-specific arguments forwarded by Streamlit."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--database", type=Path)
    arguments, _ = parser.parse_known_args(argv)
    return arguments.database


def main(argv: Sequence[str] | None = None) -> None:
    """Compose application use cases and render the dashboard."""
    st.set_page_config(page_title="PAMS", page_icon="📊", layout="wide")
    try:
        with compose_operations(parse_database_override(argv)) as application:
            assert application.portfolio_history is not None
            render_dashboard(
                application.portfolio_status,
                application.portfolio_history,
                application.update_portfolio,
            )
    except Exception as error:
        st.title("PAMS")
        st.caption("Personal Asset Management System")
        st.error(f"Dashboard unavailable: {error}")


if __name__ == "__main__":
    main()

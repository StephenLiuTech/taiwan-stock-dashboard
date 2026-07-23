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
            assert application.valuate_portfolio is not None
            assert application.analyze_portfolio is not None
            render_dashboard(
                application.valuate_portfolio,
                application.analyze_portfolio,
            )
    except Exception:
        st.title("PAMS")
        st.caption("Personal Asset Management System")
        st.error("Dashboard unavailable. Check configuration and database access.")


if __name__ == "__main__":
    main()

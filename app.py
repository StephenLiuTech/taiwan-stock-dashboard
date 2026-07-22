"""Streamlit entry point for PAMS."""

import streamlit as st

from config import get_settings, load_app_config, load_logging_config
from core.logging import configure_logging
from database import initialize_database
from repositories import SQLiteHoldingRepository, SQLiteLiabilityRepository
from services import BootstrapService


def main() -> None:
    """Compose and render the PAMS application shell."""
    settings = get_settings()
    app_config = load_app_config()
    logging_config = load_logging_config()
    configure_logging(settings.log_level or logging_config.level, logging_config.format)

    with initialize_database(settings.database_url) as connection:
        BootstrapService(
            connection,
            SQLiteHoldingRepository(connection),
            SQLiteLiabilityRepository(connection),
        ).initialize()

    title = settings.app_title or app_config.application.name
    st.set_page_config(page_title=title, page_icon="📊", layout="wide")
    st.title(title)
    st.info("PAMS v0.2 domain and local persistence are initialized.")


if __name__ == "__main__":
    main()

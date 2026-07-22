"""Smoke tests for the Streamlit application."""

import importlib

from streamlit.testing.v1 import AppTest


def test_application_imports() -> None:
    """The application composition root imports without errors."""
    application = importlib.import_module("app")

    assert callable(application.main)


def test_streamlit_application_starts() -> None:
    """Streamlit can execute the application entry point."""
    app = AppTest.from_file("app.py").run()

    assert not app.exception
    assert app.title[0].value == "PAMS"

"""Smoke tests for the Streamlit application."""

import importlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

import app
from config import get_settings
from pams.application import DemoDataUseCase


def test_application_imports() -> None:
    """The application composition root imports without errors."""
    application = importlib.import_module("app")

    assert callable(application.main)


def test_streamlit_application_starts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Streamlit starts against isolated local data without external access."""
    for name in tuple(os.environ):
        if name.startswith("PAMS_"):
            monkeypatch.delenv(name)
    database_path = tmp_path / "dashboard.db"
    DemoDataUseCase(tmp_path / "production.db").execute(database_path)
    monkeypatch.setenv("PAMS_ENVIRONMENT", "test")
    monkeypatch.setenv("PAMS_DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()

    try:
        application = AppTest.from_file("app.py").run()

        assert not application.exception
        assert application.title[0].value == "PAMS"
    finally:
        get_settings.cache_clear()


def test_entry_point_passes_current_application_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The entry point passes the composed valuation and analytics use cases."""
    valuation_use_case = object()
    analytics_use_case = object()
    received: list[tuple[object, object]] = []

    @contextmanager
    def fake_compose(_database: object) -> Iterator[SimpleNamespace]:
        yield SimpleNamespace(
            valuate_portfolio=valuation_use_case,
            analyze_portfolio=analytics_use_case,
        )

    monkeypatch.setattr(app, "compose_operations", fake_compose)
    monkeypatch.setattr(
        app,
        "render_dashboard",
        lambda valuation, analytics: received.append((valuation, analytics)),
    )
    monkeypatch.setattr(app.st, "set_page_config", lambda **_kwargs: None)

    app.main([])

    assert received == [(valuation_use_case, analytics_use_case)]

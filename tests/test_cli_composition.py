"""Offline integration tests for CLI composition and persistence modes."""

import sqlite3
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

import pams.composition
from config import get_settings
from domain import Market
from market_data.transport import JSONRecord
from pams.application import AddTransactionCommand
from pams.composition import (
    compose_application,
    compose_daily_report,
    compose_ledger_operations,
    resolve_database_path,
)
from services import DuplicateSnapshotError


class StaticProvider:
    """Official-shape deterministic provider for composition tests."""

    def __init__(self, market: Market, records: Sequence[JSONRecord]) -> None:
        self.market = market
        self.source = f"{market.value}-test"
        self.records = records

    def fetch(self) -> Sequence[JSONRecord]:
        return self.records


def providers(source_date: str = "1150722") -> tuple[StaticProvider, ...]:
    return (
        StaticProvider(
            Market.TWSE,
            [
                {"Date": source_date, "Code": symbol, "ClosingPrice": "100"}
                for symbol in ("0050", "2027", "2330")
            ],
        ),
        StaticProvider(
            Market.TPEX,
            [
                {
                    "Date": source_date,
                    "SecuritiesCompanyCode": symbol,
                    "Close": "100",
                }
                for symbol in ("3293", "8299")
            ],
        ),
    )


def row_count(connection: sqlite3.Connection, table: str) -> int:
    return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_configured_database_path_is_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configured = tmp_path / "configured.db"
    monkeypatch.setenv("PAMS_DATABASE_URL", f"sqlite:///{configured.as_posix()}")
    get_settings.cache_clear()
    try:
        assert resolve_database_path() == configured.resolve()
    finally:
        get_settings.cache_clear()


def test_override_database_path_and_bootstrap_are_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "override.db"
    with compose_application(database, providers=providers()) as first:
        assert first.database_path == database.resolve()
        assert first.seeded is True
        assert row_count(first.connection, "holdings") == 5
    with compose_application(database, providers=providers()) as second:
        assert second.seeded is False
        assert row_count(second.connection, "holdings") == 5
        assert row_count(second.connection, "liabilities") == 2


def test_ledger_composition_initializes_schema_without_bootstrap(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.db"
    with compose_ledger_operations(database, providers=providers()) as application:
        assert application.seeded is False
        assert row_count(application.connection, "holdings") == 0
        assert row_count(application.connection, "liabilities") == 0
        assert row_count(application.connection, "transactions") == 0


def test_dry_run_validates_but_does_not_write_market_tables(tmp_path: Path) -> None:
    database = tmp_path / "dry-run.db"
    with compose_application(database, providers=providers()) as application:
        result = application.engine.preview(date(2026, 7, 22))
        assert len(result.quotes) == 5
        assert row_count(application.connection, "price_quotes") == 0
        assert row_count(application.connection, "daily_snapshots") == 0
        assert row_count(application.connection, "position_snapshots") == 0


def test_application_update_is_idempotent_and_engine_protects_duplicate(
    tmp_path: Path,
) -> None:
    database = tmp_path / "persisted.db"
    with compose_application(database, providers=providers()) as application:
        first = application.update_portfolio.execute()
        assert first.mode.value == "updated"
        assert row_count(application.connection, "price_quotes") == 5
        assert row_count(application.connection, "daily_snapshots") == 1
        assert row_count(application.connection, "position_snapshots") == 5

        repeated = application.update_portfolio.execute()
        assert repeated.mode.value == "no_update_snapshot_exists"
        assert row_count(application.connection, "price_quotes") == 5
        assert row_count(application.connection, "daily_snapshots") == 1
        assert row_count(application.connection, "position_snapshots") == 5

        with pytest.raises(DuplicateSnapshotError):
            application.engine.refresh(date(2026, 7, 22))


def test_update_projects_all_same_day_transactions_into_shared_position(
    tmp_path: Path,
) -> None:
    database = tmp_path / "transaction-projection.db"
    trade_date = date(2026, 7, 24)
    with compose_application(database, providers=providers("1150724")) as application:
        assert application.add_transaction is not None
        application.add_transaction.execute(
            AddTransactionCommand(
                symbol="0050",
                market="TWSE",
                transaction_type="buy",
                trade_date=trade_date,
                settlement_date=trade_date,
                quantity=Decimal("5800"),
                price=Decimal("83.95"),
                fees=Decimal("0"),
                taxes=Decimal("0"),
                currency="TWD",
                transaction_id="0050-initial",
            )
        )
        first = application.update_portfolio.execute()
        assert next(
            item for item in first.positions if item.symbol == "0050"
        ).shares == (Decimal("5800"))

        application.add_transaction.execute(
            AddTransactionCommand(
                symbol="0050",
                market="TWSE",
                transaction_type="buy",
                trade_date=trade_date,
                settlement_date=trade_date,
                quantity=Decimal("100"),
                price=Decimal("101.70"),
                fees=Decimal("0"),
                taxes=Decimal("0"),
                currency="TWD",
                transaction_id="0050-additional",
            )
        )
        skipped = application.update_portfolio.execute()
        assert skipped.mode.value == "no_update_snapshot_exists"
        stale_position = application.connection.execute(
            """SELECT quantity FROM position_snapshots
               WHERE snapshot_date = ? AND symbol = ?""",
            (trade_date.isoformat(), "0050"),
        ).fetchone()
        assert Decimal(stale_position[0]) == Decimal("5800")

        result = application.update_portfolio.execute(force=True)
        position = next(item for item in result.positions if item.symbol == "0050")
        assert position.shares == Decimal("5900")
        assert position.average_cost == Decimal("497080") / Decimal("5900")
        snapshot_position = application.connection.execute(
            """SELECT quantity, average_cost, cost_basis
               FROM position_snapshots
               WHERE snapshot_date = ? AND symbol = ?""",
            (trade_date.isoformat(), "0050"),
        ).fetchone()
        assert tuple(Decimal(value) for value in snapshot_position) == (
            Decimal("5900"),
            Decimal("497080") / Decimal("5900"),
            Decimal("497080"),
        )

        assert application.valuate_portfolio is not None
        valuation = application.valuate_portfolio.execute()
        valued = next(item for item in valuation.holdings if item.symbol == "0050")
        assert valued.quantity == Decimal("5900")
        assert valued.average_cost == Decimal("497080") / Decimal("5900")
        assert valued.cost_basis == Decimal("497080")


def test_dashboard_use_cases_are_composed_for_populated_database(
    tmp_path: Path,
) -> None:
    database = tmp_path / "dashboard.db"
    with compose_application(database, providers=providers()) as application:
        application.update_portfolio.execute()
        assert application.valuate_portfolio is not None
        assert application.analyze_portfolio is not None
        valuation = application.valuate_portfolio.execute()
        analytics = application.analyze_portfolio.execute()

    assert valuation.valuation_date == date(2026, 7, 22)
    assert analytics.end_date == date(2026, 7, 22)


def test_daily_report_production_composition_dry_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = tmp_path / "daily-report.db"
    monkeypatch.setenv("PAMS_EMAIL_FROM", "sender@example.com")
    monkeypatch.setenv("PAMS_EMAIL_TO", "recipient@example.com")
    get_settings.cache_clear()
    try:
        with compose_daily_report(
            database, dry_run=True, providers=providers()
        ) as application:
            application.engine.refresh(date(2026, 7, 22))
            assert application.send_daily_report is not None
            result = application.send_daily_report.execute(
                date(2026, 7, 22), dry_run=True
            )
            delivery_count = row_count(application.connection, "report_deliveries")
        assert result.status == "dry_run"
        assert result.recipient == "recipient@example.com"
        assert delivery_count == 0
    finally:
        get_settings.cache_clear()


def test_daily_report_production_composition_selects_microsoft_graph(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = tmp_path / "graph-report.db"
    cache_path = tmp_path / "msal-cache.json"
    created: dict[str, object] = {}

    class AuthenticatorStub:
        def __init__(self, client_id: str, tenant: str, path: Path) -> None:
            created["authenticator"] = (client_id, tenant, path)

    class TransportStub:
        def __init__(self, authenticator: object) -> None:
            created["transport"] = authenticator

        def send(self, envelope: object) -> None:
            created["envelope"] = envelope

    monkeypatch.setattr(
        pams.composition, "MicrosoftGraphAuthenticator", AuthenticatorStub
    )
    monkeypatch.setattr(pams.composition, "MicrosoftGraphEmailTransport", TransportStub)
    monkeypatch.setenv("PAMS_EMAIL_TRANSPORT", "microsoft_graph")
    monkeypatch.setenv("PAMS_MICROSOFT_CLIENT_ID", "public-client-id")
    monkeypatch.setenv("PAMS_MICROSOFT_TENANT", "consumers")
    monkeypatch.setenv("PAMS_MICROSOFT_TOKEN_CACHE", str(cache_path))
    monkeypatch.setenv("PAMS_EMAIL_FROM", "sender@hotmail.com")
    monkeypatch.setenv("PAMS_EMAIL_TO", "recipient@example.com")
    get_settings.cache_clear()
    try:
        with compose_daily_report(database, providers=providers()) as application:
            application.engine.refresh(date(2026, 7, 22))
            assert application.send_daily_report is not None
            result = application.send_daily_report.execute(date(2026, 7, 22))
        assert result.status == "sent"
        assert created["authenticator"] == (
            "public-client-id",
            "consumers",
            cache_path.resolve(),
        )
        assert "envelope" in created
    finally:
        get_settings.cache_clear()


def test_daily_report_production_composition_selects_resend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = tmp_path / "resend-report.db"
    created: dict[str, object] = {}

    class TransportStub:
        def __init__(self, api_key: str) -> None:
            created["api_key"] = api_key

        def send(self, envelope: object) -> None:
            created["envelope"] = envelope

    class AssetStoreStub:
        def __init__(self, url: str, key: str, bucket: str, prefix: str | None) -> None:
            created["asset_store"] = (url, key, bucket, prefix)

        def publish(self, content: bytes, content_type: str, object_name: str) -> str:
            created["published"] = (content, content_type, object_name)
            return (
                "https://project.supabase.co/storage/v1/object/public/"
                "bucket/prefix/chart.png"
            )

    monkeypatch.setattr(pams.composition, "ResendEmailTransport", TransportStub)
    monkeypatch.setattr(pams.composition, "SupabaseReportAssetStore", AssetStoreStub)
    monkeypatch.setenv("PAMS_EMAIL_TRANSPORT", "resend")
    monkeypatch.setenv("PAMS_RESEND_API_KEY", "re_test-key")
    monkeypatch.setenv("PAMS_SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("PAMS_SUPABASE_SERVICE_ROLE_KEY", "service-role-key")
    monkeypatch.setenv("PAMS_REPORT_ASSET_BUCKET", "report-assets")
    monkeypatch.setenv("PAMS_REPORT_ASSET_PREFIX", "random-prefix")
    monkeypatch.setenv("PAMS_EMAIL_FROM", "reports@example.com")
    monkeypatch.setenv("PAMS_EMAIL_TO", "recipient@example.com")
    get_settings.cache_clear()
    try:
        with compose_daily_report(database, providers=providers()) as application:
            application.engine.refresh(date(2026, 7, 22))
            assert application.send_daily_report is not None
            result = application.send_daily_report.execute(date(2026, 7, 22))
        assert result.status == "sent"
        assert created["api_key"] == "re_test-key"
        assert created["asset_store"] == (
            "https://project.supabase.co",
            "service-role-key",
            "report-assets",
            "random-prefix",
        )
        assert "envelope" in created
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(
    ("transport", "configured", "missing", "display_name"),
    [
        (
            "resend",
            {},
            "PAMS_RESEND_API_KEY",
            "Resend",
        ),
        (
            "smtp",
            {
                "PAMS_SMTP_USERNAME": "user",
                "PAMS_SMTP_PASSWORD": "secret",
            },
            "PAMS_SMTP_HOST",
            "SMTP",
        ),
        (
            "microsoft_graph",
            {},
            "PAMS_MICROSOFT_CLIENT_ID",
            "Microsoft Graph",
        ),
    ],
)
def test_daily_report_validates_only_selected_transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transport: str,
    configured: dict[str, str],
    missing: str,
    display_name: str,
) -> None:
    monkeypatch.setenv("PAMS_EMAIL_TRANSPORT", transport)
    monkeypatch.setenv("PAMS_EMAIL_FROM", "reports@example.com")
    monkeypatch.setenv("PAMS_EMAIL_TO", "recipient@example.com")
    for name in (
        "PAMS_RESEND_API_KEY",
        "PAMS_SUPABASE_URL",
        "PAMS_SUPABASE_SERVICE_ROLE_KEY",
        "PAMS_REPORT_ASSET_BUCKET",
        "PAMS_REPORT_ASSET_PREFIX",
        "PAMS_SMTP_HOST",
        "PAMS_SMTP_USERNAME",
        "PAMS_SMTP_PASSWORD",
        "PAMS_MICROSOFT_CLIENT_ID",
    ):
        monkeypatch.setenv(name, "")
    for name, value in configured.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    try:
        with pytest.raises(
            ValueError,
            match=rf"Missing configuration for {display_name} transport:\n{missing}",
        ):
            with compose_daily_report(
                tmp_path / f"{transport}.db", providers=providers()
            ):
                pass
    finally:
        get_settings.cache_clear()


def test_operational_status_reports_database_state(tmp_path: Path) -> None:
    database = tmp_path / "status.db"
    with compose_application(database, providers=providers()) as application:
        application.engine.refresh(date(2026, 7, 22))
        status = application.status.read(application.calendar.market_availability())
        assert status.database_path == database.resolve()
        assert status.latest_quote_date == date(2026, 7, 22)
        assert status.latest_daily_snapshot == date(2026, 7, 22)
        assert status.latest_position_snapshot == date(2026, 7, 22)
        assert status.holdings_count == 5
        assert status.liabilities_count == 2
        assert status.schema_version == 4
        assert status.database_size_bytes > 0


def test_offline_operational_verification_passes(tmp_path: Path) -> None:
    database = tmp_path / "verify.db"
    with compose_application(database, providers=providers()) as application:
        report = application.verification.run()
        assert report.failed is False
        assert {check.name for check in report.checks} >= {
            "Configuration",
            "Database",
            "Schema",
            "Holdings",
            "Liabilities",
            "TWSE endpoint",
            "TPEx endpoint",
            "Market Calendar",
            "Market Data Engine",
        }


def test_offline_verification_fails_when_official_data_is_empty(
    tmp_path: Path,
) -> None:
    database = tmp_path / "verify-failure.db"
    failing_providers = (
        StaticProvider(Market.TWSE, []),
        StaticProvider(
            Market.TPEX,
            [{"Date": "1150722", "SecuritiesCompanyCode": "8299", "Close": "1"}],
        ),
    )
    with compose_application(database, providers=failing_providers) as application:
        report = application.verification.run()
        assert report.failed is True
        twse = next(check for check in report.checks if check.name == "TWSE endpoint")
        assert twse.level.value == "FAIL"


def test_verification_warns_when_official_market_dates_disagree(
    tmp_path: Path,
) -> None:
    database = tmp_path / "verify-warning.db"
    mixed_providers = (
        StaticProvider(
            Market.TWSE,
            [{"Date": "1150721", "Code": "2330", "ClosingPrice": "1"}],
        ),
        StaticProvider(
            Market.TPEX,
            [{"Date": "1150722", "SecuritiesCompanyCode": "8299", "Close": "1"}],
        ),
    )
    with compose_application(database, providers=mixed_providers) as application:
        report = application.verification.run()
        calendar = next(
            check for check in report.checks if check.name == "Market Calendar"
        )
        assert report.failed is False
        assert calendar.level.value == "WARN"
        assert "historical providers for 2026-07-21" in calendar.detail

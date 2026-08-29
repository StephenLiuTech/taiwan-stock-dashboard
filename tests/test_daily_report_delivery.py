"""Offline tests for daily portfolio report delivery."""

import json
import re
import smtplib
from dataclasses import replace
from datetime import date
from decimal import Decimal
from http.client import IncompleteRead
from io import BytesIO

import pytest
from PIL import Image

from domain import (
    AnnualPnlSnapshot,
    Currency,
    DailyReportSections,
    DailySnapshot,
    DividendCalendarItem,
    DividendCalendarSection,
    Holding,
    Market,
    PositionSnapshot,
    RealizedPnlBySymbol,
)
from market_calendar import MarketAvailability, MarketCalendarUnavailableError
from market_data import ProviderDataError
from market_data.transport import UrllibJSONDocumentTransport
from pams.application import (
    DailyReportDeliveryError,
    DailyReportSendResult,
    DailyReportSnapshotMissingError,
    SendDailyReportUseCase,
)
from pams.application.send_daily_report import EmailEnvelope, InlineImage
from pams.delivery import DailyEmailReportRenderer, SMTPEmailTransport
from pams.delivery.rendering import PORTFOLIO_TREND_SERIES


def snapshot(
    snapshot_date: date = date(2026, 7, 22),
    *,
    market_value: str = "1200",
    net_asset_value: str = "1100",
) -> DailySnapshot:
    return DailySnapshot(
        snapshot_date=snapshot_date,
        total_market_value=Decimal(market_value),
        total_cost_basis=Decimal("1000"),
        total_unrealized_pnl=Decimal("200"),
        total_liabilities=Decimal("100"),
        net_asset_value=Decimal(net_asset_value),
        leverage_ratio=Decimal("0.0833333333"),
        high_water_mark=Decimal("1100"),
        drawdown=Decimal("0"),
    )


def positions() -> list[PositionSnapshot]:
    return [
        PositionSnapshot(
            snapshot_date=date(2026, 7, 22),
            holding_id="h1",
            symbol="2330",
            quantity=Decimal("2"),
            average_cost=Decimal("400"),
            close_price=Decimal("500"),
            cost_basis=Decimal("800"),
            market_value=Decimal("1000"),
            unrealized_pnl=Decimal("200"),
            unrealized_return=Decimal("0.25"),
            portfolio_weight=Decimal("0.8333333333"),
            daily_value_change=Decimal("20"),
            daily_return=Decimal("0.02"),
        ),
        PositionSnapshot(
            snapshot_date=date(2026, 7, 22),
            holding_id="h2",
            symbol="8299",
            quantity=Decimal("2"),
            average_cost=Decimal("100"),
            close_price=Decimal("100"),
            cost_basis=Decimal("200"),
            market_value=Decimal("200"),
            unrealized_pnl=Decimal("0"),
            unrealized_return=Decimal("0"),
            portfolio_weight=Decimal("0.1666666667"),
            daily_value_change=Decimal("-10"),
            daily_return=Decimal("-0.05"),
        ),
    ]


class UpdateStub:
    def __init__(self) -> None:
        self.calls: list[tuple[date | None, bool, bool]] = []

    def execute(
        self,
        requested_date: date | None = None,
        *,
        dry_run: bool = False,
        force: bool = False,
    ) -> object:
        self.calls.append((requested_date, dry_run, force))
        resolved = requested_date or date(2026, 7, 22)
        return type(
            "Update",
            (),
            {
                "requested_date": resolved,
                "verified_source_date": resolved,
            },
        )()


class SnapshotStub:
    def __init__(
        self,
        value: DailySnapshot | None = None,
        history: list[DailySnapshot] | None = None,
    ) -> None:
        self.value = value
        self.history = history if history is not None else ([value] if value else [])

    def get_latest(self) -> DailySnapshot | None:
        return self.value

    def get_by_date(self, report_date: date) -> DailySnapshot | None:
        return (
            self.value
            if self.value and self.value.snapshot_date == report_date
            else None
        )

    def list_between_dates(self, start: date, end: date) -> list[DailySnapshot]:
        return [item for item in self.history if start <= item.snapshot_date <= end]


class PositionStub:
    def __init__(self, values: list[PositionSnapshot] | None = None) -> None:
        self.values = values if values is not None else positions()

    def list_by_date(self, report_date: date) -> list[PositionSnapshot]:
        assert report_date == date(2026, 7, 22)
        return self.values


class HoldingStub:
    def list_all(self) -> list[Holding]:
        return [
            Holding(
                id="h1",
                symbol="2330",
                name="TSMC",
                market=Market.TWSE,
                currency=Currency.TWD,
                quantity=Decimal("2"),
                average_cost=Decimal("400"),
            ),
            Holding(
                id="h2",
                symbol="8299",
                name="Phison",
                market=Market.TPEX,
                currency=Currency.TWD,
                quantity=Decimal("2"),
                average_cost=Decimal("100"),
            ),
        ]


class DeliveryStub:
    def __init__(self) -> None:
        self.status: str | None = None
        self.error: str | None = None

    def claim(self, *_args: object) -> bool:
        if self.status in {"SENT", "SENDING"}:
            return False
        self.status = "SENDING"
        return True

    def mark_sent(self, *_args: object) -> None:
        self.status = "SENT"
        self.error = None

    def mark_failed(self, *_args: object) -> None:
        self.status = "FAILED"
        self.error = str(_args[-1])


class TransportStub:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.messages: list[EmailEnvelope] = []

    def send(self, envelope: EmailEnvelope) -> None:
        if self.error:
            raise self.error
        self.messages.append(envelope)


class AssetStoreStub:
    def __init__(
        self,
        url: str = (
            "https://project.supabase.co/storage/v1/object/public/"
            "pams-report-assets/random-prefix/daily-report/"
            "2026-07-22/asset-change.png"
        ),
        error: Exception | None = None,
    ) -> None:
        self.url = url
        self.error = error
        self.calls: list[tuple[bytes, str, str]] = []

    def publish(self, content: bytes, content_type: str, object_name: str) -> str:
        self.calls.append((content, content_type, object_name))
        if self.error:
            raise self.error
        return self.url


def use_case(
    *,
    value: DailySnapshot | None = None,
    delivery: DeliveryStub | None = None,
    transport: TransportStub | None = None,
    history: list[DailySnapshot] | None = None,
    position_values: list[PositionSnapshot] | None = None,
    asset_store: AssetStoreStub | None = None,
) -> tuple[SendDailyReportUseCase, UpdateStub, DeliveryStub, TransportStub]:
    update = UpdateStub()
    deliveries = delivery or DeliveryStub()
    email = transport or TransportStub()
    return (
        SendDailyReportUseCase(
            update,  # type: ignore[arg-type]
            SnapshotStub(value, history),  # type: ignore[arg-type]
            PositionStub(position_values),  # type: ignore[arg-type]
            HoldingStub(),  # type: ignore[arg-type]
            deliveries,
            DailyEmailReportRenderer(),
            email,
            "sender@example.com",
            "recipient@example.com",
            asset_store,
        ),
        update,
        deliveries,
        email,
    )


def live_date_case(
    *,
    live_date: date,
    persisted_dates: tuple[date, ...],
    force: bool = False,
) -> tuple[DailyReportSendResult, list[tuple[date | None, bool, bool]], TransportStub]:
    snapshots_by_date = {
        value: snapshot(value, market_value="1200", net_asset_value="1100")
        for value in persisted_dates
    }
    calls: list[tuple[date | None, bool, bool]] = []

    class LiveUpdate:
        def execute(
            self,
            requested_date: date | None = None,
            *,
            dry_run: bool = False,
            force: bool = False,
        ) -> object:
            calls.append((requested_date, dry_run, force))
            if not dry_run and (force or live_date not in snapshots_by_date):
                snapshots_by_date[live_date] = snapshot(live_date)
            return type(
                "Update",
                (),
                {
                    "requested_date": live_date,
                    "verified_source_date": live_date,
                },
            )()

    class LiveSnapshots:
        def get_latest(self) -> DailySnapshot | None:
            return max(
                snapshots_by_date.values(),
                key=lambda item: item.snapshot_date,
                default=None,
            )

        def get_by_date(self, report_date: date) -> DailySnapshot | None:
            return snapshots_by_date.get(report_date)

        def list_between_dates(self, start: date, end: date) -> list[DailySnapshot]:
            return sorted(
                (
                    value
                    for value in snapshots_by_date.values()
                    if start <= value.snapshot_date <= end
                ),
                key=lambda value: value.snapshot_date,
            )

    class LivePositions:
        def list_by_date(self, report_date: date) -> list[PositionSnapshot]:
            return [
                value.model_copy(update={"snapshot_date": report_date})
                for value in positions()
            ]

    transport = TransportStub()
    case = SendDailyReportUseCase(
        LiveUpdate(),  # type: ignore[arg-type]
        LiveSnapshots(),  # type: ignore[arg-type]
        LivePositions(),  # type: ignore[arg-type]
        HoldingStub(),  # type: ignore[arg-type]
        DeliveryStub(),
        DailyEmailReportRenderer(),
        transport,
        "sender@example.com",
        "recipient@example.com",
    )
    return case.execute(force=force), calls, transport


def test_portfolio_summary_renders_expected_annual_dividend_card_and_text() -> None:
    case, _, _, _ = use_case(value=snapshot())
    report = case._build_report(snapshot(), date(2026, 7, 22))
    item = DividendCalendarItem(
        symbol="2330",
        name="TSMC",
        ex_dividend_date=date(2026, 3, 17),
        record_date=None,
        payment_date=date(2026, 4, 9),
        event="Cash dividend",
        dividend_per_share=Decimal("6"),
        eligible_quantity=Decimal("1000"),
        estimated_cash_dividend=Decimal("6000"),
        actual_cash_received=Decimal("6000"),
        status="Paid",
        source_status="official",
    )
    sections = DailyReportSections(
        dividend_calendar=DividendCalendarSection(
            items=(item,),
            estimated_annual_dividend=Decimal("9000"),
            already_received=Decimal("6000"),
        )
    )
    rendered = DailyEmailReportRenderer().render(replace(report, sections=sections))
    assert "Expected Annual Dividend" in rendered.html
    assert "Estimated<br><strong>NT$9,000</strong>" in rendered.html
    assert "Already Received<br><strong>NT$6,000</strong>" in rendered.html
    assert "Remaining<br><strong>NT$3,000</strong>" in rendered.html
    assert "Expected Annual Dividend" in rendered.plain_text
    assert "Remaining: NT$3,000" in rendered.plain_text
    width_wrapper = rendered.html.split(
        '<table role="presentation" class="pams-wide-tables"', maxsplit=1
    )[1].rsplit("</td></tr></table>\n</div></body>", maxsplit=1)[0]
    assert ">Today's Contributors</h2>" in width_wrapper
    assert ">Holdings</h2>" in width_wrapper
    assert ">Dividend Calendar</h2>" in width_wrapper
    assert width_wrapper.count('class="pams-responsive-table"') == 2


def test_automatic_report_uses_newer_live_date_than_persisted_snapshot() -> None:
    result, calls, transport = live_date_case(
        live_date=date(2026, 7, 27),
        persisted_dates=(date(2026, 7, 24),),
    )

    assert result.report_date == date(2026, 7, 27)
    assert calls == [(None, False, False)]
    assert "Report date: 2026-07-27" in transport.messages[0].plain_text


def test_forced_automatic_report_rebuilds_and_sends_live_date() -> None:
    result, calls, transport = live_date_case(
        live_date=date(2026, 7, 27),
        persisted_dates=(date(2026, 7, 24), date(2026, 7, 27)),
        force=True,
    )

    assert result.report_date == date(2026, 7, 27)
    assert calls == [(None, False, True)]
    assert "Report date: 2026-07-27" in transport.messages[0].plain_text


def test_automatic_report_preserves_existing_date_when_live_date_is_not_newer() -> None:
    result, calls, _ = live_date_case(
        live_date=date(2026, 7, 24),
        persisted_dates=(date(2026, 7, 24),),
    )

    assert result.report_date == date(2026, 7, 24)
    assert calls == [(None, False, False)]


def test_failed_live_date_resolution_prevents_report_delivery() -> None:
    class FailingUpdate:
        def execute(self, *_args: object, **_kwargs: object) -> object:
            raise ProviderDataError("TWSE live date resolution failed")

    transport = TransportStub()
    case = SendDailyReportUseCase(
        FailingUpdate(),  # type: ignore[arg-type]
        SnapshotStub(snapshot(date(2026, 7, 24))),  # type: ignore[arg-type]
        PositionStub(),  # type: ignore[arg-type]
        HoldingStub(),  # type: ignore[arg-type]
        DeliveryStub(),
        DailyEmailReportRenderer(),
        transport,
        "sender@example.com",
        "recipient@example.com",
    )

    with pytest.raises(ProviderDataError, match="live date resolution failed"):
        case.execute()
    assert transport.messages == []


def _temporary_calendar_failure(
    *, twse_date: date | None = None, tpex_date: date | None = None
) -> MarketCalendarUnavailableError:
    return MarketCalendarUnavailableError(
        MarketAvailability(
            twse_date=twse_date,
            tpex_date=tpex_date,
            twse_source="live_provider" if twse_date else "unavailable",
            tpex_source="live_provider" if tpex_date else "unavailable",
            twse_error=None if twse_date else "TemporaryProviderUnavailableError",
            tpex_error=None if tpex_date else "TemporaryProviderUnavailableError",
        )
    )


def test_complete_same_date_snapshot_sends_without_live_calendar_dependency() -> None:
    class UnexpectedUpdate:
        def execute(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("live calendar must not be required")

    transport = TransportStub()
    case = SendDailyReportUseCase(
        UnexpectedUpdate(),  # type: ignore[arg-type]
        SnapshotStub(snapshot()),  # type: ignore[arg-type]
        PositionStub(),  # type: ignore[arg-type]
        HoldingStub(),  # type: ignore[arg-type]
        DeliveryStub(),
        DailyEmailReportRenderer(),
        transport,
        "sender@example.com",
        "recipient@example.com",
        today=lambda: date(2026, 7, 22),
    )

    result = case.execute()

    assert result.status == "sent"
    assert result.report_date == date(2026, 7, 22)
    assert len(transport.messages) == 1


@pytest.mark.parametrize(
    ("twse_date", "tpex_date"),
    ((date(2026, 7, 23), None), (None, date(2026, 7, 23))),
)
def test_one_transient_source_uses_complete_persisted_snapshot_and_keeps_its_date(
    twse_date: date | None,
    tpex_date: date | None,
) -> None:
    class FailingUpdate:
        def execute(self, *_args: object, **_kwargs: object) -> object:
            raise _temporary_calendar_failure(twse_date=twse_date, tpex_date=tpex_date)

    transport = TransportStub()
    case = SendDailyReportUseCase(
        FailingUpdate(),  # type: ignore[arg-type]
        SnapshotStub(snapshot()),  # type: ignore[arg-type]
        PositionStub(),  # type: ignore[arg-type]
        HoldingStub(),  # type: ignore[arg-type]
        DeliveryStub(),
        DailyEmailReportRenderer(),
        transport,
        "sender@example.com",
        "recipient@example.com",
        today=lambda: date(2026, 7, 23),
    )

    result = case.execute()

    assert result.report_date == date(2026, 7, 22)
    assert "Report date: 2026-07-22" in transport.messages[0].plain_text
    assert "Report date: 2026-07-23" not in transport.messages[0].plain_text


def test_both_transient_sources_use_complete_persisted_snapshot() -> None:
    class FailingUpdate:
        def execute(self, *_args: object, **_kwargs: object) -> object:
            raise _temporary_calendar_failure()

    transport = TransportStub()
    case = SendDailyReportUseCase(
        FailingUpdate(),  # type: ignore[arg-type]
        SnapshotStub(snapshot()),  # type: ignore[arg-type]
        PositionStub(),  # type: ignore[arg-type]
        HoldingStub(),  # type: ignore[arg-type]
        DeliveryStub(),
        DailyEmailReportRenderer(),
        transport,
        "sender@example.com",
        "recipient@example.com",
        today=lambda: date(2026, 7, 23),
    )

    assert case.execute().status == "sent"
    assert len(transport.messages) == 1


def test_both_transient_sources_without_safe_snapshot_fail_explicitly() -> None:
    class FailingUpdate:
        def execute(self, *_args: object, **_kwargs: object) -> object:
            raise _temporary_calendar_failure()

    case = SendDailyReportUseCase(
        FailingUpdate(),  # type: ignore[arg-type]
        SnapshotStub(),  # type: ignore[arg-type]
        PositionStub(),  # type: ignore[arg-type]
        HoldingStub(),  # type: ignore[arg-type]
        DeliveryStub(),
        DailyEmailReportRenderer(),
        TransportStub(),
        "sender@example.com",
        "recipient@example.com",
        today=lambda: date(2026, 7, 23),
    )

    with pytest.raises(DailyReportSnapshotMissingError, match="no complete persisted"):
        case.execute()


def test_scheduled_report_succeeds_after_transient_tpex_read_failure() -> None:
    payload = json.dumps({"date": "20260722"}).encode()
    calls = 0

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return payload

    def open_request(_request: object, *, timeout: float) -> Response:
        nonlocal calls
        assert timeout == 15
        calls += 1
        if calls == 1:
            raise IncompleteRead(payload[:5])
        return Response()

    market_transport = UrllibJSONDocumentTransport(
        provider_name="TPEx",
        opener=open_request,
        sleeper=lambda _delay: None,
        jitter=lambda: 0,
    )

    class RetryingUpdate(UpdateStub):
        def execute(
            self,
            requested_date: date | None = None,
            *,
            dry_run: bool = False,
            force: bool = False,
        ) -> object:
            market_transport.get_document("https://www.tpex.org.tw/official")
            return super().execute(requested_date, dry_run=dry_run, force=force)

    email_transport = TransportStub()
    case = SendDailyReportUseCase(
        RetryingUpdate(),  # type: ignore[arg-type]
        SnapshotStub(snapshot()),  # type: ignore[arg-type]
        PositionStub(),  # type: ignore[arg-type]
        HoldingStub(),  # type: ignore[arg-type]
        DeliveryStub(),
        DailyEmailReportRenderer(),
        email_transport,
        "sender@example.com",
        "recipient@example.com",
    )

    result = case.execute()

    assert result.status == "sent"
    assert calls == 2
    assert len(email_transport.messages) == 1


def test_html_and_plain_text_contain_correct_persisted_figures() -> None:
    case, _, _, transport = use_case(value=snapshot())
    result = case.execute(date(2026, 7, 22))
    message = transport.messages[0]
    assert result.status == "sent"
    assert "Total stock market value: NT$1,200" in message.plain_text
    assert "Total return: +20.00%" in message.plain_text
    assert "TSMC" in message.plain_text
    assert "<html>" in message.html
    assert "Today's Contributors" in message.html
    assert "Share of Net P/L" not in message.html
    assert "Share of net daily P/L" not in message.plain_text
    assert message.html.index("Portfolio Summary") < message.html.index(
        "Portfolio Trend"
    )
    assert message.html.index("Portfolio Trend") < message.html.index(
        "Today's Contributors"
    )
    assert message.html.index("Today's Contributors") < message.html.index(
        ">Holdings</h2>"
    )
    assert message.plain_text.index("Portfolio Summary") < message.plain_text.index(
        "Portfolio Trend"
    )


def test_final_annual_performance_section_uses_persisted_dates_and_values() -> None:
    annual_snapshot = AnnualPnlSnapshot(
        snapshot_date=date(2026, 7, 23),
        valuation_date=date(2026, 7, 22),
        year=2026,
        realized_pnl_ytd=Decimal("100"),
        unrealized_pnl=Decimal("-50"),
        dividend_income_ytd=Decimal("20"),
        financing_cost_ytd=Decimal("30"),
        other_cost_ytd=Decimal("10"),
        total_pnl_ytd=Decimal("30"),
    )

    class AnnualSnapshots:
        def __init__(self) -> None:
            self.rows = (annual_snapshot,)

        def list_for_year(self, year: int) -> list[AnnualPnlSnapshot]:
            return [item for item in self.rows if item.year == year]

        def list_between_dates(self, start: date, end: date) -> list[AnnualPnlSnapshot]:
            return [item for item in self.rows if start <= item.snapshot_date <= end]

    class AnnualPnl:
        def __init__(self) -> None:
            self.mutations = 0

        def ensure(self, *_args: object, **_kwargs: object) -> AnnualPnlSnapshot:
            return annual_snapshot

        def realized_pnl_by_symbol(
            self, *, as_of: date
        ) -> tuple[RealizedPnlBySymbol, ...]:
            assert as_of == date(2026, 7, 23)
            return (
                RealizedPnlBySymbol("2330", "TWSE", Decimal("125")),
                RealizedPnlBySymbol("8299", "TPEx", Decimal("-25")),
            )

    annual = AnnualPnl()
    repository = AnnualSnapshots()
    transport = TransportStub()
    case = SendDailyReportUseCase(
        UpdateStub(),  # type: ignore[arg-type]
        SnapshotStub(snapshot()),  # type: ignore[arg-type]
        PositionStub(),  # type: ignore[arg-type]
        HoldingStub(),  # type: ignore[arg-type]
        DeliveryStub(),
        DailyEmailReportRenderer(),
        transport,
        "sender@example.com",
        "recipient@example.com",
        today=lambda: date(2026, 7, 23),
        annual_pnl=annual,  # type: ignore[arg-type]
        annual_snapshots=repository,  # type: ignore[arg-type]
    )

    case.execute()

    message = transport.messages[0]
    legacy_transport = TransportStub()
    legacy_case = SendDailyReportUseCase(
        UpdateStub(),  # type: ignore[arg-type]
        SnapshotStub(snapshot()),  # type: ignore[arg-type]
        PositionStub(),  # type: ignore[arg-type]
        HoldingStub(),  # type: ignore[arg-type]
        DeliveryStub(),
        DailyEmailReportRenderer(),
        legacy_transport,
        "sender@example.com",
        "recipient@example.com",
        today=lambda: date(2026, 7, 23),
    )
    legacy_case.execute()
    legacy_message = legacy_transport.messages[0]

    annual_marker = '<div class="pams-annual-performance"'
    existing_html, annual_html = message.html.split(annual_marker, 1)
    legacy_existing_html, _ = legacy_message.html.split(annual_marker, 1)
    assert existing_html == legacy_existing_html
    assert message.inline_images == legacy_message.inline_images
    assert "pams-ytd-composition-chart" not in existing_html
    assert "pams-realized-symbol-chart" not in existing_html
    assert "pams-ytd-composition-chart" not in annual_html
    assert "pams-realized-symbol-chart" in annual_html
    assert "2026 年度投資績效（YTD）" in message.html
    assert "Accounting Date:</strong> 2026-07-23" in message.html
    assert "Valuation Date:</strong> 2026-07-22" in annual_html
    for label, amount in (
        ("Realized P/L YTD", "+NT$100"),
        ("Dividend Income YTD", "NT$20"),
        ("Financing Cost YTD", "NT$30"),
        ("Other Cost YTD", "NT$10"),
        ("Total P/L YTD", "+NT$30"),
    ):
        assert label in annual_html
        assert amount in annual_html
        assert f"{label}: {amount}" in message.plain_text
    assert "Unrealized P/L" not in annual_html
    assert "YTD P/L Composition" not in annual_html
    assert annual_html.count('role="img"') == 1
    assert 'class="pams-ytd-composition-chart"' not in message.html
    assert 'class="pams-realized-symbol-chart"' in message.html
    assert message.html.index("TWSE 2330") < message.html.index("TPEx 8299")
    assert "#b91c1c" in message.html
    assert "#15803d" in message.html
    assert "Portfolio Summary" in message.html
    assert "Portfolio Trend" in message.html
    assert "Today's Contributors" in message.html
    assert ">Holdings</h2>" in message.html
    assert message.html.index("2026 年度投資績效（YTD）") > message.html.index(
        ">Holdings</h2>"
    )
    assert "各股票 YTD 已實現損益" in message.plain_text
    assert "TWSE | 2330 | +NT$125" in message.plain_text
    assert repository.rows == (annual_snapshot,)
    assert annual.mutations == 0


def test_final_annual_performance_section_warns_when_data_is_unavailable() -> None:
    case, _, _, transport = use_case(value=snapshot())

    case.execute(date(2026, 7, 22))

    message = transport.messages[0]
    assert "年度投資績效（YTD）" in message.html
    assert "Annual P/L data is unavailable for this report." in message.html
    assert "Warning: Annual P/L data is unavailable for this report." in (
        message.plain_text
    )
    assert "pams-ytd-composition-chart" not in message.html


def test_positive_daily_profit_loss_uses_persisted_position_movements() -> None:
    case, _, _, transport = use_case(value=snapshot())

    case.execute(date(2026, 7, 22))

    message = transport.messages[0]
    assert "Amount: +NT$10" in message.plain_text
    assert "Percentage: +0.84%" in message.plain_text
    assert "+NT$10" in message.html
    assert "+0.84%" in message.html


def test_negative_daily_profit_loss_preserves_negative_sign() -> None:
    negative_positions = [
        item.model_copy(update={"daily_value_change": Decimal("-10")})
        for item in positions()
    ]
    case, _, _, transport = use_case(
        value=snapshot(), position_values=negative_positions
    )

    case.execute(date(2026, 7, 22))

    message = transport.messages[0]
    assert "Amount: -NT$20" in message.plain_text
    assert "Percentage: -1.64%" in message.plain_text


def test_position_daily_contributions_cover_positive_negative_and_zero() -> None:
    contribution_positions = [
        positions()[0].model_copy(
            update={
                "daily_value_change": Decimal("25"),
                "daily_return": Decimal("0.0256410256"),
            }
        ),
        positions()[1].model_copy(
            update={
                "daily_value_change": Decimal("-10"),
                "daily_return": Decimal("-0.0476190476"),
            }
        ),
        positions()[1].model_copy(
            update={
                "holding_id": "h3",
                "symbol": "ZERO",
                "daily_value_change": Decimal("0"),
                "daily_return": Decimal("0"),
            }
        ),
    ]
    case, _, _, transport = use_case(
        value=snapshot(), position_values=contribution_positions
    )

    case.execute(date(2026, 7, 22))

    text = transport.messages[0].plain_text
    assert "TWSE | 2330 | TSMC | N/A | +NT$25 | +2.56%" in text
    assert "TWSE | 8299 | Phison | N/A | -NT$10 | -4.76%" in text
    assert "TWSE | ZERO | ZERO | N/A | NT$0 | 0.00%" in text
    html = transport.messages[0].html
    contributors = html.split(">Today's Contributors</h2>", maxsplit=1)[1].split(
        "</table>", maxsplit=1
    )[0]
    assert 'color:#b91c1c;font-weight:600">+NT$25</td>' in contributors
    assert 'color:#15803d;font-weight:600">-NT$10</td>' in contributors
    assert 'color:#6b7280;font-weight:600">NT$0</td>' in contributors


def test_contributors_are_ranked_by_daily_profit_loss_descending() -> None:
    impact_positions = [
        positions()[0].model_copy(update={"daily_value_change": Decimal("5")}),
        positions()[1].model_copy(update={"daily_value_change": Decimal("-50")}),
    ]
    case, _, _, transport = use_case(value=snapshot(), position_values=impact_positions)

    case.execute(date(2026, 7, 22))

    contributor_section = transport.messages[0].plain_text.split(
        "Today's Contributors\n", maxsplit=1
    )[1]
    contributor_rows = contributor_section.split("\n")
    assert contributor_rows[1].startswith("1 | TWSE | 2330 | TSMC | N/A | +NT$5")
    assert contributor_rows[2].startswith("2 | TWSE | 8299 | Phison | N/A | -NT$50")


def test_holdings_omit_daily_pnl_columns_but_keep_unrealized_fields() -> None:
    case, _, _, transport = use_case(value=snapshot())

    case.execute(date(2026, 7, 22))

    message = transport.messages[0]
    plain_holdings = message.plain_text.split("Holdings\n", 1)[1]
    html_holdings = message.html.split(">Holdings</h2>", 1)[1]
    assert "Today's P/L" not in plain_holdings.split("\n", 1)[0]
    assert "Unrealized P/L" in plain_holdings.split("\n", 1)[0]
    assert "Return %" in plain_holdings.split("\n", 1)[0]
    assert ">Today&#x27;s P/L</th>" not in html_holdings
    assert ">Today&#x27;s P/L %</th>" not in html_holdings
    assert ">Unrealized P/L</th>" in html_holdings
    assert ">Return %</th>" in html_holdings


def test_html_holdings_uses_compact_non_scrolling_column_set() -> None:
    case, _, _, transport = use_case(value=snapshot())

    case.execute(date(2026, 7, 22))

    html = transport.messages[0].html
    holdings = html.split(">Holdings</h2>", maxsplit=1)[1].split(
        "</table>", maxsplit=1
    )[0]
    expected = (
        "Symbol",
        "Name",
        "Quantity",
        "Average Cost",
        "Close",
        "Unrealized P/L",
        "Return %",
        "Market Value",
    )
    assert all(f">{label}</th>" in holdings for label in expected)
    assert "Daily Change" not in holdings
    assert ">Return</th>" not in holdings
    assert ">Weight</th>" not in holdings
    assert "overflow-x:auto" not in holdings
    assert "word-break:keep-all" in holdings
    assert ">2</td>" in holdings
    assert ">NT$400</td>" in holdings
    assert ">NT$500</td>" in holdings
    assert "+NT$20</td>" not in holdings
    assert "NT$1,000</td>" in holdings
    assert "NT$1,000.00</td>" not in holdings
    assert "table-layout:auto" in holdings
    for label in (
        "Quantity",
        "Average Cost",
        "Close",
        "Unrealized P/L",
        "Return %",
        "Market Value",
    ):
        assert re.search(
            rf'<th style="[^"]*text-align:right[^"]*">{label}</th>', holdings
        )


def test_html_contributor_columns_and_readable_alignment_are_preserved() -> None:
    case, _, _, transport = use_case(value=snapshot())

    case.execute(date(2026, 7, 22))

    html = transport.messages[0].html
    contributors = html.split(">Today's Contributors</h2>", maxsplit=1)[1].split(
        "</table>", maxsplit=1
    )[0]
    for label in (
        "Rank",
        "Market",
        "Symbol",
        "Name",
        "Quote Date",
        "Today&#x27;s P/L",
        "Today&#x27;s P/L %",
    ):
        assert f">{label}</th>" in contributors
    assert "Share of Net P/L" not in contributors
    assert "font-size:14px" in contributors
    assert "text-align:right" in contributors
    assert "word-break:keep-all" in contributors
    assert re.search(
        r'<th style="[^"]*text-align:left[^"]*width:38%">Name</th>', contributors
    )
    assert "+NT$20</td>" in contributors
    assert "+NT$20.00</td>" not in contributors
    assert "Ranked by Today's P/L from highest to lowest." in html
    assert re.search(r'<th style="[^"]*text-align:right[^"]*">Rank</th>', contributors)

    contributor_table = html.split(
        '<table class="pams-canonical-report-table"', maxsplit=1
    )[1].split(">", maxsplit=1)[0]
    holdings_table = (
        html.split(
            '<h2 style="font-size:18px;margin-top:24px">Holdings</h2>', maxsplit=1
        )[1]
        .split('<table class="pams-canonical-report-table"', maxsplit=1)[1]
        .split(">", maxsplit=1)[0]
    )
    assert "width:100%" in contributor_table
    assert "width:100%" in holdings_table
    assert 'width="100%"' in contributor_table
    width_reference = (
        '<table role="presentation" class="pams-wide-tables" width="100%" '
        'style="border-collapse:collapse;width:100%;table-layout:auto">'
    )
    wrapper_start = html.index(width_reference)
    contributors_start = html.index(">Today's Contributors</h2>")
    holdings_start = html.index(">Holdings</h2>")
    assert wrapper_start < contributors_start < holdings_start
    assert "white-space:nowrap" in html[holdings_start:]


def test_html_table_numbers_follow_display_precision_rules() -> None:
    formatted = positions()[0].model_copy(
        update={
            "quantity": Decimal("6100"),
            "average_cost": Decimal("97.60"),
            "close_price": Decimal("44.45"),
            "daily_value_change": Decimal("37500.40"),
            "market_value": Decimal("622200.40"),
            "unrealized_pnl": Decimal("105970.40"),
        }
    )
    case, _, _, transport = use_case(value=snapshot(), position_values=[formatted])

    case.execute(date(2026, 7, 22))

    html = transport.messages[0].html
    holdings = html.split(">Holdings</h2>", maxsplit=1)[1].split(
        "</table>", maxsplit=1
    )[0]
    contributors = html.split(">Today's Contributors</h2>", maxsplit=1)[1].split(
        "</table>", maxsplit=1
    )[0]
    assert ">6,100</td>" in holdings
    assert ">NT$97.6</td>" in holdings
    assert ">NT$44.45</td>" in holdings
    assert "+NT$105,970</td>" in holdings
    assert "NT$622,200</td>" in holdings
    assert "+NT$37,500</td>" in contributors
    assert ".40</td>" not in holdings


def test_html_summary_emphasizes_daily_profit_loss() -> None:
    case, _, _, transport = use_case(value=snapshot())

    case.execute(date(2026, 7, 22))

    html = transport.messages[0].html
    assert "font-size:20px;font-weight:700;color:#b91c1c" in html
    assert "+NT$10" in html


def test_zero_daily_profit_loss_is_zero_without_positive_sign() -> None:
    unchanged_positions = [
        item.model_copy(update={"daily_value_change": Decimal("0")})
        for item in positions()
    ]
    case, _, _, transport = use_case(
        value=snapshot(), position_values=unchanged_positions
    )

    case.execute(date(2026, 7, 22))

    message = transport.messages[0]
    assert "Amount: NT$0" in message.plain_text
    assert "Percentage: 0.00%" in message.plain_text


def test_multiple_snapshots_create_embedded_png_and_readable_text_history() -> None:
    history = [
        snapshot(date(2026, 7, 20), market_value="1000", net_asset_value="900"),
        snapshot(date(2026, 7, 21), market_value="1100", net_asset_value="1000"),
        snapshot(),
    ]
    case, _, _, transport = use_case(value=snapshot(), history=history)

    case.execute(date(2026, 7, 22))

    message = transport.messages[0]
    assert 'src="cid:pams-asset-change-chart"' in message.html
    assert len(message.inline_images) == 1
    assert message.inline_images[0].content.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(BytesIO(message.inline_images[0].content)) as chart:
        assert chart.format == "PNG"
        assert chart.size == (1200, 650)
    assert "2026-07-20 to 2026-07-22 (3 available snapshots)" in message.plain_text
    assert "2026-07-21 | NT$1,100 | NT$1,000" in message.plain_text


def test_portfolio_trend_contains_exactly_the_two_original_series() -> None:
    assert PORTFOLIO_TREND_SERIES == (
        "Total stock market value",
        "Net stock equity",
    )

    history = [snapshot(date(2026, 7, 21)), snapshot()]
    case, _, _, transport = use_case(value=snapshot(), history=history)
    case.execute(date(2026, 7, 22))

    message = transport.messages[0]
    trend_text = message.plain_text.split("Portfolio Trend\n", 1)[1].split(
        "\n\nToday's Contributors", 1
    )[0]
    assert "Date | Total stock market value | Net stock equity" in trend_text
    assert "Total P/L YTD" not in trend_text
    assert "Realized P/L YTD" not in trend_text
    assert "Unrealized P/L" not in trend_text
    assert "Dividend Income YTD" not in trend_text


def test_resend_asset_flow_publishes_png_and_uses_https_without_attachment() -> None:
    history = [
        snapshot(date(2026, 7, 21), market_value="1100", net_asset_value="1000"),
        snapshot(),
    ]
    store = AssetStoreStub()
    case, _, _, transport = use_case(
        value=snapshot(), history=history, asset_store=store
    )

    case.execute(date(2026, 7, 22))

    assert len(store.calls) == 1
    content, content_type, object_name = store.calls[0]
    assert content.startswith(b"\x89PNG\r\n\x1a\n")
    assert content_type == "image/png"
    assert object_name == "daily-report/2026-07-22/asset-change.png"
    message = transport.messages[0]
    assert f'src="{store.url}"' in message.html
    assert "cid:" not in message.html
    assert message.inline_images == ()
    assert "2026-07-21 | NT$1,100 | NT$1,000" in message.plain_text


def test_forced_resend_upserts_the_same_date_object_path() -> None:
    history = [snapshot(date(2026, 7, 21)), snapshot()]
    store = AssetStoreStub()
    delivery = DeliveryStub()
    delivery.status = "SENT"
    case, _, _, transport = use_case(
        value=snapshot(),
        history=history,
        delivery=delivery,
        asset_store=store,
    )

    case.execute(date(2026, 7, 22), force=True)
    case.execute(date(2026, 7, 22), force=True)

    assert [call[2] for call in store.calls] == [
        "daily-report/2026-07-22/asset-change.png",
        "daily-report/2026-07-22/asset-change.png",
    ]
    assert len(transport.messages) == 2


def test_asset_upload_failure_marks_retryable_and_prevents_email() -> None:
    history = [snapshot(date(2026, 7, 21)), snapshot()]
    delivery = DeliveryStub()
    store = AssetStoreStub(error=RuntimeError("storage unavailable"))
    case, _, _, transport = use_case(
        value=snapshot(),
        history=history,
        delivery=delivery,
        asset_store=store,
    )

    with pytest.raises(
        DailyReportDeliveryError,
        match="daily report delivery failed: RuntimeError: storage unavailable",
    ):
        case.execute(date(2026, 7, 22))

    assert transport.messages == []
    assert delivery.status == "FAILED"


def test_one_snapshot_resend_fallback_does_not_publish_asset() -> None:
    store = AssetStoreStub()
    case, _, _, transport = use_case(value=snapshot(), asset_store=store)

    case.execute(date(2026, 7, 22))

    assert store.calls == []
    assert transport.messages[0].inline_images == ()
    assert "a trend chart requires at least two snapshots" in transport.messages[0].html


def test_fewer_than_thirty_snapshots_are_rendered_without_padding() -> None:
    history = [
        snapshot(date(2026, 7, 21), market_value="1100", net_asset_value="1000"),
        snapshot(),
    ]
    case, _, _, transport = use_case(value=snapshot(), history=history)

    case.execute(date(2026, 7, 22))

    assert "(2 available snapshots)" in transport.messages[0].plain_text
    assert len(transport.messages[0].inline_images) == 1


def test_history_is_limited_to_most_recent_thirty_snapshots() -> None:
    history = [
        snapshot(
            date(2026, 6, 18 + index),
            market_value=str(900 + index),
            net_asset_value=str(800 + index),
        )
        for index in range(1, 13)
    ]
    history.extend(
        snapshot(
            date(2026, 7, index),
            market_value=str(1000 + index),
            net_asset_value=str(900 + index),
        )
        for index in range(1, 23)
    )
    case, _, _, transport = use_case(value=snapshot(), history=history)

    case.execute(date(2026, 7, 22))

    text = transport.messages[0].plain_text
    assert "(30 available snapshots)" in text
    assert "2026-06-19 |" not in text
    assert "2026-06-23 |" in text


def test_one_snapshot_uses_clear_chart_fallback_without_attachment() -> None:
    case, _, _, transport = use_case(value=snapshot())

    case.execute(date(2026, 7, 22))

    message = transport.messages[0]
    fallback = (
        "Only one portfolio snapshot is available; "
        "a trend chart requires at least two snapshots."
    )
    assert fallback in message.plain_text
    assert fallback in message.html
    assert message.inline_images == ()


def test_repeated_delivery_sends_the_same_report_date_again() -> None:
    delivery = DeliveryStub()
    case, _, _, transport = use_case(value=snapshot(), delivery=delivery)
    assert case.execute(date(2026, 7, 22)).status == "sent"
    assert case.execute(date(2026, 7, 22)).status == "sent"
    assert len(transport.messages) == 2


def test_force_resends_a_sent_report() -> None:
    delivery = DeliveryStub()
    delivery.status = "SENT"
    case, update, _, transport = use_case(value=snapshot(), delivery=delivery)
    assert case.execute(date(2026, 7, 22), force=True).status == "sent"
    assert update.calls == [(date(2026, 7, 22), False, True)]
    assert len(transport.messages) == 1


def test_automatic_force_rebuilds_before_delivery() -> None:
    case, update, _, transport = use_case(value=snapshot())

    assert case.execute(force=True).status == "sent"
    assert update.calls == [(None, False, True)]
    assert len(transport.messages) == 1


def test_failure_is_retryable_and_success_replaces_failed_state() -> None:
    delivery = DeliveryStub()
    failing = TransportStub(RuntimeError("SMTP unavailable"))
    case, _, _, _ = use_case(value=snapshot(), delivery=delivery, transport=failing)
    with pytest.raises(
        DailyReportDeliveryError,
        match="daily report delivery failed: RuntimeError: SMTP unavailable",
    ) as raised:
        case.execute(date(2026, 7, 22))
    assert isinstance(raised.value.__cause__, RuntimeError)
    assert delivery.status == "FAILED"
    assert "SMTP unavailable" in (delivery.error or "")

    successful = TransportStub()
    retry, _, _, _ = use_case(value=snapshot(), delivery=delivery, transport=successful)
    assert retry.execute(date(2026, 7, 22)).status == "sent"
    assert delivery.status == "SENT"


def test_dry_run_does_not_send_or_mark_sent() -> None:
    case, _, delivery, transport = use_case(value=snapshot())
    result = case.execute(date(2026, 7, 22), dry_run=True)
    assert result.status == "dry_run"
    assert result.subject == "PAMS Daily Portfolio Report - 2026-07-22"
    assert transport.messages == []
    assert delivery.status is None


def test_explicit_missing_snapshot_does_not_fall_back() -> None:
    case, update, _, transport = use_case(value=None)
    with pytest.raises(DailyReportSnapshotMissingError):
        case.execute(date(2026, 7, 21))
    assert update.calls == []
    assert transport.messages == []


def test_automatic_mode_updates_then_uses_latest_persisted_snapshot() -> None:
    case, update, _, transport = use_case(value=snapshot())
    result = case.execute()
    assert update.calls == [(None, False, False)]
    assert result.report_date == date(2026, 7, 22)
    assert len(transport.messages) == 1


def test_smtp_transport_uses_starttls_and_multipart_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            calls.append((host, port, timeout))

        def __enter__(self) -> "FakeSMTP":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def ehlo(self) -> None:
            calls.append("ehlo")

        def starttls(self, *, context: object) -> None:
            calls.append(("starttls", context is not None))

        def login(self, username: str, password: str) -> None:
            calls.append(("login", username, password))

        def send_message(self, message: object) -> None:
            calls.append(message)

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    SMTPEmailTransport("smtp.example.com", 587, "user", "secret").send(
        EmailEnvelope(
            "from@example.com",
            "to@example.com",
            "subject",
            "plain",
            '<p>html<img src="cid:chart"></p>',
            (InlineImage("chart", "chart.png", "image/png", b"png-content"),),
        )
    )
    assert ("starttls", True) in calls
    assert ("login", "user", "secret") in calls
    message = calls[-1]
    assert message.is_multipart()
    attachment = next(
        part for part in message.walk() if part.get_content_type() == "image/png"
    )
    assert attachment["Content-ID"] == "<chart>"
    assert attachment.get_content_disposition() == "inline"
    assert attachment.get_filename() is None
    assert attachment.get_payload(decode=True) == b"png-content"

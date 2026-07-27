"""Offline tests for daily portfolio report delivery."""

import smtplib
from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from PIL import Image

from domain import Currency, DailySnapshot, Holding, Market, PositionSnapshot
from market_data import ProviderDataError
from pams.application import (
    DailyReportDeliveryError,
    DailyReportSendResult,
    DailyReportSnapshotMissingError,
    SendDailyReportUseCase,
)
from pams.application.send_daily_report import EmailEnvelope, InlineImage
from pams.delivery import DailyEmailReportRenderer, SMTPEmailTransport


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


def test_html_and_plain_text_contain_correct_persisted_figures() -> None:
    case, _, _, transport = use_case(value=snapshot())
    result = case.execute(date(2026, 7, 22))
    message = transport.messages[0]
    assert result.status == "sent"
    assert "Total stock market value: NT$1,200.00" in message.plain_text
    assert "Total return: +20.00%" in message.plain_text
    assert "TSMC" in message.plain_text
    assert "<html>" in message.html
    assert "Today's Contributors" in message.html
    assert "Share of net daily P/L" in message.html


def test_positive_daily_profit_loss_uses_persisted_position_movements() -> None:
    case, _, _, transport = use_case(value=snapshot())

    case.execute(date(2026, 7, 22))

    message = transport.messages[0]
    assert "Amount: +NT$10.00" in message.plain_text
    assert "Percentage: +0.84%" in message.plain_text
    assert "+NT$10.00" in message.html
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
    assert "Amount: -NT$20.00" in message.plain_text
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
    assert "2330 | TSMC | +NT$25.00 | +2.56%" in text
    assert "8299 | Phison | -NT$10.00 | -4.76%" in text
    assert "ZERO | ZERO | NT$0.00 | 0.00%" in text


def test_contributors_are_ranked_by_absolute_portfolio_impact() -> None:
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
    assert contributor_rows[1].startswith("1 | 8299 | Phison | -NT$50.00")
    assert contributor_rows[2].startswith("2 | 2330 | TSMC | +NT$5.00")


def test_holdings_include_daily_pnl_columns_and_conditional_formatting() -> None:
    case, _, _, transport = use_case(value=snapshot())

    case.execute(date(2026, 7, 22))

    message = transport.messages[0]
    assert "Today's P/L | Today's P/L %" in message.plain_text
    assert ">Today&#x27;s P/L</th>" in message.html
    assert ">Today&#x27;s P/L %</th>" in message.html
    assert 'color:#15803d;font-weight:600">+NT$20.00</td>' in message.html
    assert 'color:#b91c1c;font-weight:600">-NT$10.00</td>' in message.html


def test_html_summary_emphasizes_daily_profit_loss() -> None:
    case, _, _, transport = use_case(value=snapshot())

    case.execute(date(2026, 7, 22))

    html = transport.messages[0].html
    assert "font-size:20px;font-weight:700;color:#15803d" in html
    assert "+NT$10.00" in html


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
    assert "Amount: NT$0.00" in message.plain_text
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
    assert "2026-07-21 | NT$1,100.00 | NT$1,000.00" in message.plain_text


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
    assert "2026-07-21 | NT$1,100.00 | NT$1,000.00" in message.plain_text


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


def test_first_delivery_sends_and_duplicate_is_successful_no_op() -> None:
    delivery = DeliveryStub()
    case, _, _, transport = use_case(value=snapshot(), delivery=delivery)
    assert case.execute(date(2026, 7, 22)).status == "sent"
    assert case.execute(date(2026, 7, 22)).status == "already_sent"
    assert len(transport.messages) == 1


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

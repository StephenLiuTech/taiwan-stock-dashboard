"""Offline tests for daily portfolio report delivery."""

import smtplib
from datetime import date
from decimal import Decimal

import pytest

from domain import Currency, DailySnapshot, Holding, Market, PositionSnapshot
from pams.application import (
    DailyReportDeliveryError,
    DailyReportSnapshotMissingError,
    SendDailyReportUseCase,
)
from pams.application.send_daily_report import EmailEnvelope
from pams.delivery import DailyEmailReportRenderer, SMTPEmailTransport


def snapshot() -> DailySnapshot:
    return DailySnapshot(
        snapshot_date=date(2026, 7, 22),
        total_market_value=Decimal("1200"),
        total_cost_basis=Decimal("1000"),
        total_unrealized_pnl=Decimal("200"),
        total_liabilities=Decimal("100"),
        net_asset_value=Decimal("1100"),
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
        return type("Update", (), {"verified_source_date": date(2026, 7, 22)})()


class SnapshotStub:
    def __init__(self, value: DailySnapshot | None = None) -> None:
        self.value = value

    def get_latest(self) -> DailySnapshot | None:
        return self.value

    def get_by_date(self, report_date: date) -> DailySnapshot | None:
        return (
            self.value
            if self.value and self.value.snapshot_date == report_date
            else None
        )


class PositionStub:
    def list_by_date(self, report_date: date) -> list[PositionSnapshot]:
        assert report_date == date(2026, 7, 22)
        return positions()


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


def use_case(
    *,
    value: DailySnapshot | None = None,
    delivery: DeliveryStub | None = None,
    transport: TransportStub | None = None,
) -> tuple[SendDailyReportUseCase, UpdateStub, DeliveryStub, TransportStub]:
    update = UpdateStub()
    deliveries = delivery or DeliveryStub()
    email = transport or TransportStub()
    return (
        SendDailyReportUseCase(
            update,  # type: ignore[arg-type]
            SnapshotStub(value),  # type: ignore[arg-type]
            PositionStub(),  # type: ignore[arg-type]
            HoldingStub(),  # type: ignore[arg-type]
            deliveries,
            DailyEmailReportRenderer(),
            email,
            "sender@example.com",
            "recipient@example.com",
        ),
        update,
        deliveries,
        email,
    )


def test_html_and_plain_text_contain_correct_persisted_figures() -> None:
    case, _, _, transport = use_case(value=snapshot())
    result = case.execute(date(2026, 7, 22))
    message = transport.messages[0]
    assert result.status == "sent"
    assert "Total stock market value: NT$1,200.00" in message.plain_text
    assert "Total return: +20.00%" in message.plain_text
    assert "TSMC" in message.plain_text
    assert "<html>" in message.html
    assert "Top gainer:</strong> 2330" in message.html
    assert "Top loser:</strong> 8299" in message.html


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
            "<p>html</p>",
        )
    )
    assert ("starttls", True) in calls
    assert ("login", "user", "secret") in calls
    message = calls[-1]
    assert message.is_multipart()

"""Daily portfolio report delivery orchestration."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol

from domain import DailySnapshot, PositionSnapshot
from pams.application.update_portfolio import UpdatePortfolioUseCase
from repositories import (
    HoldingRepository,
    PositionSnapshotRepository,
    SnapshotRepository,
)

REPORT_TYPE = "daily_portfolio"


class DailyReportError(RuntimeError):
    """Base class for controlled daily-report failures."""


class DailyReportSnapshotMissingError(DailyReportError):
    """The requested aggregate snapshot does not exist."""


class DailyReportDeliveryError(DailyReportError):
    """The transport failed and the delivery remains retryable."""


@dataclass(frozen=True)
class DailyEmailPosition:
    symbol: str
    name: str
    quantity: Decimal
    average_cost: Decimal
    close_price: Decimal
    daily_return: Decimal | None
    market_value: Decimal
    unrealized_pnl: Decimal
    unrealized_return: Decimal
    portfolio_weight: Decimal


@dataclass(frozen=True)
class DailyEmailReport:
    report_date: date
    verified_source_date: date | None
    total_market_value: Decimal
    total_cost_basis: Decimal
    total_unrealized_pnl: Decimal
    total_return: Decimal
    total_liabilities: Decimal
    net_asset_value: Decimal
    liability_ratio: Decimal
    positions: tuple[DailyEmailPosition, ...]
    top_gainer: DailyEmailPosition | None
    top_loser: DailyEmailPosition | None


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    plain_text: str
    html: str


@dataclass(frozen=True)
class EmailEnvelope:
    sender: str
    recipient: str
    subject: str
    plain_text: str
    html: str


@dataclass(frozen=True)
class DailyReportSendResult:
    report_date: date
    recipient: str
    subject: str
    status: str


class EmailTransport(Protocol):
    def send(self, envelope: EmailEnvelope) -> None: ...


class DailyEmailRenderer(Protocol):
    def render(self, report: DailyEmailReport) -> RenderedEmail: ...


class ReportDeliveryRepository(Protocol):
    def claim(self, report_type: str, report_date: date, recipient: str) -> bool: ...

    def mark_sent(
        self, report_type: str, report_date: date, recipient: str, sent_at: datetime
    ) -> None: ...

    def mark_failed(
        self, report_type: str, report_date: date, recipient: str, error: str
    ) -> None: ...


class SendDailyReportUseCase:
    """Update when automatic, load persisted report facts, and deliver once."""

    def __init__(
        self,
        update_portfolio: UpdatePortfolioUseCase,
        snapshots: SnapshotRepository,
        positions: PositionSnapshotRepository,
        holdings: HoldingRepository,
        deliveries: ReportDeliveryRepository,
        renderer: DailyEmailRenderer,
        transport: EmailTransport,
        sender: str,
        recipient: str,
    ) -> None:
        self._update_portfolio = update_portfolio
        self._snapshots = snapshots
        self._positions = positions
        self._holdings = holdings
        self._deliveries = deliveries
        self._renderer = renderer
        self._transport = transport
        self._sender = sender
        self._recipient = recipient

    def execute(
        self,
        requested_date: date | None = None,
        *,
        dry_run: bool = False,
        force: bool = False,
    ) -> DailyReportSendResult:
        """Perform one automatic or explicit-date delivery workflow."""
        if requested_date is None:
            update = self._update_portfolio.execute(dry_run=dry_run, force=force)
            snapshot = self._snapshots.get_latest()
            verified_source_date = update.verified_source_date
        elif force:
            update = self._update_portfolio.execute(
                requested_date, dry_run=dry_run, force=True
            )
            snapshot = self._snapshots.get_by_date(requested_date)
            verified_source_date = update.verified_source_date
        else:
            snapshot = self._snapshots.get_by_date(requested_date)
            verified_source_date = requested_date
        if snapshot is None or (
            requested_date is not None and snapshot.snapshot_date != requested_date
        ):
            target = requested_date.isoformat() if requested_date else "latest"
            raise DailyReportSnapshotMissingError(
                f"aggregate portfolio snapshot does not exist for {target}"
            )
        report_date = snapshot.snapshot_date
        report = self._build_report(snapshot, verified_source_date or report_date)
        rendered = self._renderer.render(report)
        if dry_run:
            return DailyReportSendResult(
                report_date, self._recipient, rendered.subject, "dry_run"
            )
        if not force and not self._deliveries.claim(
            REPORT_TYPE, report_date, self._recipient
        ):
            return DailyReportSendResult(
                report_date, self._recipient, rendered.subject, "already_sent"
            )
        envelope = EmailEnvelope(
            self._sender,
            self._recipient,
            rendered.subject,
            rendered.plain_text,
            rendered.html,
        )
        try:
            self._transport.send(envelope)
        except Exception as error:
            self._deliveries.mark_failed(
                REPORT_TYPE,
                report_date,
                self._recipient,
                f"{type(error).__name__}: {error}",
            )
            raise DailyReportDeliveryError(
                f"daily report delivery failed: {type(error).__name__}: {error}"
            ) from error
        self._deliveries.mark_sent(
            REPORT_TYPE, report_date, self._recipient, datetime.now(UTC)
        )
        return DailyReportSendResult(
            report_date, self._recipient, rendered.subject, "sent"
        )

    def _build_report(
        self, snapshot: DailySnapshot, verified_source_date: date
    ) -> DailyEmailReport:
        names = {holding.id: holding.name for holding in self._holdings.list_all()}
        persisted = self._positions.list_by_date(snapshot.snapshot_date)
        positions = tuple(
            self._position(item, names.get(item.holding_id, item.symbol))
            for item in sorted(persisted, key=lambda value: value.symbol)
        )
        ranked = [item for item in positions if item.daily_return is not None]
        top_gainer = (
            max(ranked, key=lambda item: (item.daily_return, item.symbol))
            if ranked
            else None
        )
        top_loser = (
            min(ranked, key=lambda item: (item.daily_return, item.symbol))
            if ranked
            else None
        )
        return DailyEmailReport(
            snapshot.snapshot_date,
            verified_source_date,
            snapshot.total_market_value,
            snapshot.total_cost_basis,
            snapshot.total_unrealized_pnl,
            snapshot.total_return,
            snapshot.total_liabilities,
            snapshot.net_asset_value,
            snapshot.leverage_ratio,
            positions,
            top_gainer,
            top_loser,
        )

    @staticmethod
    def _position(value: PositionSnapshot, name: str) -> DailyEmailPosition:
        return DailyEmailPosition(
            value.symbol,
            name,
            value.quantity,
            value.average_cost,
            value.close_price,
            value.daily_return,
            value.market_value,
            value.unrealized_pnl,
            value.unrealized_return,
            value.portfolio_weight,
        )

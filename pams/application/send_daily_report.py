"""Daily portfolio report delivery orchestration."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol

from domain import DailyReportSections, DailySnapshot, PositionSnapshot
from pams.application.report_sections import BuildReportSectionsUseCase
from pams.application.update_portfolio import UpdatePortfolioUseCase
from repositories import (
    HoldingRepository,
    PositionSnapshotRepository,
    SnapshotRepository,
)
from services import PortfolioService

REPORT_TYPE = "daily_portfolio"


class DailyReportError(RuntimeError):
    """Base class for controlled daily-report failures."""


class DailyReportSnapshotMissingError(DailyReportError):
    """The requested aggregate snapshot does not exist."""


class DailyReportDeliveryError(DailyReportError):
    """The transport failed and the delivery remains retryable."""


@dataclass(frozen=True)
class ChartSource:
    """Transport-neutral HTML URI and optional MIME-related image."""

    uri: str
    attachment: "InlineImage | None" = None


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
    daily_profit_loss: Decimal
    daily_profit_loss_percentage: Decimal
    daily_profit_loss_share: Decimal | None


@dataclass(frozen=True)
class DailyEmailHistoryPoint:
    snapshot_date: date
    total_market_value: Decimal
    net_asset_value: Decimal


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
    daily_profit_loss: Decimal
    daily_profit_loss_percentage: Decimal
    history: tuple[DailyEmailHistoryPoint, ...]
    positions: tuple[DailyEmailPosition, ...]
    sections: DailyReportSections = DailyReportSections()


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    plain_text: str
    html: str
    inline_images: tuple["InlineImage", ...] = ()


@dataclass(frozen=True)
class InlineImage:
    content_id: str
    filename: str
    content_type: str
    content: bytes


@dataclass(frozen=True)
class EmailEnvelope:
    sender: str
    recipient: str
    subject: str
    plain_text: str
    html: str
    inline_images: tuple[InlineImage, ...] = ()


@dataclass(frozen=True)
class DailyReportSendResult:
    report_date: date
    recipient: str
    subject: str
    status: str


class EmailTransport(Protocol):
    def send(self, envelope: EmailEnvelope) -> None: ...


class DailyEmailRenderer(Protocol):
    def render(
        self, report: DailyEmailReport, chart_source: ChartSource | None = None
    ) -> RenderedEmail: ...


class ReportAssetStore(Protocol):
    """Publish a generated report asset and return its public HTTPS URL."""

    def publish(
        self,
        content: bytes,
        content_type: str,
        object_name: str,
    ) -> str: ...


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
        asset_store: ReportAssetStore | None = None,
        section_builder: BuildReportSectionsUseCase | None = None,
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
        self._asset_store = asset_store
        self._section_builder = section_builder

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
            resolved_date = update.requested_date or update.verified_source_date
            if resolved_date is None:
                raise DailyReportSnapshotMissingError(
                    "automatic market-date resolution returned no report date"
                )
            snapshot = self._snapshots.get_by_date(resolved_date)
            verified_source_date = update.verified_source_date or resolved_date
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
        try:
            if self._asset_store is not None and rendered.inline_images:
                chart = rendered.inline_images[0]
                chart_url = self._asset_store.publish(
                    chart.content,
                    chart.content_type,
                    (f"daily-report/{report_date.isoformat()}/" "asset-change.png"),
                )
                if not chart_url.startswith("https://"):
                    raise ValueError("report asset store must return an HTTPS URL")
                rendered = self._renderer.render(
                    report,
                    ChartSource(uri=chart_url),
                )
            envelope = EmailEnvelope(
                self._sender,
                self._recipient,
                rendered.subject,
                rendered.plain_text,
                rendered.html,
                rendered.inline_images,
            )
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
        daily_performance = PortfolioService.calculate_daily_performance(persisted)
        contribution_by_holding = {
            item.holding_id: item for item in daily_performance.positions
        }
        historical_snapshots = sorted(
            self._snapshots.list_between_dates(date.min, snapshot.snapshot_date),
            key=lambda item: item.snapshot_date,
        )[-30:]
        if not historical_snapshots:
            historical_snapshots = [snapshot]
        history = tuple(
            DailyEmailHistoryPoint(
                item.snapshot_date,
                item.total_market_value,
                item.net_asset_value,
            )
            for item in historical_snapshots
        )
        positions = tuple(
            self._position(
                item,
                names.get(item.holding_id, item.symbol),
                contribution_by_holding[item.holding_id].profit_loss,
                contribution_by_holding[item.holding_id].return_percentage,
                contribution_by_holding[item.holding_id].portfolio_profit_loss_share,
            )
            for item in sorted(persisted, key=lambda value: value.symbol)
        )
        sections = (
            self._section_builder.execute(
                snapshot.snapshot_date,
                persisted,
                daily_performance.profit_loss,
                total_market_value=snapshot.total_market_value,
                net_asset_value=snapshot.net_asset_value,
                liability_ratio=snapshot.leverage_ratio,
            )
            if self._section_builder is not None
            else DailyReportSections()
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
            daily_performance.profit_loss,
            daily_performance.return_percentage,
            history,
            positions,
            sections,
        )

    @staticmethod
    def _position(
        value: PositionSnapshot,
        name: str,
        daily_profit_loss: Decimal,
        daily_profit_loss_percentage: Decimal,
        daily_profit_loss_share: Decimal | None,
    ) -> DailyEmailPosition:
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
            daily_profit_loss,
            daily_profit_loss_percentage,
            daily_profit_loss_share,
        )

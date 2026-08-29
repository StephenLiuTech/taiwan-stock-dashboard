"""Daily portfolio report delivery orchestration."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol

from domain import (
    AnnualPnlSnapshot,
    Currency,
    DailyReportSections,
    DailySnapshot,
    Market,
    PositionSnapshot,
    RealizedPnlBySymbol,
)
from market_calendar import MarketCalendarUnavailableError
from pams.application.annual_pnl import AnnualPnlUseCase
from pams.application.report_sections import BuildReportSectionsUseCase
from pams.application.update_portfolio import UpdatePortfolioUseCase
from repositories import (
    AnnualPnlSnapshotRepository,
    HoldingRepository,
    PositionSnapshotRepository,
    SnapshotRepository,
)
from services import PortfolioService

REPORT_TYPE = "daily_portfolio"
_LOGGER = logging.getLogger(__name__)


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
    close_price: Decimal | None
    daily_return: Decimal | None
    market_value: Decimal | None
    unrealized_pnl: Decimal | None
    unrealized_return: Decimal | None
    portfolio_weight: Decimal | None
    daily_profit_loss: Decimal | None
    daily_profit_loss_percentage: Decimal | None
    daily_profit_loss_share: Decimal | None
    market: Market = Market.TWSE
    native_currency: Currency = Currency.TWD
    quote_date: date | None = None
    fx_rate: Decimal = Decimal("1")
    fx_rate_date: date | None = None


@dataclass(frozen=True)
class DailyEmailHistoryPoint:
    snapshot_date: date
    total_market_value: Decimal
    net_asset_value: Decimal
    total_pnl_ytd: Decimal | None = None
    realized_pnl_ytd: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    dividend_income_ytd: Decimal | None = None


@dataclass(frozen=True)
class DailyEmailAnnualPerformance:
    """Persisted annual accounting truth prepared for email presentation."""

    snapshot: AnnualPnlSnapshot
    realized_by_symbol: tuple[RealizedPnlBySymbol, ...]


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
    annual_performance: DailyEmailAnnualPerformance | None = None
    annual_warning: str | None = None


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
        today: Callable[[], date] = date.today,
        annual_pnl: AnnualPnlUseCase | None = None,
        annual_snapshots: AnnualPnlSnapshotRepository | None = None,
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
        self._today = today
        self._annual_pnl = annual_pnl
        self._annual_snapshots = annual_snapshots

    def execute(
        self,
        requested_date: date | None = None,
        *,
        dry_run: bool = False,
        force: bool = False,
    ) -> DailyReportSendResult:
        """Perform one automatic or explicit-date delivery workflow."""
        accounting_limit = requested_date or self._today()
        if requested_date is None:
            today = accounting_limit
            latest = self._snapshots.get_latest()
            if (
                latest is not None
                and latest.snapshot_date == today
                and self._is_complete_snapshot(latest)
            ):
                snapshot = latest
                verified_source_date = latest.snapshot_date
                _LOGGER.info(
                    "Using persisted complete snapshot: date=%s "
                    "source=persisted_daily_snapshot+persisted_position_snapshots",
                    latest.snapshot_date,
                )
            else:
                try:
                    update = self._update_portfolio.execute(
                        dry_run=dry_run, force=force
                    )
                    resolved_date = update.requested_date or update.verified_source_date
                    if resolved_date is None:
                        raise DailyReportSnapshotMissingError(
                            "automatic market-date resolution returned no report date"
                        )
                    snapshot = self._snapshots.get_by_date(resolved_date)
                    verified_source_date = update.verified_source_date or resolved_date
                except MarketCalendarUnavailableError as error:
                    snapshot = self._safe_persisted_fallback(today)
                    if snapshot is None:
                        raise DailyReportSnapshotMissingError(
                            "live market availability is temporarily unavailable and "
                            "no complete persisted portfolio snapshot is available"
                        ) from error
                    verified_source_date = snapshot.snapshot_date
                    availability = error.availability
                    _LOGGER.warning(
                        "Live market availability unavailable; using safe persisted "
                        "snapshot: report_date=%s twse_date=%s twse_source=%s "
                        "tpex_date=%s tpex_source=%s fallback_source=%s",
                        snapshot.snapshot_date,
                        availability.twse_date,
                        availability.twse_source,
                        availability.tpex_date,
                        availability.tpex_source,
                        "persisted_daily_snapshot+persisted_position_snapshots",
                    )
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
        if self._annual_pnl is not None:
            self._annual_pnl.ensure(report_date, persist=not dry_run)
        report = self._build_report(
            snapshot,
            verified_source_date or report_date,
            accounting_limit,
        )
        rendered = self._renderer.render(report)
        if dry_run:
            return DailyReportSendResult(
                report_date, self._recipient, rendered.subject, "dry_run"
            )
        if not force:
            self._deliveries.claim(REPORT_TYPE, report_date, self._recipient)
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

    def _safe_persisted_fallback(self, today: date) -> DailySnapshot | None:
        snapshot = self._snapshots.get_latest()
        if (
            snapshot is None
            or snapshot.snapshot_date > today
            or not self._is_complete_snapshot(snapshot)
        ):
            return None
        return snapshot

    def _is_complete_snapshot(self, snapshot: DailySnapshot) -> bool:
        active_holdings = {
            holding.id: holding
            for holding in self._holdings.list_all()
            if holding.quantity > 0
        }
        if not active_holdings:
            return False
        positions = self._positions.list_by_date(snapshot.snapshot_date)
        positions_by_id = {position.holding_id: position for position in positions}
        return (
            len(positions) == len(positions_by_id)
            and positions_by_id.keys() == active_holdings.keys()
            and all(
                position.snapshot_date == snapshot.snapshot_date
                for position in positions
            )
            and all(
                positions_by_id[holding_id].quantity == holding.quantity
                and positions_by_id[holding_id].average_cost == holding.average_cost
                for holding_id, holding in active_holdings.items()
            )
        )

    def _build_report(
        self,
        snapshot: DailySnapshot,
        verified_source_date: date,
        accounting_limit: date | None = None,
    ) -> DailyEmailReport:
        holdings = self._holdings.list_all()
        names = {holding.id: holding.name for holding in holdings}
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
        annual_by_date = (
            {
                item.snapshot_date: item
                for item in self._annual_snapshots.list_between_dates(
                    historical_snapshots[0].snapshot_date,
                    snapshot.snapshot_date,
                )
            }
            if self._annual_snapshots is not None
            else {}
        )
        history = tuple(
            DailyEmailHistoryPoint(
                item.snapshot_date,
                item.total_market_value,
                item.net_asset_value,
                (
                    annual_by_date[item.snapshot_date].total_pnl_ytd
                    if item.snapshot_date in annual_by_date
                    else None
                ),
                (
                    annual_by_date[item.snapshot_date].realized_pnl_ytd
                    if item.snapshot_date in annual_by_date
                    else None
                ),
                (
                    annual_by_date[item.snapshot_date].unrealized_pnl
                    if item.snapshot_date in annual_by_date
                    else None
                ),
                (
                    annual_by_date[item.snapshot_date].dividend_income_ytd
                    if item.snapshot_date in annual_by_date
                    else None
                ),
            )
            for item in historical_snapshots
        )
        quoted_positions = tuple(
            self._position(
                item,
                names.get(item.holding_id, item.symbol),
                contribution_by_holding[item.holding_id].profit_loss,
                contribution_by_holding[item.holding_id].return_percentage,
                contribution_by_holding[item.holding_id].portfolio_profit_loss_share,
            )
            for item in sorted(
                persisted, key=lambda value: (value.market.value, value.symbol)
            )
        )
        quoted_ids = {item.holding_id for item in persisted}
        unavailable_positions = tuple(
            DailyEmailPosition(
                symbol=item.symbol,
                name=item.name,
                quantity=item.quantity,
                average_cost=item.average_cost,
                close_price=None,
                daily_return=None,
                market_value=None,
                unrealized_pnl=None,
                unrealized_return=None,
                portfolio_weight=None,
                daily_profit_loss=None,
                daily_profit_loss_percentage=None,
                daily_profit_loss_share=None,
                market=item.market,
                native_currency=item.currency,
            )
            for item in holdings
            if item.quantity > 0 and item.id not in quoted_ids
        )
        positions = tuple(
            sorted(
                (*quoted_positions, *unavailable_positions),
                key=lambda value: (value.market.value, value.symbol),
            )
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
        annual_performance, annual_warning = self._annual_performance(
            accounting_limit or snapshot.snapshot_date,
            snapshot.snapshot_date,
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
            annual_performance,
            annual_warning,
        )

    def _annual_performance(
        self, accounting_limit: date, valuation_date: date
    ) -> tuple[DailyEmailAnnualPerformance | None, str | None]:
        if self._annual_snapshots is None or self._annual_pnl is None:
            return None, "Annual P/L data is unavailable for this report."
        candidates = [
            item
            for item in self._annual_snapshots.list_for_year(accounting_limit.year)
            if item.snapshot_date <= accounting_limit
            and item.valuation_date == valuation_date
        ]
        if not candidates:
            return (
                None,
                "Annual P/L data is unavailable for the report's accounting "
                "and valuation dates.",
            )
        snapshot = candidates[-1]
        return (
            DailyEmailAnnualPerformance(
                snapshot,
                self._annual_pnl.realized_pnl_by_symbol(as_of=snapshot.snapshot_date),
            ),
            None,
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
            value.market,
            value.native_currency,
            value.quote_date,
            value.fx_rate,
            value.fx_rate_date,
        )

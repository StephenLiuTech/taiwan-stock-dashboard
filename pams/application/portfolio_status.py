"""Operational portfolio status application workflow."""

from decimal import Decimal

from market_calendar import MarketCalendar
from pams.application.dto import (
    HoldingOverview,
    MarketAvailabilitySummary,
    PortfolioOverview,
)
from pams.operations import OperationalStatusService
from repositories.interfaces import (
    HoldingRepository,
    PositionSnapshotRepository,
    PriceQuoteRepository,
    SnapshotRepository,
)


class PortfolioStatusUseCase:
    """Collect status and official availability behind one application boundary."""

    def __init__(
        self,
        calendar: MarketCalendar,
        status_service: OperationalStatusService,
        holdings: HoldingRepository | None = None,
        snapshots: SnapshotRepository | None = None,
        position_snapshots: PositionSnapshotRepository | None = None,
        quotes: PriceQuoteRepository | None = None,
    ) -> None:
        self.calendar = calendar
        self.status_service = status_service
        self.holdings = holdings
        self.snapshots = snapshots
        self.position_snapshots = position_snapshots
        self.quotes = quotes

    def execute(self) -> PortfolioOverview:
        """Return current operational state as an immutable DTO."""
        try:
            availability = self.calendar.market_availability()
        except Exception:
            availability = None
        status = self.status_service.read(availability)
        latest = self.snapshots.get_latest() if self.snapshots else None
        holdings_by_id = (
            {holding.id: holding for holding in self.holdings.list_all()}
            if self.holdings
            else {}
        )
        positions = (
            self.position_snapshots.list_by_date(latest.snapshot_date)
            if latest and self.position_snapshots
            else []
        )
        holding_rows = []
        for position in sorted(
            positions, key=lambda item: item.market_value, reverse=True
        ):
            holding = holdings_by_id.get(position.holding_id)
            if holding is None:
                continue
            quote = (
                self.quotes.get_latest(position.symbol, holding.market.value)
                if self.quotes
                else None
            )
            holding_rows.append(
                HoldingOverview(
                    symbol=position.symbol,
                    name=holding.name,
                    market=holding.market.value,
                    shares=position.quantity,
                    average_cost=position.average_cost,
                    latest_price=position.close_price,
                    market_value=position.market_value,
                    cost_basis=position.cost_basis,
                    unrealized_pnl=position.unrealized_pnl,
                    unrealized_return=position.unrealized_return,
                    portfolio_weight=position.portfolio_weight,
                    quote_date=quote.trade_date if quote else position.snapshot_date,
                )
            )
        return PortfolioOverview(
            database_path=status.database_path,
            latest_quote_date=status.latest_quote_date,
            latest_daily_snapshot=status.latest_daily_snapshot,
            latest_position_snapshot=status.latest_position_snapshot,
            holdings_count=status.holdings_count,
            liabilities_count=status.liabilities_count,
            schema_version=status.schema_version,
            database_size_bytes=status.database_size_bytes,
            market_availability=MarketAvailabilitySummary(
                twse_latest_date=status.twse_latest_source_date,
                tpex_latest_date=status.tpex_latest_source_date,
                commonly_ingestible_date=status.commonly_ingestible_date,
            ),
            market_value=latest.total_market_value if latest else None,
            net_equity=latest.net_asset_value if latest else None,
            unrealized_pnl=latest.total_unrealized_pnl if latest else None,
            todays_pnl=(
                sum(
                    (position.daily_value_change for position in positions),
                    start=Decimal("0"),
                )
                if positions
                else None
            ),
            total_liabilities=latest.total_liabilities if latest else None,
            leverage_ratio=latest.leverage_ratio if latest else None,
            holdings=tuple(holding_rows),
        )

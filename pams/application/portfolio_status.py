"""Operational portfolio status application workflow."""

from market_calendar import MarketCalendar
from pams.application.dto import MarketAvailabilitySummary, PortfolioSummary
from pams.operations import OperationalStatusService


class PortfolioStatusUseCase:
    """Collect status and official availability behind one application boundary."""

    def __init__(
        self, calendar: MarketCalendar, status_service: OperationalStatusService
    ) -> None:
        self.calendar = calendar
        self.status_service = status_service

    def execute(self) -> PortfolioSummary:
        """Return current operational state as an immutable DTO."""
        availability = self.calendar.market_availability()
        status = self.status_service.read(availability)
        return PortfolioSummary(
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
        )

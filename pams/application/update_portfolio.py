"""Portfolio update application workflow."""

from collections.abc import Callable
from datetime import date
from pathlib import Path

from market_calendar import MarketAvailability, MarketCalendar
from market_data.engine import MarketDataEngine, MarketDataRefreshResult
from pams.application.dto import (
    MarketAvailabilitySummary,
    PortfolioTotals,
    PositionSummary,
    UpdateMode,
    UpdateResult,
)


def _availability_dto(value: MarketAvailability) -> MarketAvailabilitySummary:
    return MarketAvailabilitySummary(
        twse_latest_date=value.twse_date,
        tpex_latest_date=value.tpex_date,
        commonly_ingestible_date=value.commonly_ingestible_date,
    )


class UpdatePortfolioUseCase:
    """Resolve, validate, value, and optionally persist one portfolio update."""

    def __init__(
        self,
        calendar: MarketCalendar,
        engine: MarketDataEngine,
        database_path: Path,
        historical_engine_factory: Callable[[date], MarketDataEngine] | None = None,
    ) -> None:
        self.calendar = calendar
        self.engine = engine
        self.database_path = database_path
        self.historical_engine_factory = historical_engine_factory

    def execute(
        self, requested_date: date | None = None, *, dry_run: bool = False
    ) -> UpdateResult:
        """Run an explicit or latest commonly ingestible update."""
        automatic = requested_date is None
        availability = None
        if automatic:
            source_availability = self.calendar.market_availability()
            availability = _availability_dto(source_availability)
            requested_date = source_availability.commonly_ingestible_date
            if requested_date is None:
                return UpdateResult(
                    mode=UpdateMode.SOURCES_UNSYNCHRONIZED,
                    database_path=self.database_path,
                    requested_date=None,
                    verified_source_date=None,
                    availability=availability,
                )

        selected_engine = (
            self.historical_engine_factory(requested_date)
            if self.historical_engine_factory is not None and not automatic
            else self.engine
        )
        engine_result = (
            selected_engine.preview(requested_date)
            if dry_run
            else selected_engine.refresh(requested_date)
        )
        return self._result_dto(
            engine_result, requested_date, dry_run=dry_run, availability=availability
        )

    def _result_dto(
        self,
        result: MarketDataRefreshResult,
        requested_date: date,
        *,
        dry_run: bool,
        availability: MarketAvailabilitySummary | None,
    ) -> UpdateResult:
        holdings = {holding.id: holding for holding in result.holdings}
        quotes = {(quote.symbol, quote.market): quote for quote in result.quotes}
        positions = []
        for position in sorted(result.summary.positions, key=lambda item: item.symbol):
            holding = holdings[position.holding_id]
            quote = quotes[(position.symbol, holding.market)]
            positions.append(
                PositionSummary(
                    symbol=position.symbol,
                    name=holding.name,
                    market=holding.market.value,
                    shares=position.quantity,
                    average_cost=position.average_cost,
                    close_price=position.close_price,
                    previous_close=quote.previous_close,
                    daily_change_percentage=position.daily_return,
                    market_value=position.market_value,
                    unrealized_pnl=position.unrealized_pnl,
                    unrealized_return=position.unrealized_return,
                    portfolio_weight=position.portfolio_weight,
                )
            )
        summary = result.summary
        return UpdateResult(
            mode=UpdateMode.DRY_RUN if dry_run else UpdateMode.UPDATED,
            database_path=self.database_path,
            requested_date=requested_date,
            verified_source_date=result.verified_source_date,
            availability=availability,
            positions=tuple(positions),
            totals=PortfolioTotals(
                total_market_value=summary.total_market_value,
                total_cost_basis=summary.total_cost_basis,
                total_unrealized_pnl=summary.total_unrealized_pnl,
                total_liabilities=summary.total_liabilities,
                net_asset_value=summary.net_asset_value,
                liability_ratio=summary.leverage_ratio,
                position_count=len(summary.positions),
            ),
        )

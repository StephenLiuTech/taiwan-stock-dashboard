"""Portfolio update application workflow."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from domain import Holding
from market_calendar import (
    MarketAvailability,
    MarketCalendar,
    MarketCalendarUnavailableError,
)
from market_data.engine import MarketDataEngine, MarketDataRefreshResult
from pams.application.dto import (
    MarketAvailabilitySummary,
    PortfolioTotals,
    PositionSummary,
    UpdateMode,
    UpdateResult,
)
from repositories import HoldingRepository, SnapshotRepository, TransactionRepository
from services import TransactionEngine


class AnnualPnlWriter(Protocol):
    """Daily annual-P/L persistence boundary used after valuation."""

    def ensure(
        self,
        snapshot_date: date,
        *,
        unrealized_pnl: Decimal | None = None,
        persist: bool = True,
    ) -> object: ...


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
        snapshot_repository: SnapshotRepository | None = None,
        transaction_repository: TransactionRepository | None = None,
        holding_repository: HoldingRepository | None = None,
        transaction_engine: TransactionEngine | None = None,
        prefer_historical_for_automatic: bool = False,
        annual_pnl: AnnualPnlWriter | None = None,
    ) -> None:
        self.calendar = calendar
        self.engine = engine
        self.database_path = database_path
        self.historical_engine_factory = historical_engine_factory
        self.snapshot_repository = snapshot_repository
        self.transaction_repository = transaction_repository
        self.holding_repository = holding_repository
        self.transaction_engine = transaction_engine or TransactionEngine()
        self.prefer_historical_for_automatic = prefer_historical_for_automatic
        self.annual_pnl = annual_pnl

    def execute(
        self,
        requested_date: date | None = None,
        *,
        dry_run: bool = False,
        force: bool = False,
    ) -> UpdateResult:
        """Run an explicit update or the newest jointly available market date."""
        automatic = requested_date is None
        availability = None
        sources_synchronized = True
        if automatic:
            source_availability = self.calendar.market_availability()
            availability = _availability_dto(source_availability)
            requested_date = source_availability.commonly_ingestible_date
            sources_synchronized = source_availability.synchronized
            if requested_date is None:
                raise MarketCalendarUnavailableError(source_availability)

        needs_historical_provider = (
            not automatic
            or not sources_synchronized
            or self.prefer_historical_for_automatic
        )
        if needs_historical_provider and self.historical_engine_factory is None:
            raise ValueError(
                "Historical market-data providers are required for the requested date"
            )
        selected_engine = (
            self.historical_engine_factory(requested_date)
            if needs_historical_provider
            else self.engine
        )
        projected_holdings = self._project_current_holdings()
        existing_snapshot = (
            self.snapshot_repository.get_by_date(requested_date)
            if self.snapshot_repository is not None
            else None
        )
        enrichment_required = (
            existing_snapshot is not None
            and not force
            and hasattr(selected_engine, "requires_enrichment")
            and selected_engine.requires_enrichment(
                requested_date, holdings_override=projected_holdings
            )
        )
        if existing_snapshot is not None and not force and not enrichment_required:
            if self.annual_pnl is not None:
                self.annual_pnl.ensure(requested_date, persist=not dry_run)
            return UpdateResult(
                mode=UpdateMode.SNAPSHOT_EXISTS,
                database_path=self.database_path,
                requested_date=requested_date,
                verified_source_date=None,
                availability=availability,
            )
        if dry_run:
            engine_result = (
                selected_engine.preview(requested_date)
                if projected_holdings is None
                else selected_engine.preview(
                    requested_date, holdings_override=projected_holdings
                )
            )
        elif enrichment_required:
            engine_result = selected_engine.enrich_existing(
                requested_date, holdings_override=projected_holdings
            )
        elif force:
            engine_result = (
                selected_engine.rebuild(requested_date)
                if projected_holdings is None
                else selected_engine.rebuild(
                    requested_date, holdings_override=projected_holdings
                )
            )
        else:
            engine_result = (
                selected_engine.refresh(requested_date)
                if projected_holdings is None
                else selected_engine.refresh(
                    requested_date, holdings_override=projected_holdings
                )
            )
        if self.annual_pnl is not None:
            self.annual_pnl.ensure(
                requested_date,
                unrealized_pnl=engine_result.summary.total_unrealized_pnl,
                persist=not dry_run,
            )
        return self._result_dto(
            engine_result,
            requested_date,
            dry_run=dry_run,
            availability=availability,
            enriched=enrichment_required,
        )

    def _project_current_holdings(self) -> tuple[Holding, ...] | None:
        if self.transaction_repository is None or self.holding_repository is None:
            return None
        return self.transaction_engine.project_current_holdings(
            self.transaction_repository.list_all(),
            self.holding_repository.list_all(),
        )

    def _result_dto(
        self,
        result: MarketDataRefreshResult,
        requested_date: date,
        *,
        dry_run: bool,
        availability: MarketAvailabilitySummary | None,
        enriched: bool = False,
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
            mode=(
                UpdateMode.DRY_RUN
                if dry_run
                else (UpdateMode.ENRICHED if enriched else UpdateMode.UPDATED)
            ),
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

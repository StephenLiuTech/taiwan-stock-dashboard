"""Deterministic, isolated demo database generation workflow."""

import os
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from database import initialize_database, initialize_schema
from domain import Currency, PositionSnapshot, PriceQuote
from pams.application.dto import DemoDataResult
from pams.application.exceptions import ProductionDatabaseProtectedError
from repositories import (
    SQLiteHoldingRepository,
    SQLiteLiabilityRepository,
    SQLitePositionSnapshotRepository,
    SQLitePriceQuoteRepository,
    SQLiteSnapshotRepository,
)
from services import PortfolioService, SnapshotService
from services.bootstrap import SEED_HOLDINGS, SEED_LIABILITIES

DEMO_QUOTE_DATE = date(2026, 7, 22)
DEMO_HISTORY_DAYS = 30
DEMO_LATEST_PRICES = {
    "0050": Decimal("92.30"),
    "2027": Decimal("44.80"),
    "2330": Decimal("1955.00"),
    "3293": Decimal("910.00"),
    "8299": Decimal("2685.00"),
}


class DemoDataUseCase:
    """Create a replaceable synthetic database without provider access."""

    def __init__(self, production_database_path: Path) -> None:
        self.production_database_path = production_database_path.resolve()

    def execute(self, database_path: Path) -> DemoDataResult:
        """Build a complete temporary database and atomically publish it."""
        target = database_path.expanduser().resolve()
        if target == self.production_database_path:
            raise ProductionDatabaseProtectedError(
                f"refusing to overwrite production database: {target}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.building")
        if temporary.exists():
            temporary.unlink()

        connection = initialize_database(f"sqlite:///{temporary.as_posix()}")
        try:
            initialize_schema(connection)
            holdings = SQLiteHoldingRepository(connection, auto_commit=False)
            liabilities = SQLiteLiabilityRepository(connection, auto_commit=False)
            quotes = SQLitePriceQuoteRepository(connection, auto_commit=False)
            snapshots = SQLiteSnapshotRepository(connection, auto_commit=False)
            position_snapshots = SQLitePositionSnapshotRepository(
                connection, auto_commit=False
            )
            portfolio = PortfolioService()
            snapshot_service = SnapshotService(snapshots)

            connection.execute("BEGIN IMMEDIATE")
            try:
                for holding in SEED_HOLDINGS:
                    holdings.upsert(holding)
                for liability in SEED_LIABILITIES:
                    liabilities.upsert(liability)

                previous_prices: dict[str, Decimal] = {}
                final_summary = None
                for offset in range(DEMO_HISTORY_DAYS):
                    trade_date = DEMO_QUOTE_DATE - timedelta(
                        days=DEMO_HISTORY_DAYS - offset - 1
                    )
                    factor = Decimal("0.94") + (
                        Decimal("0.06") * Decimal(offset) / Decimal("29")
                    )
                    daily_quotes = []
                    for holding in SEED_HOLDINGS:
                        final_price = DEMO_LATEST_PRICES[holding.symbol]
                        close = (
                            final_price
                            if offset == DEMO_HISTORY_DAYS - 1
                            else (final_price * factor).quantize(
                                Decimal("0.01"), rounding=ROUND_HALF_UP
                            )
                        )
                        daily_quotes.append(
                            PriceQuote(
                                symbol=holding.symbol,
                                market=holding.market,
                                trade_date=trade_date,
                                close_price=close,
                                previous_close=previous_prices.get(holding.symbol),
                                currency=Currency.TWD,
                                source="demo_fixture",
                                fetched_at=datetime(2026, 7, 22, tzinfo=UTC),
                            )
                        )
                        previous_prices[holding.symbol] = close
                    quotes.upsert_many(daily_quotes)
                    summary = portfolio.value_portfolio(
                        list(SEED_HOLDINGS),
                        daily_quotes,
                        list(SEED_LIABILITIES),
                        trade_date,
                    )
                    snapshot_service.create(summary)
                    position_snapshots.add_many(
                        [
                            PositionSnapshot(
                                snapshot_date=trade_date, **position.model_dump()
                            )
                            for position in summary.positions
                        ]
                    )
                    final_summary = summary
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            assert final_summary is not None
            result = DemoDataResult(
                database_path=target,
                holdings_count=len(SEED_HOLDINGS),
                liabilities_count=len(SEED_LIABILITIES),
                quote_date=DEMO_QUOTE_DATE,
                history_points=DEMO_HISTORY_DAYS,
                total_market_value=final_summary.total_market_value,
                total_cost_basis=final_summary.total_cost_basis,
                total_unrealized_pnl=final_summary.total_unrealized_pnl,
                total_liabilities=final_summary.total_liabilities,
                net_equity=final_summary.net_asset_value,
            )
        except Exception:
            connection.close()
            if temporary.exists():
                temporary.unlink()
            raise
        else:
            connection.close()
            try:
                os.replace(temporary, target)
            except Exception:
                if temporary.exists():
                    temporary.unlink()
                raise
            return result

"""Pure deterministic calculations for modular daily-report sections."""

import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from domain import (
    AllocationItem,
    CollateralHoldingReference,
    DividendCalendarItem,
    DividendCalendarSection,
    DividendEvent,
    FinancingLeverageSection,
    Holding,
    HoldingType,
    Liability,
    LiabilityType,
    MarginFinancingSection,
    NewsItem,
    NewsSection,
    PortfolioAllocationSection,
    PortfolioInsightsSection,
    PositionSnapshot,
    RiskItem,
    RiskMonitorSection,
    StockPledgeSection,
    Transaction,
    TransactionSummaryItem,
    TransactionSummarySection,
    TransactionType,
    UpcomingEventItem,
    UpcomingEventsSection,
)
from services.transaction_engine import TransactionEngine


def _metadata_money(notes: str | None, label: str) -> Decimal | None:
    if not notes:
        return None
    match = re.search(rf"{re.escape(label)}:\s*NT\$([\d,]+(?:\.\d+)?)", notes)
    return Decimal(match.group(1).replace(",", "")) if match else None


def _metadata_percent(notes: str | None, label: str) -> Decimal | None:
    if not notes:
        return None
    match = re.search(rf"{re.escape(label)}:\s*([\d,]+(?:\.\d+)?)%", notes)
    return Decimal(match.group(1).replace(",", "")) / 100 if match else None


def _metadata_updated_date(notes: str | None) -> date | None:
    if not notes:
        return None
    match = re.search(r"Source/update:.*?(\d{4}-\d{2}-\d{2})", notes)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def _margin_reference(description: str | None) -> tuple[str | None, Decimal | None]:
    if not description:
        return None, None
    match = re.search(
        r"([0-9A-Z]+)\s+margin quantity:\s*([\d,]+)\s+shares", description
    )
    if not match:
        return None, None
    return match.group(1), Decimal(match.group(2).replace(",", ""))


def _collateral_references(
    description: str | None,
) -> tuple[CollateralHoldingReference, ...]:
    if not description:
        return ()
    return tuple(
        CollateralHoldingReference(symbol, Decimal(quantity.replace(",", "")))
        for symbol, quantity in re.findall(
            r"([0-9A-Z]+):\s*([\d,]+)\s+shares", description
        )
    )


class ReportSectionService:
    """Build report facts from repository-loaded domain records."""

    @staticmethod
    def financing(
        liabilities: list[Liability],
        total_market_value: Decimal,
        net_asset_value: Decimal,
        liability_ratio: Decimal,
    ) -> FinancingLeverageSection | None:
        """Build financing facts from principal and descriptive metadata."""
        if not liabilities:
            return None
        principal = sum((item.principal for item in liabilities), Decimal("0"))
        accrued_values = [
            _metadata_money(item.notes, "Accrued interest reference")
            for item in liabilities
        ]
        total_accrued = (
            sum((item for item in accrued_values if item is not None), Decimal("0"))
            if all(item is not None for item in accrued_values)
            else None
        )
        margin = next(
            (
                item
                for item in liabilities
                if item.liability_type is LiabilityType.MARGIN_FINANCING
            ),
            None,
        )
        pledge = next(
            (
                item
                for item in liabilities
                if item.liability_type is LiabilityType.STOCK_PLEDGE
            ),
            None,
        )
        margin_reference = (
            _margin_reference(margin.collateral_description) if margin else (None, None)
        )
        margin_section = (
            MarginFinancingSection(
                margin.principal,
                _metadata_money(margin.notes, "Accrued interest reference"),
                margin_reference[0],
                margin_reference[1],
                _metadata_updated_date(margin.notes),
            )
            if margin
            else None
        )
        pledge_section = (
            StockPledgeSection(
                pledge.principal,
                _metadata_money(pledge.notes, "Accrued interest reference"),
                _metadata_money(pledge.notes, "Repayment total reference"),
                _metadata_money(pledge.notes, "Collateral market value reference"),
                _metadata_percent(pledge.notes, "Maintenance ratio reference"),
                _collateral_references(pledge.collateral_description),
                _metadata_updated_date(pledge.notes),
            )
            if pledge
            else None
        )
        return FinancingLeverageSection(
            principal,
            total_accrued,
            principal + total_accrued if total_accrued is not None else None,
            total_market_value,
            net_asset_value,
            liability_ratio,
            margin_section,
            pledge_section,
        )

    @staticmethod
    def allocation(
        positions: list[PositionSnapshot], holdings: list[Holding]
    ) -> PortfolioAllocationSection:
        metadata = {item.id: item for item in holdings}
        total = sum((item.market_value for item in positions), Decimal("0"))

        def weight(value: Decimal) -> Decimal:
            return value / total if total else Decimal("0")

        by_holding = tuple(
            AllocationItem(
                item.symbol,
                metadata[item.holding_id].name if item.holding_id in metadata else None,
                item.market_value,
                weight(item.market_value),
            )
            for item in sorted(
                positions, key=lambda value: (-value.market_value, value.symbol)
            )
        )
        market_values: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        instrument_values: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        quoted_ids = {item.holding_id for item in positions}
        for item in positions:
            holding = metadata.get(item.holding_id)
            market_values[
                holding.market.value if holding else "Unknown"
            ] += item.market_value
            kind = (
                "ETF"
                if holding and holding.holding_type is HoldingType.ETF
                else (
                    "Individual Stock"
                    if holding and holding.holding_type is HoldingType.STOCK
                    else "Unknown"
                )
            )
            instrument_values[kind] += item.market_value
        groups = lambda values: tuple(  # noqa: E731
            AllocationItem(label, None, value, weight(value))
            for label, value in sorted(values.items())
        )
        unquoted = tuple(
            sorted(
                f"{item.symbol} ({item.market.value})"
                for item in holdings
                if item.quantity > 0 and item.id not in quoted_ids
            )
        )
        return PortfolioAllocationSection(
            by_holding, groups(market_values), groups(instrument_values), unquoted
        )

    @staticmethod
    def insights(
        allocation: PortfolioAllocationSection,
        positions: list[PositionSnapshot],
        daily_profit_loss: Decimal,
        dividends: DividendCalendarSection | None = None,
    ) -> PortfolioInsightsSection:
        insights: list[str] = []
        if allocation.by_holding:
            largest = allocation.by_holding[0]
            insights.append(
                f"{largest.label} is the largest holding at "
                f"{largest.weight * 100:.2f}% of quoted market value."
            )
            top_two = sum(
                (item.weight for item in allocation.by_holding[:2]), Decimal("0")
            )
            if len(allocation.by_holding) >= 2:
                insights.append(
                    f"The two largest holdings account for {top_two * 100:.2f}% "
                    "of quoted market value."
                )
        if positions:
            contributor = max(
                positions, key=lambda item: (abs(item.daily_value_change), item.symbol)
            )
            insights.append(
                f"{contributor.symbol} had the largest absolute daily P/L impact."
            )
            insights.append(
                f"Portfolio market value changed by NT${daily_profit_loss:,.0f} today."
            )
        if allocation.unquoted_holdings:
            insights.append(
                f"{len(allocation.unquoted_holdings)} active holding(s) have no eligible quote."
            )
        if dividends is not None:
            available = [
                item
                for item in dividends.items
                if item.estimated_cash_dividend is not None
            ]
            if available:
                largest = max(
                    available,
                    key=lambda item: (item.estimated_cash_dividend, item.symbol),
                )
                insights.extend(
                    (
                        f"Largest expected dividend: {largest.symbol} "
                        f"NT${largest.estimated_cash_dividend:,.0f}",
                        f"Already received: NT${dividends.already_received:,.0f}",
                        "Remaining expected this year: "
                        f"NT${dividends.estimated_annual_dividend - dividends.already_received:,.0f}",
                    )
                )
        return PortfolioInsightsSection(tuple(insights))

    @staticmethod
    def risk(
        allocation: PortfolioAllocationSection,
        positions: list[PositionSnapshot],
        *,
        single_threshold: Decimal,
        top3_threshold: Decimal,
        market_threshold: Decimal,
    ) -> RiskMonitorSection:
        holding_weights = [item.weight for item in allocation.by_holding]
        largest = max(holding_weights, default=Decimal("0"))
        top3 = sum(holding_weights[:3], Decimal("0"))
        largest_market = max(
            (item.weight for item in allocation.by_market), default=Decimal("0")
        )
        instrument_weights = {
            item.label: item.weight for item in allocation.by_instrument
        }
        largest_loss = min(
            positions, key=lambda item: (item.unrealized_pnl, item.symbol), default=None
        )
        daily_loss = min(
            positions,
            key=lambda item: (item.daily_value_change, item.symbol),
            default=None,
        )
        return RiskMonitorSection(
            (
                RiskItem(
                    "Largest holding weight",
                    f"{largest * 100:.2f}%",
                    largest >= single_threshold,
                ),
                RiskItem(
                    "Top 3 concentration", f"{top3 * 100:.2f}%", top3 >= top3_threshold
                ),
                RiskItem(
                    "Largest market concentration",
                    f"{largest_market * 100:.2f}%",
                    largest_market >= market_threshold,
                ),
                RiskItem(
                    "ETF concentration",
                    f"{instrument_weights.get('ETF', Decimal('0')) * 100:.2f}%",
                ),
                RiskItem(
                    "Individual-stock concentration",
                    f"{instrument_weights.get('Individual Stock', Decimal('0')) * 100:.2f}%",
                ),
                RiskItem("Active quoted holdings", str(len(positions))),
                RiskItem(
                    "Holdings without eligible quote",
                    str(len(allocation.unquoted_holdings)),
                    bool(allocation.unquoted_holdings),
                ),
                RiskItem(
                    "Largest unrealized loss",
                    (
                        f"{largest_loss.symbol}: NT${largest_loss.unrealized_pnl:,.0f}"
                        if largest_loss
                        else "N/A"
                    ),
                    bool(largest_loss and largest_loss.unrealized_pnl < 0),
                ),
                RiskItem(
                    "Largest daily loss contributor",
                    (
                        f"{daily_loss.symbol}: NT${daily_loss.daily_value_change:,.0f}"
                        if daily_loss
                        else "N/A"
                    ),
                    bool(daily_loss and daily_loss.daily_value_change < 0),
                ),
            )
        )

    @staticmethod
    def transaction_summary(
        report_date: date, transactions: list[Transaction], names: dict[str, str]
    ) -> TransactionSummarySection:
        items = []
        for transaction in sorted(
            (item for item in transactions if item.trade_date == report_date),
            key=lambda item: (
                0 if item.transaction_type is TransactionType.BUY else 1,
                item.id,
            ),
        ):
            gross = transaction.quantity * transaction.price
            net = (
                -(gross + transaction.fees + transaction.taxes)
                if transaction.transaction_type is TransactionType.BUY
                else gross - transaction.fees - transaction.taxes
            )
            items.append(
                TransactionSummaryItem(
                    transaction.transaction_type.value,
                    transaction.symbol,
                    names.get(transaction.symbol, transaction.symbol),
                    transaction.market.value,
                    transaction.quantity,
                    transaction.price,
                    gross,
                    transaction.fees,
                    transaction.taxes,
                    net,
                )
            )
        same_day = [item for item in transactions if item.trade_date == report_date]
        expenses = TransactionEngine.summarize_expenses(same_day)
        return TransactionSummarySection(
            tuple(items),
            expenses.total_buy_fees,
            expenses.total_sell_fees,
            expenses.total_taxes,
        )

    @staticmethod
    def dividends(
        report_date: date,
        dividends: list[DividendEvent],
        eligible_quantities: dict[tuple[str, date], Decimal],
    ) -> DividendCalendarSection:
        def status(value: DividendEvent) -> str:
            if report_date < value.ex_dividend_date:
                return "Upcoming Ex-Date"
            if value.payment_date is None:
                return "Unknown Payment Date"
            if report_date < value.payment_date:
                return "Waiting for Payment"
            return "Paid"

        def estimated(value: DividendEvent) -> Decimal | None:
            quantity = eligible_quantities.get(
                (value.symbol, value.ex_dividend_date), Decimal("0")
            )
            return (
                quantity * value.cash_dividend_per_share
                if value.cash_dividend_per_share is not None
                else None
            )

        def actual(value: DividendEvent) -> Decimal | None:
            amount = estimated(value)
            if amount is None:
                return None
            if value.payment_date is not None and value.payment_date <= report_date:
                return amount
            return Decimal("0")

        items = tuple(
            DividendCalendarItem(
                item.symbol,
                item.name,
                item.ex_dividend_date,
                item.record_date,
                item.payment_date,
                (
                    "Cash dividend"
                    if item.cash_dividend_per_share is not None
                    else "Stock dividend"
                ),
                item.cash_dividend_per_share,
                eligible_quantities.get(
                    (item.symbol, item.ex_dividend_date), Decimal("0")
                ),
                estimated(item),
                actual(item),
                status(item),
                item.source,
            )
            for item in sorted(
                dividends, key=lambda value: (value.ex_dividend_date, value.symbol)
            )
        )
        totals = {
            "Paid": Decimal("0"),
            "Waiting for Payment": Decimal("0"),
            "Unknown Payment Date": Decimal("0"),
            "Upcoming Ex-Date": Decimal("0"),
        }
        annual = Decimal("0")
        for item in items:
            if (
                item.ex_dividend_date.year == report_date.year
                and item.estimated_cash_dividend is not None
            ):
                annual += item.estimated_cash_dividend
                totals[item.status] += item.estimated_cash_dividend
        return DividendCalendarSection(
            items,
            estimated_annual_dividend=annual,
            already_received=sum(
                (
                    item.actual_cash_received
                    for item in items
                    if item.ex_dividend_date.year == report_date.year
                    and item.actual_cash_received is not None
                ),
                Decimal("0"),
            ),
            waiting_for_payment=totals["Waiting for Payment"],
            upcoming_ex_date=totals["Upcoming Ex-Date"],
            unknown_payment_date=totals["Unknown Payment Date"],
        )

    @staticmethod
    def upcoming(
        report_date: date,
        horizon_days: int,
        dividends: DividendCalendarSection,
        holding_symbols: set[str],
        external: tuple[UpcomingEventItem, ...] = (),
    ) -> UpcomingEventsSection:
        end = report_date + timedelta(days=horizon_days)
        dividend_events = (
            UpcomingEventItem(
                item.ex_dividend_date,
                "Dividend",
                item.symbol,
                f"{item.symbol} ex-dividend date",
                item.symbol in holding_symbols,
                item.source_status,
            )
            for item in dividends.items
        )
        items = tuple(
            sorted(
                (
                    item
                    for item in (*dividend_events, *external)
                    if report_date <= item.event_date <= end
                ),
                key=lambda item: (
                    item.event_date,
                    item.event_type,
                    item.symbol_or_scope,
                ),
            )
        )
        return UpcomingEventsSection(items)


class NewsService:
    """Normalize provider results through deterministic deduplication and limits."""

    @staticmethod
    def select(items: tuple[NewsItem, ...], maximum: int = 5) -> NewsSection:
        selected: list[NewsItem] = []
        seen: set[str] = set()
        for item in sorted(
            items, key=lambda value: (-value.published_at.timestamp(), value.headline)
        ):
            key = " ".join(item.headline.lower().split())
            if key in seen:
                continue
            seen.add(key)
            selected.append(item)
            if len(selected) == maximum:
                break
        return NewsSection(tuple(selected), "available")

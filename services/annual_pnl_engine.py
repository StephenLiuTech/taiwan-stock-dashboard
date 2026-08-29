"""Pure calendar-year investment profit/loss calculations."""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from domain import (
    AnnualPnlSnapshot,
    CorporateAction,
    Currency,
    DividendEvent,
    InvestmentCostEvent,
    InvestmentCostType,
    Market,
    Transaction,
    TransactionType,
)
from services.transaction_engine import TransactionEngine


class AnnualPnlFxUnavailableError(ValueError):
    """A historical native-currency cash flow cannot be translated safely."""


class AnnualPnlEngine:
    """Build deterministic TWD annual P/L from ledger-derived facts."""

    def __init__(self, transactions: TransactionEngine | None = None) -> None:
        self.transactions = transactions or TransactionEngine()

    def calculate(
        self,
        snapshot_date: date,
        transactions: list[Transaction],
        unrealized_pnl: Decimal,
        dividends: list[DividendEvent],
        costs: list[InvestmentCostEvent],
        exchange_rates: Mapping[tuple[Currency, date], Decimal],
        corporate_actions: list[CorporateAction] | None = None,
        *,
        valuation_date: date | None = None,
    ) -> AnnualPnlSnapshot:
        eligible = [item for item in transactions if item.trade_date <= snapshot_date]
        eligible_actions = (
            [item for item in corporate_actions if item.effective_date <= snapshot_date]
            if corporate_actions is not None
            else None
        )
        ledger = self.transactions.build_ledger(eligible, eligible_actions)
        realized = sum(
            (
                self._convert(
                    sale.realized_pnl,
                    sale.currency,
                    sale.trade_date,
                    exchange_rates,
                )
                for sale in ledger.realized_sales
                if sale.trade_date.year == snapshot_date.year
            ),
            Decimal("0"),
        )
        other = sum(
            (
                self._convert(
                    item.fees + item.taxes,
                    item.currency,
                    item.trade_date,
                    exchange_rates,
                )
                for item in eligible
                if item.trade_date.year == snapshot_date.year
                and item.transaction_type is TransactionType.BUY
            ),
            Decimal("0"),
        )
        financing = Decimal("0")
        for event in costs:
            if (
                event.event_date.year != snapshot_date.year
                or event.event_date > snapshot_date
            ):
                continue
            amount = self._convert(
                event.amount, event.currency, event.event_date, exchange_rates
            )
            if event.cost_type is InvestmentCostType.FINANCING:
                financing += amount
            else:
                other += amount
        dividends_ytd = self._dividends(
            snapshot_date,
            eligible,
            dividends,
            exchange_rates,
            eligible_actions,
        )
        total = realized + unrealized_pnl + dividends_ytd - financing - other
        return AnnualPnlSnapshot(
            snapshot_date=snapshot_date,
            valuation_date=valuation_date or snapshot_date,
            year=snapshot_date.year,
            realized_pnl_ytd=realized,
            unrealized_pnl=unrealized_pnl,
            dividend_income_ytd=dividends_ytd,
            financing_cost_ytd=financing,
            other_cost_ytd=other,
            total_pnl_ytd=total,
        )

    def _dividends(
        self,
        snapshot_date: date,
        transactions: list[Transaction],
        dividends: list[DividendEvent],
        exchange_rates: Mapping[tuple[Currency, date], Decimal],
        corporate_actions: list[CorporateAction] | None,
    ) -> Decimal:
        total = Decimal("0")
        for event in dividends:
            if (
                event.payment_date is None
                or event.payment_date.year != snapshot_date.year
                or event.payment_date > snapshot_date
                or event.cash_dividend_per_share is None
            ):
                continue
            ledger = self.transactions.build_ledger(
                [
                    item
                    for item in transactions
                    if item.trade_date < event.ex_dividend_date
                ],
                (
                    [
                        item
                        for item in corporate_actions
                        if item.effective_date < event.ex_dividend_date
                    ]
                    if corporate_actions is not None
                    else None
                ),
            )
            quantity = sum(
                (
                    position.quantity
                    for position in ledger.positions
                    if position.symbol == event.symbol
                    and position.market is event.market
                ),
                Decimal("0"),
            )
            amount = quantity * event.cash_dividend_per_share
            currency = Currency.USD if event.market is Market.US else Currency.TWD
            total += self._convert(
                amount,
                currency,
                event.payment_date,
                exchange_rates,
            )
        return total

    @staticmethod
    def _convert(
        amount: Decimal,
        currency: Currency,
        effective_date: date,
        rates: Mapping[tuple[Currency, date], Decimal],
    ) -> Decimal:
        if currency is Currency.TWD:
            return amount
        rate = rates.get((currency, effective_date))
        if rate is None:
            raise AnnualPnlFxUnavailableError(
                f"historical {currency.value}/TWD FX is unavailable for {effective_date}"
            )
        return amount * rate

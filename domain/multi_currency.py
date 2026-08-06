"""Immutable constant-report-date FX valuation results."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from domain.enums import Currency, Market


@dataclass(frozen=True)
class TranslatedHoldingValuation:
    """One holding retaining native values and translated TWD results."""

    market: Market
    symbol: str
    name: str
    native_currency: Currency
    quantity: Decimal
    native_average_cost: Decimal
    native_close: Decimal
    quote_date: date
    fx_rate: Decimal
    fx_rate_date: date | None
    reporting_currency: Currency
    cost_basis_twd: Decimal
    market_value_twd: Decimal
    daily_pnl_twd: Decimal
    daily_return_pct: Decimal
    unrealized_pnl_twd: Decimal
    unrealized_return_pct: Decimal
    portfolio_weight: Decimal = Decimal("0")

    @property
    def average_cost(self) -> Decimal:
        """Expose the native average cost through the shared presentation contract."""
        return self.native_average_cost

    @property
    def last_price(self) -> Decimal:
        """Expose the native close through the shared presentation contract."""
        return self.native_close

    @property
    def cost_basis(self) -> Decimal:
        return self.cost_basis_twd

    @property
    def market_value(self) -> Decimal:
        return self.market_value_twd

    @property
    def unrealized_pl(self) -> Decimal:
        return self.unrealized_pnl_twd

    @property
    def unrealized_return(self) -> Decimal:
        return self.unrealized_return_pct


@dataclass(frozen=True)
class MultiCurrencyPortfolioValuation:
    """Unified TWD totals with independently dated native market closes."""

    report_date: date
    reporting_currency: Currency
    holdings: tuple[TranslatedHoldingValuation, ...]
    total_cost_twd: Decimal
    total_market_value_twd: Decimal
    total_unrealized_pnl_twd: Decimal
    total_return: Decimal
    taiwan_market_value_twd: Decimal
    us_market_value_twd: Decimal

    @property
    def valuation_date(self) -> date:
        return self.report_date

    @property
    def total_cost(self) -> Decimal:
        return self.total_cost_twd

    @property
    def total_market_value(self) -> Decimal:
        return self.total_market_value_twd

    @property
    def total_unrealized_pl(self) -> Decimal:
        return self.total_unrealized_pnl_twd

"""Presentation-only rendering for annual investment P/L queries."""

import json
from dataclasses import asdict
from decimal import Decimal

from domain import AnnualPnlSnapshot, Currency, RealizedSale
from pams.application import AnnualPnlHistory


def _json_default(value: object) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()  # type: ignore[union-attr]
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "value"):
        return str(value.value)  # type: ignore[union-attr]
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _money(value: Decimal) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}NT${value:,.2f}"


def _native_money(value: Decimal, currency: Currency, *, signed: bool = False) -> str:
    prefix = "+" if signed and value > 0 else "-" if value < 0 else ""
    symbol = "US$" if currency is Currency.USD else "NT$"
    return f"{prefix}{symbol}{abs(value):,.4f}".rstrip("0").rstrip(".")


def format_realized_sales(sales: tuple[RealizedSale, ...], *, json_output: bool) -> str:
    """Render ledger-derived realized sales without recalculation."""
    if json_output:
        return json.dumps(
            [asdict(item) for item in sales],
            default=_json_default,
            ensure_ascii=False,
            indent=2,
        )
    lines = ["Realized P/L Transactions", f"Count: {len(sales)}"]
    for item in sales:
        lines.extend(
            [
                "",
                f"{item.trade_date} {item.market} {item.symbol}",
                f"Quantity sold: {item.quantity_sold}",
                f"Average cost basis: {item.average_cost_basis}",
                f"Total cost basis: {_native_money(item.total_cost_basis, item.currency)}",
                f"Gross proceeds: {_native_money(item.gross_proceeds, item.currency)}",
                f"Fees: {_native_money(item.fees, item.currency)}",
                f"Taxes: {_native_money(item.taxes, item.currency)}",
                f"Net proceeds: {_native_money(item.net_proceeds, item.currency)}",
                "Realized P/L: "
                f"{_native_money(item.realized_pnl, item.currency, signed=True)}",
                f"Realized return: {item.realized_return:.2%}",
            ]
        )
    return "\n".join(lines)


def format_annual_summary(
    snapshot: AnnualPnlSnapshot | None, *, json_output: bool
) -> str:
    """Render one immutable annual P/L snapshot."""
    if snapshot is None:
        return "null" if json_output else "No annual P/L snapshots found."
    if json_output:
        return snapshot.model_dump_json(indent=2)
    return "\n".join(
        [
            "Annual / YTD P&L",
            f"Accounting date: {snapshot.snapshot_date}",
            f"Market valuation date: {snapshot.valuation_date}",
            f"Realized P/L YTD: {_money(snapshot.realized_pnl_ytd)}",
            f"Unrealized P/L: {_money(snapshot.unrealized_pnl)}",
            f"Dividend income YTD: {_money(snapshot.dividend_income_ytd)}",
            f"Financing cost YTD: {_money(snapshot.financing_cost_ytd)}",
            f"Other cost YTD: {_money(snapshot.other_cost_ytd)}",
            f"Total P/L YTD: {_money(snapshot.total_pnl_ytd)}",
        ]
    )


def format_annual_history(history: AnnualPnlHistory, *, json_output: bool) -> str:
    """Render the immutable annual daily time series."""
    if json_output:
        return json.dumps(
            [item.model_dump(mode="json") for item in history.snapshots],
            ensure_ascii=False,
            indent=2,
        )
    lines = ["Annual P/L History", f"Year: {history.year}"]
    lines.extend(
        f"{item.snapshot_date}  Total {_money(item.total_pnl_ytd)}  "
        f"Realized {_money(item.realized_pnl_ytd)}  "
        f"Unrealized {_money(item.unrealized_pnl)}  "
        f"Dividend {_money(item.dividend_income_ytd)}"
        for item in history.snapshots
    )
    return "\n".join(lines)

"""Legacy human-readable and JSON rendering of application DTOs."""

import json
from datetime import date
from decimal import Decimal

from pams.application import (
    DemoDataResult,
    HoldingChangePlan,
    HoldingsQueryResult,
    PortfolioOverview,
    PortfolioValuation,
    TransactionList,
    TransactionRecord,
    UpdateMode,
    UpdateResult,
    VerificationReport,
)


def format_decimal(value: Decimal) -> str:
    """Format a monetary or quantity value with separators and two decimals."""
    return f"{value:,.2f}"


def format_percentage(value: Decimal | None) -> str:
    """Format a decimal-fraction percentage or an unavailable marker."""
    return "N/A" if value is None else f"{value * 100:,.2f}%"


def format_human_report(result: UpdateResult) -> str:
    """Render one update result without business calculations."""
    if result.mode is UpdateMode.SNAPSHOT_EXISTS:
        return "\n".join(
            [
                "PAMS Market Data Update",
                "No update performed: snapshot already exists for "
                f"{result.requested_date}",
                f"Database: {result.database_path}",
            ]
        )
    if result.mode is UpdateMode.SOURCES_UNSYNCHRONIZED:
        availability = result.availability
        assert availability is not None
        return "\n".join(
            [
                "PAMS Market Data Update",
                "Result: no update performed; official sources are not synchronized",
                f"TWSE latest source date: {availability.twse_latest_date}",
                f"TPEx latest source date: {availability.tpex_latest_date}",
                f"Database: {result.database_path}",
            ]
        )
    assert result.requested_date is not None
    assert result.verified_source_date is not None
    assert result.totals is not None
    mode = "dry-run" if result.mode is UpdateMode.DRY_RUN else "persisted"
    lines = [
        "PAMS Market Data Update",
        f"Requested trade date: {result.requested_date}",
        f"Verified source date: {result.verified_source_date}",
        f"Database: {result.database_path}",
        f"Mode: {mode}",
        "",
        "Positions:",
    ]
    for item in result.positions:
        lines.extend(
            [
                f"  {item.symbol} {item.name} ({item.market})",
                f"    Shares: {format_decimal(item.shares)}",
                f"    Average cost: {format_decimal(item.average_cost)}",
                f"    Close / previous: {format_decimal(item.close_price)} / "
                f"{format_decimal(item.previous_close) if item.previous_close is not None else 'N/A'}",
                f"    Daily change: {format_percentage(item.daily_change_percentage)}",
                f"    Market value: {format_decimal(item.market_value)}",
                f"    Unrealized P/L: {format_decimal(item.unrealized_pnl)} "
                f"({format_percentage(item.unrealized_return)})",
                f"    Portfolio weight: {format_percentage(item.portfolio_weight)}",
            ]
        )
    totals = result.totals
    lines.extend(
        [
            "",
            "Portfolio totals:",
            f"  Total stock market value: {format_decimal(totals.total_market_value)}",
            f"  Total investment cost: {format_decimal(totals.total_cost_basis)}",
            f"  Total unrealized P/L: {format_decimal(totals.total_unrealized_pnl)}",
            f"  Total liabilities: {format_decimal(totals.total_liabilities)}",
            f"  Net stock equity: {format_decimal(totals.net_asset_value)}",
            "  Liability ratio (liabilities / market value): "
            f"{format_percentage(totals.liability_ratio)}",
            f"  Number of positions: {totals.position_count}",
        ]
    )
    return "\n".join(lines)


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def format_json_report(result: UpdateResult) -> str:
    """Render an update result with lossless Decimal strings."""
    availability = result.availability
    payload: dict[str, object] = {
        "mode": result.mode.value,
        "database_path": str(result.database_path),
        "requested_date": result.requested_date,
        "verified_source_date": result.verified_source_date,
        "positions": [vars(item) for item in result.positions],
        "totals": vars(result.totals) if result.totals is not None else None,
    }
    if availability is not None:
        payload.update(
            {
                "twse_latest_source_date": availability.twse_latest_date,
                "tpex_latest_source_date": availability.tpex_latest_date,
                "commonly_ingestible_date": availability.commonly_ingestible_date,
            }
        )
    return json.dumps(payload, default=_json_default, ensure_ascii=True, sort_keys=True)


def format_status_report(status: PortfolioOverview) -> str:
    """Render operational portfolio status."""
    unavailable = "none"
    availability = status.market_availability
    return "\n".join(
        [
            "PAMS Operational Status",
            f"Database: {status.database_path}",
            f"Latest persisted quote date: {status.latest_quote_date or unavailable}",
            "Latest persisted daily snapshot: "
            f"{status.latest_daily_snapshot or unavailable}",
            "Latest persisted position snapshot: "
            f"{status.latest_position_snapshot or unavailable}",
            f"Holdings count: {status.holdings_count}",
            f"Liabilities count: {status.liabilities_count}",
            f"Schema version: {status.schema_version or unavailable}",
            f"Database size: {status.database_size_bytes:,} bytes",
            "Latest live TWSE source date: "
            f"{availability.twse_latest_date or unavailable}",
            "Latest live TPEx source date: "
            f"{availability.tpex_latest_date or unavailable}",
            "Latest live commonly ingestible date: "
            f"{availability.commonly_ingestible_date or 'not currently available'}",
        ]
    )


def format_portfolio_valuation(
    valuation: PortfolioValuation, *, json_output: bool = False
) -> str:
    """Render a current portfolio valuation without recalculating its values."""
    if json_output:
        return json.dumps(
            {
                "valuation_date": valuation.valuation_date,
                "total_cost": valuation.total_cost,
                "total_market_value": valuation.total_market_value,
                "total_unrealized_pl": valuation.total_unrealized_pl,
                "total_return": valuation.total_return,
                "holdings": [vars(item) for item in valuation.holdings],
            },
            default=_json_default,
            ensure_ascii=False,
            sort_keys=True,
        )
    return "\n".join(
        [
            "PAMS Portfolio Summary",
            f"Valuation date: {valuation.valuation_date or 'none'}",
            f"Market Value: {format_decimal(valuation.total_market_value)}",
            f"Cost: {format_decimal(valuation.total_cost)}",
            f"Unrealized: {format_decimal(valuation.total_unrealized_pl)}",
            f"Return: {format_percentage(valuation.total_return)}",
        ]
    )


def format_verification_report(report: VerificationReport) -> str:
    """Render PASS, FAIL, and WARN verification rows."""
    lines = ["PAMS Operational Verification"]
    lines.extend(
        f"{item.level.value:<4} {item.name}: {item.detail}" for item in report.items
    )
    return "\n".join(lines)


def format_demo_data_report(result: DemoDataResult) -> str:
    """Render creation details and the exact dashboard launch command."""
    return "\n".join(
        [
            "PAMS Demo Data",
            "",
            f"Database: {result.database_path}",
            f"Holdings: {result.holdings_count}",
            f"Liabilities: {result.liabilities_count}",
            f"Quote date: {result.quote_date}",
            f"History points: {result.history_points}",
            "Result: demo database created",
            "",
            "Launch dashboard:",
            "python -m streamlit run app.py -- --database " f'"{result.database_path}"',
        ]
    )


def format_holding_change_plan(plan: HoldingChangePlan) -> str:
    """Render a holding rebuild plan and explicit applied state."""
    lines = [
        "PAMS Holding Rebuild",
        "",
        "Symbol | Action    | Old Qty | New Qty | Old Avg | New Avg",
    ]
    for item in plan.items:
        lines.append(
            f"{item.symbol:<6} | {item.action.value:<9} | "
            f"{format_decimal(item.old_quantity) if item.old_quantity is not None else '-':>7} | "
            f"{format_decimal(item.new_quantity):>7} | "
            f"{format_decimal(item.old_average_cost) if item.old_average_cost is not None else '-':>7} | "
            f"{format_decimal(item.new_average_cost):>7}"
        )
    lines.extend(
        [
            "",
            f"CREATE: {len(plan.created_holdings)}",
            f"UPDATE: {len(plan.updated_holdings)}",
            f"UNCHANGED: {len(plan.unchanged_holdings)}",
            f"CLOSE: {len(plan.closed_holdings)}",
            f"Warnings: {len(plan.warnings)}",
        ]
    )
    lines.extend(f"  - {warning}" for warning in plan.warnings)
    lines.extend(
        [
            f"Projected Cost Basis: {format_decimal(plan.projected_total_cost_basis)}",
            f"Applied: {'Yes' if plan.applied else 'No'}",
        ]
    )
    return "\n".join(lines)


def format_holding_change_plan_json(plan: HoldingChangePlan) -> str:
    """Render a machine-readable holding rebuild plan."""
    return json.dumps(
        {
            "created_holdings": [vars(item) for item in plan.created_holdings],
            "updated_holdings": [vars(item) for item in plan.updated_holdings],
            "unchanged_holdings": [vars(item) for item in plan.unchanged_holdings],
            "closed_holdings": [vars(item) for item in plan.closed_holdings],
            "warnings": plan.warnings,
            "transaction_count": plan.transaction_count,
            "projected_total_cost_basis": plan.projected_total_cost_basis,
            "applied": plan.applied,
        },
        default=_json_default,
        ensure_ascii=False,
        sort_keys=True,
    )


def format_holdings_list(result: HoldingsQueryResult) -> str:
    """Render active transaction-derived holdings with shared valuation facts."""
    lines = [
        "PAMS Holdings",
        f"Requested as-of date: {result.as_of_date or 'current'}",
        f"Valuation date: {result.valuation_date or 'none'}",
        "",
        "Symbol | Market | Quantity | Average Cost | Total Cost | Latest Price | "
        "Quote Date | Market Value | Unrealized P/L | Return",
    ]
    lines.extend(
        f"{item.symbol} | {item.market} | {format_decimal(item.quantity)} | "
        f"{format_decimal(item.average_cost)} | {format_decimal(item.total_cost)} | "
        f"{format_decimal(item.latest_price) if item.latest_price is not None else 'N/A'} | "
        f"{item.quote_date or 'N/A'} | "
        f"{format_decimal(item.market_value) if item.market_value is not None else 'N/A'} | "
        f"{format_decimal(item.unrealized_pl) if item.unrealized_pl is not None else 'N/A'} | "
        f"{format_percentage(item.unrealized_return)}"
        for item in result.holdings
    )
    lines.append(f"Count: {len(result.holdings)}")
    return "\n".join(lines)


def format_holding_detail(result: HoldingsQueryResult) -> str:
    """Render one active holding with its transaction date range."""
    item = result.holdings[0]
    return "\n".join(
        [
            "PAMS Holding",
            f"Requested as-of date: {result.as_of_date or 'current'}",
            f"Symbol: {item.symbol}",
            f"Market: {item.market}",
            f"Quantity: {format_decimal(item.quantity)}",
            f"Average cost: {format_decimal(item.average_cost)}",
            f"Total cost: {format_decimal(item.total_cost)}",
            "Latest available market price: "
            f"{format_decimal(item.latest_price) if item.latest_price is not None else 'N/A'}",
            f"Quote date: {item.quote_date or 'N/A'}",
            "Market value: "
            f"{format_decimal(item.market_value) if item.market_value is not None else 'N/A'}",
            "Unrealized P/L: "
            f"{format_decimal(item.unrealized_pl) if item.unrealized_pl is not None else 'N/A'}",
            f"Unrealized return: {format_percentage(item.unrealized_return)}",
            f"Transaction count: {item.transaction_count}",
            f"First trade date: {item.first_trade_date}",
            f"Latest trade date: {item.latest_trade_date}",
        ]
    )


def format_transaction_record(record: TransactionRecord) -> str:
    """Render confirmation for one recorded transaction."""
    return "\n".join(
        [
            "PAMS Transaction Added",
            f"ID: {record.id}",
            f"Symbol: {record.symbol}",
            f"Market: {record.market}",
            f"Type: {record.transaction_type}",
            f"Trade date: {record.trade_date}",
            f"Quantity: {record.quantity}",
            f"Price: {record.price}",
        ]
    )


def format_transaction_list(result: TransactionList, *, json_output: bool) -> str:
    """Render filtered transactions as text or lossless JSON."""
    if json_output:
        return json.dumps(
            {"transactions": [vars(item) for item in result.transactions]},
            default=_json_default,
            ensure_ascii=False,
            sort_keys=True,
        )
    lines = [
        "PAMS Transactions",
        "",
        "ID | Symbol | Market | Type | Trade Date | Quantity | Price",
    ]
    lines.extend(
        f"{item.id} | {item.symbol} | {item.market} | {item.transaction_type} | "
        f"{item.trade_date} | {item.quantity} | {item.price}"
        for item in result.transactions
    )
    lines.append(f"Count: {len(result.transactions)}")
    return "\n".join(lines)

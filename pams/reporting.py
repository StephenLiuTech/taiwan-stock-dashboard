"""Human-readable and JSON terminal reporting."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from market_data.engine import MarketDataRefreshResult


def format_decimal(value: Decimal) -> str:
    """Format a monetary or quantity value with separators and two decimals."""
    return f"{value:,.2f}"


def format_percentage(value: Decimal | None) -> str:
    """Format a decimal-fraction percentage or an unavailable marker."""
    return "N/A" if value is None else f"{value * 100:,.2f}%"


def _position_records(result: MarketDataRefreshResult) -> list[dict[str, object]]:
    holdings = {holding.id: holding for holding in result.holdings}
    quotes = {(quote.symbol, quote.market): quote for quote in result.quotes}
    records: list[dict[str, object]] = []
    for position in sorted(result.summary.positions, key=lambda item: item.symbol):
        holding = holdings[position.holding_id]
        quote = quotes[(position.symbol, holding.market)]
        records.append(
            {
                "symbol": position.symbol,
                "name": holding.name,
                "market": holding.market.value,
                "shares": position.quantity,
                "average_cost": position.average_cost,
                "close_price": position.close_price,
                "previous_close": quote.previous_close,
                "daily_change_percentage": position.daily_return,
                "market_value": position.market_value,
                "unrealized_pnl": position.unrealized_pnl,
                "unrealized_return": position.unrealized_return,
                "portfolio_weight": position.portfolio_weight,
            }
        )
    return records


def format_human_report(
    result: MarketDataRefreshResult,
    requested_date: date,
    database_path: Path,
    *,
    dry_run: bool,
) -> str:
    """Render one stable terminal report without performing calculations."""
    mode = "dry-run" if dry_run else "persisted"
    lines = [
        "PAMS Market Data Update",
        f"Requested trade date: {requested_date.isoformat()}",
        f"Verified source date: {result.verified_source_date.isoformat()}",
        f"Database: {database_path}",
        f"Mode: {mode}",
        "",
        "Positions:",
    ]
    for item in _position_records(result):
        lines.extend(
            [
                f"  {item['symbol']} {item['name']} ({item['market']})",
                f"    Shares: {format_decimal(item['shares'])}",
                f"    Average cost: {format_decimal(item['average_cost'])}",
                f"    Close / previous: {format_decimal(item['close_price'])} / "
                f"{format_decimal(item['previous_close']) if item['previous_close'] is not None else 'N/A'}",
                f"    Daily change: {format_percentage(item['daily_change_percentage'])}",
                f"    Market value: {format_decimal(item['market_value'])}",
                f"    Unrealized P/L: {format_decimal(item['unrealized_pnl'])} "
                f"({format_percentage(item['unrealized_return'])})",
                f"    Portfolio weight: {format_percentage(item['portfolio_weight'])}",
            ]
        )
    summary = result.summary
    lines.extend(
        [
            "",
            "Portfolio totals:",
            f"  Total stock market value: {format_decimal(summary.total_market_value)}",
            f"  Total investment cost: {format_decimal(summary.total_cost_basis)}",
            f"  Total unrealized P/L: {format_decimal(summary.total_unrealized_pnl)}",
            f"  Total liabilities: {format_decimal(summary.total_liabilities)}",
            f"  Net stock equity: {format_decimal(summary.net_asset_value)}",
            "  Liability ratio (liabilities / market value): "
            f"{format_percentage(summary.leverage_ratio)}",
            f"  Number of positions: {len(summary.positions)}",
        ]
    )
    return "\n".join(lines)


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def format_json_report(
    result: MarketDataRefreshResult,
    requested_date: date,
    database_path: Path,
    *,
    dry_run: bool,
) -> str:
    """Render a machine-readable report with lossless Decimal strings."""
    summary = result.summary
    payload = {
        "requested_date": requested_date,
        "verified_source_date": result.verified_source_date,
        "mode": "dry-run" if dry_run else "persisted",
        "database_path": str(database_path),
        "positions": _position_records(result),
        "totals": {
            "total_market_value": summary.total_market_value,
            "total_cost_basis": summary.total_cost_basis,
            "total_unrealized_pnl": summary.total_unrealized_pnl,
            "total_liabilities": summary.total_liabilities,
            "net_asset_value": summary.net_asset_value,
            "liability_ratio": summary.leverage_ratio,
            "position_count": len(summary.positions),
        },
    }
    return json.dumps(
        payload, default=_json_default, ensure_ascii=False, sort_keys=True
    )

"""Human-readable and JSON terminal reporting."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from market_calendar import MarketAvailability
from market_data.engine import MarketDataRefreshResult
from pams.operations import OperationalStatus, VerificationReport


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
        "mode": "dry_run" if dry_run else "updated",
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


def format_status_report(status: OperationalStatus) -> str:
    """Render operational database status."""
    unavailable = "none"
    return "\n".join(
        [
            "PAMS Operational Status",
            f"Database: {status.database_path}",
            f"Latest quote date: {status.latest_quote_date or unavailable}",
            f"Latest daily snapshot: {status.latest_daily_snapshot or unavailable}",
            f"Latest position snapshot: {status.latest_position_snapshot or unavailable}",
            f"Holdings count: {status.holdings_count}",
            f"Liabilities count: {status.liabilities_count}",
            f"Schema version: {status.schema_version or unavailable}",
            f"Database size: {status.database_size_bytes:,} bytes",
            f"TWSE latest source date: {status.twse_latest_source_date}",
            f"TPEx latest source date: {status.tpex_latest_source_date}",
            "Commonly ingestible dataset: "
            f"{status.commonly_ingestible_date or 'not currently available'}",
        ]
    )


def format_verification_report(report: VerificationReport) -> str:
    """Render PASS, FAIL, and WARN verification rows."""
    lines = ["PAMS Operational Verification"]
    lines.extend(
        f"{check.level.value:<4} {check.name}: {check.detail}"
        for check in report.checks
    )
    return "\n".join(lines)


def format_no_update_report(
    availability: MarketAvailability, database_path: Path
) -> str:
    """Render a normal automatic no-op caused by staggered publication."""
    return "\n".join(
        [
            "PAMS Market Data Update",
            "Result: no update performed; official sources are not synchronized",
            f"TWSE latest source date: {availability.twse_date.isoformat()}",
            f"TPEx latest source date: {availability.tpex_date.isoformat()}",
            f"Database: {database_path}",
        ]
    )


def format_no_update_json(availability: MarketAvailability, database_path: Path) -> str:
    """Render a machine-readable automatic no-op result."""
    return json.dumps(
        {
            "mode": "no_update_sources_unsynchronized",
            "database_path": str(database_path),
            "twse_latest_source_date": availability.twse_date.isoformat(),
            "tpex_latest_source_date": availability.tpex_date.isoformat(),
            "commonly_ingestible_date": None,
        },
        sort_keys=True,
    )

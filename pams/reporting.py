"""Human-readable and JSON rendering of application DTOs."""

import json
from datetime import date
from decimal import Decimal

from pams.application import (
    PortfolioSummary,
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
    return json.dumps(
        payload, default=_json_default, ensure_ascii=False, sort_keys=True
    )


def format_status_report(status: PortfolioSummary) -> str:
    """Render operational portfolio status."""
    unavailable = "none"
    availability = status.market_availability
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
            f"TWSE latest source date: {availability.twse_latest_date}",
            f"TPEx latest source date: {availability.tpex_latest_date}",
            "Commonly ingestible dataset: "
            f"{availability.commonly_ingestible_date or 'not currently available'}",
        ]
    )


def format_verification_report(report: VerificationReport) -> str:
    """Render PASS, FAIL, and WARN verification rows."""
    lines = ["PAMS Operational Verification"]
    lines.extend(
        f"{item.level.value:<4} {item.name}: {item.detail}" for item in report.items
    )
    return "\n".join(lines)

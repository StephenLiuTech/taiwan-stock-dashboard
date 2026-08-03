"""Daily report engine and backward-compatible operational renderers."""

from pams.reporting.builder import (
    DailyReport,
    DailyReportBuilder,
    PortfolioReportSummary,
)
from pams.reporting.html import HtmlReportRenderer
from pams.reporting.legacy import (
    format_decimal,
    format_demo_data_report,
    format_holding_change_plan,
    format_holding_change_plan_json,
    format_holding_detail,
    format_holdings_list,
    format_human_report,
    format_json_report,
    format_percentage,
    format_portfolio_valuation,
    format_status_report,
    format_transaction_list,
    format_transaction_record,
    format_verification_report,
)
from pams.reporting.markdown import MarkdownReportRenderer

__all__ = [
    "DailyReport",
    "DailyReportBuilder",
    "HtmlReportRenderer",
    "MarkdownReportRenderer",
    "PortfolioReportSummary",
    "format_decimal",
    "format_demo_data_report",
    "format_holding_change_plan",
    "format_holding_change_plan_json",
    "format_holding_detail",
    "format_holdings_list",
    "format_human_report",
    "format_json_report",
    "format_percentage",
    "format_portfolio_valuation",
    "format_status_report",
    "format_transaction_list",
    "format_transaction_record",
    "format_verification_report",
]

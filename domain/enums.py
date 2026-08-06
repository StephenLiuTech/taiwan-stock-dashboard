"""Constrained domain categories."""

from enum import StrEnum


class Market(StrEnum):
    """Supported securities markets."""

    TWSE = "TWSE"
    TPEX = "TPEx"
    US = "US"


class Currency(StrEnum):
    """Supported monetary currencies."""

    TWD = "TWD"
    USD = "USD"


class HoldingType(StrEnum):
    """Portfolio holding classifications."""

    STOCK = "stock"
    ETF = "etf"


class TransactionType(StrEnum):
    """Supported transaction directions."""

    BUY = "buy"
    SELL = "sell"


class DividendStatus(StrEnum):
    """Dividend lifecycle states."""

    EXPECTED = "expected"
    RECEIVED = "received"


class LiabilityType(StrEnum):
    """Portfolio liability classifications."""

    MARGIN_FINANCING = "margin_financing"
    STOCK_PLEDGE = "stock_pledge"
    OTHER = "other"

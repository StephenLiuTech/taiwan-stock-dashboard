"""Shared semantic HTML styles for Taiwan-market financial performance."""

import re
from decimal import Decimal

TAIWAN_GAIN_COLOR = "#b91c1c"
TAIWAN_LOSS_COLOR = "#15803d"
NEUTRAL_COLOR = "#6b7280"


def taiwan_performance_color(value: Decimal | None) -> str:
    """Map an unchanged signed value to Taiwan-market presentation colors."""
    if value is None or value == 0:
        return NEUTRAL_COLOR
    return TAIWAN_GAIN_COLOR if value > 0 else TAIWAN_LOSS_COLOR


def taiwan_performance_color_from_text(value: str) -> str:
    """Color a renderer-owned financial fact without changing its text."""
    match = re.search(r"(?:([+-])NT\$|NT\$([+-]?))([\d,]+(?:\.\d+)?)", value)
    if match is None:
        return NEUTRAL_COLOR
    sign = match.group(1) or match.group(2)
    amount = Decimal(match.group(3).replace(",", ""))
    if sign == "-":
        amount = -amount
    return taiwan_performance_color(amount)

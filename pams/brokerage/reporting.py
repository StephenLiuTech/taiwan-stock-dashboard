"""Presentation-only broker reconciliation rendering."""

import json
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from enum import Enum

from pams.brokerage.models import ReconciliationPlan, ReconciliationStatus


def format_reconciliation(
    plan: ReconciliationPlan, *, json_output: bool = False
) -> str:
    if json_output:
        return json.dumps(asdict(plan), default=_json_default, sort_keys=True, indent=2)
    lines = [
        "PAMS Broker Reconciliation",
        f"Source SHA-256: {plan.source_fingerprint}",
        f"Total source rows: {plan.source_rows}",
        f"Parsed: {plan.parsed_rows}",
    ]
    for status in ReconciliationStatus:
        lines.append(f"{status.value.title()}: {plan.count(status)}")
    lines.append(f"Duplicate source rows: {len(plan.duplicate_source_rows)}")
    lines.append("Result: READ-ONLY; no database writes")
    for item in plan.items:
        if item.status is ReconciliationStatus.MATCHED:
            continue
        row = item.normalized_record
        lines.extend(
            (
                "",
                f"{item.status.value} [CSV row {row.source_row}]",
                f"{row.trade_date or 'N/A'} | {row.symbol or 'N/A'} | "
                f"{row.market.value if row.market else 'N/A'} | "
                f"{row.transaction_type.value if row.transaction_type else row.kind.value} | "
                f"{row.quantity if row.quantity is not None else 'N/A'} @ "
                f"{row.price if row.price is not None else 'N/A'} | "
                f"fee {row.fee} | tax {row.tax}",
            )
        )
        if item.matched_transaction_id:
            lines.append(f"Candidate: {item.matched_transaction_id}")
        for field, broker, existing in item.field_differences:
            lines.append(f"Difference {field}: broker={broker}; PAMS={existing}")
        lines.extend(f"Dependency: {value}" for value in item.dependencies)
        lines.extend(f"Warning: {value}" for value in item.warnings)
    return "\n".join(lines)


def _json_default(value: object) -> object:
    if isinstance(value, (date, Decimal, Enum)):
        return str(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"Object is not JSON serializable: {type(value).__name__}")

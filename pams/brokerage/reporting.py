"""Presentation-only broker reconciliation rendering."""

import json
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from enum import Enum

from pams.brokerage.models import (
    BrokerApplyPlan,
    BrokerApplyResult,
    ReconciliationPlan,
    ReconciliationStatus,
)


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


def format_apply_plan(
    value: BrokerApplyPlan | BrokerApplyResult, *, json_output: bool = False
) -> str:
    plan = value.plan if isinstance(value, BrokerApplyResult) else value
    if json_output:
        return json.dumps(
            asdict(value), default=_json_default, sort_keys=True, indent=2
        )
    reconciliation = plan.reconciliation
    blocked = sum(item.apply_status.value.endswith("BLOCKED") for item in plan.items)
    lines = [
        "PAMS Broker Import Plan",
        f"Source SHA-256: {reconciliation.source_fingerprint}",
        f"Total source rows: {reconciliation.source_rows}",
        f"Matched: {reconciliation.count(ReconciliationStatus.MATCHED)}",
        f"New eligible: {plan.eligible_count}",
        f"Blocked: {blocked}",
        f"Mismatch: {reconciliation.count(ReconciliationStatus.MISMATCH)}",
        f"Ambiguous: {reconciliation.count(ReconciliationStatus.AMBIGUOUS)}",
        f"Unsupported: {reconciliation.count(ReconciliationStatus.UNSUPPORTED)}",
    ]
    for item in plan.items:
        if item.domain_action:
            lines.append(
                f"{item.apply_status.value}: {item.domain_action} "
                f"{item.target_entity_type} {item.proposed_entity_id} "
                f"from {item.source_reference}"
            )
        lines.extend(f"  {reason}" for reason in item.blocking_reasons)
    if isinstance(value, BrokerApplyResult):
        lines.extend(
            (
                f"Applied: {'yes' if value.applied else 'no (dry-run)'}",
                f"Transactions inserted: {value.inserted_transactions}",
                f"Principal events inserted: {value.inserted_principal_events}",
                f"Provenance records inserted: {value.inserted_provenance}",
            )
        )
    return "\n".join(lines)


def _json_default(value: object) -> object:
    if isinstance(value, (date, Decimal, Enum)):
        return str(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"Object is not JSON serializable: {type(value).__name__}")

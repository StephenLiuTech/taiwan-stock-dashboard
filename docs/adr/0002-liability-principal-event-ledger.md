# ADR 0002: Replay liability principal from dated events

## Status

Accepted.

## Decision

PAMS stores immutable principal changes in `liability_principal_events`. Each
event has a liability account, inclusive effective date, explicit same-day
sequence, semantic type, signed Decimal delta, and optional resulting-principal
invariant. A pure domain service replays from zero.

The sequence is required because timestamps and deterministic IDs are not
accounting chronology. The margin liability is the brokerage margin debt
account; its financed-symbol fields remain a current-position summary while
event references preserve historical instruments.

## Consequences

Principal is auditable and queryable as of any date. Automatic interest accrual
remains outside this checkpoint.

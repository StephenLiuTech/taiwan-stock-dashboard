# ADR 0004: Broker import provenance requires schema v13

## Status

Accepted by Checkpoint 8. Schema v13 and the safe transaction apply boundary
implement this decision.

## Context

Schema v12 transactions have only free-form `notes`. A safe, idempotent broker
import must retain broker identity, source-file SHA-256, and source-row identity
as structured, queryable provenance. Encoding those facts in notes would create
fragile parsing and would not provide enforceable duplicate protection.

## Decision

The read-only parser and reconciliation engine require no schema change. Before
apply can be enabled, schema v13 should add a dedicated
`broker_import_records` table rather than rewrite financial transactions:

```text
broker_import_records
  id
  broker
  source_fingerprint
  source_row_reference
  record_type
  domain_entity_type
  domain_entity_id
  normalized_identity
  imported_at
```

Required uniqueness is `(broker, source_fingerprint, source_row_reference,
domain_entity_type)`. Apply inserts a domain event and provenance in one unit
of work. Existing schema-v12 rows remain unchanged and still match by economic
identity and source reference.

## Consequences

- `pams broker reconcile` remains safe and read-only.
- No raw file contents or account identifiers are persisted.
- `pams broker import --apply` accepts only reconciled, dependency-complete NEW
  transactions. Corporate actions and dividends remain blocked in this scope.
- Mismatch, ambiguous, unsupported, and financing-principal dependencies can
  never be auto-written.

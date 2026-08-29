# ADR 0004: Broker import provenance requires schema v13

## Status

Proposed. Checkpoint 7 implements read-only reconciliation only; production
migration and apply remain blocked pending separate approval.

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
  record_kind
  transaction_id nullable
  corporate_action_id nullable
  dividend_id nullable
  normalized_fingerprint
  imported_at
```

Required uniqueness is `(broker, source_fingerprint, source_row_reference)`.
Apply would insert a domain event and its provenance record in one unit of
work. Existing schema-v12 rows remain unchanged and can still match by economic
identity and existing source reference.

## Consequences

- `pams broker reconcile` is safe and read-only on schema v12.
- No raw file contents or account identifiers are persisted.
- `pams broker import --apply` is deliberately unavailable until schema v13 is
  approved, migrated, and covered by SQLite/PostgreSQL transactional tests.
- Mismatch, ambiguous, unsupported, and financing-principal dependencies can
  never be auto-written.

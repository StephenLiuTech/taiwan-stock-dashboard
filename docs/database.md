# Database design

## Tables

- `schema_version`: applied integer version and timestamp.
- `holdings`: identity, symbol/name, market/currency, quantity, average cost, type, pledge state, notes, timestamps.
- `transactions`: identity, security, type, trade/settlement dates, quantity, price, fees, taxes, currency, notes.
- `dividends`: identity, security, ex/payment dates, per-share and total amounts, tax, status.
- `dividend_events`: normalized official distribution grain with nullable record
  and payment dates; payment enrichment updates the same deterministic event.
- `liabilities`: identity, type, principal, optional decimal-fraction interest rate, currency, dates, collateral, notes.
- `price_quotes`: normalized TWSE/TPEx close and previous-close values by symbol, market, and trading date.
- `daily_snapshots`: aggregate grain; exactly one portfolio totals row per date.
- `position_snapshots`: position grain; one row per `(snapshot_date, holding_id)`.

## Constraints and indexes

Primary keys use stable text IDs. Holding symbols are unique. Dividends are unique by symbol, market, and ex-dividend date. Quotes are unique by symbol, market, and trade date. `daily_snapshots.snapshot_date` is its primary key. `position_snapshots` has a composite `(snapshot_date, holding_id)` primary key and a foreign key to holdings. Composite indexes support market-data and position-history lookups.

## Serialization

Decimals are stored as canonical text and reconstructed with `Decimal`, avoiding binary floating-point loss. Dates and datetimes are stored as ISO 8601 text and parsed with standard-library types. SQL parameters are used for all record values.

## Schema versions

Version 2 adds normalized quotes and holding-grain position snapshots while preserving the version-1 aggregate `daily_snapshots` table. No rows are copied between snapshot grains. Future changes should use ordered transactional migrations.

Version 3 adds the nullable pre-calculated `daily_return` field to position snapshots for reporting without duplicating valuation logic.

Version 4 adds idempotent daily-report delivery state.

Version 6 adds `dividend_events`. Its existing nullable `payment_date` column is
used for official MOPS enrichment, so payment-date support requires no schema
version change or data rewrite. Upserts never replace a known payment date with
an unavailable value.

## Transaction-derived holding rebuilds

The `transactions` table is the ordered accounting source for the v0.7 ledger. A rebuild compares its active projection with `holdings` and classifies CREATE, UPDATE, UNCHANGED, and CLOSE actions before writing. Applying uses one explicit SQLite transaction with holding repository auto-commit disabled. CLOSE updates quantity and average cost to canonical zero values; rows are never deleted, preserving stable IDs and metadata.

The rebuild transaction does not expose or write `daily_snapshots`, `position_snapshots`, or `price_quotes`. Those tables remain immutable historical observations. Rebuilt holdings become inputs only for snapshots created by later portfolio updates.
## Report deliveries

`report_deliveries` records one operational email outcome per report type,
report date, and recipient. Its uniqueness constraint is the durable
idempotency key. `SENDING` is an atomic claim, `SENT` prevents normal duplicate
delivery, and `FAILED` remains retryable.

## Multi-currency provenance

Schema version 7 adds `fx_rates` at grain `(base_currency, quote_currency,
rate_date, source)`, makes holding identity unique by `(symbol, market)`, and
adds market, native currency, quote date, FX rate, and FX date to each position
snapshot. Existing Taiwan rows migrate as TWD/FX 1 without rewriting aggregate
snapshot values.

Schema version 8 adds nullable `transactions.financing_type` and structured
`liabilities.financed_symbol` / `financed_quantity` fields. Existing cash
transactions and liabilities remain valid with null values. Margin entry writes
these fields through the same transaction as its holding and principal update.

Schema version 11 adds `liability_principal_events`, indexed by liability,
effective date, and explicit same-day sequence. Migration creates only this
ledger and preserves every schema-v10 business row unchanged.

Schema version 12 adds the non-null `annual_pnl_snapshots.valuation_date`
provenance column. Migration resolves each legacy row to the latest prior
`daily_snapshots` row with the same unrealized P/L and aborts transactionally
if any legacy provenance cannot be proven.

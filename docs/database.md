# Database design

## Tables

- `schema_version`: applied integer version and timestamp.
- `holdings`: identity, symbol/name, market/currency, quantity, average cost, type, pledge state, notes, timestamps.
- `transactions`: identity, security, type, trade/settlement dates, quantity, price, fees, taxes, currency, notes.
- `dividends`: identity, security, ex/payment dates, per-share and total amounts, tax, status.
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

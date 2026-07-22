# Database design

## Tables

- `schema_version`: applied integer version and timestamp.
- `holdings`: identity, symbol/name, market/currency, quantity, average cost, type, pledge state, notes, timestamps.
- `transactions`: identity, security, type, trade/settlement dates, quantity, price, fees, taxes, currency, notes.
- `dividends`: identity, security, ex/payment dates, per-share and total amounts, tax, status.
- `liabilities`: identity, type, principal, optional decimal-fraction interest rate, currency, dates, collateral, notes.
- `daily_snapshots`: unique date and calculated portfolio totals, high-water mark, and drawdown.

## Constraints and indexes

Primary keys use stable text IDs. Holding symbols are unique. Dividends are unique by symbol, market, and ex-dividend date. Snapshot dates are primary keys, preventing duplicates. Boolean pledge values have a check constraint. Composite indexes support transaction and dividend symbol/date queries; snapshot dates and holding symbols are indexed.

## Serialization

Decimals are stored as canonical text and reconstructed with `Decimal`, avoiding binary floating-point loss. Dates and datetimes are stored as ISO 8601 text and parsed with standard-library types. SQL parameters are used for all record values.

## Schema versions

Version 1 is created idempotently by `initialize_schema`. The version table is intentionally separate from table creation. Future changes should add ordered, transactional migration functions rather than modifying existing deployed versions in place.

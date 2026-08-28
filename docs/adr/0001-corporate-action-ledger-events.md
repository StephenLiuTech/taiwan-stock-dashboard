# ADR 0001: Corporate actions are separate ledger events

## Status

Accepted.

## Context

Stock splits and ETF quantity conversions change units without representing a
purchase, sale, cash flow, expense, or profit. Encoding additional units as a
zero-price BUY would falsify transaction history.

## Decision

Schema v10 adds `corporate_actions`, storing symbol, market, effective date,
positive Decimal quantity multiplier, source, reference, and notes. The
Transaction Engine replays BUY, corporate action, then SELL on the same day. It
multiplies active quantity, preserves cost basis, and recalculates average cost.

## Consequences

SQLite and PostgreSQL implement one repository protocol. Existing v9 business
rows remain unchanged during migration. Realized P/L, annual P/L, fees, taxes,
dividends, and financing retain their existing accounting sources.

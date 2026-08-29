# ADR 0003: Separate Annual P/L accounting and valuation dates

## Status

Accepted for schema version 12.

## Context

Financing interest accrues every calendar day, while official portfolio market
valuations exist only for valid market sessions. Requiring both facts to share
one date would either omit weekend interest or fabricate market data.

## Decision

`annual_pnl_snapshots.snapshot_date` is the accounting cutoff and the new
non-null `valuation_date` identifies the latest immutable portfolio snapshot
not later than that cutoff. A confirmed non-trading day creates financing cost
and Annual P/L facts without creating quotes, daily snapshots, or position
snapshots. Both official Taiwan date-query adapters must confirm no dataset;
transport failures and unexpected missing trading-day data remain fatal.

Daily financing cost starts structurally on 2026-08-29 and uses replayed
principal, an injected liability-type rate policy, Actual/365, Decimal, and a
deterministic liability/date event ID.

## Consequences

Weekend Annual P/L is reproducible and clearly identifies stale-by-design
market provenance. Historical weekend rows remain immutable when a later
market valuation arrives. Schema-v11 rows migrate only when their valuation
source can be proven from prior daily snapshots and matching unrealized P/L.

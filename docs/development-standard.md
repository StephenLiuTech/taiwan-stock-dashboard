# PAMS Development Standard

Version: 1.0

## Purpose

This document defines the engineering standards for PAMS.

Every contribution should follow these rules.

## Architecture First

No feature should be implemented before its architecture is defined.

Large features should begin with:

- Product Scope
- Architecture
- Domain Design

Implementation comes last.

## Layer Responsibilities

### Presentation

Includes:

- CLI
- Dashboard
- API

Presentation must never calculate business rules.

### Application

Responsible for:

- orchestration
- validation
- repository coordination

Application must never perform financial calculations.

### Domain

Contains:

- entities
- value objects
- domain services
- engines

The Domain must remain deterministic.

### Infrastructure

Responsible only for:

- SQLite
- Files
- HTTP
- External APIs

Infrastructure must contain no business logic.

## Financial Rules

Always use `Decimal`.

Never use `float`.

Money is immutable.

## Testing

Every feature must include:

- Unit Tests
- Regression Tests

Bug fixes require regression tests.

## Documentation

Every architectural change updates:

- README
- Roadmap
- Architecture
- Product Vision

If architecture changes, update the relevant Architecture Decision Record
(ADR).

## Git Workflow

```text
One Sprint
    ↓
Review
    ↓
Tests
    ↓
Commit
    ↓
Push
    ↓
Next Sprint
```

Never commit unfinished work.

## Definition of Done

A Sprint is complete only if:

- Tests pass
- Black passes
- Ruff passes
- Compileall passes
- `git diff --check` passes
- Documentation is updated

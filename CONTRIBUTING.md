# Contributing to PAMS

Thank you for helping improve PAMS. Contributions must follow the
[Product Vision](docs/product-vision.md),
[Development Standard](docs/development-standard.md), and
[Architecture](docs/architecture.md).

## Development workflow

1. Confirm product scope and architectural boundaries before implementation.
2. Create a focused branch from the latest `main`.
3. Implement one coherent change without unrelated refactoring.
4. Add unit tests and regression tests where applicable.
5. Update required documentation.
6. Run the complete local validation suite.
7. Open a pull request and address review feedback.

Do not commit secrets, local databases, generated reports, virtual
environments, caches, or unfinished work.

## Branch strategy

`main` is the stable integration branch and should remain releasable.

Use short-lived branches with descriptive names:

- `feat/<topic>` for product capabilities
- `fix/<topic>` for defects
- `docs/<topic>` for documentation
- `chore/<topic>` for maintenance and governance
- `refactor/<topic>` for behavior-preserving structural work

Rebase or merge the latest `main` before final review according to repository
maintainer preference. Do not force-push shared branches.

## Commit message convention

Use Conventional Commit-style messages:

```text
<type>: <imperative summary>
```

Common types:

- `feat`
- `fix`
- `docs`
- `test`
- `refactor`
- `chore`
- `ci`

Examples:

```text
feat: integrate analytics application layer
fix: preserve source date integrity
docs: clarify transaction-first architecture
chore: establish project governance
```

Keep each commit internally consistent and independently reviewable.

## Coding standards

- Keep dependencies flowing from Presentation to Application to Domain, with
  infrastructure behind repository and provider boundaries.
- Never place business or financial calculations in Presentation,
  Application, or infrastructure adapters.
- Keep domain engines deterministic and independent of files, databases,
  HTTP, CLI, Streamlit, Pandas, and Plotly.
- Use `Decimal` for money, quantities, prices, ratios, and returns. Never use
  `float` for financial values.
- Prefer immutable DTOs and explicit typed exceptions.
- Keep changes minimal and backward compatible unless an approved architecture
  change states otherwise.
- Follow Black formatting and Ruff rules configured in `pyproject.toml`.

## Testing requirements

Every feature requires focused unit tests and appropriate regression coverage.
Every bug fix requires a test that fails before the fix.

Run:

```bash
python -m black --check .
python -m ruff check .
python -m pytest --basetemp=.pytest_tmp
python -m compileall .
git diff --check
```

Tests should be deterministic and offline unless a narrowly scoped integration
test is explicitly approved.

## Documentation requirements

Update documentation whenever behavior, commands, architecture, or release
scope changes. Architectural changes require updates to:

- `README.md`
- `docs/product-vision.md`
- `docs/architecture.md`
- `docs/roadmap.md`
- the relevant ADR, when present

Release-visible changes should also update `CHANGELOG.md`.

## Pull request checklist

Before requesting review, confirm:

- The change has a clear and limited scope.
- Tests and regression coverage are included.
- Black, Ruff, Pytest, Compileall, and `git diff --check` pass.
- Documentation is updated.
- Architecture is unchanged or the change has explicit architectural approval.
- Presentation contains no business logic.
- No secrets, local data, generated artifacts, or unrelated changes are present.

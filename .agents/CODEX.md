# Codex Development Rules

Before changing PAMS, read completely:

- `docs/product-vision.md`
- `docs/development-standard.md`
- `docs/architecture.md`
- `docs/roadmap.md`

## Never

- Redesign architecture without an approved architecture task and ADR.
- Put business or financial calculations in Presentation, Application, or
  infrastructure.
- Use `float` for financial values.
- Commit changes.
- Push branches.
- Create or push tags.
- Include secrets, personal financial data, local databases, caches, or
  generated artifacts.

## Always

- Preserve transaction-first reproducibility and downward dependency direction.
- Keep domain engines deterministic and infrastructure-independent.
- Make the smallest change that satisfies the approved scope.
- Update unit and regression tests.
- Update required documentation.
- Run Black, Ruff, Pytest, Compileall, and `git diff --check`.
- Report every changed file, validation result, assumption, and remaining risk.
- Leave commit, push, and tag actions to the user unless a later explicit
  repository policy authorizes them.

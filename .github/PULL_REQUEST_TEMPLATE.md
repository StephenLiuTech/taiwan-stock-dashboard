## Summary

Describe the problem, the chosen solution, and the scope of this pull request.

## Architecture impact

State whether architecture changes. If it does, link the approved design and
ADR. If it does not, explain which existing boundary the change follows.

## Validation

List the commands run and their results.

## Checklist

- [ ] Tests pass
- [ ] Documentation is updated
- [ ] Architecture is unchanged, or an approved architecture change is linked
- [ ] Presentation contains no business logic
- [ ] `python -m black --check .` passes
- [ ] `python -m ruff check .` passes
- [ ] `python -m pytest --basetemp=.pytest_tmp` passes
- [ ] `python -m compileall .` passes
- [ ] `git diff --check` passes
- [ ] No secrets, local databases, or generated artifacts are included

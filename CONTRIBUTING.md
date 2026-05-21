# Contributing to Cadence

Thank you for considering a contribution. Cadence improves *through*
contributions — the whole point of the retrospective protocol is that
recurring findings from real-world use upgrade the framework for
everyone.

## Three ways to contribute

### 1. Submit a framework finding (most valuable)

You ran Cadence on a real project, hit a recurring issue, and the
framework didn't catch it or didn't have a pattern for it. That's the
canonical contribution.

Open an issue using the **Framework Finding** template. Include:
- What happened (one sentence)
- Was it auto-catchable? (could a lint or boundary rule have caught it?)
- Did an existing ADR cover it? (no = new ADR opportunity)
- Proposed fix and which of the four layers it belongs to
  (Patterns / Process / Automated gates / Launch)

Maintainers triage findings and convert recurring ones into new core
ADRs or rules in the next release.

### 2. Propose an ADR or pattern change

Open a pull request that:
- Adds a new ADR under `templates/docs/ADR/` following the Nygard
  template at `templates/docs/ADR/0000-template.md.tmpl`
- Updates `templates/docs/PATTERNS.md.tmpl` with a new section citing
  the ADR
- Adds a `FRAMEWORK_CHANGELOG.md.tmpl` entry showing what triggered the
  change

Reviewer enforces: every PATTERNS.md update must cite an ADR.

### 3. Add or improve a stack recipe

Recipes live at `recipes/<stack>.yaml`. To add a new stack:
- Add `recipes/<stack>.yaml` modeled on the existing examples
- Add `docs/recipes/<stack>.md` walkthrough
- Add a fixture under `tests/fixtures/<stack>-sample/` so the boundary
  checker has something to validate against

## Code style

- Python: PEP 8, `ruff check .` clean
- Shell: shellcheck clean
- Markdown: prose follows GitHub-flavored markdown; line length
  80 columns where practical
- YAML: 2-space indent

## Commit messages

Follow [Conventional Commits 1.0](https://www.conventionalcommits.org/en/v1.0.0/):

```
feat(skills): add cadence-init scaffolder
fix(boundaries): handle Python relative imports
docs(adr): add ADR-0008 cross-feature shared state
chore(deps): bump GitHub Actions dependencies
```

Breaking changes get `BREAKING CHANGE:` in the footer and bump MAJOR.

## Pull request process

1. Open PR with a descriptive title (Conventional Commits format)
2. Fill out the PR template
3. Ensure CI is green (format, lint, tests, boundary checks)
4. One maintainer review required to merge
5. Squash-merge is the default

## Code of Conduct

This project adopts the [Contributor Covenant 3.0](CODE_OF_CONDUCT.md).
By participating, you agree to abide by it.

## License

By contributing, you agree that your contributions will be licensed
under the Apache License 2.0.

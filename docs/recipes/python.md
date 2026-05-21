---
layout: default
title: Python recipe
---

# Recipe — Python

A starter `cadence.yaml` for a typical Python project.

```yaml
commands:
  format: ["ruff format --check ."]
  lint:   ["ruff check .", "mypy ."]
  test:   ["pytest"]

boundaries:
  - where: "src/myapp/features/**"
    forbidden:
      - "src/myapp/data/sources/**"
      - "src/myapp/data/repositories/**"
    reason: "Features must read via narrow providers in src/myapp/app/providers"

  - where: "src/myapp/data/sources/**"
    forbidden:
      - "src/myapp/features/**"
      - "src/myapp/app/**"
    reason: "Data sources are leaf-level"

  - where: "**/*"
    forbidden:
      - "src/myapp/data/models/**"
    reason: "Models live in src/myapp/domain/models; data/models is forbidden"
```

## Notes

- Replace `myapp` with your top-level package name
- The boundary checker recognizes `from X import Y`, `import X`, and
  relative-import patterns
- If you use Poetry, the commands stay the same (Poetry's `poetry run`
  prefix can be added if needed)
- For src-layout vs flat layout, adjust glob roots accordingly

## Suggested first ADRs

1. **Dependency injection approach** (constructor injection / functools.partial / a DI framework)
2. **Error handling pattern** (exceptions / Result types / per-call try)
3. **Async vs sync boundary** (where async starts and ends)

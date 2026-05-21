---
layout: default
title: Go recipe
---

# Recipe — Go

A starter `cadence.yaml` for a typical Go project.

```yaml
commands:
  format: ["gofmt -l .", "goimports -l ."]
  lint:   ["golangci-lint run"]
  test:   ["go test ./..."]

boundaries:
  - where: "internal/features/**"
    forbidden:
      - "internal/data/sources/**"
      - "internal/data/repositories/**"
    reason: "Features must read via narrow providers in internal/app"

  - where: "internal/data/sources/**"
    forbidden:
      - "internal/features/**"
      - "internal/app/**"
    reason: "Data sources are leaf-level"
```

## Notes

- Go's `internal/` convention plays well with Cadence's layer boundaries
  — use `internal/features/`, `internal/data/`, `internal/app/`,
  `internal/domain/models/`
- `gofmt -l` returns non-empty if any file needs reformatting (CI
  treats non-empty output as failure); verify.sh handles this
- For modules, run `/cadence-init` at the repo root with the module
  path reflected in the boundary globs

## Suggested first ADRs

1. **Error handling pattern** (sentinel errors / wrapped errors / typed errors)
2. **Context propagation** (which functions take `context.Context`, defaults)
3. **Logging library and structured-log shape**

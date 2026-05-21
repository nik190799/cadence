---
layout: default
title: TypeScript recipe
---

# Recipe — TypeScript / Node

A starter `cadence.yaml` for a typical TypeScript project (Node app,
React/Next app, or library).

```yaml
commands:
  format: ["prettier --check ."]
  lint:   ["eslint ."]
  test:   ["npm test", "vitest run"]

boundaries:
  - where: "src/features/**"
    forbidden:
      - "src/data/sources/**"
      - "src/data/repositories/**"
    reason: "Features must read via narrow providers in src/app/providers"

  - where: "src/data/sources/**"
    forbidden:
      - "src/features/**"
      - "src/app/**"
    reason: "Data sources are leaf-level; no upward dependencies"

  - where: "**/*"
    forbidden:
      - "src/data/models/**"
    reason: "Models live in src/domain/models; data/models is forbidden"
```

## Notes

- Replace `npm test` with `yarn test`, `pnpm test`, or your test runner
- If you use absolute imports via `tsconfig.json` paths, the boundary
  checker matches on the resolved path components (e.g., `@/features/X`
  matching `src/features/X`); adjust glob accordingly
- For monorepos, run `/cadence-init` inside each package directory
  with its own `cadence.yaml`

## Suggested first ADRs to write

For a typical TypeScript project, the first three ADRs likely cover:

1. **State management choice** (Redux / Zustand / Jotai / signals)
2. **Data fetching pattern** (React Query / SWR / RTK Query)
3. **Form handling pattern** (react-hook-form / Formik / native)

Use `/cadence-adr` to draft them in Nygard format.

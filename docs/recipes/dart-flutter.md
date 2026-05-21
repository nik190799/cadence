---
layout: default
title: Dart / Flutter recipe
---

# Recipe — Dart / Flutter

A starter `cadence.yaml` for a Flutter app (also works for pure Dart).

```yaml
commands:
  format: ["dart format --set-exit-if-changed --output none lib/ test/"]
  lint:   ["flutter analyze --no-fatal-infos"]
  test:   ["flutter test"]

boundaries:
  - where: "lib/features/**"
    forbidden:
      - "lib/data/sources/**"
      - "lib/data/repositories/**"
    reason: "Features must read via narrow providers in lib/app/providers.dart"

  - where: "lib/data/sources/**"
    forbidden:
      - "lib/features/**"
      - "lib/app/**"
    reason: "Data sources are leaf-level"

  - where: "lib/app/**"
    forbidden:
      - "lib/features/**"
    reason: "app/ may not import features/ (cycle avoidance per ADR-0007)"

  - where: "**/*"
    forbidden:
      - "lib/data/models/**"
    reason: "Models live in lib/domain/models/ per ADR-0002"
```

## Notes

- For a pure Dart package, replace `flutter analyze` / `flutter test`
  with `dart analyze` / `dart test`
- The strictest version of `analysis_options.yaml` complements
  Cadence's boundary checks — see the [Flutter sandbox case study](../case-studies/flutter-sandbox.md)
  for an example with `unawaited_futures`, `discarded_futures`,
  `avoid_print`, etc.

## Suggested first ADRs

These were the actual ADRs that emerged from the [Flutter sandbox](../case-studies/flutter-sandbox.md):

1. **Mutation pattern** — optimistic-then-write vs write-then-reread
2. **Model location** — `lib/domain/models/` is canonical
3. **Error surface** — `AsyncError` for whole-screen, state-field for
   per-action, map for per-row
4. **Narrow function-typed providers** — testability pattern
5. **UI async invocation** — wrap fire-and-forget in `unawaited()`
6. **Test coverage requirements** — happy + edge + sequence + error +
   write log
7. **Cross-feature shared state** — lives in `lib/app/state/`, not in
   feature folders

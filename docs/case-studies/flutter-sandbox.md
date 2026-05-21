---
layout: default
title: Case study — Flutter sandbox
---

# Case study — Flutter sandbox

Cadence was prototyped on a Flutter + Firebase project and validated
through two iterations before being extracted as a stack-agnostic
plugin. The sandbox shows what a real Cadence-aligned project looks like.

## The sandbox

A small Flutter app (counter, todo list, settings) mirroring the
architecture of a production Flutter app called *The Atelier* (a
real-world consumer app the author maintains).

- Riverpod for state management
- Firebase backend (Auth, Firestore, Functions) — faked with in-memory
  data sources in the sandbox
- Material 3 design

## What Cadence produced on this project

After two team runs and two retrospectives, the sandbox accumulated:

- **7 ADRs** covering: mutation pattern (optimistic-then-write),
  model location (`lib/domain/models/`), error surface (3-level
  hierarchy), narrow function-typed providers, UI async invocation
  (`unawaited()` wrapper), test coverage requirements, cross-feature
  shared state
- **A populated `FRAMEWORK_CHANGELOG.md`** with two retrospective
  entries showing the four-layer fix mapping in action
- **A working language-specific Layer 3** — a Dart `tool/check_imports.dart`
  that enforces the architecture's import rules; strict
  `analysis_options.yaml` with `unawaited_futures`, `discarded_futures`,
  `avoid_print`, etc.
- **A passing test suite** demonstrating the ADR-0006 coverage pattern
  (happy + edge + sequence + error + write log)

## The retrospective loop in action

### Bundle A (first team run — todos feature)

The team produced a correct, working todos feature. The retrospective
found:

1. **Pattern inconsistency** — Counter used optimistic update; Todos
   used write-then-reread. Fix: ADR-0001 made optimistic the standard.
2. **No error handling** — Todos controller methods had no try/catch.
   Fix: ADR-0003 codified the three-level error surface.
3. **Fire-and-forget callbacks** — Four widget callbacks discarded
   futures. Fix: ADR-0005 mandated `unawaited()` wrapper.
4. **Model location** — Team placed `Todo` in `lib/data/models/`
   without a rule to follow. Fix: ADR-0002 made `lib/domain/models/`
   canonical; `tool/check_imports.dart` mechanically rejects
   `data/models/` references.
5. **Missing edge-case test** — Trim semantics on non-empty input.
   Fix: ADR-0006 codified minimum coverage.

### Bundle D (second team run — settings toggle)

A "Big numbers" settings toggle was added. The retrospective found:

1. **Cross-feature dependency** — Counter needed to read Settings;
   no rule existed for cross-feature state sharing. Fix: ADR-0007
   said shared state lives in `lib/app/state/`; import boundary
   rule added; `providers.dart` exemption documented as a deferred
   migration.
2. **Tearoff fire-and-forget** — ADR-0005 forbade arrow-form discard
   but was silent on tearoff form. Fix: ADR-0005 tightened to cover
   both.

## What this validates

- The retrospective protocol **actually produces ADRs** that improve
  the framework
- The four-layer fix mapping is **objectively applicable** to real
  observed issues
- Mechanical enforcement (Layer 3) caught issues the Reviewer would
  have missed
- The framework can absorb new findings without breaking existing
  ones — every ADR added is backward compatible
- Two runs were enough to graduate Cadence from sandbox-specific to
  general purpose

## Sandbox repository

The full sandbox lives at
[github.com/nik190799/agent_teams_sandbox](https://github.com/nik190799/agent_teams_sandbox)
(separate repository — Cadence itself ships zero Flutter code).

---
name: cadence-adr-writer
description: Auto-loaded helper for writing Architecture Decision Records in the Nygard format. Activates when the user mentions ADR, architectural decision, judgment call, design rationale, or asks how to document a non-obvious decision. Provides the Nygard format and ADR-writing guidance.
---

# Cadence ADR writer

> ⚠️ **Stub — Phase 1.**

## What this skill will load (Phase 1)

When activated, it should provide Claude with:

### The Nygard ADR format

```markdown
# ADR-NNNN — <title>

## Status
Accepted | Superseded by ADR-NNNN | Deprecated

## Context
What's the situation forcing this decision? Why does this matter now?

## Decision
What did we decide? One sentence, then specifics.

## Consequences
What does this make easier? What does it make harder? What are the
known trade-offs?

## Trigger
What prompted writing this ADR? Usually a retrospective entry —
link to `docs/FRAMEWORK_CHANGELOG.md`.
```

### Numbering rules

- **Monotonic.** Never reuse a number.
- **Superseding** preserves history: old ADR keeps its number, marked
  `Superseded by ADR-NNNN`.
- **`/cadence-adr <title>`** is the canonical way to create one;
  manual creation should also update `docs/ADR/README.md` index.

### When to write an ADR

- A decision affects more than one file's structure
- A reasonable competing alternative exists
- A future maintainer (or your future self) would ask "why did we do
  this?"
- A retrospective identified a missing pattern

### When NOT to write an ADR

- Pure formatting / naming convention (put in `PATTERNS.md` directly)
- Decision contained in one function (use a code comment)
- Implementation detail with no architectural weight

See: [docs/architecture.md](../../docs/architecture.md) — Layer 1 section

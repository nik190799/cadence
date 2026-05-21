---
name: cadence-adr
description: Create the next-numbered Architecture Decision Record in the project's docs/ADR/ directory, using the Nygard format (Status, Context, Decision, Consequences). Use when the user runs /cadence-adr, asks to write an ADR, document an architectural decision, or capture a judgment call.
argument-hint: "<title>"
---

# /cadence-adr

> ⚠️ **Stub — Phase 1.**

## Intended behavior

1. **Determine the next ADR number** by scanning `docs/ADR/NNNN-*.md`
2. **Slugify the title** (lowercase, kebab-case)
3. **Copy `docs/ADR/0000-template.md`** to `docs/ADR/NNNN-<slug>.md`
4. **Prompt interactively** for:
   - **Status** — default Accepted
   - **Context** — what's the situation forcing this decision?
   - **Decision** — what did we decide? (one sentence + specifics)
   - **Consequences** — what does this make easier / harder?
   - **Trigger** — what prompted writing this ADR? (usually a
     retrospective entry — link to `FRAMEWORK_CHANGELOG.md`)
5. **Update `docs/ADR/README.md`** index with the new entry
6. **Suggest a `PATTERNS.md` update** if the ADR introduces a new
   pattern that should be cited there

## Format reference

Per [Michael Nygard's ADR format](https://www.cognitect.com/blog/2011/11/15/documenting-architecture-decisions):

```markdown
# ADR-NNNN — <title>

## Status
Accepted | Superseded by ADR-NNNN | Deprecated

## Context
...

## Decision
...

## Consequences
...

## Trigger
...
```

Numbering is monotonic. Never reuse a number. When superseding, mark
the old ADR `Superseded by ADR-NNNN` and keep it.

See: [docs/architecture.md](../../docs/architecture.md) — Layer 1 section

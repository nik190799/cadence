---
name: cadence-adr-writer
description: Auto-loaded helper for writing Architecture Decision Records in the Nygard format. Activates when the user mentions ADR, architectural decision, judgment call, design rationale, or asks how to document a non-obvious decision. Provides the Nygard format and ADR-writing guidance.
---

# Cadence ADR writer

ADRs in Cadence projects follow Michael Nygard's format: short,
focused, one decision per record.

## The format

```markdown
# ADR-NNNN — <title>

## Status
Accepted | Proposed | Deprecated | Superseded by ADR-NNNN

## Context
What's the situation that forced this decision? Two or three short
paragraphs. The "why now."

## Decision
What did we decide? One sentence, then specifics. Include a code
example if the decision affects how code is written.

## Consequences
What does this make easier? What does it make harder? What are the
known trade-offs? What's the migration cost on existing code?

## Trigger
What prompted writing this ADR? Usually a retrospective entry — link
to docs/FRAMEWORK_CHANGELOG.md. If greenfield, say so.
```

## Numbering rules

- **Monotonic.** Never reuse a number.
- **Superseding** preserves history: the old ADR keeps its number,
  marked `Superseded by ADR-NNNN`.
- **`/cadence-adr <title>`** is the canonical way to create one; it
  also updates `docs/ADR/README.md` index.

## When to write an ADR

- A decision affects more than one file's structure
- A reasonable competing alternative exists
- A future maintainer (or your future self) would ask "why?"
- A retrospective identified a missing pattern

## When NOT to write an ADR

- Pure formatting / naming convention (put in `PATTERNS.md` directly)
- Decision contained in one function (use a code comment)
- Implementation detail with no architectural weight
- Decisions that are "obviously the only choice" — no real trade-off

## Quality checks

A good ADR:

- **Has real context.** Not "we needed a database, so we picked X."
  Instead: "We have 50M rows, write-heavy, with strong consistency
  needs. Options were A, B, C. We picked C because..."
- **Is honest about trade-offs.** No real decision has zero downside.
  If the Consequences section says only good things, push back.
- **Is short.** One page max. Longer means it's actually 2+ decisions
  bundled.
- **References code or other ADRs.** ADRs in isolation rot. Linked
  ADRs survive.

## Common Cadence-aligned ADRs

When initializing a Cadence project, these are the first ADRs most
projects benefit from writing (in this order):

| # | Topic | Why first |
|---|---|---|
| 0001 | Layer boundaries | Drives `cadence.yaml` boundaries config |
| 0002 | Model location | Drives import-check rule |
| 0003 | Mutation pattern | Drives controller code shape |
| 0004 | Error surface | Drives state-class design |
| 0005 | UI async invocation | Drives lint config |
| 0006 | Test coverage | Drives reviewer's checklist |

See the [Cadence Flutter case-study](https://github.com/nik190799/agent_teams_sandbox)
for examples of all six in a real project.

## Source of truth

When in doubt about ADR conventions in this specific project, check
`docs/ADR/README.md` (project-level guidance) and any existing ADRs
for style.

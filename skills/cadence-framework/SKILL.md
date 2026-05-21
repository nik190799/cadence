---
name: cadence-framework
description: Auto-loaded reference for the Cadence framework's patterns, definition of done, and role specs. Activates when the user asks about patterns, definition of done, DoD, role specs, narrow providers, optimistic mutation, the four-layer model, or other Cadence framework concepts.
---

# Cadence framework — reference

This project uses the **Cadence framework**: a four-layer
self-improving system for AI-assisted development.

## The four layers

1. **Patterns** (`docs/PATTERNS.md` + `docs/ADR/`) — codebase
   conventions. Every rule cites an ADR.
2. **Process** (`docs/DEFINITION_OF_DONE.md`, `docs/ROLE_SPECS.md`,
   `docs/TEAM_PROTOCOL.md`) — what "done" means, who owns what, how
   teammates coordinate.
3. **Automated gates** (`.cadence/cadence.yaml`, `scripts/verify.*`,
   `tool/check_boundaries.py`) — mechanical enforcement.
4. **Launch** (`docs/TEAM_LAUNCH_TEMPLATE.md`) — the prompt that
   spawns a Cadence team.

The fifth concept — the **retrospective loop**
(`docs/RETROSPECTIVE_PROTOCOL.md`) — wires the layers together so the
framework grows as the team uses it.

## Layer rules (the most-cited rules)

Per the project's `docs/PATTERNS.md` §1:

- `features/**` may NOT import `data/sources/**` or `data/repositories/**`
- `data/sources/**` may NOT import `features/**` or `app/**`
- `app/**` may NOT import `features/**` (cycle avoidance)
- Models live under `domain/models/**` (NEVER `data/models/**`)

These are enforced by `tool/check_boundaries.py` against the
boundaries in `.cadence/cadence.yaml`.

## Controller shape

Per PATTERNS §2:

| Screen kind | Shape |
|---|---|
| Stateful + async + mutations | AsyncNotifier / equivalent |
| Stateful + sync only | Notifier / useState |
| Read-only + one-shot load | FutureProvider / single hook |
| Pure static | Plain component |

## Narrow function-typed providers

Per PATTERNS §3 (and ADR-0004 in the case-study sandbox): feature code
NEVER reads repositories directly. It reads narrow function-typed
providers that close over the one method the feature needs.

This is the testability seam: tests override the closure, not the
whole repository.

## Mutation pattern

Per PATTERNS §4 and ADR-0003: **optimistic-then-write**.

```
set state optimistically → try { await write } catch → AsyncError
```

For list mutations, compute the next list LOCALLY (don't re-read).

## Error surface

Per PATTERNS §5 and ADR-0004:

- Whole-screen failure: `AsyncError`
- Per-action failure: state field
- Per-row failure: map keyed by row id

Never silently swallow exceptions.

## UI fire-and-forget

Per PATTERNS §6 and ADR-0005: wrap explicitly.

```
✅ onPress: () => unawaited(controller.delete(id))
❌ onPress: () => controller.delete(id)
```

The arrow-form discard is technically allowed by most type systems —
but the wrapper makes intent visible.

## Test requirements

Per PATTERNS §7 and ADR-0006, every controller test file has:

1. One happy-path test per public method
2. ≥1 edge case per public method
3. One sequence test (3+ mutations)
4. One error test if the method has an error path
5. Write log assertion in every mutation test

## Role boundaries

Per `docs/ROLE_SPECS.md`:

| Role | Owns | Forbidden |
|---|---|---|
| Data Engineer | data/sources, data/repositories, providers | features/, app/ |
| Feature Engineer | features/ | data/sources, data/repositories |
| Tester | tests/features/ | data/sources, data/repositories |
| Reviewer | nothing (read-only) | writing code |

Don't write outside your zone. If you need a change in another
teammate's zone, message them.

## Source of truth

When in doubt, defer to the user's local files — they are the
authoritative copy. The summary above is a condensed view; the local
docs may have project-specific extensions.

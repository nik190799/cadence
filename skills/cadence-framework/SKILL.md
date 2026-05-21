---
name: cadence-framework
description: Auto-loaded reference for the Cadence framework's patterns, definition of done, and role specs. Activates when the user asks about patterns, definition of done, DoD, role specs, narrow providers, optimistic mutation, the four-layer model, or other Cadence framework concepts.
---

# Cadence framework — reference

> ⚠️ **Stub — Phase 1.** This skill will inline a condensed cheat sheet
> of the user's local `docs/PATTERNS.md`, `docs/DEFINITION_OF_DONE.md`,
> and `docs/ROLE_SPECS.md` so Claude always has the framework context.

## What this skill will load (Phase 1)

When activated, it should provide Claude with:

### The four layers (one-paragraph each)

- **Layer 1 Patterns** — `docs/PATTERNS.md` + `docs/ADR/` cite every rule
- **Layer 2 Process** — `docs/DEFINITION_OF_DONE.md`, `docs/ROLE_SPECS.md`, `docs/TEAM_PROTOCOL.md`
- **Layer 3 Automated gates** — `cadence.yaml`, `scripts/verify.{sh,ps1}`, `tool/check_boundaries.py`
- **Layer 4 Launch** — `docs/TEAM_LAUNCH_TEMPLATE.md`

### The four-layer fix mapping table

From `docs/RETROSPECTIVE_PROTOCOL.md` — used whenever the user
mentions "retro", "post-mortem", or after any team run.

### The role boundaries

From `docs/ROLE_SPECS.md` — Data engineer / Feature engineer / Tester /
Reviewer ownership rules. Critical for keeping a multi-agent team
from stepping on each other.

### The DoD checklist (condensed)

A short version of `docs/DEFINITION_OF_DONE.md` that fits in context;
when Claude needs the full checklist it should `Read` the file.

## Why auto-loaded

Without this skill, Claude in a Cadence-aligned project has to discover
the framework conventions through file reads on every turn. With it,
the conventions are in context immediately, and Claude knows to defer
to the local docs as the authoritative source.

See: [docs/architecture.md](../../docs/architecture.md)

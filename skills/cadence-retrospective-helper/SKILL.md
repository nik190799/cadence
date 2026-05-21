---
name: cadence-retrospective-helper
description: Auto-loaded helper for running Cadence retrospectives. Activates when the user mentions retro, retrospective, post-mortem, lessons learned, what went wrong, framework finding, or asks for a structured reflection on completed work. Provides the four-layer fix mapping and the proper output format.
---

# Cadence retrospective helper

> ⚠️ **Stub — Phase 1.**

## What this skill will load (Phase 1)

When activated, it should provide Claude with:

### The four-layer fix mapping table

For each observed issue:

| Field | Value |
|---|---|
| What happened | one-line description |
| Auto-catchable? | yes (what would catch it) / no (judgment call) |
| Rule existed? | yes (cite ADR / PATTERNS §) / no |
| Proposed fix | concrete action with file paths |
| Fix layer | **3** (Automated) > **1** (Patterns) > **2** (Process) > **4** (Launch) |

Preference order: **higher numbers descending into lower**. If Layer 3
automation can catch it, automate; only fall to Layer 1 docs when
automation can't. Layer 4 is the last resort.

### The output format (Keep a Changelog 1.1.0 style)

```markdown
## YYYY-MM-DD — <feature> retrospective

### Issues observed
- **<issue>**: <description>
  - Auto-catchable? <yes/no — and what>
  - Existing rule? <yes/no — link>
  - Fix: <layer> — <action>

### Near-misses / gaps
- ...

### What worked
- ...

### Lead's decisions
- [x] Approved & landed: <fix>
- [ ] Deferred: <fix> — reason: <...>
- [ ] Rejected: <fix> — reason: <...>
```

### Anti-patterns

- "Everything went great" → push back, find at least one improvement
- Proposing a doc fix when automation would work
- Rejecting a fix without a recorded reason
- Not appending to `FRAMEWORK_CHANGELOG.md`

See: [docs/self-improvement-loop.md](../../docs/self-improvement-loop.md)

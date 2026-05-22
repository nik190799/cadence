---
name: cadence-retrospective-helper
description: Auto-loaded helper for running Cadence retrospectives. Activates when the user mentions retro, retrospective, post-mortem, lessons learned, what went wrong, framework finding, or asks for a structured reflection on completed work. Provides the four-layer fix mapping and the proper output format.
---

# Cadence retrospective helper

A Cadence retrospective is a structured pass, not a free-form chat.
Use the four-layer fix mapping for every observed issue.

## The mechanical pass

For each issue observed (Reviewer flags + near-misses), fill a row:

| Field | Value |
|---|---|
| What happened | one-line description of the issue |
| Auto-catchable? | yes (what would catch it) / no (judgment call) |
| Rule existed? | yes (cite ADR / PATTERNS §) / no |
| Proposed fix | concrete action with file paths |
| Fix layer | **3** > **1** > **2** > **4** |

## The four valid fix layers

Preference: **higher numbers descending into lower**. Automate where
possible; doc-only as fallback.

| Layer | Use when | Concrete action |
|---|---|---|
| **3 (Automated)** | Mechanically detectable | Add lint, schema, or boundary rule |
| **1 (Patterns)** | Judgment call with defensible answer | Write ADR + update PATTERNS.md |
| **2 (Process)** | Coordination issue, not code | Update DoD / ROLE_SPECS / TEAM_PROTOCOL |
| **4 (Launch)** | Launch-prompt ambiguity | Update TEAM_LAUNCH_TEMPLATE |

If you can't place a fix in one of these four layers, the fix isn't
concrete enough yet.

## Output format (Keep a Changelog 1.1.0)

Append to `docs/FRAMEWORK_CHANGELOG.md`:

```markdown
## YYYY-MM-DD — <feature> retrospective

### Issues observed
- **<issue>**: <description>
  - Auto-catchable? <yes/no — what>
  - Existing rule? <yes/no — link>
  - Fix: Layer <N> — <concrete action>

### Near-misses / gaps
- ...

### What worked
- ...

### Lead's decisions
- [x] Approved & landed: <fix>
- [ ] Deferred: <fix> — reason: <...>
- [ ] Rejected: <fix> — reason: <...>
```

## Anti-patterns to flag

- "Everything went great" → push back; find at least one improvement
- Proposing a doc fix when automation would work
- Rejecting a fix without recording a reason
- Lead applying changes silently — every framework change appears in
  the changelog with a triggering retrospective

## Verifying Layer 3 fixes

After adding a new automated rule (lint, boundary, etc.), confirm it
actually fires on the original violation that triggered it. A rule
that doesn't fire on its own trigger case is worse than no rule —
it's misleading.

## Upstreaming a finding

If the issue is generic (would affect other Cadence users, not just
this project), suggest the user open a Framework Finding at:

https://github.com/nik190799/cadence/issues/new?template=framework_finding.md

The community loop is opt-in but it's how the canonical framework
grows.

## Source of truth

The full protocol is at `docs/RETROSPECTIVE_PROTOCOL.md`. This skill
is a condensed cheat sheet; the user's local doc is authoritative.

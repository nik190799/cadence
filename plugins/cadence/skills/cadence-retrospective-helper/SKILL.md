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

## Verifying Layer 3 fixes — the emitter is mandatory

Layer 3 fixes do not become "landed" by hand-editing
`.cadence/cadence.yaml`. They go through `tool/emit_rule.py`, which:

1. Builds a regression fixture under `tests/fixtures/retro/<id>/`
   containing the exact offending import
2. Writes a fixture cadence.yaml with the proposed rule
3. Runs the checker against the fixture
4. **Refuses to mark the finding as landed unless the rule fires on
   the sample**

A rule that doesn't catch its own trigger case is worse than no rule
— the emitter enforces this at the code level.

Required JSON block when `auto_method: "boundary-rule"`:

```json
"violation_sample": {
  "kind": "boundary-rule",
  "language": "ts|py|go|rs|dart|java|kt|swift",
  "where": "<glob>",
  "import_line": "<exact offending import>",
  "forbidden_pattern": "<glob>",
  "reason": "<why>"
}
```

Run: `python tool/emit_rule.py --input finding.json --apply`

Exit 0 = land it. Exit 1 = rule doesn't fire, revise. Exit 2 = bad input.

Full protocol lives in [cadence-retro](../cadence-retro/SKILL.md) §4a.

## Upstreaming a finding

If the issue is generic (would affect other Cadence users, not just
this project), suggest the user open a Framework Finding at:

https://github.com/nik190799/cadence/issues/new?template=framework_finding.md

The community loop is opt-in but it's how the canonical framework
grows.

## Source of truth

The full protocol is at `docs/RETROSPECTIVE_PROTOCOL.md`. This skill
is a condensed cheat sheet; the user's local doc is authoritative.

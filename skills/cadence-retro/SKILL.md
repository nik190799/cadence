---
name: cadence-retro
description: Drive the Cadence retrospective protocol after a team run. For each observed issue, map to one of the four fix layers (Patterns, Process, Automated gates, Launch) and emit specific patches. Use when the user runs /cadence-retro, asks to do a retrospective, runs a post-mortem on work just completed, or asks how to capture lessons learned.
---

# /cadence-retro

> ⚠️ **Stub — Phase 1.**

## Intended behavior

1. **Gather signal** from the just-completed work:
   - `git log` since last tag or last commit on a feature branch
   - Recent verify run output (from `scripts/verify` cache if any)
   - Conversation transcript of the team run (if accessible)

2. **Walk the four-layer fix mapping table** (from
   `docs/RETROSPECTIVE_PROTOCOL.md`) for each observed issue:

   | Field | Value |
   |---|---|
   | What happened | (one-line description) |
   | Auto-catchable? | yes / no — and what would catch it |
   | Existing rule? | yes (cite ADR / section) / no |
   | Proposed fix | concrete action |
   | Fix layer | 1 (Patterns) / 2 (Process) / 3 (Automated) / 4 (Launch) |

3. **For each row, emit patches**:
   - **Layer 3**: a `.patch` against `cadence.yaml` boundaries or a new
     lint rule
   - **Layer 1**: a new file `docs/ADR/NNNN-<slug>.md` + a diff against
     `docs/PATTERNS.md`
   - **Layer 2**: a diff against `docs/DEFINITION_OF_DONE.md` or
     `docs/ROLE_SPECS.md`
   - **Layer 4**: a diff against `docs/TEAM_LAUNCH_TEMPLATE.md`

4. **Present interactively** to the user; for each proposed fix:
   approve / defer / reject

5. **On approval**: apply the patch immediately

6. **Append to `docs/FRAMEWORK_CHANGELOG.md`** following the Keep a
   Changelog format with sections: Issues observed, Near-misses,
   What worked, Lead's decisions

7. **Re-run verify** to confirm new gates fire on the original
   offending diff (regression test for the rule itself)

## Anti-patterns to flag

- "Everything went great" — push back; find at least one improvement
- Proposing a Layer 1 (doc) fix when Layer 3 (automation) would work
- Rejecting a fix without recording the reason

See: [docs/self-improvement-loop.md](../../docs/self-improvement-loop.md)

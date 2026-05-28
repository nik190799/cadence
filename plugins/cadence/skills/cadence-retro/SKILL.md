---
name: cadence-retro
description: Drive the Cadence retrospective protocol after a team run. For each observed issue, map to one of the four fix layers (Patterns, Process, Automated gates, Launch) and emit specific patches. Use when the user runs /cadence-retro, asks to do a retrospective, runs a post-mortem on work just completed, or asks how to capture lessons learned.
---

# /cadence-retro

You are driving the structured Cadence retrospective per
`docs/RETROSPECTIVE_PROTOCOL.md`. This is the framework's
self-improvement engine.

## Step 1 — Gather signal

Read these inputs:
- `git log` since last tag or last commit on a feature branch
- Recent verify run output (re-run `scripts/verify` if needed)
- The conversation transcript of the team run (whatever's in current
  context)
- Current state of: `docs/PATTERNS.md`, `docs/DEFINITION_OF_DONE.md`,
  any new files in this branch

## Step 2 — Draft the table

For EACH issue observed (the Reviewer's flags + any near-misses),
create a row:

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

If the user says "everything went great" — push back. Find at least
one improvement. Even great runs reveal something (e.g., "the handoff
to Tester took longer than it should have because Feature Engineer
didn't message — flag in TEAM_PROTOCOL.md").

## Step 3 — Present interactively

For each row, ask the user: **approve / defer / reject**.

- **Approve** → apply the patch immediately in this same chat session
- **Defer** → add to `## Open retrospective items` section in
  `FRAMEWORK_CHANGELOG.md`
- **Reject** → record the reason in the changelog entry so future
  retros don't re-propose it

## Step 4 — Apply approved patches

For each approved fix:

| Layer | Patch shape |
|---|---|
| 3 (Automated) | **Must round-trip through `tool/emit_rule.py`** — see Step 4a |
| 1 (Patterns) | Create new `docs/ADR/NNNN-<slug>.md` + update `docs/PATTERNS.md` §X to cite it |
| 2 (Process) | Edit `docs/DEFINITION_OF_DONE.md`, `docs/ROLE_SPECS.md`, or `docs/TEAM_PROTOCOL.md` |
| 4 (Launch) | Edit `docs/TEAM_LAUNCH_TEMPLATE.md` |

### Step 4a — Layer 3 emission protocol (mandatory)

A Layer 3 finding is **only** considered landed after the framework
auto-generates a regression fixture and confirms the new rule fires
on the original offending sample. The emitter enforces this — never
hand-edit `.cadence/cadence.yaml` for Layer 3 without going through it.

For each approved Layer 3 finding:

1. **Build the finding JSON.** Construct an object matching
   `plugins/cadence/schemas/retro.schema.json` (or the local copy at
   `.cadence/retro.schema.json` after `/cadence-init`). The
   `violation_sample` block is **required** when
   `auto_method: "boundary-rule"`:

   ```json
   {
     "id": "<uuid4>",
     "ts": "<ISO 8601>",
     "what_happened": "<one-line>",
     "auto_catchable": true,
     "auto_method": "boundary-rule",
     "rule_existed": false,
     "proposed_fix": "<concrete action>",
     "fix_layer": 3,
     "decision": "approved",
     "violation_sample": {
       "kind": "boundary-rule",
       "language": "ts|py|go|rs|dart|java|kt|swift",
       "where": "<glob>",
       "import_line": "<the exact offending import>",
       "forbidden_pattern": "<glob>",
       "reason": "<why this boundary exists>"
     }
   }
   ```

2. **Run the emitter:**

   ```bash
   python tool/emit_rule.py --input finding.json --apply
   ```

   - `--apply` also appends the new rule to the project's root
     `.cadence/cadence.yaml` (idempotent — duplicate rules are
     detected and skipped)
   - Drop `--apply` if the user wants to review the fixture before
     merging the rule into the project config

3. **Check the exit code.**
   - **Exit 0** — fixture written under `tests/fixtures/retro/<id>/`
     and the new rule fires on the offending sample. The finding is
     safe to record as landed.
   - **Exit 1** — the rule did **not** fire. A rule that doesn't
     catch its own trigger case is worse than no rule. Do NOT record
     this finding as landed. Revise the `where`, `forbidden_pattern`,
     or `import_line` and re-run.
   - **Exit 2** — schema invalid, bad input, or fixture already
     exists. Read the stderr message; usually a structural issue.

4. **Cite the generated fixture in the changelog.** In Step 5's
   "Lead's decisions" line, record the fixture path:

   ```
   - [x] Approved & landed: <fix> — fixture tests/fixtures/retro/<id>/, commit <SHA>
   ```

5. **Never bypass the emitter for Layer 3.** If a finding can't be
   expressed as a boundary-rule sample (e.g., requires a new lint
   rule or schema check), it isn't a `boundary-rule` finding — set
   `auto_method` to the appropriate value once that emitter ships, or
   demote it to Layer 1 (patterns) for now and document why.

## Step 5 — Append to FRAMEWORK_CHANGELOG.md

Use this exact format (Keep a Changelog 1.1.0):

```markdown
## YYYY-MM-DD — <feature> retrospective

### Issues observed

- **<issue>**: <description>
  - Auto-catchable? <yes/no — what>
  - Existing rule? <yes/no — link if yes>
  - Fix: Layer <N> — <concrete action>

### Near-misses / gaps

- **<gap>**: <description>
  - Fix: Layer <N> — <action>

### What worked

- <thing to amplify in future runs>

### Lead's decisions

- [x] Approved & landed: <fix> — commit <SHA>
- [ ] Deferred: <fix> — reason: <why>
- [ ] Rejected: <fix> — reason: <why>
```

## Step 6 — Suggest upstreaming

If any approved fix would benefit other Cadence users (not just this
project), suggest the user open a Framework Finding at:

https://github.com/nik190799/cadence/issues/new?template=framework_finding.md

Provide the link text they can paste.

## Step 7 — Wrap

Tell the user:

```
Retrospective complete.
N findings: <approved> approved, <deferred> deferred, <rejected> rejected.
Framework changelog updated.
Safe to /clean up the team now.
```

## Safety rules

- Never skip the retrospective with "nothing to report"
- Never apply a fix without explicit user approval
- Every framework change MUST appear in FRAMEWORK_CHANGELOG.md
- For Layer 3 patches, verify the new rule actually fires before
  considering it landed

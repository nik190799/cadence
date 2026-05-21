---
name: cadence-launch
description: Fill in the Cadence team launch template for a new feature, render the launch prompt, and optionally spawn four coordinated subagents (data engineer, feature engineer, tester, reviewer). Use when the user runs /cadence-launch or asks to kick off a feature with a Cadence team.
argument-hint: "<feature-name> [--no-team]"
---

# /cadence-launch

> ⚠️ **Stub — Phase 1.** This skill's full behavior is implemented in
> Phase 1.

## Intended behavior

When invoked as `/cadence-launch <feature>`:

1. **Read `docs/TEAM_LAUNCH_TEMPLATE.md`** from the user's repo
2. **Walk the placeholders interactively**:
   - `{{one-line description}}` — confirm with user
   - `{{requirements}}` — gather as bullets
   - `{{acceptance criteria}}` — beyond Definition of Done
   - `{{out of scope}}` — explicit non-goals
3. **Render the final launch prompt** to `docs/TEAM_LAUNCH.md`
   (gitignored)
4. **Unless `--no-team` is passed:** spawn the four Cadence subagents
   (`cadence-data-engineer`, `cadence-feature-engineer`, `cadence-tester`,
   `cadence-reviewer`) configured per `docs/ROLE_SPECS.md`
5. **Print the launch prompt** so the user can paste it manually if
   they prefer

## Coordination

The subagents coordinate via the shared task list and the handoff
phrases defined in `docs/TEAM_PROTOCOL.md`:
- `Providers ready for <feature>:`
- `Controller for <feature> ready.`
- `Tests for <feature> ready:`
- `Reviewer sign-off:`
- `Blocked on:`

See: [docs/architecture.md](../../docs/architecture.md) — Layer 4 section

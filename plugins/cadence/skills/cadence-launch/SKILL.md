---
name: cadence-launch
description: Fill in the Cadence team launch template for a new feature, render the launch prompt, and optionally spawn four coordinated subagents (data engineer, feature engineer, tester, reviewer). Use when the user runs /cadence-launch or asks to kick off a feature with a Cadence team.
argument-hint: "<feature-name> [--no-team] [--solo]"
---

# /cadence-launch

You are rendering the Cadence team launch prompt for a new feature and
(optionally) spawning four coordinated subagents.

## Inputs

- Required positional: `<feature-name>` — short name like
  `favorites-tab` or `csv-export`
- Optional flags:
  - `--no-team`: render the prompt but don't spawn agents; user pastes
    it manually
  - `--solo`: smaller team variant; spawn only Data Engineer + Feature
    Engineer (no Tester or Reviewer)

## Step 1 — Verify prerequisites

Read these files (fail loudly if any is missing):
- `docs/PATTERNS.md`
- `docs/DEFINITION_OF_DONE.md`
- `docs/ROLE_SPECS.md`
- `docs/TEAM_PROTOCOL.md`
- `docs/TEAM_LAUNCH_TEMPLATE.md`
- `.cadence/cadence.yaml`

If any is missing, suggest the user run `/cadence-init` first.

## Step 2 — Gather feature spec interactively

Ask the user for:

1. **One-line description**: "<verb> <object>" — e.g., "Add favorites
   tab to todos screen"
2. **Requirements** (bulleted): user-facing behavior, 3-7 bullets
3. **Acceptance criteria beyond DoD** (or "none")
4. **Out of scope** (explicit list, or "none")

Refuse to proceed if the user gives only a one-liner — push back and
ask for requirements.

## Step 3 — Render the launch prompt

Take `docs/TEAM_LAUNCH_TEMPLATE.md` and substitute the gathered values
into the `{{...}}` placeholders. Write the resolved prompt to
`docs/TEAM_LAUNCH.md` (gitignored). Print it to the chat as well.

## Step 4 — Spawn the team (unless --no-team)

Spawn the four Cadence subagents defined in the plugin's `agents/`
directory:

- `cadence-data-engineer`
- `cadence-feature-engineer`
- `cadence-tester`
- `cadence-reviewer`

If `--solo`, only spawn the first two and tell the user "Tester role
merged into Feature Engineer for this run."

Each agent receives the launch prompt as initial context plus a
pointer to `.cadence/cadence.yaml` for path conventions.

## Step 5 — Coordination check-in

Print a short summary to the user:

```
Team spawned: <N> agents.

Coordination:
- Data Engineer goes first (typedefs in app/providers.<ext>)
- Feature Engineer + Tester unblock when providers land
- Reviewer audits in flight per docs/TEAM_PROTOCOL.md
- I (the lead, you) intervene if a teammate stalls

Handoff phrases (so you can follow along):
- "Providers ready for <feature>:" → Data Eng → Feature Eng
- "Controller for <feature> ready." → Feature Eng → Tester
- "Tests for <feature> ready:" → Tester → Reviewer
- "Reviewer sign-off:" → Reviewer → you

Use Ctrl+T for the shared task list. Shift+Down to cycle terminals.
```

## Step 6 — Don't end the session yet

After spawning, remind the user that the team is running and that
they should NOT clean up until after `/cadence-retro` completes.

## Safety rules

- Don't spawn the team without explicit user confirmation of the
  feature spec
- Don't proceed if `.cadence/cadence.yaml` is missing — surface the
  issue clearly
- If the user's project doesn't have a reference feature pattern yet,
  warn them and suggest writing one first (so the team has something
  to align to)

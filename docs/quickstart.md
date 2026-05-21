---
layout: default
title: Quickstart
---

# Quickstart

Five minutes from `claude plugins add` to the framework improving
itself on your repo.

## Prerequisites

- [Claude Code](https://claude.com/code) v2.1.32 or newer
- A git repository you want to add Cadence to (any stack)

## 1. Install the plugin

```bash
claude plugins add nik190799/cadence
```

Or from inside Claude Code:

```
/plugin install nik190799/cadence
```

## 2. Initialize the framework in your project

```
/cadence-init
```

Cadence detects your project's stack from common markers
(`package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`,
`pubspec.yaml`, `build.gradle`) and prefills `.cadence/cadence.yaml`
with sensible default commands.

It writes:

- `docs/PATTERNS.md`, `DEFINITION_OF_DONE.md`, `ROLE_SPECS.md`,
  `TEAM_PROTOCOL.md`, `RETROSPECTIVE_PROTOCOL.md`,
  `TEAM_LAUNCH_TEMPLATE.md`, `FRAMEWORK_CHANGELOG.md`
- `docs/ADR/{README.md, 0000-template.md}`
- `scripts/verify.{sh,ps1}`
- `tool/check_boundaries.py`
- `.github/workflows/cadence.yml`
- A pointer in `CLAUDE.md`

Review `.cadence/cadence.yaml` and adjust as needed.

## 3. Verify the gates fire on your existing code

```
/cadence-verify
```

This runs the verify pipeline (format → lint → boundary check → test)
using your project's tooling and renders a Definition-of-Done aligned
checklist. Existing issues become Cadence findings against named rules.

## 4. Launch a team for your next feature

```
/cadence-launch "favorites tab"
```

Cadence fills in `TEAM_LAUNCH_TEMPLATE.md` placeholders. You can choose
to spawn four subagents (data engineer, feature engineer, tester,
reviewer) who coordinate via a shared task list — or use the prompt
yourself as a single session.

## 5. Run the retrospective

After the work is done:

```
/cadence-retro
```

The Reviewer walks the four-layer fix mapping: for each issue observed,
is it auto-catchable (Layer 3), a missing pattern (Layer 1), a process
gap (Layer 2), or a launch ambiguity (Layer 4)? Approved fixes land
immediately as new ADRs, PATTERNS entries, boundary rules, or DoD
lines. The change is recorded in `FRAMEWORK_CHANGELOG.md`.

Your framework just got smarter on YOUR codebase.

## What next

- Open a [Framework Finding](https://github.com/nik190799/cadence/issues/new?template=framework_finding.md)
  to share a recurring issue with the community so the canonical
  framework can improve for everyone.
- Read [Architecture](architecture.md) to understand the four-layer
  model.
- Read [Self-improvement loop](self-improvement-loop.md) to understand
  how findings flow from local → community.

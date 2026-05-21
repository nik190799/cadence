---
layout: default
title: Cross-tool portability
---

# Cross-tool portability

Cadence is designed to work in Claude Code, but the artifacts it
produces are tool-neutral. Anyone using a different AI dev tool can
still get most of the value.

## What every tool can use

- **`docs/PATTERNS.md`, `docs/ADR/*.md`** — plain markdown. Any agent
  with file-reading capability can consume them.
- **`docs/DEFINITION_OF_DONE.md`** — checklist; any reviewer (human or
  agent) can walk it.
- **`docs/RETROSPECTIVE_PROTOCOL.md`** — the structured table walk;
  works in any chat-based agent.
- **`scripts/verify.{sh,ps1}`** — pure shell, runs from any environment
  with the configured tooling.
- **`tool/check_boundaries.py`** — pure Python, runs anywhere with
  Python 3.10+.
- **`cadence.yaml`** — declarative configuration; not tied to any agent.

## What Claude Code adds

- **Slash commands** (`/cadence-init`, `/cadence-launch`, etc.) — quick
  invocation
- **Auto-invoked skills** — `cadence-framework`, `cadence-retrospective-helper`,
  `cadence-adr-writer` activate based on conversation context
- **Subagent dispatch** — the four roles spawn as separate Claude Code
  sessions with their own context windows
- **Plugin distribution** — one-command install and update

## Using Cadence in other tools

### Codex (OpenAI Codex CLI)

After running `npx cadence init` (Phase 2 capability), point Codex at
`CADENCE.md` (a root pointer file Cadence creates that references the
patterns and DoD). Codex's repository-level instructions can include:

```
This project uses the Cadence framework. Read docs/PATTERNS.md and
docs/DEFINITION_OF_DONE.md before making any changes. After completing
work, run scripts/verify.sh and follow docs/RETROSPECTIVE_PROTOCOL.md.
```

### Cursor / Windsurf

Add to `.cursorrules` / `.windsurfrules`:

```
This project uses the Cadence framework (docs/PATTERNS.md). Follow
the patterns there. Run scripts/verify.sh before declaring work done.
For architectural decisions, follow docs/ADR/0000-template.md format
and append to docs/FRAMEWORK_CHANGELOG.md.
```

### Cline / Roo Code / Continue

These tools have modes/roles. The closest match to Cadence's subagent
roles is to define four custom modes (data-engineer, feature-engineer,
tester, reviewer) that each load `docs/ROLE_SPECS.md` and the relevant
file-ownership glob.

### Aider

Use `CONVENTIONS.md` (Aider's convention) to point at Cadence:

```markdown
# Project conventions

This project uses the Cadence framework. Before changing code:
- Read docs/PATTERNS.md
- Follow the role boundaries in docs/ROLE_SPECS.md

Before declaring work done:
- Run scripts/verify.sh
- Walk docs/DEFINITION_OF_DONE.md
- Run a retrospective per docs/RETROSPECTIVE_PROTOCOL.md
```

## What you lose in degraded mode

Outside Claude Code, you lose:

- One-command install (`claude plugins add`) — instead, `npx cadence init`
- Automatic skill activation — agents need to be reminded to consult docs
- Native subagent dispatch — you simulate roles via prompt templates
- Native upgrade flow — manual `git pull` on your `.cadence/` if you
  vendored it

What you keep:

- The full framework, untouched
- The retrospective protocol
- The verify pipeline
- Compliance reports
- The community improvement loop (open issues / PRs against this repo
  same as anyone else)

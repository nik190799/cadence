# Subagents

Four pre-configured Cadence subagents matching the role definitions in
`templates/docs/ROLE_SPECS.md.tmpl`. Spawned by `/cadence-launch` when
the user opts to run a team.

| Agent | Owns | Forbidden |
|---|---|---|
| `cadence-data-engineer` | data sources, repositories, narrow providers | importing features |
| `cadence-feature-engineer` | feature folders | importing data sources/repositories directly |
| `cadence-tester` | test directory | importing data sources/repositories in tests |
| `cadence-reviewer` | nothing (read-only) | writing code |

Each agent's system prompt cites the user's local `docs/ROLE_SPECS.md`
as the canonical source — the agent reads `.cadence/cadence.yaml` at
session start to learn the project's specific ownership paths.

## Format

Each agent is `agents/<name>.md` with frontmatter:

```yaml
---
name: cadence-reviewer
description: Read-only reviewer that enforces Cadence's DoD and drives the retrospective protocol.
model: opus
tools: ["Read", "Grep", "Glob", "Bash"]
---
```

## Status

Stub frontmatter only in v0.0.1 (Phase 0). Agent system prompts arrive
in Phase 1.

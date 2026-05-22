# Skills

Each subdirectory here is one Cadence skill (`SKILL.md` file per
Anthropic's plugin convention). Cadence ships nine skills:

## Slash-command-style (6)

Invoked explicitly by the user via `/<name>`:

| Skill | Slash | Phase 1 content |
|---|---|---|
| `cadence-init` | `/cadence-init` | Scaffold framework into current repo |
| `cadence-launch` | `/cadence-launch <feature>` | Fill launch template, optionally spawn 4 subagents |
| `cadence-verify` | `/cadence-verify` | Run verify pipeline, render DoD checklist |
| `cadence-retro` | `/cadence-retro` | Drive retrospective protocol |
| `cadence-adr` | `/cadence-adr <title>` | Create next-numbered ADR |
| `cadence-compliance` | `/cadence-compliance --standard <X>` | Generate compliance report |

## Auto-invoked (3)

Activated by Claude when conversation context matches the skill's
description triggers:

| Skill | Triggers on |
|---|---|
| `cadence-framework` | "patterns", "DoD", "role spec", "narrow provider" |
| `cadence-retrospective-helper` | "retro", "post-mortem", "lessons learned" |
| `cadence-adr-writer` | "ADR", "architectural decision", "judgment call" |

## Format

Each skill is a directory `skills/<name>/` containing `SKILL.md` with
required `description` frontmatter, optional `name` (defaults to
directory name), optional `argument-hint`. See
[Anthropic's plugin docs](https://code.claude.com/docs/en/plugins-reference)
for the spec.

## Status

Stubs only in v0.0.1 (Phase 0 — bootstrap). Skill bodies arrive in
Phase 1.

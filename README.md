# Cadence

A stack-agnostic, self-improving AI development framework — packaged as
a Claude Code plugin. Four layers (Patterns / Process / Automated gates
/ Launch) plus a retrospective loop that lets the framework grow as
your team uses it.

Aligned with NIST SSDF v1.1, ISO/IEC 25010:2023, ADR (Nygard),
Conventional Commits 1.0, and Semantic Versioning 2.0.

> Status: **v0.1.0 — Phase 1 complete.** All 9 skills, 4 subagents,
> full template content, the language-agnostic boundary checker, the
> verify orchestrator, and the NIST SSDF + ISO 25010 standards
> mappings are in place. Marketplace submission tracked under
> `[Unreleased]` in [CHANGELOG.md](CHANGELOG.md).

## Installation

```bash
# Claude Code
claude plugins add nik190799/cadence
# or, from inside Claude Code
/plugin install nik190799/cadence
```

## Commands

| Command | Description |
|---|---|
| `/cadence-init` | Scaffold the framework into the current repo |
| `/cadence-launch <feature>` | Fill the launch template, optionally spawn 4 subagents |
| `/cadence-verify` | Run the verify pipeline, render a DoD-aligned checklist |
| `/cadence-retro` | Drive the structured retrospective, append to `FRAMEWORK_CHANGELOG.md` |
| `/cadence-adr <title>` | Create the next-numbered ADR in Nygard format |
| `/cadence-compliance --standard ssdf\|iso25010` | Generate a compliance report |

## Skills (auto-invoked)

| Skill | Triggers on |
|---|---|
| `cadence-framework` | "patterns", "DoD", "role spec", "narrow provider" |
| `cadence-retrospective-helper` | "retro", "post-mortem", "lessons learned" |
| `cadence-adr-writer` | "ADR", "architectural decision", "judgment call" |

## Subagents

- `cadence-data-engineer` — owns data sources / repositories / provider declarations
- `cadence-feature-engineer` — owns feature folders; must read via narrow providers
- `cadence-tester` — owns the test root; enforces ADR-aligned coverage
- `cadence-reviewer` — read-only; drives DoD + retrospective

## Example workflow

```bash
# 1. Install the plugin (one-time, per workstation)
claude plugins add nik190799/cadence

# 2. In a fresh project repo, scaffold the framework
/cadence-init                          # detects stack; prefills cadence.yaml

# 3. Launch a team for a new feature
/cadence-launch "favorites tab"        # fills template, optionally spawns 4 subagents

# 4. After the team's work is done, verify and run the retrospective
/cadence-verify                        # format / lint / boundary check / test
/cadence-retro                         # framework gets smarter
```

## Standards alignment

- **[ADR (Nygard)](https://www.cognitect.com/blog/2011/11/15/documenting-architecture-decisions)** — every architectural decision lands as one short markdown record
- **[NIST SSDF v1.1](https://csrc.nist.gov/projects/ssdf)** — practice IDs (PO, PS, PW, RV) map to artifacts shipped by the framework
- **[ISO/IEC 25010:2023](https://www.iso.org/standard/78176.html)** — DoD checklist items tagged with quality characteristics
- **[Conventional Commits 1.0](https://www.conventionalcommits.org/en/v1.0.0/)** + **[Semantic Versioning 2.0](https://semver.org/)** — enforced by the Reviewer subagent
- **[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)** — `FRAMEWORK_CHANGELOG.md` format

## See Cadence applied to a real codebase

The [Flutter sandbox case study](docs/case-studies/flutter-sandbox.md)
shows the framework applied to a real Flutter + Firebase project, with
seven completed ADRs and two retrospective iterations showing the
self-improvement loop in action.

## Cross-tool portability

Non-Claude-Code users (Codex, Gemini CLI, Cline) can run `npx cadence
init` (Phase 2) to scaffold the framework into any repo. Slash commands
and subagent dispatch are Claude Code–specific; the framework, verify
pipeline, retrospective protocol, and compliance reports all work
anywhere.

## Contributing

Cadence improves through community retrospective findings. If you used
the framework and hit a recurring issue, open an issue with the
[Framework finding](.github/ISSUE_TEMPLATE/framework_finding.md)
template. See [CONTRIBUTING.md](CONTRIBUTING.md) for the full process.

## License

Apache 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).

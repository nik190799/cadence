# Changelog

All notable changes to Cadence are documented here. Format follows
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/); version
numbers follow [Semantic Versioning 2.0](https://semver.org/).

## [Unreleased]

### Planned for v0.3.0
- Stack-specific high-accuracy boundary checkers (TS, Python, Go) —
  trigger when basic line-pattern matching produces false positives
- npx CLI for non-Claude-Code users (Codex, Gemini CLI, Cline)

## [0.2.0] — 2026-05-21

### Changed (BREAKING for upstream marketplaces)
- **Repo restructured: plugin internals moved into `plugins/cadence/`.**
  This matches the convention every external plugin in
  `anthropics/claude-plugins-official` already follows (42Crunch,
  Adobe, etc.) and is required for the official marketplace
  submission. The self-hosted marketplace.json now sources from
  `./plugins/cadence`.
- Why: when a marketplace entry referenced this repo with `path: "."`,
  Claude Code's installer wrote a sparse-checkout pattern
  (`/*` + `!/*/`) that excluded every subdirectory — skills, agents,
  and templates were silently dropped from the install. Putting the
  plugin in a subdir avoids the bug entirely.
- Migration: users who installed v0.1.0 via the self-hosted marketplace
  should run `/plugin update cadence@cadence` to pick up the new
  layout. The plugin manifest version bump (0.1.0 → 0.2.0) ensures
  Claude Code refetches.

### Added
- `.claude-plugin/marketplace.json` at repo root pointing at
  `./plugins/cadence` (the marketplace catalog stays at root; the
  plugin itself lives in the subdir).

### Fixed
- Tests and CI workflow updated to reference the new paths under
  `plugins/cadence/`.

## [0.1.0] — 2026-05-21

### Added — Phase 1 implementation

- **Real `tool/check_boundaries.py`** — language-agnostic import-boundary
  checker. Handles path-form (TS/JS/Dart/Go), dotted-form
  (Python/Java), and colon-form (Rust) imports. Word-boundary
  matching avoids false positives like `app.` inside `myapp.`. 14
  tests covering happy paths, edge cases, and language-specific
  forms.
- **Real `scripts/verify.{sh,ps1}`** — orchestrate format → lint →
  boundary → test from `.cadence/cadence.yaml`. Fail-fast with
  DoD-shaped summary.
- **Real bodies for all 9 skills** — `cadence-init`, `cadence-launch`,
  `cadence-verify`, `cadence-retro`, `cadence-adr`, `cadence-compliance`
  (slash commands); `cadence-framework`, `cadence-retrospective-helper`,
  `cadence-adr-writer` (auto-invoked).
- **Real subagent system prompts** for the four roles: data engineer,
  feature engineer, tester, reviewer. Each reads `.cadence/cadence.yaml`
  on session start to learn the project's paths.
- **Full template content** for all `templates/docs/*.md.tmpl` —
  PATTERNS, DoD, ROLE_SPECS, TEAM_PROTOCOL, RETROSPECTIVE_PROTOCOL,
  TEAM_LAUNCH_TEMPLATE, FRAMEWORK_CHANGELOG, ADR template + index,
  CLAUDE.md patcher, GitHub Actions workflow.
- **Full standards mappings:**
  - `standards/nist-ssdf-1.1.yaml` — all PO/PS/PW/RV practices with
    artifact mappings
  - `standards/iso-25010-2023.yaml` — all 9 characteristics (including
    the new Safety characteristic from Edition 2) with sub-characteristics
- **Tightened JSON schemas** — `cadence-yaml.schema.json` and
  `retro.schema.json` now have validation constraints (required fields,
  enum constraints for fix_layer, pattern checks for SSDF practice IDs).
- **CI improvements:**
  - `boundary-check-fixtures` job: runs pytest on every push
  - `validate-cadence-yaml-schema` job: validates every recipe and
    fixture cadence.yaml against the schema
- **Test fixtures** for TypeScript and Python projects with deliberate
  boundary violations to exercise the checker.

### Changed
- `plugin.json` version 0.0.1 → 0.1.0
- README status line updated to reflect Phase 1 complete

## [0.0.1] — 2026-05-21

### Added
- Initial public repository bootstrap
- Apache 2.0 license + NOTICE
- README with installation, commands, skills, subagents tables
- Documentation site under `docs/` (architecture, quickstart,
  self-improvement loop, cross-tool portability, recipes, standards
  mappings, Flutter case-study link)
- OSS hygiene: CONTRIBUTING, CODE_OF_CONDUCT (Contributor Covenant 3.0),
  SECURITY
- GitHub Actions CI workflow (dogfood verify on placeholder fixtures)
- Issue templates: bug report, feature request, framework finding
- Pull request template
- Dependabot config
- `.claude-plugin/plugin.json` manifest stub (v0.0.1)
- Directory stubs for `skills/`, `agents/`, `templates/`, `recipes/`,
  `standards/`, `schemas/`, `tests/` with placeholder READMEs explaining
  Phase 1 content

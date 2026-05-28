# Changelog

All notable changes to Cadence are documented here. Format follows
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/); version
numbers follow [Semantic Versioning 2.0](https://semver.org/).

## [Unreleased]

### Planned for v0.3.0 (still pending)
- Stack-specific high-accuracy boundary checkers (TS, Python, Go) —
  trigger when basic line-pattern matching produces false positives
- npx CLI for non-Claude-Code users (Codex, Gemini CLI, Cline)
- Additional emitter kinds for `tool/emit_rule.py`: lint-rule and
  schema-rule (currently only `boundary-rule` is wired)

## [0.3.0-rc.1] — 2026-05-27

### Added — retrospective loop closes at the code level

- **`plugins/cadence/templates/tool/emit_rule.py`** — Layer-3 rule
  emitter. Reads a single retrospective finding (JSON matching
  `retro.schema.json`) on stdin or `--input`. For findings with
  `fix_layer: 3` and `auto_method: "boundary-rule"`, the helper:
  1. Materializes a regression fixture at
     `tests/fixtures/retro/<short-id>/` containing the exact offending
     import
  2. Writes a fixture `.cadence/cadence.yaml` with the proposed new
     boundary rule
  3. Runs the existing checker against the fixture
  4. **Refuses to record the finding as landed unless the new rule
     fires on the offending sample.** Exit code 1 means the rule is
     broken — a rule that doesn't catch its own trigger case is worse
     than no rule.
  5. With `--apply`, also appends the rule to the project's root
     `.cadence/cadence.yaml` (idempotent — duplicate rules are
     detected and skipped).
- **`tests/test_emit_rule.py`** — 13 tests covering the happy path,
  per-language samples (ts, py, go, rs, dart), the safety case where
  the proposed rule doesn't fire, schema rejection paths, `--apply`
  idempotency, and no-clobber on existing fixtures. This is the
  literal proof of the self-improving claim: any user can clone the
  repo and watch the loop close.

### Changed — retro skills require the emitter for Layer 3

- **`cadence-retro` SKILL.md** — Step 4a is now a mandatory protocol
  for Layer 3 findings: build a finding JSON with a `violation_sample`
  block, pipe to `tool/emit_rule.py`, check the exit code. The skill
  refuses to record a Layer 3 fix as "landed" without going through
  the emitter. Hand-editing `.cadence/cadence.yaml` for Layer 3 is no
  longer the supported path.
- **`cadence-retrospective-helper` SKILL.md** — Verifying Layer 3
  fixes section rewritten to point at the mandatory emitter flow.
- **`cadence-init` SKILL.md** — Adds `tool/emit_rule.py` and
  `.cadence/retro.schema.json` to the scaffold so user projects ship
  the emitter and schema locally.

### Changed — schema

- **`retro.schema.json`** — adds `violation_sample` sub-schema (kind,
  language, where, import_line, forbidden_pattern, reason). An `allOf`
  conditional makes `violation_sample` required when
  `fix_layer == 3 && auto_method == "boundary-rule"`.

### Why this exists

Cadence claims to be "self-improving" because retrospectives update
`FRAMEWORK_CHANGELOG.md` and patch `docs/PATTERNS.md`. That's true at
the documentation level, but other frameworks (gstack `/learn`,
Superpowers retrospectives) make similar claims. The differentiator
is making the loop close at the **code** level: a Layer 3 retro now
produces a test fixture + a boundary rule + a passing regression
check, and the framework refuses to record the finding as landed if
the rule doesn't actually catch the original violation. None of the
comparable frameworks (gstack, graphify, Superpowers, GSD) do this.

### Pre-release note

Tagged as `0.3.0-rc.1` (not `0.3.0`) so existing self-hosted-install
users on `v0.2.1` don't auto-upgrade. Promote to `0.3.0` after dogfood
runs on at least one external project.

## [0.2.1] — 2026-05-27

### Docs
- `docs/marketplace-submission.md` rewritten: the previous version
  described opening a PR against `anthropics/claude-plugins-official`,
  which auto-closes external PRs (team-only repo). Correct path is the
  web form at https://claude.ai/settings/plugins/submit — third-party
  plugins land in `anthropics/claude-plugins-community` instead.
  Discovered when our PR #1968 was auto-closed.
- Submission doc refreshed for the v0.2.1 cut: pins this tag's commit
  SHA so reviewers pulling from the form get a snapshot that includes
  the corrected submission path.

### Changed
- Plugin manifest + marketplace.json version bump 0.2.0 → 0.2.1. No
  functional changes to skills, agents, templates, schemas, or the
  verify pipeline — this release exists to cut a tag that bundles the
  corrected marketplace-submission doc, so the official-marketplace
  submission can reference a clean tagged ref.

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

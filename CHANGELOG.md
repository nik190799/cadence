# Changelog

All notable changes to Cadence are documented here. Format follows
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/); version
numbers follow [Semantic Versioning 2.0](https://semver.org/).

## [Unreleased]

### Planned for v0.1.0 (Phase 1)
- Implement the 9 SKILL.md skills (6 slash + 3 auto-invoked)
- Implement 4 subagent definitions
- Implement language-agnostic `tool/check_boundaries.py`
- Implement `scripts/verify.{sh,ps1}` orchestrators
- Implement 6 stack recipes
- Implement NIST SSDF v1.1 and ISO 25010:2023 standards mappings
- Submit to Claude Code marketplace

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

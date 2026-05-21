---
layout: default
title: Marketplace submission
---

# Submitting Cadence to the Claude Code marketplace

The Anthropic-curated marketplace lives at
[anthropics/claude-plugins-official](https://github.com/anthropics/claude-plugins-official).
Adding Cadence is a manual pull request — there's no automated
submission form.

This page documents the exact steps so the submission is
reproducible. It is **not** auto-executed by the plugin (third-party
PRs against external repos should always be a deliberate human
action).

## Step 1 — Fork the marketplace repo

```bash
gh repo fork anthropics/claude-plugins-official --clone --remote
cd claude-plugins-official
```

## Step 2 — Add Cadence to `marketplace.json`

Open `.claude-plugin/marketplace.json` (location is at the root of
the marketplace repo; if the schema has moved, follow the repo's
README). Add a new entry:

```json
{
  "name": "cadence",
  "display_name": "Cadence",
  "description": "Stack-agnostic, self-improving AI development framework — four layers + retrospective loop. Aligned with NIST SSDF v1.1, ISO 25010:2023, ADR.",
  "version": "0.1.0",
  "author": {
    "name": "Nikhil Kadalge",
    "url": "https://github.com/nik190799"
  },
  "homepage": "https://nik190799.github.io/cadence",
  "repository": "https://github.com/nik190799/cadence",
  "license": "Apache-2.0",
  "keywords": [
    "claude-code-plugin",
    "ai-development",
    "agent-framework",
    "software-architecture",
    "adr",
    "nist-ssdf",
    "iso-25010",
    "self-improving"
  ],
  "category": "engineering"
}
```

Sort the entry alphabetically with the others in `marketplace.json`.

## Step 3 — Commit and open the PR

```bash
git checkout -b add-cadence-plugin
git add .claude-plugin/marketplace.json
git commit -m "feat: add Cadence plugin to marketplace"
git push -u origin add-cadence-plugin
gh pr create --base main --title "feat: add Cadence plugin" --body "$(cat <<'EOF'
## Summary

Adds [Cadence](https://github.com/nik190799/cadence) v0.1.0 to the
official marketplace.

Cadence is a stack-agnostic, self-improving AI development framework
packaged as a Claude Code plugin. It ships four layers (Patterns,
Process, Automated gates, Launch) plus a retrospective protocol that
lets the framework grow as users apply it on real projects.

## What it ships

- 9 skills (6 slash + 3 auto-invoked)
- 4 subagents (data engineer, feature engineer, tester, reviewer)
- Language-agnostic import-boundary checker (`tool/check_boundaries.py`)
- Verify orchestrator (`scripts/verify.{sh,ps1}`)
- 6 stack recipes (TypeScript, Python, Go, Rust, Java, Dart/Flutter)
- Full NIST SSDF v1.1 and ISO/IEC 25010:2023 standards mappings
- 14 passing tests + cross-stack fixtures

## Test plan

- [x] `claude plugins add nik190799/cadence` installs successfully
      from upstream GitHub
- [x] `/cadence-init` scaffolds correctly in TypeScript and Python
      sample repos
- [x] `/cadence-verify` runs the full pipeline
- [x] `/cadence-launch` renders the launch template
- [x] `/cadence-retro` drives the structured retrospective
- [x] All CI checks green on the source repo

## License

Apache 2.0 — patent grant for enterprise legal review.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

## Step 4 — Respond to maintainer review

- If the maintainers request changes (schema fields, naming, etc.),
  apply them to your fork and push to the same branch — the PR
  updates automatically.
- After merge, install from the official marketplace verbatim:
  `claude plugins add cadence` (no `@nik190799/` prefix needed once
  it's in the curated marketplace).

## Step 5 — Update the Cadence repo

After the marketplace PR merges:

- Update the README installation section to show the simpler
  `claude plugins add cadence` form
- Bump `plugin.json` patch version if a follow-up release ships
  alongside the marketplace listing
- Add the marketplace acceptance to `CHANGELOG.md`

## Why this is a manual step

Cadence opens an external PR against an Anthropic-maintained repo.
Auto-submitting from a plugin would:

- Be presumptuous about Anthropic's review process and timing
- Risk spam if the plugin is installed widely and each install
  triggers a submission
- Make the human Cadence maintainer (you) not the actual author of
  the PR

The submission is a one-time deliberate action by you, not the
plugin's automation.

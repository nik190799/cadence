# Security Policy

## Reporting a vulnerability

If you believe you've found a security issue in Cadence (the plugin
itself, the boundary checker, the verify scripts, or any released
artifact), please **do not file a public issue.**

Instead:

1. Email **cadence-security@kadalge.dev** with:
   - A description of the issue
   - Steps to reproduce
   - Affected version (`cadence --version` or `plugin.json` value)
   - Suggested severity (low / medium / high / critical)

2. We aim to acknowledge within 48 hours and triage within one week.

3. Coordinated disclosure: we ask reporters not to publicly disclose
   until a fix is released or 90 days have elapsed, whichever is sooner.

## Supported versions

Cadence is in early development. The latest minor release is the only
supported version. Security fixes will not be backported to older
minors until v1.0.

| Version | Supported |
|---|---|
| 0.1.x | ✅ |
| < 0.1.0 | ❌ (bootstrap only) |

## What's in scope

- Code execution risks in `tool/check_boundaries.py`, `scripts/verify.*`
- Path traversal or unintended writes in `/cadence-init` scaffolding
- Sensitive-data leakage in retrospective output or compliance reports
- Privilege escalation via subagent definitions

## What's out of scope

- Issues in user-supplied `cadence.yaml` (those are user configuration)
- Issues in third-party tools invoked by `cadence.yaml` commands
  (eslint, ruff, pytest, etc.)
- The case-study Flutter sandbox (separate repository, separate policy)

## Disclosure

After a fix is released, we'll publish a security advisory via GitHub
Security Advisories with the standard CVSS scoring and patch
information.

---
name: cadence-compliance
description: Generate a compliance report mapping the project's Cadence artifacts to industry standards (NIST SSDF v1.1 or ISO/IEC 25010:2023). Use when the user runs /cadence-compliance, asks for a compliance report, asks how the project aligns with NIST SSDF, or asks for an ISO 25010 coverage roll-up.
argument-hint: "--standard <ssdf | iso25010> [--format md|json|sarif]"
---

# /cadence-compliance

You are generating a compliance report mapping Cadence artifacts to an
industry standard.

## Inputs

- Required: `--standard <ssdf | iso25010>`
- Optional: `--format <md | json | sarif>` (default `md`)
- Optional: `--output <path>` (default `.cadence/reports/<standard>-<date>.<ext>`)

## Step 1 — Load the standard mapping

Read the plugin's mapping YAML:

- `standards/nist-ssdf-1.1.yaml` for `--standard ssdf`
- `standards/iso-25010-2023.yaml` for `--standard iso25010`

Each maps control/practice/characteristic IDs to artifact path globs
in the user's repo (e.g., `docs/PATTERNS.md#security`,
`docs/DEFINITION_OF_DONE.md`, `standards/nist-ssdf-1.1.yaml`).

## Step 2 — Resolve artifacts

For each entry in the mapping:

1. **Glob** the artifact path against the user's repo
2. **Existence check**: does at least one match exist?
3. **Verification check**: if there's a way to confirm the artifact
   actually fires its gate (e.g., the last `scripts/verify` run
   succeeded), record that as evidence

Status assignment:
- **Implemented** — artifact present AND verify confirms the gate fires
- **Partial** — artifact present, no verify evidence
- **Gap** — no artifact found
- **N/A** — explicitly marked in `.cadence/cadence.yaml` `compliance.exclude:`

## Step 3 — For SSDF, also scan ADR frontmatter

Look for `ssdf:` tags in:
- ADR frontmatter (e.g., `<!-- ssdf: [PW.7] -->`)
- DoD checkbox HTML comments (e.g., `<!-- ssdf: [PW.7] -->`)

Aggregate these per practice ID to enrich the mapping.

## Step 4 — For ISO 25010, scan iso25010 tags

Same as Step 3 but for `iso25010:` tags. Roll up coverage per
characteristic:

- functional-suitability
- performance-efficiency
- compatibility
- interaction-capability
- reliability
- security
- maintainability
- flexibility
- safety *(new in 2023)*

Print a coverage table per characteristic with the count of tagged
artifacts.

## Step 5 — Render the report

### Markdown (`--format md`, default)

```markdown
# NIST SSDF v1.1 — Compliance Report
Project: <project-name>
Generated: <YYYY-MM-DD HH:MM>
Cadence version: <version>
Last verify SHA: <commit-sha or "unknown">

## Summary

| Status | Count |
|---|---|
| Implemented | 7 |
| Partial | 3 |
| Gap | 2 |
| N/A | 0 |

## Per-practice detail

### PO.1 — Define security requirements
Status: **Implemented**
Artifacts:
- `docs/PATTERNS.md#security`
- `docs/ADR/0008-secret-handling.md` (tagged `ssdf: [PO.1]`)

### PO.4 — Implement supporting toolchains
Status: **Implemented**
Artifacts:
- `.cadence/cadence.yaml`
- `scripts/verify.sh`

### PW.7 — Review and analyze code
...
```

### JSON (`--format json`)

Emit a structured object suitable for GRC tool ingestion:

```json
{
  "standard": "nist-ssdf",
  "version": "1.1",
  "generated_at": "2026-05-21T10:30:00Z",
  "project": "myapp",
  "cadence_version": "0.1.0",
  "summary": { "implemented": 7, "partial": 3, "gap": 2, "na": 0 },
  "practices": [
    { "id": "PO.1", "status": "implemented", "artifacts": [...] },
    ...
  ]
}
```

### SARIF (`--format sarif`)

Emit a SARIF 2.1.0 run with `tool.driver.name = "cadence"` and one
result per Gap, mapping the practice ID to a `ruleId`. Suitable for
GitHub Advanced Security ingestion.

## Step 6 — Save

Write the report to the resolved output path. Print the path to the
user.

## Step 7 — Honest framing

Append a footer:

> Cadence's compliance mapping is **opinionated, not authoritative**.
> Practice IDs map to artifacts that *support* compliance; they do not
> constitute formal certification. For SSDF self-attestation per
> [OMB M-22-18](https://www.whitehouse.gov/wp-content/uploads/2022/09/M-22-18.pdf),
> consult your organization's compliance team.

## Safety rules

- Never claim Implemented without verify evidence — degrade to Partial
- Never auto-fix gaps; the report identifies them, humans address them
- For SARIF output, follow the spec exactly (validators will reject
  invalid SARIF)
- Don't include secrets or credentials in the report (the report ends
  up shared with stakeholders)

---
name: cadence-compliance
description: Generate a compliance report mapping the project's Cadence artifacts to industry standards (NIST SSDF v1.1, ISO/IEC 25010:2023, SOC 2 TSC stub, ISO 27001:2022 stub). Use when the user runs /cadence-compliance, asks for a compliance report, asks for an audit packet, asks how the project aligns with NIST SSDF, or asks for an ISO 25010 coverage roll-up.
argument-hint: "[--standard <ssdf | iso25010 | soc2 | iso27001 | all>] [--format md|json|html|all|sarif] [--audit-packet]"
---

# /cadence-compliance

You are generating a compliance report mapping Cadence artifacts to one
or more industry standards. For the `--standard all` and
`--audit-packet` flows, the heavy lifting is done by a deterministic
Python script (`tool/compliance_report.py`); your job is to parse the
user's request, invoke the script with the right flags, and surface the
output paths. Determinism matters here — the report ends up in front of
an auditor.

## Inputs

- Optional: `--standard <ssdf | iso25010 | soc2 | iso27001 | all>`
  (default `all`)
- Optional: `--format <md | json | html | all | sarif>` (default
  `html`; `sarif` is single-standard only — see SARIF flow below)
- Optional: `--audit-packet` — bundle the report with ADR index,
  FRAMEWORK_CHANGELOG, PATTERNS, and the last verify log into a single
  folder. Implies `--format all`.
- Optional: `--output <path>` (default
  `.cadence/reports/<YYYY-MM-DD>/`)

## When to use the script vs. the skill flow directly

| Flow | Use |
|---|---|
| `--standard all` (default), `--audit-packet`, multi-standard reports | `tool/compliance_report.py` — deterministic, auditor-grade |
| Single-standard SARIF for GitHub Advanced Security | Skill flow below (Step 5 SARIF) — the script does not emit SARIF in v0.3.0-rc.2 |

## Default flow — invoke the script

```bash
python tool/compliance_report.py \
    --standard all \
    --format html \
    --audit-packet
```

The script:
1. Discovers standard YAMLs under `standards/` or
   `.cadence/standards/` (or, when running from the Cadence repo
   itself, `plugins/cadence/standards/`)
2. Resolves each declared artifact against the project (glob)
3. Assigns status:
   - **implemented** — artifact present AND `.cadence/.last_verify_ok`
     marker is present (verify pipeline succeeded)
   - **partial** — artifact present, no verify evidence
   - **gap** — no artifact found, but the item declares artifacts
   - **planned** — the YAML explicitly marks the item as a stub
     (SOC 2 and ISO 27001 currently)
   - **out-of-scope** — the YAML explicitly marks the item as
     not applicable
   - **n/a** — listed in `.cadence/cadence.yaml`'s
     `compliance.exclude:` array
4. Emits `compliance-report.{md,json,html}` to the output directory
5. With `--audit-packet`, bundles them with ADRs and supporting docs
   into `<output-dir>/audit-packet/` with a `MANIFEST.txt`

After invocation, print the output paths and tell the user what's in
the packet. Do not paraphrase the report contents — the file *is*
the deliverable.

## SARIF flow (single-standard only)

If the user asked for `--format sarif`, fall back to the per-standard
skill flow below. The deterministic script does not emit SARIF in
v0.3.0-rc.2 — that path is reserved for GitHub Advanced Security
ingestion and uses a more constrained schema.

## Step 1 — Load the standard mapping

Read the plugin's mapping YAML:

- `standards/nist-ssdf-1.1.yaml` for `--standard ssdf`
- `standards/iso-25010-2023.yaml` for `--standard iso25010`
- `standards/soc2-2017.yaml` for `--standard soc2` (stub)
- `standards/iso-27001-2022.yaml` for `--standard iso27001` (stub)

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
> constitute formal certification. SOC 2 and ISO 27001 mappings ship
> as stubs (status: planned) — they are roadmap scaffolding, not
> compliance claims. For SSDF self-attestation per
> [OMB M-22-18](https://www.whitehouse.gov/wp-content/uploads/2022/09/M-22-18.pdf),
> or for SOC 2 / ISO 27001 readiness, work with your organization's
> compliance team or a licensed auditor.

## Safety rules

- Never claim Implemented without verify evidence — degrade to Partial
- Never auto-fix gaps; the report identifies them, humans address them
- Never present SOC 2 / ISO 27001 stub coverage as a compliance claim —
  always carry the `planned` status through to the rendered output
- For SARIF output, follow the spec exactly (validators will reject
  invalid SARIF)
- Don't include secrets or credentials in the report (the report ends
  up shared with stakeholders)

---
layout: default
title: NIST SSDF v1.1 mapping
---

# NIST SSDF v1.1 mapping

[NIST SP 800-218](https://csrc.nist.gov/projects/ssdf), the Secure
Software Development Framework v1.1, defines four practice groups:

- **PO** — Prepare the Organization
- **PS** — Protect the Software
- **PW** — Produce Well-Secured Software
- **RV** — Respond to Vulnerabilities

Cadence ships `standards/nist-ssdf-1.1.yaml` (Phase 1) which maps each
practice ID to the Cadence artifact that satisfies (or partially
satisfies) it. The compliance report generator (`/cadence-compliance
--standard ssdf`) walks the map and produces a per-practice status
table.

## Practice → artifact mapping (representative)

> Phase 0 ships the documentation outline below; the actual YAML and
> report generator land in Phase 1.

| Practice | Description | Cadence artifact |
|---|---|---|
| **PO.1** | Define security requirements for development | `docs/PATTERNS.md` security section + ADRs tagged `ssdf: [PO.1]` |
| **PO.4** | Implement supporting toolchains | `cadence.yaml` commands + `scripts/verify.{sh,ps1}` |
| **PS.1** | Protect all forms of code from unauthorized access | Git access controls (out of scope), Cadence boundary rules (in scope) |
| **PS.2** | Provide a mechanism for verifying software release integrity | SLSA provenance (Phase 2) |
| **PW.4** | Reuse existing, well-secured software when feasible | ADRs documenting dependency choices |
| **PW.7** | Review and analyze human-readable code | Code review by `cadence-reviewer` subagent against `DEFINITION_OF_DONE.md` |
| **PW.8** | Test executable code | `tests/` + `commands.test` in `cadence.yaml` |
| **RV.1** | Identify and confirm vulnerabilities on an ongoing basis | Dependabot config + lint rules in CI |
| **RV.2** | Assess, prioritize, and remediate vulnerabilities | Retrospective protocol's framework-finding loop |

## Compliance report shape (Phase 1)

`/cadence-compliance --standard ssdf` produces:

- A Markdown summary table (practice ID → status: Implemented / Partial
  / Gap / N/A)
- A JSON sidecar suitable for ingestion by GRC tools
- A SARIF export for GitHub Advanced Security integration

The report cites the artifact path that satisfies each practice and the
last commit SHA where the verify pipeline confirmed the gate fires.

## Limitations

Cadence's NIST SSDF mapping is **opinionated**, not authoritative.
Practice IDs map to documentation and process artifacts that *support*
compliance; they do not constitute formal certification. For formal
SSDF self-attestation (per [OMB M-22-18](https://www.whitehouse.gov/wp-content/uploads/2022/09/M-22-18.pdf)),
consult your organization's compliance team.

## Versioning

The mapping pins to SSDF v1.1 explicitly. SSDF v1.2 is in draft as of
mid-2026; Cadence will ship a v1.2 mapping under
`standards/nist-ssdf-1.2.yaml` after the draft is finalized.

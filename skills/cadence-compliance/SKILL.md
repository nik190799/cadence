---
name: cadence-compliance
description: Generate a compliance report mapping the project's Cadence artifacts to industry standards (NIST SSDF v1.1 or ISO/IEC 25010:2023). Use when the user runs /cadence-compliance, asks for a compliance report, asks how the project aligns with NIST SSDF, or asks for an ISO 25010 coverage roll-up.
argument-hint: "--standard <ssdf | iso25010> [--format md|json|sarif]"
---

# /cadence-compliance

> ⚠️ **Stub — Phase 1.**

## Intended behavior

When invoked as `/cadence-compliance --standard ssdf`:

1. **Load `standards/nist-ssdf-1.1.yaml`** from the plugin
2. **Walk each practice (PO, PS, PW, RV)** and identify which Cadence
   artifacts in the user's repo claim to satisfy it (via frontmatter
   tags or explicit mappings)
3. **Determine status** for each practice:
   - **Implemented** — at least one artifact present and verify
     pipeline confirms its gate fires
   - **Partial** — artifact present but no verification evidence
   - **Gap** — no artifact claims this practice
   - **N/A** — explicitly marked not applicable in `cadence.yaml`
4. **Produce a report** in the requested format:
   - **`md`** (default) — Markdown table, drillable, paste-into-vendor-questionnaire ready
   - **`json`** — sidecar for GRC tool ingestion
   - **`sarif`** — GitHub Advanced Security integration

When invoked as `/cadence-compliance --standard iso25010`:

1. **Load `standards/iso-25010-2023.yaml`**
2. **Scan `docs/DEFINITION_OF_DONE.md`** for `iso25010:` frontmatter tags
3. **Roll up coverage** per quality characteristic (functional
   suitability, performance efficiency, compatibility, interaction
   capability, reliability, security, maintainability, flexibility,
   safety)
4. **Identify blind spots** — characteristics with zero tagged artifacts
5. **Produce report**

## Output location

Reports are written to `.cadence/reports/<standard>-<YYYY-MM-DD>.<ext>`
and printed to the chat for immediate review.

See: [docs/standards/nist-ssdf.md](../../docs/standards/nist-ssdf.md),
     [docs/standards/iso-25010.md](../../docs/standards/iso-25010.md)

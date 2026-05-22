# Standards mappings

YAML files mapping Cadence artifacts to industry-standard control IDs.
Used by `/cadence-compliance --standard <X>` to generate reports.

| File | Standard | Doc |
|---|---|---|
| `nist-ssdf-1.1.yaml` | NIST SP 800-218 (Secure Software Development Framework) v1.1 | [docs/standards/nist-ssdf.md](../docs/standards/nist-ssdf.md) |
| `iso-25010-2023.yaml` | ISO/IEC 25010:2023 (Software Quality Model) | [docs/standards/iso-25010.md](../docs/standards/iso-25010.md) |

## Status

Stub YAMLs in v0.0.1 (Phase 0). Full mappings + compliance report
generator in Phase 1.

## Adding a standard

To add a new standard mapping:

1. Create `standards/<standard-id>.yaml` with the practice/control IDs
   and an artifact path for each
2. Add `docs/standards/<standard-id>.md` walkthrough
3. Update the `cadence-compliance` skill to accept the new standard flag
4. Open a PR

Roadmap for future mappings: SLSA v1.0 (supply-chain), OWASP ASVS 4.0
(security verification), IEC 62304 (medical software).

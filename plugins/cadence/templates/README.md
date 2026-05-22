# Templates

Files in this directory are **scaffolded into the user's repo** by
`/cadence-init`. Each `.tmpl` extension indicates a file with
substitutable placeholders (e.g., `{{project_name}}`, `{{stack}}`).

## Layout

```
templates/
  docs/
    PATTERNS.md.tmpl              # stack-agnostic patterns doc
    DEFINITION_OF_DONE.md.tmpl    # the DoD checklist
    ROLE_SPECS.md.tmpl            # 4 subagent role definitions
    TEAM_PROTOCOL.md.tmpl         # coordination phrases + rules
    RETROSPECTIVE_PROTOCOL.md.tmpl # the 4-layer fix mapping
    TEAM_LAUNCH_TEMPLATE.md.tmpl  # feature launch prompt
    FRAMEWORK_CHANGELOG.md.tmpl   # seeded with v0.0.1 entry
    ADR/
      README.md.tmpl              # ADR index
      0000-template.md.tmpl       # Nygard ADR template
  scripts/
    verify.sh                     # POSIX orchestrator
    verify.ps1                    # PowerShell orchestrator
  tool/
    check_boundaries.py           # language-agnostic boundary checker
  .github/
    workflows/
      cadence.yml.tmpl            # CI workflow for user's repo
  CLAUDE.md.tmpl                  # patches user's CLAUDE.md with pointers
```

## Status

Stub files only in v0.0.1 (Phase 0). Real content extracted from the
Flutter sandbox case study and generalized in Phase 1.

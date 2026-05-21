# Cadence verify orchestrator (PowerShell).
#
# Stub - Phase 1. Phase 1 implementation reads .cadence/cadence.yaml
# and runs format -> lint -> boundary check -> test with fail-fast.

$ErrorActionPreference = 'Stop'

Write-Host "Cadence verify (Phase 0 stub)"
Write-Host "Phase 1 will:"
Write-Host "  1. parse .cadence/cadence.yaml"
Write-Host "  2. run `$commands.format"
Write-Host "  3. run `$commands.lint"
Write-Host "  4. run tool/check_boundaries.py"
Write-Host "  5. run `$commands.test"
Write-Host ""
Write-Host "Until Phase 1, run your stack's tooling manually."
exit 0

# Cadence verify orchestrator (PowerShell).
#
# Reads .cadence/cadence.yaml and runs:
#   1. commands.format    (each entry as a separate command)
#   2. commands.lint
#   3. tool/check_boundaries.py
#   4. commands.test
#
# Fails fast on first non-zero exit. Prints a Definition-of-Done-shaped
# summary at the end.
#
# Usage:  pwsh -File scripts/verify.ps1
# Exit:   0 = all green; non-zero = first failing step's exit code.

$ErrorActionPreference = 'Stop'
$start = Get-Date

$Config = if ($env:CADENCE_CONFIG) { $env:CADENCE_CONFIG } else { '.cadence/cadence.yaml' }
$Root   = if ($env:CADENCE_ROOT)   { $env:CADENCE_ROOT }   else { (Get-Location).Path }
$ConfigPath = Join-Path $Root $Config

if (-not (Test-Path $ConfigPath)) {
    Write-Host "FAIL: cadence config not found at $Config" -ForegroundColor Red
    Write-Host 'Hint: run /cadence-init to scaffold one.'
    exit 2
}

$Python = (Get-Command python -ErrorAction SilentlyContinue) ?? (Get-Command python3 -ErrorAction SilentlyContinue)
if (-not $Python) {
    Write-Host 'FAIL: python is required (install Python 3.10+)' -ForegroundColor Red
    exit 2
}

function Read-Commands {
    param([string] $Section)

    $script = @"
import sys
try:
    import yaml
except ImportError:
    print('__MISSING_PYYAML__')
    sys.exit(0)
path, section = sys.argv[1], sys.argv[2]
with open(path, 'r', encoding='utf-8') as fh:
    cfg = yaml.safe_load(fh) or {}
cmds = (cfg.get('commands') or {}).get(section) or []
if isinstance(cmds, str):
    cmds = [cmds]
for c in cmds:
    print(c)
"@

    $output = & $Python.Source -c $script $ConfigPath $Section 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAIL: reading commands.$Section from $Config" -ForegroundColor Red
        $output | ForEach-Object { Write-Host $_ }
        exit 2
    }
    return $output | Where-Object { $_ -ne '' }
}

function Invoke-Step {
    param(
        [string] $Label,
        [string[]] $Commands
    )

    Write-Host ''
    Write-Host "==> $Label" -ForegroundColor Cyan

    if (-not $Commands -or $Commands.Count -eq 0) {
        Write-Host "(no commands configured for $Label; skipping)" -ForegroundColor DarkGray
        return
    }

    foreach ($cmd in $Commands) {
        if ($cmd -eq '__MISSING_PYYAML__') {
            Write-Host 'FAIL: PyYAML not installed (pip install pyyaml)' -ForegroundColor Red
            exit 2
        }
        Write-Host "`$ $cmd" -ForegroundColor DarkGray
        cmd /c $cmd
        $rc = $LASTEXITCODE
        if ($rc -ne 0) {
            Write-Host "FAIL: $Label (exit $rc)" -ForegroundColor Red
            exit $rc
        }
    }
}

Set-Location $Root

Invoke-Step 'format' (Read-Commands 'format')
Invoke-Step 'lint'   (Read-Commands 'lint')

# Boundary check is built-in.
if (Test-Path (Join-Path $Root 'tool/check_boundaries.py')) {
    Write-Host ''
    Write-Host '==> boundaries' -ForegroundColor Cyan
    Write-Host '$ python tool/check_boundaries.py' -ForegroundColor DarkGray
    & $Python.Source 'tool/check_boundaries.py' '--config' $Config
    $rc = $LASTEXITCODE
    if ($rc -ne 0) {
        Write-Host "FAIL: boundaries (exit $rc)" -ForegroundColor Red
        exit $rc
    }
} else {
    Write-Host ''
    Write-Host '(tool/check_boundaries.py not found; skipping boundary check)' -ForegroundColor DarkGray
}

Invoke-Step 'test' (Read-Commands 'test')

$elapsed = (Get-Date) - $start
Write-Host ''
Write-Host ("OK ({0:N1}s)" -f $elapsed.TotalSeconds) -ForegroundColor Green
Write-Host ''
Write-Host 'Definition of Done (mechanical, auto-enforced):'
Write-Host '  format'
Write-Host '  lint'
Write-Host '  boundaries'
Write-Host '  test'
Write-Host ''
Write-Host 'Manual DoD lines (Reviewer responsibility) - see docs/DEFINITION_OF_DONE.md.'

---
name: cadence-verify
description: Run the Cadence verify pipeline (format / lint / boundary check / test) using the project's cadence.yaml configuration, then render the output as a Definition-of-Done aligned checklist. Use when the user runs /cadence-verify, asks to verify the project, or asks whether work is done.
---

# /cadence-verify

You are running the Cadence verify pipeline and presenting the result
as a Definition-of-Done aligned checklist.

## Step 1 — Locate the runner

Look for `scripts/verify.sh` (POSIX) or `scripts/verify.ps1` (Windows
PowerShell). If neither exists, tell the user to run `/cadence-init`
first.

Detect platform:
- Windows: prefer `pwsh -File scripts/verify.ps1`
- POSIX:   prefer `bash scripts/verify.sh`
- If `pwsh` isn't available on Windows, fall back to `powershell -File`
- If Python is available but Bash isn't (Windows without WSL), the
  verify script's Python calls still work

## Step 2 — Run

Invoke the verify script. Capture stdout + stderr + exit code.

```bash
bash scripts/verify.sh
```

The verify script orchestrates (in order):
1. format check (from `.cadence/cadence.yaml` `commands.format`)
2. lint (from `commands.lint`)
3. import boundary check (`tool/check_boundaries.py`)
4. tests (from `commands.test`)

Fail-fast: first non-zero exit stops the pipeline.

## Step 3 — Render the checklist

Read `docs/DEFINITION_OF_DONE.md`. For each (auto) line, mark its
status based on the verify output:

```
Definition of Done — auto-enforced lines:
  ✓ Format check          (clean)
  ✓ Lint                  (0 issues)
  ✗ Import boundaries     (2 violations — see below)
  – Tests                 (not reached; earlier step failed)

Manual lines (Reviewer responsibility):
  ? Files placed per PATTERNS.md §1
  ? Controller uses correct shape per §2
  ? ...

Open the failures above before claiming "done". The verify script's
output below has the specifics.
```

For each FAILING (auto) line, include the relevant lines of script
output verbatim under the checklist.

## Step 4 — Cite rules on violations

When a boundary check fails, the output already includes the violation
context + the rule's reason from `cadence.yaml`. Don't add extra
commentary — the rule statement is the citation.

When a lint fails, the linter's own output usually carries the rule
name. Don't paraphrase.

## Step 5 — Don't suggest fixes (Reviewer's job)

`/cadence-verify` reports status, it doesn't propose fixes. Fixes come
from `/cadence-retro` or directly from the user.

## Safety rules

- Never run verify on a directory that isn't a Cadence-initialized
  project (no `.cadence/cadence.yaml`)
- Don't auto-fix lint failures (the user's tooling may have a `--fix`
  flag they can run themselves)
- Surface secrets safely — if verify output contains a token or
  credential, redact it before showing

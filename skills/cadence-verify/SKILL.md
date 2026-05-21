---
name: cadence-verify
description: Run the Cadence verify pipeline (format / lint / boundary check / test) using the project's cadence.yaml configuration, then render the output as a Definition-of-Done aligned checklist. Use when the user runs /cadence-verify, asks to verify the project, or asks whether work is done.
---

# /cadence-verify

> ⚠️ **Stub — Phase 1.**

## Intended behavior

1. **Read `.cadence/cadence.yaml`** to get the project's commands
2. **Run `scripts/verify`** (the orchestrator script)
3. **Parse output** — for each step:
   - format check
   - lint
   - boundary check (`tool/check_boundaries.py`)
   - test
4. **Render as a Definition-of-Done checklist** matching the structure
   in `docs/DEFINITION_OF_DONE.md`:

   ```
   Mechanical (auto-enforced):
   ✓ Format check
   ✓ Lint
   ✗ Import boundaries (2 violations) — see below
   ✓ Tests (32 passing)

   Architecture:
   ? Files placed per PATTERNS.md §1 (Reviewer must verify)
   ? Controller uses right Riverpod shape (Reviewer must verify)
   ...
   ```

5. **For any failing step**, surface the specific violation with the
   relevant ADR or PATTERNS section reference

## When to call vs `/cadence-retro`

- `/cadence-verify` runs ONCE and reports status
- `/cadence-retro` is called AFTER verify passes and the team has
  completed work; it captures lessons from the run, not the verify
  output itself

See: [docs/architecture.md](../../docs/architecture.md) — Layer 3 section

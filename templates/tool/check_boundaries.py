#!/usr/bin/env python3
"""
Cadence import-boundary checker — language-agnostic.

⚠️ Stub — Phase 1. Phase 1 implementation reads .cadence/cadence.yaml
boundaries and scans the project tree for forbidden imports.

Phase 1 behavior (target):
- Read .cadence/cadence.yaml
- For each `boundaries:` rule, glob the `where:` pattern and scan each
  file for `import`/`require`/`include`/`use`/`from` lines containing
  any `forbidden:` substring
- Print violations with file:line + the rule's reason
- Exit 0 on clean, 1 on any violation

Until Phase 1, this is a no-op.
"""

import sys

def main() -> int:
    print("check_boundaries.py (Phase 0 stub) — no-op")
    print("Phase 1 will read .cadence/cadence.yaml and enforce boundary rules.")
    return 0

if __name__ == "__main__":
    sys.exit(main())

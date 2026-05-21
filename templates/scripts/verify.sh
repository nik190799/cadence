#!/usr/bin/env bash
# Cadence verify orchestrator (POSIX).
#
# ⚠️ Stub — Phase 1. Phase 1 implementation reads .cadence/cadence.yaml
# and runs format → lint → boundary check → test with fail-fast.

set -euo pipefail

echo "Cadence verify (Phase 0 stub)"
echo "Phase 1 will:"
echo "  1. parse .cadence/cadence.yaml"
echo "  2. run \$commands.format"
echo "  3. run \$commands.lint"
echo "  4. run tool/check_boundaries.py"
echo "  5. run \$commands.test"
echo
echo "Until Phase 1, run your stack's tooling manually."
exit 0

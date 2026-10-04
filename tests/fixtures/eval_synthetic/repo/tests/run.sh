#!/usr/bin/env bash
# Runs every tests/test_*.sh from the repo root.
set -e
for t in tests/test_*.sh; do
  bash "$t"
done
echo "all tests passed"

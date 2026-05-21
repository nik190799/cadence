# Tests

Tests for Cadence's own code (the boundary checker, the verify script,
the schemas, etc.) and fixtures for cross-stack validation.

## Layout (Phase 1)

```
tests/
  README.md
  test_check_boundaries.py     # unit tests for tool/check_boundaries.py
  test_cadence_yaml_schema.py  # validates recipes/*.yaml against schema
  test_compliance_report.py    # tests SSDF/ISO 25010 report generator
  fixtures/
    ts-sample/                 # synthetic TS project with known violations
    python-sample/             # synthetic Python project
    go-sample/                 # synthetic Go project
    dart-sample/               # synthetic Dart project
    java-sample/               # synthetic Java project
    rust-sample/               # synthetic Rust project
```

Each fixture contains a deliberately-broken import (e.g., a `features/`
file importing from `data/sources/`) so the boundary checker can be
tested against real-shaped source trees.

## Status

Stub only in v0.0.1 (Phase 0). Test implementation arrives in Phase 1
alongside `tool/check_boundaries.py`.

---
layout: default
title: Rust recipe
---

# Recipe — Rust

A starter `cadence.yaml` for a typical Rust project.

```yaml
commands:
  format: ["cargo fmt --check"]
  lint:   ["cargo clippy -- -D warnings"]
  test:   ["cargo test"]

boundaries:
  - where: "src/features/**"
    forbidden:
      - "src/data/sources/**"
      - "src/data/repositories/**"
    reason: "Features must read via narrow providers in src/app"

  - where: "src/data/sources/**"
    forbidden:
      - "src/features/**"
      - "src/app/**"
    reason: "Data sources are leaf-level"
```

## Notes

- The boundary checker recognizes `use X::Y::Z`, `mod X`, and `crate::`
  references
- For workspaces, each crate gets its own `cadence.yaml`, or use a
  single root config with multi-crate globs
- Clippy's `-D warnings` promotes warnings to errors — Cadence aligns
  with this strictness

## Suggested first ADRs

1. **Error type strategy** (`Result<T, MyError>` / `anyhow` / `thiserror`)
2. **Async runtime** (Tokio / async-std / smol)
3. **Trait-based polymorphism vs enum dispatch**

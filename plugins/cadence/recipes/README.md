# Recipes

Per-stack `cadence.yaml` examples that `/cadence-init` uses to prefill
new projects.

| File | Stack |
|---|---|
| `typescript.yaml` | TypeScript / Node / React / Next |
| `python.yaml` | Python (Poetry, pip, or uv) |
| `go.yaml` | Go |
| `rust.yaml` | Rust (Cargo) |
| `java.yaml` | Java (Maven or Gradle) |
| `dart-flutter.yaml` | Dart / Flutter |

See [docs/recipes/](../docs/recipes/) for full walkthroughs of each.

## Adding a recipe

To add a new stack:

1. Create `recipes/<stack>.yaml` modeled on the existing examples
2. Add `docs/recipes/<stack>.md` walkthrough
3. Add a fixture under `tests/fixtures/<stack>-sample/` for the
   boundary checker to validate against
4. Open a PR

## Status

Stub YAMLs in v0.0.1 (Phase 0). Full recipes from `docs/recipes/`
ported in Phase 1.

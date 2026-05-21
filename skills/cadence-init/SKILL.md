---
name: cadence-init
description: Scaffold the Cadence framework into the current repository. Use when the user runs /cadence-init or asks to set up the Cadence framework, install patterns, bootstrap docs/ADRs/verify scripts, or initialize the four-layer development framework in a repo.
argument-hint: "[stack-hint: typescript | python | go | rust | java | dart-flutter | auto]"
---

# /cadence-init

> ⚠️ **Stub — Phase 1.** This skill's full behavior is implemented in
> Phase 1. Currently it documents intended behavior only.

## Intended behavior

When the user runs `/cadence-init`, this skill should:

1. **Detect the project's stack** from common markers (or use the
   provided hint):
   - `package.json` → typescript / javascript
   - `pyproject.toml`, `requirements.txt`, `setup.py` → python
   - `go.mod` → go
   - `Cargo.toml` → rust
   - `pubspec.yaml` → dart-flutter
   - `pom.xml`, `build.gradle`, `build.gradle.kts` → java
   - Unknown → ask the user, default to "generic"

2. **Write `.cadence/cadence.yaml`** prefilled with the stack's
   default commands and a starter `boundaries:` block (empty in v1;
   user fills in based on their architecture).

3. **Copy `templates/docs/`** into the user's repo at `docs/`,
   substituting any `{{stack}}` placeholders.

4. **Copy `templates/scripts/verify.{sh,ps1}`** into the user's
   `scripts/` directory.

5. **Copy `templates/tool/check_boundaries.py`** into the user's
   `tool/` directory.

6. **Copy `templates/.github/workflows/cadence.yml.tmpl`** into the
   user's `.github/workflows/cadence.yml`.

7. **Patch `CLAUDE.md`** (creating it if absent) with a pointer block:

   ```markdown
   ## Cadence framework

   This repository uses the Cadence framework. Before any work:
   - Read `docs/PATTERNS.md` for codebase conventions
   - Read `docs/DEFINITION_OF_DONE.md` for the done checklist
   - Run `scripts/verify` before declaring work done
   - For new judgment calls, follow `docs/ADR/0000-template.md`
   ```

8. **Print a "next steps" summary** with `/cadence-launch` and
   `/cadence-verify` suggestions.

## Upgrade mode

When invoked as `/cadence-init --upgrade`:

- Read existing `.cadence/cadence.yaml`
- Diff the user's `docs/` (project overrides) against `templates/docs/`
  (latest core)
- Present a three-way diff (old core / new core / user overrides) for
  each changed file, prompting the user row-by-row before writing
- Never silently overwrite project-overrides

See: [docs/self-improvement-loop.md](../../docs/self-improvement-loop.md)

---
name: cadence-init
description: Scaffold the Cadence framework into the current repository. Use when the user runs /cadence-init or asks to set up the Cadence framework, install patterns, bootstrap docs/ADRs/verify scripts, or initialize the four-layer development framework in a repo.
argument-hint: "[stack-hint: typescript | python | go | rust | java | dart-flutter | auto] [--upgrade]"
---

# /cadence-init

You are scaffolding the Cadence framework into the user's project. Be
methodical, never destructive, and confirm before overwriting any
file the user might have customized.

## Inputs

- Optional positional argument: stack hint (one of `typescript`,
  `python`, `go`, `rust`, `java`, `dart-flutter`, `auto`). Default
  `auto`.
- Optional flag: `--upgrade` (re-runs against an existing Cadence
  install; uses three-way diff instead of straight copy).

## Step 1 — Detect stack

Look at the current working directory for these markers (in order):

| Marker | Stack |
|---|---|
| `pubspec.yaml` | `dart-flutter` |
| `Cargo.toml` | `rust` |
| `go.mod` | `go` |
| `pyproject.toml`, `requirements.txt`, `setup.py`, `Pipfile` | `python` |
| `pom.xml`, `build.gradle`, `build.gradle.kts` | `java` |
| `package.json` | `typescript` (assume TS unless only JS files exist) |

If `auto` is the hint and no marker matches, ask the user to pick.

## Step 2 — Generate `.cadence/cadence.yaml`

Read the matching recipe from the plugin's `recipes/<stack>.yaml`. Copy
its content to `.cadence/cadence.yaml`, replacing any placeholders
(e.g., `{{pkg}}` in the python recipe) by prompting the user.

If the file already exists and we're NOT in `--upgrade` mode, refuse
and tell the user to pass `--upgrade`.

## Step 3 — Copy templates

For each file in the plugin's `templates/` tree, copy to the
corresponding path in the user's repo:

- `templates/docs/*.md.tmpl` → `docs/*.md` (strip the `.tmpl` extension)
- `templates/docs/ADR/*.md.tmpl` → `docs/ADR/*.md`
- `templates/scripts/verify.sh` → `scripts/verify.sh` (+ make executable)
- `templates/scripts/verify.ps1` → `scripts/verify.ps1`
- `templates/tool/check_boundaries.py` → `tool/check_boundaries.py`
- `templates/tool/emit_rule.py` → `tool/emit_rule.py`
- `templates/tool/compliance_report.py` → `tool/compliance_report.py`
- `schemas/retro.schema.json` → `.cadence/retro.schema.json` (so the
  emitter can validate locally without reaching into the plugin tree)
- `standards/*.yaml` → `standards/` (so /cadence-compliance can find
  the mappings locally; the user can edit them per-project)
- `templates/.github/workflows/cadence.yml.tmpl` → `.github/workflows/cadence.yml`
- `templates/CLAUDE.md.tmpl` → append to existing `CLAUDE.md` (or
  create new); preserve any user content above the Cadence section.

For each `.tmpl` file, substitute placeholders:
- `{{project_name}}` → directory name of the project
- `{{init_date}}` → today's date (YYYY-MM-DD)
- `{{cadence_version}}` → plugin version from `.claude-plugin/plugin.json`
- `{{src_root}}`, `{{ext}}`, `{{lang}}` → stack-appropriate values
  (see table below)

| Stack | src_root | ext | lang |
|---|---|---|---|
| typescript | `src` | `ts` | `typescript` |
| python | `src/<pkg>` | `py` | `python` |
| go | `internal` | `go` | `go` |
| rust | `src` | `rs` | `rust` |
| java | `src/main/java/<pkg>` | `java` | `java` |
| dart-flutter | `lib` | `dart` | `dart` |

## Step 4 — Pre-flight (do NOT skip)

Before writing any file:
1. Run `git status --porcelain` to confirm the user has a clean working
   tree (or warn them and ask to proceed)
2. List every file that will be created or modified
3. Ask the user "Proceed?" unless `--non-interactive` is set

## Step 5 — `--upgrade` mode

If `--upgrade` was passed:
1. For each `templates/<file>` that already exists in the user's repo:
   - Compute three diffs: old-core vs new-core, new-core vs user's-current
   - Present the diff and ask: accept (overwrite), keep-mine (skip),
     merge (open in editor)
2. Never silently overwrite project overrides
3. The `.tmpl` placeholders are re-substituted with current values

## Step 6 — Print next steps

After successful scaffold, print:

```
Cadence v<version> installed.

Files created:
  docs/ (10 files)
  scripts/verify.{sh,ps1}
  tool/check_boundaries.py
  tool/emit_rule.py
  tool/compliance_report.py
  standards/ (NIST SSDF, ISO 25010, SOC 2 stub, ISO 27001 stub)
  .cadence/cadence.yaml
  .cadence/retro.schema.json
  .github/workflows/cadence.yml
  CLAUDE.md (patched)

Next steps:
  1. Review docs/PATTERNS.md and adjust §1 layer rules for your project.
  2. Edit .cadence/cadence.yaml to confirm commands.{format,lint,test}.
  3. Run /cadence-verify to baseline your current code.
  4. Use /cadence-launch <feature> to spawn a Cadence team.
  5. After each team run, /cadence-retro to grow the framework.
```

## Safety rules

- NEVER overwrite a file without explicit user approval (or `--upgrade`
  with three-way diff approval)
- NEVER touch `.git/`, `node_modules/`, `.venv/`, or other generated dirs
- If anything is ambiguous, ask before acting

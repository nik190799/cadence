# Schemas

JSON Schemas for the structured data Cadence produces and consumes.

| File | Purpose |
|---|---|
| `cadence-yaml.schema.json` | Validates user-authored `.cadence/cadence.yaml` |
| `retro.schema.json` | Validates the JSON form of a retrospective entry |
| `observation.schema.json` | One factory build attempt's evidence (learning loop) |
| `classify.schema.json` | The `cadence-findings` skill's labels (learning loop) |
| `lessons.schema.json` | `.cadence/lessons.yaml`, the learned-lesson ladder (learning loop) |
| `retro-plan.schema.json` | A retro plan and what was applied (learning loop) |
| `metrics.schema.json` | The learning metrics report (learning loop) |

All schemas are JSON Schema draft 2020-12.

## Changes for the learning loop

The factory's learning loop ([docs/LEARNING.md](../../../docs/LEARNING.md))
added five schemas and changed two. The tools validate with
`Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)`
and also check in code that every `id` is a UUID and every `ts` or `*_at`
value is an ISO 8601 timestamp, because `format: date-time` is not enforced
without extra packages. A tool finds a schema in `--schema-dir`, else in
`.cadence/<name>`, else here; a factory repo copies the files into
`.cadence/`.

### `retro.schema.json` (changed, backward compatible)

- New optional `factory` object, present only on findings the factory
  records (`tool/signals.py`). It holds the class key (`family:body`,
  e.g. `import-edge:src/domain->src/db`), family, signal (`gate`,
  `guarded`, `detector`, `human-edit`, `review-comment`,
  `reviewer-command`, `pr-outcome`), trust (`A` static, `B` parsed from
  agent-influenced output, `C` a model label code verified), phase,
  whether the gate caught it and whether it reached a PR, the rule id,
  repo, issue, PR, run and commit shas, path and line, comment id with the
  sha256 of the comment (never its text), the classification, and
  `judge: null`, the hook for a later shadow judge. Manual
  `/cadence-retro` findings leave it out and validate as before.
- `violation_sample` is stricter: `where` and `forbidden_pattern` use the
  characters `A-Za-z0-9_.@*-/` and may not start with `/`; `import_line`
  is one line of at most 300 characters.

### `cadence-yaml.schema.json` (changed)

Boundary rules take an optional `id`: `L-xxxxxxxx` for a rule the
factory learned, `B-xxxxxxxx` for a seed rule. `tool/check_boundaries.py`
computes `B-` + 8 hex digits of `sha256(where|forbidden...)` for a rule
without one, and reports the id with each violation.

### `observation.schema.json` (new)

`tool/signals.py observe` writes one per build attempt;
`cadence/state` stores it as `observations/<run_id>-<run_attempt>.json`.
It holds ids, results (`apply_status`, `agent_result`, `verify_result`,
`gate_step`), hashes of the detector code, ruleset and learning config,
and evidence lists capped at 200 entries: changed `files`, added
cross-area `import_edges` (with the line itself only when it is strictly
import-shaped), `guarded` operations, boundary `rule_hits` on added lines,
and `failing_tests` from the verify log. `classes` lists the classes
observe derives itself (guarded, missing-test, test, gate, agent);
import edges become classes only once something seeds them, so the
ladder and metrics recompute them from the evidence. The optional
`lessons_cited` (added 2026-10-02; older observations have none) lists
the active base lessons the approved spec names, `[]` for none and
`null` when unknown. It is informational only.

### `classify.schema.json` (new)

The only output of the read-only `cadence-findings` skill: per review
comment, a `category` from a fixed list, optionally `same_as` (a known
class key) and `edge` (one of the item's own agent-added import lines),
and a `confidence`. No free text; `tool/signals.py apply-classified`
checks every claim before using it.

### `lessons.schema.json` (new)

`.cadence/lessons.yaml` as loaded: the ladder state per class
(`pattern`, `check`, `retired`, `suppressed`), with fixed-template text,
the issues and finding ids it rests on, the check's rule id and fixture,
history, rejections and `pinned`. Written only by `tool/ladder.py apply`
through the retro PR.

### `retro-plan.schema.json` (new)

`plan.json` and `applied.json` from `tool/ladder.py`: the proposed
transitions (with the real failing sample for a check), skipped classes,
the "needs a human" list, the fixture replay and the metrics snapshot.
`plan_sha` identifies a plan, so an unchanged plan never re-opens the
retro PR.

### `metrics.schema.json` (new)

The output of `tool/metrics.py report`: repeat rate and escape rate
(with Wilson intervals once there are enough opportunities), per-family
numbers, post-PR numbers, and the kill-criterion support numbers. The
optional `lessons_cited` block (added 2026-10-02) counts the lessons the
approved specs cited, split into cited and absent and cited and present,
plus the attempts where this is unknown. It is informational: no other
number reads it.

## Status

Stub schemas in v0.0.1 (Phase 0). Full schemas with validation
constraints in Phase 1.

## Usage

CI (`.github/workflows/ci.yml`) validates that every shipped YAML/JSON
conforms to its schema. Users can validate their own
`.cadence/cadence.yaml` with any JSON Schema validator pointed at
`cadence-yaml.schema.json`.

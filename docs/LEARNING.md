---
layout: default
title: Learning loop
---

# Learning loop

> **Status: built 2026-10-02 on the `factory` branch (phase 1b); not yet run
> on GitHub.** Open items are listed under [Open questions](#open-questions). Part of
> [factory mode](FACTORY.md). Background: "How it learns" and "Measurement"
> in the [decision doc](https://claude.ai/code/artifact/2e1da873-1f03-4602-b3ab-7618ce6e2d56).

The factory learns from its own mistakes:

- Every agent attempt is scanned, and every failure becomes a finding.
- Lessons climb **note → pattern → check**.
- A check lands only after `tool/emit_rule.py` shows it fires on the real
  failing line and on nothing in `main`.
- Every change to `.cadence/` arrives as one rolling pull request that a
  human merges.

The same records produce the numbers the 2026-11-13 gate needs, with
learning on versus learning frozen:

- the **repeat rate**: how often a mistake class comes back;
- the **escape rate**: how often a repeat reaches a pull request.

Three rules shape the design:

1. **Keep the instrument apart from the treatment.** The state branch stores
   evidence (import edges, guarded-path operations, rule hits), not verdicts.
   Metrics recompute classes from that evidence with one detector set. Both
   eval arms are therefore scored the same way, and a rule learned later
   also scores earlier attempts.
2. **Deterministic first.** Class keys, promotions, rule text and retirements
   come from code, using validated keys, paths and numbers. No model sits on
   the critical path. The optional comment classifier can only pick enum
   values or point at lines that exist.
3. **Untrusted in, nothing executable out.** Agent patches, verify logs and
   PR comments are data. A job that reads them holds no write token. Anything
   it passes on is checked against a schema before a job with a token uses it.

## How a lesson travels

| Step | Job | Tokens | Produces |
|---|---|---|---|
| Build | `agent` | read; Anthropic key | `change.patch` artifact |
| Gate | `verify` | read; no secrets | result and log |
| Scan | `observe` (new) | read; no secrets | observation and findings, as a job output |
| Book | `ledger` | App | `observations/`, `findings/`, `patches/`, `prs/` on `cadence/state` |
| PR closed | `harvest` (new) | read | human-edit and review findings |
| Label (optional, off by default) | `classify` (new) | read; Anthropic key | enum labels only |
| Record | `learn-record` (new) | App | harvest records, learn marker, daily metrics |
| Plan | `retro-plan` (new) | read; no secrets | retro patch, plan, PR body |
| Publish | `retro-publish` (new) | App | branch `cadence/retro` and one PR |
| Decide | a human | none | merge, delete an entry, or close |

In build runs, `observe` sits between `verify` and `ledger`. The learn chain
(`harvest` → `classify` → `learn-record` → `retro-plan` → `retro-publish`) runs
in two cases:

- in the hourly sweep, when `signals.py due` reports new observations or
  closed PRs that have not been harvested;
- on `workflow_dispatch` with `stage=learn`.

No trigger type is added.

## Data model

### Attempts and observations

An **attempt** is one build run attempt whose agent produced a patch that
applies to its base commit. Runs with an empty or broken patch, or a
cancelled agent, count as operations, not attempts. Re-runs with an identical
patch collapse into one: attempts are unique by `(issue, patch_sha256)`.

Each attempt gets one **observation** (`observation.schema.json`). It holds
ids, results and these evidence lists, each capped at 200 entries:

- `files`: the path, operation (A/M/D) and area of each changed file.
- `import_edges`: every import line the patch **added** that crosses areas.
  Each entry has the resolved target, the class key and the location. When
  the line matches the strict import-line pattern, the line itself is kept.
- `guarded`: operations on guarded roots.
- `rule_hits`: live boundary rules (the base `.cadence/cadence.yaml`) that
  fire on added lines.
- `failing_tests`: test files named in the verify log (tier B).

### Class keys

Code builds every key as `family:body`. Identical strings mean the same
class, and no fuzzy matching is used.

| Family | Key | From | Trust | Scope |
|---|---|---|---|---|
| import-edge | `import-edge:<from>-><to>` | an added import; `<to>` is an area or `pkg:<name>` | A | headline |
| guarded | `guarded:<root>:<add\|modify\|delete>` | the patch touches a guarded root (new files under test roots are allowed) | A | headline |
| missing-test | `missing-test:<area>` | source changed in the area, and no test file was touched | A | headline |
| test | `test:<path>` | a failing test named in the verify log that exists in base | B | headline |
| edit | `edit:<test-added\|revert-file\|delete-file\|other>:<area>` | human commits after the agent's | A | post-PR |
| review | `review:<category>:<area\|pr>` | a review comment from a user with write access | A or C | post-PR |
| gate, agent, pr | `gate:<step>`, `agent:<subtype>`, `pr:<merged\|closed-unmerged>` | outcomes | A or B | operations only |

- **Area.** The first `area_depth` (default 2) segments of a file's
  directory: `src/domain/order.ts` → `src/domain`.
- **Import targets.**
  - Relative TS/JS/Dart imports resolve against the importing file.
  - Python dotted names resolve to an existing module directory.
  - Bare specifiers become `pkg:<name>`.
  - Aliases, builtins and other languages give no edge in v1.
- **Seeding.** An import edge counts as a mistake only after something has
  **seeded** it: a rule hit, a human removing that import, or
  `/cadence-forbid`. Importing `src/domain` from `src/http` is normal; only a
  rule or a human can say that importing `src/http` from `src/domain` is
  not. Once seeded, the edge counts in every attempt, earlier ones included.
- **Trust.**
  - A: read statically from the patch or the GitHub API, with no agent code
    run.
  - B: parsed from output the agent can influence (the verify log, the
    result file), using exact patterns and checked against the base tree.
  - C: a model's label on human text. It counts only where code has
    verified the claim.

### Findings

A finding is a `retro.schema.json` entry with a new optional `factory`
object. That object holds:

- class key, family, signal and trust
- `gate_caught` and `reached_pr`
- rule id
- issue, PR, run and commit shas
- path and line
- comment id and its sha256
- classification
- `judge: null`

The required retro fields come from fixed templates. Import-edge findings
carry a `violation_sample`, so `emit_rule.py` can use them as they are.
Manual `/cadence-retro` findings leave `factory` out and validate as before.
The text of comments and edits is never stored, only ids and sha256 hashes.

### Where records live

`cadence/state` is an orphan branch. Only the App writes to it, and every
file is created once and never changed.

| Path | Holds | Written by |
|---|---|---|
| `runs/<run>-<attempt>.json` | ledger record, plus `stage`, `pr`, `published_sha`, `base_sha` | `ledger`, `learn-record` |
| `observations/<run>-<attempt>.json` | the observation | `ledger` |
| `findings/<run>-<attempt>.jsonl` | 0 to 25 findings; the file's existence means "scanned" | `ledger` |
| `patches/<run>-<attempt>.patch` | the agent patch, up to 512 KiB | `ledger` |
| `prs/<pr>-<run>.json` | which attempt a PR published | `ledger` |
| `findings/pr-<pr>-<head12>.jsonl` | post-PR findings | `learn-record` |
| `patches/pr-<pr>-<head12>.patch` | the human delta | `learn-record` |
| `harvest/pr-<pr>.json` | marks the PR as harvested | `learn-record` |
| `decisions/retro-pr-<pr>.json` | what a closed retro PR proposed, and what landed | `learn-record` |
| `retro/plans/<plan_sha>.json` | each published plan | `retro-publish` |
| `learn/<run>-<attempt>.json` | a learn run finished, with the observations it saw | `learn-record` |
| `reports/metrics-<date>.json` | the first metrics snapshot of each UTC day | `learn-record` |

Counts are never stored. The ladder and the metrics recompute them from
these files, so concurrent writers cannot conflict.

On `main`, these change only through the retro PR:

- `.cadence/lessons.yaml`: the ladder state for patterns, checks, and
  retired and suppressed classes (`lessons.schema.json`). Notes never enter
  `.cadence/`.
- `.cadence/cadence.yaml`: learned boundary rules, each with
  `id: L-xxxxxxxx`.
- `docs/PATTERNS.md`: the generated section `## Learned patterns (factory)`.
- `tests/fixtures/retro/<id>/`: the real failing sample, its one-rule config,
  `finding.json` and `provenance.json`.

### Config

`.cadence/factory.yaml` gains a `learning:` block. The retro PR can never
write this file.

```yaml
learning:
  mode: on                 # observe (measure only; the frozen arm) | on | eval-sandbox
  promote_after: 2         # distinct issues
  window_attempts: 50
  window_days: 60
  repeat_window: 10        # N for repeat and escape rate; 0 = all earlier attempts
  area_depth: 2
  max_checks_per_pr: 3
  max_patterns_per_pr: 5
  max_retirements_per_pr: 5
  max_active_patterns: 25
  retire_dormant: false
  guarded_paths: [tests, test, .github, .cadence, scripts, tool]
  test_roots: [tests, test]
  test_globs: ["tests/**", "test/**", "**/*.test.*", "**/*.spec.*", "**/test_*.py", "**/*_test.py", "**/*_test.go"]
  edit_ignore: ["package-lock.json", "yarn.lock", "pnpm-lock.yaml", "**/*.snap"]
  harvest_since_days: 30
  harvest_max_prs: 10
  settle_minutes: 10
  classify: false          # model labels for review comments; always off in eval-sandbox
  model: ""                # pin a model id when classify is on
  budget:
    per_run_usd: 0.25
    daily_usd: 1.00        # also counted inside budget.daily_usd
```

`verify` also reads `guarded_paths` and `test_roots`. This closes the `test/`
gap: the sandbox keeps its tests in `test/`, and edits there were neither
restored nor flagged.

## Signals

### 1. Gate failures

`observe` runs after `verify` in every build that passed the gate. It holds
`contents: read` and no secrets. It applies `change.patch` to a scratch
worktree of the base commit and only reads the result:

- It recomputes import edges and rule hits on **added lines**, using the base
  checker and the base rules. It does this even when verify stopped at lint,
  so occurrences are counted before any gate.
- From the verify log it takes the failing test files and verify's
  `FAIL: <step>` line, matching exact shapes only (tier B).
- The agent's stop reason becomes `agent:<subtype>`.

The observation and findings leave as a job output, so the agent cannot swap
them. `ledger` then:

1. checks the stored patch's sha256 against the one `observe` reported;
2. validates everything against the schemas;
3. writes it to `cadence/state`.

### 2. Guarded paths

`observe` recomputes guarded operations from the staged diff
(`--name-status -z --no-renames`) instead of trusting verify's output.
`verify` still restores guarded paths and flags them. `publish` still pushes
the full patch, so a guarded edit in a published PR counts as an escape.

### 3. Human edits

`harvest` holds read tokens only. It takes closed PRs, merged or not, that:

- are at least `settle_minutes` old;
- come from `cadence/issue-N` in this repo;
- were opened by the factory App;
- have not been harvested yet.

For each PR:

- **The two commits.** P is the agent's published commit (from `prs/`). H is
  the PR head, fetched as objects and never checked out.
- **The human delta.**
  - If P is an ancestor of H: the non-merge, non-bot commits in P..H,
    limited to the paths those commits touched.
  - Otherwise (a rebase): the tree diff P→H on the agent's paths
    (`edit_basis: tree-diff`).
  - Diffs use `-w --no-ext-diff --no-textconv` and skip `edit_ignore`.
- **What becomes a finding.**
  - A removed line that matches one of the published attempt's import
    edges seeds that `import-edge` class. The agent's exact line is the
    `violation_sample`.
  - New test files become `edit:test-added`.
  - Files reverted to base become `edit:revert-file`.
  - Deleted agent files become `edit:delete-file`.
  - Any other change of 3 or more lines becomes `edit:other`.
  - The outcome becomes `pr:<outcome>`.

### 4. Review comments

Review comments, review bodies and conversation comments count only if:

- the author is not a bot;
- the author has write, maintain or admin access (looked up through the
  permission API and cached);
- the comment was posted before the PR closed.

These exact-line commands are deterministic:

- `/cadence-forbid <from_area> -> <to>` must name an edge that the published
  patch added. It seeds that edge.
- `/cadence-class <defect|rule-violation|nit|new-preference|scope-change|question|other>`
  gives `review:<category>:<area>`.

Every other comment becomes `review:unclassified:<area>` and is stored as ids
and hashes.

`classify` runs the read-only `cadence-findings` skill only when both hold:

- `classify: true` is set, which never applies in eval-sandbox;
- the learn budget allows it.

The skill may only:

- choose a category;
- name an existing class key in the same area;
- point at an agent-added import line.

Code checks every one of those claims, and the skill writes no free text.

## The ladder

`tool/ladder.py plan` recomputes everything from `cadence/state` and the
`.cadence/` on `main`.

An **occurrence** is a headline class seen in an attempt inside the window.
The window covers the last `window_attempts` attempts or the last
`window_days` days, whichever holds more.

- Import edges count only after they are seeded.
- Post-PR classes feed only the "needs a human" list.
- Tier C counts only when verified.

**count(k)** is the number of distinct issues with an occurrence, so one
stubborn ticket cannot promote a rule on its own.

| Rung | Lives in | Rule |
|---|---|---|
| note | `cadence/state` only; never loaded by agents | any occurrence |
| pattern | `lessons.yaml`, plus a line in `docs/PATTERNS.md` | count ≥ `promote_after`; a template exists; no emitter fits, or emission failed |
| check | a rule in `cadence.yaml` with its fixture, plus the pattern line "Enforced by check L-…" | import-edge, count ≥ `promote_after`, not covered by a seed rule, and the proof below passes |
| retired | `lessons.yaml` keeps the history; the rule and the line are removed | see Retirement |
| suppressed | `lessons.yaml` | rejected twice; only a human clears it |

### Check proof

The sample is the newest occurrence that has both a stored patch and an
emittable line. An emittable line is TS/JS, Python or Dart, and its target is
an area, not a package. Then all of these must pass:

1. `emit_rule.py` finds the line, verbatim, as an added line at that path and
   line of the stored patch. The patch's sha256 must match the observation.
2. The rule fires on a fixture that holds that line at its real path.
3. The rule finds nothing on `main` (`--must-pass-root`).
4. `scripts/verify.sh` passes on the result.

The rule is `where: <from>/**` and `forbidden: [<to>/**]`, with
`id: L-<8 hex>` and a fixed reason. If any step fails, the class falls back
to a pattern, and the PR body says why.

### Templates

Text comes only from validated keys, paths and issue numbers:

- **Check:** "`<from>/` must not import `<to>/`. Enforced by check L-… (seen in #a, #b)."
- **Import-edge fallback:** "Code under `<from>/` must not import `<to>` (seen in #a, #b)."
- **Guarded:** either "Do not modify or delete existing files under `<root>/`;
  the gate restores them (…)", or "Do not add files under `<root>/`; the gate
  leaves them out (…)".
- **Missing test:** "Changes under `<area>/` must add or update a test (missed in #a, #b)."
- **Test:** "Changes have broken `<path>` (#a, #b); run it before finishing."
  `verify.sh` must also pass on the result, so the test passes on `main`.

Review and edit classes get no text in v1. The PR body lists them under
"Needs a human".

### Caps, rejection and retirement

**Caps.** Each PR carries at most 3 checks, 5 patterns and 5 retirements. At
most 25 patterns are active at once. Candidates are ranked by count, then by
escaped occurrences, then by most recent.

**Rejection.** A rejection is a human closing the retro PR, or deleting an
entry before merging it. `decisions/` records it. That class then needs 2 new
distinct-issue occurrences before it is proposed again. After a second
rejection the PR proposes the `suppressed` rung.

**Retirement.** Retirements are always proposed in the PR, never made
silently. A learned check retires when one of these holds:

- (a) **broken**: `emit_rule.py --replay` shows its fixture no longer fires.
- (b) **blocks-merged-code**: the rule fires on `main`.
- (c) **stale**: the `where` area or the forbidden area no longer exists.
- (d) **human-removed**: someone removed the rule from `cadence.yaml`. This
  case just brings `lessons.yaml` in line.
- (e) **dormant**: 0 hits in the last 100 exposed attempts, and at least 90
  days old. This is only reported, unless `retire_dormant: true`. "Never
  fires" can also mean "works", because agents run `verify.sh` in their own
  session.

Patterns retire in these cases:

- 0 recurrences in the last 40 exposed attempts, and at least 45 days old;
- condition (c) applies;
- retiring one keeps the active count within the cap.

Further rules:

- Seed rules, the ones without an `L-` id, are never edited. If a seed rule
  fires on `main`, the PR body lists it for a human.
- Fixtures stay when their rule retires.
- A class that has been retired and re-promoted twice becomes `pinned` and
  is never retired automatically.
- In eval-sandbox, only (a), (b) and (d) apply.

### The retro PR

`retro-plan` holds no token. It:

1. computes the plan;
2. runs `ladder.py apply`, which calls `emit_rule.py`;
3. runs `scripts/verify.sh` when a check or a `test:` pattern changed;
4. runs `ladder.py guard`;
5. uploads the patch.

`retro-publish` holds the App token. It runs `guard` again, **before**
`git apply`, and after that it runs only git and gh. It force-pushes
`cadence/retro` with a lease, and skips the push if a human has pushed to
that branch. It then opens or updates one non-draft PR. The commit carries
the trailer `Cadence-Retro-Plan: <plan_sha>`. A plan identical to the one in
the open PR changes nothing.

The human's choice maps onto the approve / defer / reject of `/cadence-retro`:

- merging approves;
- deleting an entry rejects that class;
- closing the PR rejects everything in it.

`lessons.yaml` and the PR body serve as the changelog, because the retro PR
may not touch `docs/FRAMEWORK_CHANGELOG.md`.

Auto-merge needs all three of these:

- `mode: eval-sandbox` in the reviewed `factory.yaml`;
- the repo variable `CADENCE_EVAL_SANDBOX == 'true'`;
- a private repo.

It merges with `--match-head-commit`.

## Metrics

`tool/metrics.py` reads only `cadence/state` and makes no network calls.

**The instrument.** The detector set M is:

- the seed rules;
- every edge ever seeded, including retired lessons;
- the intrinsic families.

For the eval, M is a pre-registered file, the same for both arms. C(a), the
classes of attempt a, is recomputed from a's evidence. Each report records
`detector_set_sha256` and `metrics_code_sha256`.

**Definitions.** The headline families are import-edge, guarded,
missing-test and test.

- **Order.** Attempts are ordered by `completed_at` (live) or by issue
  number (eval).
- **Earlier classes.** `Prior_N(a)` is the union of C(b) over the N attempts
  before a whose issue differs from a's. N is `repeat_window`; in the eval it
  covers all earlier attempts.
- **Exposure** X(a, k).
  - For `import-edge:<from>->…` and `missing-test:<area>`, a added or
    modified a file in that area.
  - Every other family is always exposed.
- **Sets.**
  - Opportunities: O = {(a, k) : k ∈ Prior_N(a) and X(a, k)}.
  - Repeats: R = {(a, k) ∈ O : k ∈ C(a)}.
  - Escapes: E = {(a, k) ∈ R : a was published}.
- **Repeat rate** RR = |R| / |O|.
  - It is measured on the agent's final patch, before any gate.
  - A mistake the gate blocked still counts.
  - A mistake the agent fixed in its own session does not count.
- **Escape rate** ER = |E| / |O|. Also reported: |E| / |R|, and escapes per
  10 attempts.
- **Post-PR classes** (edit, review) are reported separately, over harvested
  PRs only, and never mixed into RR. A blocked attempt can never produce
  them, so mixing them in would bias RR.
- **Kill-criterion support:**
  - `learned_check_catches`: hits by an `L-` rule after its promotion, on an
    issue outside its evidence.
  - `post_promotion_exposed_no_repeat`
  - first-pass verify rate
  - test-tampering rate (`guarded:<test root>:modify|delete`)
  - merge rate within 30 days
  - cost per attempt and per merged PR (spec, build and learn spend)
  - new-class rate
  - counts per class

**Statistics.**

- In production, a rate gets a Wilson 95% interval only when |O| ≥ 30.
  Below that it is reported as `insufficient`.
- The report exits 1 (`incomplete`) when fewer than 95% of build runs have
  observations.
- `--judge-pairs` merges classes in a shadow column only. That is the hook
  for the Jev or LLM judge. Structured keys stay the headline.

**Rules-on versus rules-frozen eval (phase 1c).**

- **Repos.** 2 arms × 3 trials make 6 private repos, all from one seed
  commit. Planted defect classes appear at least 4 times in every 20 tickets.
- **Arms.**
  - On arm: `mode: eval-sandbox`, plus `CADENCE_EVAL_SANDBOX=true`.
  - Frozen arm: `mode: observe`.
  - Both arms use the same model, prompts, caps and `CADENCE_PLUGIN_REF`.
- **Scripted reviewer** (identical in both arms). It posts `/cadence-forbid`
  or `/cadence-class` on each PR, then closes the PR without merging.
- **Running.**
  - The on arm runs as a chain: build → review → `stage=learn` →
    auto-merged retro PR → next ticket.
  - The frozen arm runs its tickets in parallel.
- **Scoring.** `metrics.py compare`, with the pre-registered detector set,
  `--order issue`, all earlier attempts as N, and a cluster bootstrap over
  tickets (2,000 resamples).
- **The eval passes** when all of these hold:
  - the 90% interval of RR_frozen − RR_on lies above 0;
  - ER_on < ER_frozen;
  - the on arm's tampering rate is no higher;
  - the on arm's median cost is at most 1.25 × the frozen arm's.
- **Ablation.** Results are also split by family (checks vs patterns).

## Security

| Threat | Control |
|---|---|
| Agent code in `verify` writes to `GITHUB_OUTPUT`, `GITHUB_ENV` and the log | `failed_step` and the log are tier B. `publish` maps `failed_step` to a fixed enum before posting it. `observe` recomputes guarded paths and rule hits from the patch |
| A leftover agent process replaces an artifact | `observe` passes its result as a job output. `ledger` stores the patch only if its sha256 matches |
| Hostile files in the scanned tree | `observe` runs base code (`python -I`, base config). It reads only regular files in the patch, up to 1 MB each, skips symlinks, and never executes anything in `work/` |
| `emit_rule.py` as a write sink | `where` and `forbidden` use a fixed character set, with no `..` and no `tests/fixtures` prefix. The input must be one import-shaped line, matched by strict per-language patterns. The sample path must stay inside the fixture. Format checks are on. Apply preserves the file's text and checks the parsed result. The checker skips `tests/fixtures/retro/` |
| The retro PR as an escalation path | **Path allowlist:** `.cadence/cadence.yaml`, `.cadence/lessons.yaml`, `docs/PATTERNS.md`, `tests/fixtures/retro/**`. **Semantic guard:** `cadence.yaml` changes only in `L-` entries; `PATTERNS.md` changes only inside its section; fixtures are new directories with no symlinks or executables. The guard runs twice, the second time before `git apply` in the job that holds the token. `factory.yaml` (budget, autonomy) can never be written |
| Stored prompt injection through lessons | In v1, lessons are fixed templates over validated keys, with no model text. PR bodies carry links and keys, never excerpts. Comment text is never stored |
| Poisoned lessons | Only humans with write access, never bots or forks, and only on PRs the factory opened. Promotion needs 2 distinct issues and must-pass-root. Per-PR caps, a human merge, cool-down and suppression |
| The model step | `classify` holds contents read and the Anthropic key only. Its tools are Read, Glob, Grep and Skill, plus Edit of one output directory; no shell, web or MCP. Turns and dollars are capped, and it has its own ledger pool. Its output is checked for known ids, enums, vocabulary and real lines |
| Auto-merge | Three switches, set in three different places, plus `--match-head-commit` |
| Triggers | Unchanged: `issues`, `issue_comment`, `workflow_dispatch`, `schedule`. PR heads are fetched as objects; jobs run only `git diff`, `git log` and `git show` on them |
| The state branch | Only the App writes it, and files are create-only. Paths and sizes are checked. No job that runs code checks it out. Recommended: a ruleset that limits `cadence/state` and `cadence/retro` to the App |
| Spend | Learn caps sit inside the global daily cap. The gate counts a running `classify` as in flight |
| Privacy | Authors are not stored, and there are no per-developer numbers. Comment text exists only in a 3-day artifact, and only when `classify` is on |

## Files

| File | Change |
|---|---|
| `plugins/cadence/templates/tool/signals.py` | new: `observe`, `finalize`, `put`, `due`, `harvest`, `apply-classified`, `config` |
| `plugins/cadence/templates/tool/ladder.py` | new: `plan`, `apply`, `guard`, `pr-body` |
| `plugins/cadence/templates/tool/metrics.py` | new: `report`, `compare` |
| `plugins/cadence/templates/tool/emit_rule.py` | provenance, `--must-pass-root`, `--rule-id`, `--retire`, `--replay`, `--json`, text-preserving apply, input hardening |
| `plugins/cadence/templates/tool/check_boundaries.py` | rule ids; `paths=`; relative skip dirs; skips `tests/fixtures/retro/` and symlinks |
| `plugins/cadence/templates/tool/ledger.py` | `--stage`, `--pr`, `--published-sha`, `--base-sha`, `check --pool learn`, `load_learning()` |
| `plugins/cadence/templates/tool/reconcile.py` | runs titled `#sweep` and `#learn` name no issue |
| `plugins/cadence/schemas/retro.schema.json` | `factory` object; stricter `violation_sample` |
| `plugins/cadence/schemas/observation.schema.json` | new |
| `plugins/cadence/schemas/classify.schema.json` | new |
| `plugins/cadence/schemas/lessons.schema.json` | new |
| `plugins/cadence/schemas/retro-plan.schema.json` | new |
| `plugins/cadence/schemas/metrics.schema.json` | new |
| `plugins/cadence/schemas/cadence-yaml.schema.json` | optional boundary `id` |
| `plugins/cadence/skills/cadence-findings/SKILL.md` | new: read-only classifier |
| `plugins/cadence/skills/cadence-retro/SKILL.md` | factory mode |
| `plugins/cadence/skills/cadence-intake/SKILL.md` | reads `lessons.yaml` |
| `plugins/cadence/templates/.github/workflows/cadence-factory.yml.tmpl` | the jobs above |
| `plugins/cadence/templates/factory.yaml.tmpl` | `learning:` block |
| `plugins/cadence/templates/docs/PATTERNS.md.tmpl` | learned section |

## Open questions

- **In-session catches.** If the agent fixes a learned-check violation inside
  its own session, nobody sees it, so `learned_check_catches` undercounts. The
  cheapest fix is to upload a tier-B log of in-session hits.
- **More emitters.** Only import edges become checks. A co-change emitter
  (turning `missing-test` into a check), plus lint and schema emitters, would
  make other lessons enforceable.
- **Package edges.** `pkg:<name>` edges stay patterns, because the checker's
  tokens do not match bare specifiers.
- **Model-written lesson text** for review and edit classes would need a
  linter and a path that only humans can approve.
- **Accepted edges.** An edge a human merged without objecting still counts
  once it is seeded. `--must-pass-root` catches the contradiction, but should
  merged edges be excluded altogether?
- **Changing `area_depth`** re-keys every class. Treat it as a new detector
  set.
- **Flaky tests.** There is no flake detection beyond requiring `verify.sh`
  to pass on `main`.
- **Cost and size.** The state branch grows by up to 512 KiB per attempt;
  warn at 200 MB. A learn chain costs about 4 runner minutes each time it is
  due.
- **Classify retries.** Items skipped because of the budget are not retried
  in v1.

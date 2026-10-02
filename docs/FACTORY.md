---
layout: default
title: Factory mode
---

# Factory mode

> **Status: phase 1a ran live; the learning loop (phase 1b) is wired and
> smoke-tested live, not yet shown end to end.** The first live runs are in
> a private sandbox repo ([setup](factory-sandbox-setup.md)); the learning
> loop is specified in [LEARNING.md](LEARNING.md).
> Work happens on the `factory` branch. Nothing here ships until the phase 1 gate on
> 2026-11-13. The full reasoning, research and sources live in the
> [Cadence Factory decision doc](https://claude.ai/code/artifact/2e1da873-1f03-4602-b3ab-7618ce6e2d56).

Factory mode turns Cadence from a framework you drive by hand into a
pipeline that runs on your own GitHub: a labelled issue becomes a spec
you approve, the agent team builds it, the Definition of Done gate
blocks or passes it, and a draft PR arrives for you to merge.

The differentiator is what happens after each run. Every failure
becomes a finding, and lessons climb **note → pattern → check**. A
check is only accepted if it fires on the real failing sample
(`emit_rule.py` already enforces this for boundary rules). The learned
checks are committed to `.cadence/`, so they survive a switch of agent
or model.

## How a run works

| Step | Where it runs | Holds |
|---|---|---|
| Issue labelled `factory` | GitHub Issues | Untrusted text; read with no secrets |
| Spec, then `/approve` from a user with write access | `cadence-intake` skill | A human gate |
| Agent job builds the change | `claude-code-action` on the user's runner | No push token; uploads its diff as an artifact |
| Verify job | Fresh checkout; tests and `.cadence/` restored from the base branch | No secrets; runs `verify.sh` |
| One retry, if verify failed at format, lint, boundaries or test | The same run: the agent again from its first patch, then verify again | The same approval, claim and caps; the daily budget is checked first |
| Publish job | Fresh checkout | A freshly minted GitHub App token; opens the draft PR |
| Human merge | GitHub | A human gate |

Every attempt is scanned (`observe`) and its observation and findings are
appended to the `cadence/state` branch. The hourly sweep also reads closed
agent PRs (the human's edits, review comments). A single, serialized retro
job climbs the ladder and opens one rolling PR against `.cadence/` that a
human merges. How it works, the data model and the metrics:
[LEARNING.md](LEARNING.md).

## Phases and gates

| Phase | Builds | Gate to the next phase |
|---|---|---|
| 1a | Infra spine: per-user GitHub App identity with loop guards, agent/verify/publish job split, per-issue concurrency, ref-claim lock, cost caps and ledger, hourly reconciler | — |
| 1b | Pipeline plus the learning ladder | — |
| 1c | Eval: rules-on vs rules-frozen replay, fanned out in parallel | **2026-11-13:** the loop beats frozen rules (kill criteria below) |
| 2 | Coordinator (plans, never codes; plan lives in issues), CI via `workflow_run`, Linear/Sentry/Jira/Slack intake as issues | Merge-conflict rate measured and low |
| 3 | Parallel writers per ownership zone; a small relay for live webhooks (the first hosted backend) | — |

### Kill criteria (week 6)

Stop if the learning-loop test shows no gain, or if two of these are
missed:

- Agent PRs merged within 30 days: at least 50%
- Median cost per ticket: at most $20, under a hard cap
- Outside use: at least 3 public repos with a committed `.cadence/cadence.yaml`
- Unique edge: at least one retro rule that later caught a real repeat

## Layout

```
plugins/cadence/                       ships to users
  skills/cadence-intake/               issue → spec file, non-interactive; reads learned lessons (run live)
  skills/cadence-findings/             review comments → enum labels, read-only (phase 1b; off by default)
  skills/cadence-retro/                retrospective; "Factory mode" reads and decides the retro PR
  skills/cadence-factory-setup/        GitHub App, budget, autonomy       (stub; manual steps below)
  schemas/                             observation, classify, lessons, retro-plan, metrics
                                       (new); retro and cadence-yaml (extended)
  templates/.github/workflows/
    cadence-factory.yml.tmpl           route → gate → intake / agent → verify → observe →
                                       (retry-gate → agent-retry → verify-retry → observe-retry)
                                       → publish, ledger, release, reconcile; learn chain
                                       harvest → classify → learn-record → retro-plan →
                                       retro-publish or retro-failed. Every action pinned to a SHA
    cadence.yml.tmpl                   the CI template: contents read, actions pinned to a SHA
  templates/factory.yaml.tmpl          budget, max_turns, retry, autonomy, learning (done)
  templates/tool/
    route.py                           event → stage, deterministic       (done, tested)
    intake_sanitize.py                 issue → clean, untrusted-marked file (done, tested)
    ledger.py                          cost caps (build and learn pools), retry switch, run log (done, tested)
    claim.py                           ref-claim lock per issue           (done, tested)
    reconcile.py                       hourly sweep for stuck work        (done, tested)
    signals.py                         observe, finalize, put, due, harvest, apply-classified, config,
                                       excerpt (the verify log for the retry)
    ladder.py                          note → pattern → check: plan, apply (--verify-failed), guard, pr-body
    metrics.py                         repeat and escape rates: report, compare
    emit_rule.py                       proves a check fires on its real sample (extended)
    check_boundaries.py                the boundary checker (rule ids, paths=, skips retro fixtures,
                                       resolves relative TS/JS and Python imports)
eval/                                  internal replay harness; never ships
docs/FACTORY.md                        this page
docs/LEARNING.md                       the learning loop: signals, ladder, metrics, security
docs/factory-auth.md                   research: OIDC workload identity instead of the stored API key
docs/factory-sandbox-setup.md          GitHub App, secrets and labels for the sandbox
```

### Wiring (2026-10-02)

The workflow template calls every tool. The build path (route to release)
ran live in the sandbox on 2026-10-01; the learning-loop jobs
([LEARNING.md](LEARNING.md)) ran live as a smoke test on 2026-10-02. The
retry jobs and `retro-failed` are wired and tested offline, not yet run on
GitHub.

| Job | Runs when | Tokens | Does |
|---|---|---|---|
| `route` | a `factory` label, an `/approve` comment, or a dispatch | `GITHUB_TOKEN`: contents read | Looks up the sender's permission; `route.py` picks `spec`, `build` or `none`; reads the caps and `retry.on_dod_fail` from `factory.yaml` |
| `gate` | build | App token (contents write); `GITHUB_TOKEN`: actions read, issues write | One global queue. Re-checks the live labels (a second `/approve` that waited in the issue's queue stops here), finds the approved spec and outputs its sha256 (`spec_sha256`), counts the slots already spending (a build whose retry was granted holds two), `ledger.py check`, `claim.py acquire`, label `building`. A refusal comments and ends the run with nothing booked |
| `intake` | spec | `GITHUB_TOKEN`: contents and issues read; `ANTHROPIC_API_KEY` | Sanitizes the issue; the `cadence-intake` skill writes one file and nothing else |
| `agent` | build, gate passed | `GITHUB_TOKEN`: contents read; `ANTHROPIC_API_KEY` | Builds; uploads `change.patch` and the cost result |
| `verify` | the agent finished | contents read, no secrets | Reads `learning.guarded_paths` and `learning.test_roots` with the base tools before the patch (`.github/`, `.cadence/`, `scripts/` and `tool/` are always guarded), applies the patch to the base commit, restores the guarded paths and leaves out new files there (except under the test roots, `tests/` and `test/` by default), records the tree it tests, runs `verify.sh`. A patch that touches `.github/workflows/` fails |
| `observe` | build past the gate, the agent ran (whatever verify said) | contents read, no secrets | Applies the patch to a scratch worktree of the base and only reads it (`python -I`, base tools and config): import edges and rule hits on added lines, guarded operations, missing tests, failing tests and the gate step from the verify log. It also reads the approved spec from `gate`'s `cadence-input` artifact, only if its sha256 equals `gate`'s `spec_sha256`, and records the active base lessons it cites (`lessons_cited`, informational). The observation and findings leave as a job output (`signals.py observe`) |
| `retry-gate` | verify failed and `retry.on_dod_fail` is 1 | `GITHUB_TOKEN`: actions and contents read, no secrets | Waits in the gate's queue. Maps verify's failed step to a fixed word: only `format`, `lint`, `boundaries` and `test` are retried (never `apply`, `policy`, a config error, an empty patch or a timeout). Counts the slots in flight with this run included, then `ledger.py check`: one more `per_run_usd` must fit the daily cap. Writes a cleaned, size-limited excerpt of the verify log with the base tools (`signals.py excerpt`). Its last step, "Grant the retry", is what the in-flight count sees |
| `agent-retry` | the retry was granted | `GITHUB_TOKEN`: contents read; `ANTHROPIC_API_KEY` | The agent job once more, with the same tools and caps: applies the first patch (with git only, last before the agent, leaving out `.claude/` and `.mcp.json`), and gets the failed step and the excerpt as untrusted data. Uploads `change-retry` and its cost result |
| `verify-retry` | the retry agent finished | contents read, no secrets | The verify job's steps, byte for byte, on the retry's patch; records the tree it tests |
| `observe-retry` | the retry agent ran | contents read, no secrets | observe's steps on the retry, under the run id `<run>.retry1` |
| `publish` | spec, or build past the gate | App token (contents, pull requests) to push; `GITHUB_TOKEN` contents read, issues write, checks write | Posts the spec with HTML comments and invisible characters removed (`spec-ready`), or picks the attempt that passed (the first, else the retry), pushes `cadence/issue-N`, posts the `cadence/verify` check for that attempt's tree and opens a draft PR (`pr-open`; the body gives the cost of both attempts and says when the retry passed), or labels `dod-failed` / `needs-human` with the failed step and one fixed sentence about the retry |
| `ledger` | always, for spec runs and builds past the gate | App token | Books cost and outcome in `runs/` on the `cadence/state` branch, the retry as its own record (`<run>.retry1`); for builds, checks both observers' bundles (sha256, schemas: `signals.py finalize`) and books `observations/`, `findings/`, `patches/` and `prs/`, create-only (`signals.py put`). The PR is booked on the attempt that was published |
| `release` | always, when the gate took the claim, after every retry job | App token | `claim.py release` |
| `reconcile` | hourly schedule | App token | `reconcile.py`; then `signals.py due` says whether the learn chain runs |
| `harvest` | hourly when due, or a dispatch with `stage=learn` | `GITHUB_TOKEN`: contents, pull requests, issues and actions read | Reads closed factory PRs (PR heads fetched as objects, never checked out): the human's edits, `/cadence-forbid` and `/cadence-class`, review comments from users with write access. Checks the learn budget (`ledger.py check --pool learn`) |
| `classify` | only if `learning.classify` is on and the learn budget allows | `GITHUB_TOKEN`: contents read; `ANTHROPIC_API_KEY` | The `cadence-findings` skill labels review comments with enums; no shell, one output file |
| `learn-record` | after harvest | App token | Checks the labels (`signals.py apply-classified`), books post-PR findings, harvest and decision markers, the learn marker, classify's spend and the daily metrics snapshot on `cadence/state` |
| `retro-plan` | after learn-record | `GITHUB_TOKEN`: contents and pull requests read, no secrets | `ladder.py plan` and `apply` (which calls `emit_rule.py`), `verify.sh` when a check changed, `ladder.py guard`; uploads the retro patch, plan and PR body. A failing `verify.sh` never fails it: it demotes the plan's checks (`ladder.py apply --verify-failed`) and runs `verify.sh` once more; a plan that still fails is not published. One retro queue (`cadence-factory-retro`) |
| `retro-publish` | the plan changed | App token (contents, pull requests) | `ladder.py guard` again on the patch, then git and gh only: force-pushes `cadence/retro` with a lease (never over a human's push) and opens or updates one non-draft PR. Merges only in an eval sandbox (`mode: eval-sandbox`, `CADENCE_EVAL_SANDBOX`, private repo) |
| `retro-failed` | retro-plan gave up on a plan | App token (contents) | Git and jq only: records `retro/failed/<plan_sha>.json` on `cadence/state`, create-only, so later learn runs skip that plan until `main` or the plan changes |

**Labels are one state at a time:** `factory` (a human adds it) →
`spec-ready` → `building` → `pr-open`, or `dod-failed` / `needs-human`.
Labels and comments are written with `GITHUB_TOKEN`, so they start no
workflow. To rebuild an approved spec, add `spec-ready` back and reply
`/approve`.

Closed from the 2026-10-01 review:

- **Claim needs a push token.** `gate` mints the App token and runs
  `claim.py acquire --run-id "$GITHUB_RUN_ID"`; `release` runs with
  `if: always()` and `--sha` of the claim it took.
- **One global gate for spending.** `gate` sits in the concurrency group
  `cadence-factory-gate` (`queue: max`, never cancelled) and passes
  `--in-flight` from the Actions API.
- **The ledger needs the result.** `intake` and `agent` upload the final
  result's numbers only (not the transcript). Job results map to
  outcomes; a skipped model job books nothing; a re-run that did not re-run
  the model books $0.
- **Where records live.** `runs/` on the orphan branch `cadence/state`,
  passed as `--records-dir`; pushes retry three times on a non-fast-forward.

Closed in the adversarial review (2026-10-01):

- **Reconciler spec retries.** `route.py` lets a `workflow_dispatch` from
  exactly `CADENCE_BOT_LOGIN` through, for `spec` only (never `build`).
  `reconcile.py` dispatches a retry only when whoever last added `factory`
  has write access now, so a triage user or an issue template that applies
  `factory` cannot get a spec the label event refused.
- **Hidden text in the approved spec.** The approver reads the rendered
  spec comment; the build agent gets its raw text. `publish` now removes
  HTML comments (past the marker line) and invisible characters before
  posting. `intake_sanitize.py` also drops variation selectors and other
  invisible fillers, and repeats comment removal until none is left.
- **A second `/approve`** queued behind the first build no longer starts a
  second build: `gate` re-checks the live labels.
- **Guarded paths.** `verify` lists changed files NUL-separated (a quoted,
  non-ASCII name slipped past the `.github/workflows/` check) and leaves
  out new files under `.github/`, `.cadence/`, `scripts/` and `tool/` (a
  new `tool/yaml.py` would have shadowed PyYAML for the boundary check).
- **The stuck-build comment** now says to swap `building` for
  `spec-ready` before `/approve`, which is what `route.py` requires.

Closed from the review of the first factory PR (2026-10-02):

- **The gate's result is visible on the PR.** Sandbox PR #2 showed only
  the bot's own checklist; the Definition of Done ran inside the factory
  run. `publish` now posts a `cadence/verify` check (with `GITHUB_TOKEN`,
  so it starts no workflow) on the commit it pushes. It is `success` only
  when that commit's tree is exactly the tree `verify` recorded before
  running `verify.sh`; otherwise, as when guarded paths were restored, it
  is `action_required` and says why. A commit a human pushes to the
  branch later gets no `cadence/verify` check, so do not make it a
  required status check: the repo's own CI covers those commits.

Closed from the review of the learning loop, and hardening (2026-10-02):

- **A retro result that fails `verify.sh`** no longer fails `retro-plan`
  (which failed every later learn run the same way). Every check in the
  plan is demoted to a pattern (a check whose class was a pattern stays
  one, and `test:` patterns are dropped, since `verify.sh` is what proves
  them), the plan is applied again and `verify.sh` runs once more. The PR
  body says what was demoted and why. A plan that still fails is recorded
  by `retro-failed` under `retro/failed/` on `cadence/state` and skipped
  until `main` or the plan changes; push to `main` or delete the record to
  retry it by hand.
- **Directory-index imports.** `check_boundaries.py` now also resolves
  relative TS/JS imports and Python imports to a repo path, so
  `from "../db"` fires a rule on `src/db/**` (and `../dbutils` does not).
  A check is proven on up to three samples, newest first.
- **One retry on a failed gate** (`retry.on_dod_fail`, default 1). It runs
  inside the same run, not as a new dispatch: `route.py` lets the App
  dispatch `spec` only, so every job that mints the App token, the hourly
  reconciler included, would otherwise gain a way to start a paid build
  nobody approved. The retry keeps the same `/approve`, spec and claim,
  and `retry-gate` checks the worst case (two `per_run_usd` for this run)
  in the gate's queue before it spends. Both attempts are observed and
  booked; the retry under the run id `<run>.retry1`.
- **Every action is pinned** to a full commit SHA, with the tag as a
  comment, in both workflow templates; `cadence.yml.tmpl` has
  `permissions: contents: read`.

Still to do:

- **Run it live** in the sandbox: spec, approve, build, PR ran on
  2026-10-01; still to see live: a budget refusal, a held claim, a
  cancelled run, a re-run, the DoD retry and a demoted retro plan.
- **Auth.** The model jobs hold a stored `ANTHROPIC_API_KEY`.
  [factory-auth.md](factory-auth.md) looks at whether GitHub Actions OIDC
  can replace it.
- **Intake is booked but not budget-checked.** Each intake run is bounded
  by `budget.per_run_usd`; decide whether it goes through `gate`.
- **The gate is not a sandbox.** Guarded paths are restored, but agent code
  still runs in `verify`, so a hostile patch can fake a pass through files
  outside them (a root `conftest.py`, package scripts) or a new file under
  `tests/` (a new `tests/conftest.py`). A green `cadence/verify` check
  means `verify.sh` passed, not that it could not be faked. The draft PR
  and the human merge remain the real gate.
- **Findings and the retro job** are wired: see [LEARNING.md](LEARNING.md)
  for the signals, the ladder, the metrics and the security model. Next is
  the live demo in the sandbox (a planted `src/db` edge over three issues).
- **`/cadence-factory-setup`** is still a stub: setup is manual
  ([sandbox steps](factory-sandbox-setup.md)).

## Rules that hold in every phase

- Users bring their own Anthropic API key and their own GitHub App.
  Cadence never resells, proxies or pays for model usage.
- The agent job never holds a push token. Tests, CI config and
  `.cadence/` are restored from the base branch before the gate runs.
- Only a human starts a build: an `/approve`, or a `stage=build` dispatch
  by a user with write access. No job dispatches one, and the one
  automatic retry runs inside the run that human started.
- No trigger runs code from a PR head (no `pull_request`,
  `pull_request_target` or `workflow_run`). Closed PRs are read by the
  hourly sweep or a `stage=learn` dispatch, as objects.
- Waking agents, approvals and permissions are deterministic: exact
  slash commands from users with write access, never free-text intent.
- Every retro change to `.cadence/` arrives as a PR a human reviews. The
  only auto-merge is a private eval repo with `mode: eval-sandbox` and the
  repository variable `CADENCE_EVAL_SANDBOX` set, all three at once.
- No per-developer metrics; report per repo and per rule.

## Open questions

- Can GitHub's app-manifest flow make "register your own App" close to
  one click from a CLI?
- What does one real run cost on a 2-vCPU private-repo runner?
- Does Jev beat a cheap LLM judge for repeat matching? (Shadow trial in
  phase 1; see the decision doc.)

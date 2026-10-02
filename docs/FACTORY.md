---
layout: default
title: Factory mode
---

# Factory mode

> **Status: tools and workflow wired, not yet run live.** The first live
> run is in a private sandbox repo ([setup](factory-sandbox-setup.md)).
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
| Publish job | Fresh checkout | A freshly minted GitHub App token; opens the draft PR |
| Human merge | GitHub | A human gate |

Every run appends one findings file to a state branch. A single,
serialized retro job turns findings into a PR against `.cadence/`.

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
  skills/cadence-intake/               issue → spec file, non-interactive (written; not run live)
  skills/cadence-factory-setup/        GitHub App, budget, autonomy       (stub; manual steps below)
  templates/.github/workflows/
    cadence-factory.yml.tmpl           route → gate → intake / agent → verify → publish,
                                       ledger, release, reconcile         (wired; not run live)
  templates/factory.yaml.tmpl          budget, max_turns, autonomy        (done)
  templates/tool/
    route.py                           event → stage, deterministic       (done, tested)
    intake_sanitize.py                 issue → clean, untrusted-marked file (done, tested)
    ledger.py                          cost cap and run log               (done, tested)
    claim.py                           ref-claim lock per issue           (done, tested)
    reconcile.py                       hourly sweep for stuck work        (done, tested)
eval/                                  internal replay harness; never ships
docs/FACTORY.md                        this page
docs/factory-sandbox-setup.md          GitHub App, secrets and labels for the sandbox
```

### Wiring (2026-10-01)

The workflow template now calls every tool. It has not run on GitHub
yet; the sandbox run is next.

| Job | Runs when | Tokens | Does |
|---|---|---|---|
| `route` | a `factory` label, an `/approve` comment, or a dispatch | `GITHUB_TOKEN`: contents read | Looks up the sender's permission; `route.py` picks `spec`, `build` or `none`; reads the caps from `factory.yaml` |
| `gate` | build | App token (contents write); `GITHUB_TOKEN`: actions read, issues write | One global queue. Re-checks the live labels (a second `/approve` that waited in the issue's queue stops here), finds the approved spec, counts runs already spending, `ledger.py check`, `claim.py acquire`, label `building`. A refusal comments and ends the run with nothing booked |
| `intake` | spec | `GITHUB_TOKEN`: contents and issues read; `ANTHROPIC_API_KEY` | Sanitizes the issue; the `cadence-intake` skill writes one file and nothing else |
| `agent` | build, gate passed | `GITHUB_TOKEN`: contents read; `ANTHROPIC_API_KEY` | Builds; uploads `change.patch` and the cost result |
| `verify` | the agent finished | contents read, no secrets | Applies the patch to the base commit, restores `tests/`, `.github/`, `.cadence/`, `scripts/` and `tool/` and leaves out new files there (except new tests), records the tree it tests, runs `verify.sh`. A patch that touches `.github/workflows/` fails |
| `publish` | spec, or build past the gate | App token (contents, pull requests) to push; `GITHUB_TOKEN` contents read, issues write, checks write | Posts the spec with HTML comments and invisible characters removed (`spec-ready`), or pushes `cadence/issue-N`, posts the `cadence/verify` check on that commit and opens a draft PR (`pr-open`), or labels `dod-failed` / `needs-human` with the reason |
| `ledger` | always, for spec runs and builds past the gate | App token | Books cost and outcome in `runs/` on the `cadence/state` branch |
| `release` | always, when the gate took the claim | App token | `claim.py release` |
| `reconcile` | hourly schedule | App token | `reconcile.py` |

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

Still to do:

- **Pin every action to a full commit SHA** before enabling anywhere but
  the sandbox (CICD_PLAN). The template uses major tags.
- **Run it live** in the sandbox: spec, approve, build, PR; then a budget
  refusal, a held claim, a cancelled run and a re-run.
- **DoD retry.** v1 labels `dod-failed` and stops; one retry that feeds the
  failure back to the agent is planned.
- **Intake is booked but not budget-checked.** Each intake run is bounded
  by `budget.per_run_usd`; decide whether it goes through `gate`.
- **The gate is not a sandbox.** Guarded paths are restored, but agent code
  still runs in `verify`, so a hostile patch can fake a pass through files
  outside them (a root `conftest.py`, package scripts) or a new file under
  `tests/` (a new `tests/conftest.py`). A green `cadence/verify` check
  means `verify.sh` passed, not that it could not be faked. The draft PR
  and the human merge remain the real gate.
- **Findings and the retro job** are not wired yet.
- **`/cadence-factory-setup`** is still a stub: setup is manual
  ([sandbox steps](factory-sandbox-setup.md)).

## Rules that hold in every phase

- Users bring their own Anthropic API key and their own GitHub App.
  Cadence never resells, proxies or pays for model usage.
- The agent job never holds a push token. Tests, CI config and
  `.cadence/` are restored from the base branch before the gate runs.
- Waking agents, approvals and permissions are deterministic: exact
  slash commands from users with write access, never free-text intent.
- Every retro change to `.cadence/` arrives as a PR a human reviews.
- No per-developer metrics; report per repo and per rule.

## Open questions

- Can GitHub's app-manifest flow make "register your own App" close to
  one click from a CLI?
- What does one real run cost on a 2-vCPU private-repo runner?
- Does Jev beat a cheap LLM judge for repeat matching? (Shadow trial in
  phase 1; see the decision doc.)

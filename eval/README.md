# Factory eval harness (internal)

> **Status: plan only (factory branch, phase 1c).** This directory never
> ships to users; the plugin is `plugins/cadence/` only.

The eval answers one question: **does the learning loop make the same
mistakes less likely?** If it does not, Cadence drops the claim
"gets better every run". The result is the phase 1 gate on 2026-11-13.

## Design

| | Rules-on arm | Rules-frozen arm |
|---|---|---|
| Rules | Retro PRs auto-merge after each ticket (sandbox only) | `.cadence/` pinned at week 1 |
| Order | Serial: ticket k sees the lessons of tickets 1..k-1 | Parallel fan-out |
| Model, prompts, budget | Identical, pinned | Identical, pinned |

- **Tickets:** 20 to start (scale to 50), built from real past failures.
  A pilot plants known, repeating defect classes (forbidden import,
  missing test, spec gap) so repeats are known by construction.
- **Trials:** 3 per ticket per arm, scored as pass^k.
- **Where:** a private sandbox repo (`cadence-eval-sandbox`). It reuses
  the private assessment repos, whose hidden tests must stay private, and the graders must stay out of the agents' reach.
- **Runner:** single-ticket `workflow_dispatch` jobs. Each rules-on job
  receives the post-promotion commit SHA as an input. Jobs are capped at
  6 h and a matrix at 256 jobs, so chain rather than fan out the
  rules-on arm.

## Metrics

| Metric | Definition |
|---|---|
| Repeat rate | Finding classes that recur within N runs, before any gate |
| Escape rate | Repeats that reach a PR |
| First-pass verify | Runs whose first attempt passes the DoD gate |
| Cost per merged PR | From the ledger; a run with no reported cost is booked at its cap |
| Test tampering | Diffs touching tests, CI config or `.cadence/` |

Repeat matching uses structured keys first (same rule, same violation
sample, same test). Pairs the keys do not settle go to a judge; Jev is
trialled there in shadow mode against a cheap LLM judge and an
embedding baseline (see the decision doc).

## Budget

Twenty tickets x 3 trials x 2 arms is 120 runs. Compute the worst case
(per-run cap x runs) before launching, and run off-hours: the eval
shares the account's concurrent-job cap with CI.

## Layout (to come)

```
eval/
  tickets/      ticket definitions and planted defects
  harness/      dispatch, chaining, scoring
  results/      dated, append-only result files
```

---
layout: default
title: Factory mode
---

# Factory mode

> **Status: design + scaffold, not functional.** Work happens on the
> `factory` branch. Nothing here ships until the phase 1 gate on
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
  skills/cadence-intake/               issue → spec, non-interactive      (stub)
  skills/cadence-factory-setup/        GitHub App, budget, autonomy       (stub)
  templates/.github/workflows/
    cadence-factory.yml.tmpl           agent / verify / publish jobs      (skeleton)
  templates/tool/
    ledger.py                          cost cap and run log               (stub)
    claim.py                           ref-claim lock per issue           (stub)
    reconcile.py                       hourly sweep for stuck work        (stub)
eval/                                  internal replay harness; never ships
docs/FACTORY.md                        this page
```

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

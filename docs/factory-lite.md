---
layout: default
title: Factory lite
---

# Factory lite: one secret instead of three

> **Status: design only (2026-10-03).** No workflow, tool or test changes
> with this page. It answers one question for [factory mode](FACTORY.md):
> could the factory run without a GitHub App?

## The question

Today a repository needs three secrets (`CADENCE_APP_ID`,
`CADENCE_APP_PRIVATE_KEY`, `ANTHROPIC_API_KEY`), a GitHub App the user
registers and installs, and the variable `CADENCE_BOT_LOGIN`. Setting up a
real repository by hand took about an hour, and most of it went on the
secrets and the App. The setup skill (`/cadence-factory-setup`, see
[setup](factory-sandbox-setup.md)) shortens it, but cannot remove the App.

Factory lite would use the workflow's own `GITHUB_TOKEN` for every write,
with the repository setting **Allow GitHub Actions to create and approve
pull requests** ticked. Adoption becomes one secret (`ANTHROPIC_API_KEY`)
and one checkbox. The outside-use kill criterion (three outside public
repositories by 2026-11-13) depends on exactly that friction.

## Where the App is used today

The labels and comments of a run, and the `cadence/verify` check, already
go through `GITHUB_TOKEN` (the reconciler's relabels are the exception: it
holds only the App token). The App token is minted in the jobs that push or
open PRs, and in `reconcile`; its login is the factory's identity:

| Job | App token does | `CADENCE_BOT_LOGIN` does |
|---|---|---|
| `gate` | `claim.py acquire`: pushes `cadence/claim/N` | |
| `publish` | pushes `cadence/issue-N` as `<slug>[bot]`, opens the draft PR | |
| `ledger`, `learn-record`, `retro-failed` | push records to `cadence/state` | |
| `release` | `claim.py release`: deletes the claim ref | |
| `reconcile` | releases stale claims, relabels stuck issues, dispatches `stage=spec` retries | |
| `retro-publish` | force-pushes `cadence/retro` with a lease, opens or updates the retro PR | |
| `route` | | loop guard; rule 0 lets exactly this login dispatch a `spec` |
| `intake`, `agent`, `agent-retry`, `classify` | | `allowed_bots` for runs the App dispatched |
| `harvest`, `reconcile` (due) | | a factory PR is one opened by this login; a factory commit is one it authored |

## What breaks with `GITHUB_TOKEN`

### 1. CI does not run on factory PRs

GitHub starts no workflow run for an event caused by `GITHUB_TOKEN`, except
`workflow_dispatch` and `repository_dispatch`. The push of `cadence/issue-N`
and the opening of the draft PR start neither `push` nor `pull_request` CI,
and neither does the retro PR. The reviewer sees only `cadence/verify`, and
a repository that requires its CI checks cannot merge the PR until a human
starts CI (closing and reopening the PR, or pushing a commit).

Fix: after opening the PR, `publish` dispatches the repository's CI
workflow on the pushed branch (`gh workflow run <ci file> --ref
cadence/issue-N`, with `actions: write`). That is the one event
`GITHUB_TOKEN` may start. It needs `workflow_dispatch:` in the CI workflow
(the setup skill can add it), and the run attaches its checks to the head
commit, so they show on the PR and match required checks by name (to be
confirmed live). It runs agent code under the CI workflow's own
permissions, which is what the App's push already does today. It does
break one invariant ("no step dispatches anything",
`test_the_factory_never_dispatches_a_run`): the test would allow exactly one
dispatch, of the configured CI file, on `cadence/issue-N`, from `publish`.

### 2. The loop guard identity

The factory's own writes start no workflow, so there is no loop to guard
against: that already holds for the labels and comments the factory writes
with `GITHUB_TOKEN`. The problem is identity. `github-actions[bot]` is the
login of every workflow's token in the repository. Setting
`CADENCE_BOT_LOGIN` to it would let any workflow with `actions: write`
dispatch a paid spec run "as the factory". Lite must leave rule 0 off
(`CADENCE_BOT_LOGIN` empty: `route.py` then ignores every bot, as it does
today when the variable is unset).

`harvest` finds factory PRs by their author and tells the human's commits
from the factory's by the commit identity. In lite both are
`github-actions[bot]`, so it needs its own setting (a PR author distinct
from the dispatch identity), and a PR that some other workflow opens from a
`cadence/issue-N` branch would be read as a factory PR. The damage is
bounded: harvest only produces findings, and findings reach `.cadence/`
only through the retro PR a human merges.

### 3. The reconciler's spec retry

`reconcile.py` dispatches `stage=spec` as the App when a `factory` label
event was lost. A `GITHUB_TOKEN` dispatch would start a run, but its sender
is `github-actions[bot]`, which `route.py` (rule 0 off) drops. Lite turns the
dispatch into a comment: "the spec run did not start; a maintainer re-adds
`factory`". Automatic recovery of a lost label event, which is rare, becomes
one human click.

### 4. Claim and ledger pushes

These work: `gate`, `release`, `reconcile`, `ledger`, `learn-record` and
`retro-failed` declare `contents: write` for `GITHUB_TOKEN` instead of
minting the App token, and `claim.py`'s `--force-with-lease` is the same
compare-and-swap. What is lost is exclusivity: a ruleset on `cadence/state`
whose bypass list names only the App keeps every other writer off the
ledger, while with `GITHUB_TOKEN` any workflow given `contents: write` could
rewrite it. (That ruleset needs GitHub Pro or Team on a private repository,
so many lite users would not have it anyway.) The rule that no agent code
runs in a job holding a push token still holds; its test must look for
`contents: write` as well as the App token action.

### 5. What does not change

- `GITHUB_TOKEN` cannot push changes to `.github/workflows/` (it has no
  `workflows` permission), the same property the App has by design.
- The model jobs, `verify` and the learning readers keep read-only tokens;
  a build still starts only from a human's `/approve`; no trigger runs code
  from a PR head.
- Token lifetime: the App token lasts an hour and is revoked when its job
  ends; `GITHUB_TOKEN` expires when its job ends and reaches this repository
  only.

## Security trade-offs

| | GitHub App (today) | Lite (`GITHUB_TOKEN`) |
|---|---|---|
| Long-lived write credential | The App private key: mints tokens with write on contents, issues, PRs and actions for every repository the App is installed on, until rotated | None. The only stored secret is the Anthropic key |
| Identity | Its own (`<slug>[bot]`): rulesets, harvest and the loop guard can single it out | Shared with every workflow in the repository |
| Repository setting | Untouched (Actions may not create or approve PRs) | Actions may create **and approve** PRs, for every workflow that asks for `pull-requests: write` |
| CI on factory PRs | Runs (App events trigger workflows) | Runs only if `publish` dispatches it |
| Spec retry after a lost event | Automatic | A human re-adds the label |

The setting is the main cost. GitHub ships it off because an approval from a
workflow can satisfy a required review: anyone who can push a workflow, or a
compromised action inside one, could approve a pull request. On a solo
maintainer's repository with no required reviews that changes nothing. On a
team repository that relies on reviews it is a real downgrade. Keeping the
default workflow permissions read-only limits it to jobs that ask for
`pull-requests: write`.

## Recommendation

**Yes, as an opt-in mode, not as the default.** The setup skill offers lite
to a solo maintainer on a personal repository: one secret, one checkbox, no
private key to leak. The App stays the default for organization
repositories and for any repository whose merge rules depend on reviews,
required CI checks or rulesets. Moving from lite to the App later is two
secrets, one variable and a re-render.

## Minimal plan

1. **Render, do not fork.** `render_factory_workflow.py --auth
   github-token` turns the one template into the lite variant: each "Mint the
   App token" step is removed, `steps.app.outputs.token` becomes
   `github.token`, each job gets as `permissions:` exactly the
   `permission-*` inputs its App token asked for, and the commit identity
   becomes `github-actions[bot]`. A mechanical transform with tests, not a
   second template to keep in sync.
2. **CI dispatch.** `factory.yaml` gains `ci.workflow` (a file name in
   `.github/workflows/`). In lite, `publish` dispatches it on
   `cadence/issue-N` after the PR opens, and `retro-publish` on
   `cadence/retro`; the setup skill adds `workflow_dispatch:` to that
   workflow in the setup PR.
3. **Identity.** Lite leaves `CADENCE_BOT_LOGIN` empty (rule 0 off), and
   `signals.py harvest` and `due` take the factory PR author as their own
   argument (`github-actions[bot]` in lite).
4. **Reconciler.** `reconcile.py --no-dispatch` comments instead of
   dispatching a spec retry.
5. **Tests.** Run `test_factory_workflow.py` against both variants (as
   `test_render_factory_workflow.py` already runs it against rendered
   workflows). Add for lite: no `CADENCE_APP_*` secret anywhere; the jobs
   with `contents: write` are exactly today's App-token jobs; no Python
   after `git apply` in any of them; the only dispatch is the CI dispatch.
6. **Setup skill.** Ask "App or lite" first; for lite, print the one secret
   command and the checkbox, and skip Step 9 (the App).
7. **Live check** in a sandbox before anyone else uses it: spec, `/approve`,
   build, draft PR with CI dispatched and shown on the PR, a retro PR, the
   hourly sweep.

About a day of work after phase 1's live runs; it does not block the
2026-11-13 gate and should not start before the App path has run live on a
second repository.

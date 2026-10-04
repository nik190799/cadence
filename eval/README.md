# Factory eval harness (internal)

> **Status: harness built; results private.** This directory never ships to
> users; the plugin is `plugins/cadence/` only. The repos, tickets, hidden
> tests and every result live in a private folder outside this repository.

The eval decides the 2026-11-13 kill-or-continue gate for factory mode
([FACTORY.md](../docs/FACTORY.md), [LEARNING.md](../docs/LEARNING.md)). It
answers two questions with numbers:

1. **Does the factory beat a plain agent?** The same tickets, run by the
   factory pipeline, against one headless session told "fix the failing
   tests and the issues in TASK.md" whose result is accepted as it is.
2. **Does the learning loop help?** The same tickets, run serially with
   lessons promoted between tickets (rules on), against the same pipeline
   with `.cadence/` frozen at the start (rules frozen). Same model, same
   prompts, same budget, three trials each.

Both are scored with **hidden tests** the agents never see.

## Design

A local runner (Linux or WSL) emulates the factory workflow job by job,
running the workflow's own scripts, prompts and `claude_args`, cut out of
`cadence-factory.yml.tmpl` at a pinned Cadence commit, byte for byte. No
GitHub repo, App or secret is involved, and nothing leaves the machine.

| Arm | What runs | Per (repo, trial) |
|---|---|---|
| A0 | One `claude -p "fix the failing tests and the issues in TASK.md"` session on the pristine repo, with the budget of three factory runs per ticket (`--max-turns 60n --max-budget-usd 15n`), no plugin; everything accepted | 1 session |
| F0 | The factory pipeline, rules frozen | n tickets x 2 epochs |
| F1 | The factory pipeline, rules on | n tickets x 2 epochs |

**One switch between F0 and F1.** Both arms use `learning.mode:
eval-sandbox` and a byte-identical `.cadence/factory.yaml`. The only
difference is the emulated repository variable `CADENCE_EVAL_SANDBOX`
('true' in F1 only), the product's own auto-merge switch for retro PRs. In
F1 each retro PR is squash-merged before the next ticket; in F0 it opens
and stays open (its plan sha is passed as `--open-plan-sha` on the next
learn run). Using `mode: on` for the frozen arm would let stale-area
retirement fire after the epoch reset, and `observe` would put the arm into
text the agent can read.

**A seed per repo**, identical in every arm and trial: the pinned commit,
then one commit that only adds the factory files (tools, schemas,
`scripts/verify.sh`, docs, the workflow, the rendered `factory.yaml` and the
private overlay's `.cadence/cadence.yaml`), with fixed dates.

**A chain** is one (arm, trial, repo): a bare repo whose `main` starts at the
seed, a worktree of `cadence/state`, and a `ghstore` the read-only gh shim
answers from. Each ticket runs, on a logical clock:

| Minute | Step |
|---|---|
| 0-10 | issue, intake (`/cadence:cadence-intake`), publish's own spec cleaning; questions get one scripted generic reply and a second intake |
| 15 | the scripted `/approve` |
| 20-60 | build: a checkout of `main` (one commit, depth 1), `npm ci`, the agent prompt and `claude_args`, "Package the diff" |
| 65 | the gate (verify's paths, apply and verify pieces; the verdict from the template's own output expression) and observe (`signals.py observe`) |
| 70-110 | one retry when the gate failed at format, lint, boundaries or test |
| 115 | publish: the picked attempt as `cadence: build #N`, a draft PR, `cadence/verify` success only on the gate's exact tree |
| 120 | ledger: `ledger.py record`, `signals.py finalize` and `put` |
| 140 | the scripted reviewer merges the published PR unchanged (a fast-forward) |
| 150-170 | the learn chain: harvest, learn-record, retro-plan (the workflow's own pieces), retro-publish or retro-failed |

**Epochs.** E1 runs the tickets in TASK order. E2 resets `main`, in both
arms, to the seed tree plus the four retro paths (`.cadence/cadence.yaml`,
`.cadence/lessons.yaml`, `docs/PATTERNS.md`, `tests/fixtures/retro/`) as they
stood at the end of E1, and runs the same tickets again under the same issue
numbers. The state, the PRs and the PR counter carry over.

**Invariants**, checked after every ticket: no commit with a
`Cadence-Retro-Plan` trailer ever lands on F0's `main`; every commit on
`main` is the seed, an E2 reset, an agent merge or (F1 only) a retro merge.
Agent edits to the retro paths are allowed (everything is accepted), recorded,
and counted as broad tampering.

## Sandboxes

Every process runs in one of five bubblewrap profiles, with an explicit
environment (nothing inherited) and `PATH=<toolchain>:<venv>:/usr/bin:/bin`:

| Profile | Network | Sees |
|---|---|---|
| agent | shared | the checkout, its `_temp` (the inputs read-only), a fresh `HOME`, the plugin (factory arms); live: the key, with `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` |
| gate | shared (npm) | the checkout, a per-attempt copy of the npm cache |
| tools | off | like gate, plus the pinned tools; every `tool/*.py` run |
| fetch | shared | `npm install --ignore-scripts` on a result, holding no grader file |
| score | off (loopback only) | a copy of one hidden harness and its lib, the result repo, its own `/tmp`, an offline npm cache |

No profile ever sees `/mnt`, `/init`, `/run`, the user's home, the results,
the private folder, the key file, a chain's bare repo or another chain. The
factory's tools always run from the pinned copy (a merged agent PR can
change `tool/` on `main`; that raises a `tool_drift` flag). The runner's own
git runs with hooks off, only on clones no agent code has touched.

## Hidden scoring

For every tree to score (each merged `main`, each try's patch on its base
when it differs, `main` at the end of each epoch, and A0's result) the runner
builds a bare repo with `main` = the pinned pristine commit and
`eval-result` = the tree's commit, prefetches npm packages without running
scripts, and runs the private `hidden_command` (for example
`node /opt/grader/hidden/<h>/run.mjs /srv/result.git eval-result`) offline.
The harness prints `PASS  <check>`, `FAIL  <check>`, `INFO  <key>  <text>`
and `== <p> pass, <f> fail ==`. A denominator check that was not printed
counts as FAIL; "npm install failed" fails every check. A pristine control
runs before each worker's first job and after any harness error. Scores are
cached by tree and never reach the chains, the state or any prompt.

A private check map labels each check `owned` (by a ticket), `regression`
or `noticing` (and `tamper: true` marks the harness's own tamper check): a
ticket passes when its owned checks and every regression check pass on
`main` after its merge; a ticket that did not merge did not pass.

## Metrics and decision rules (pre-registered)

| Question | Number | Passes when |
|---|---|---|
| Q1: factory vs autopilot | repo-mean of the trial-mean check fraction on F1's `main` at the end of E1, minus A0's; 90% CI by bootstrap (2000 resamples, seed 13; trials resampled per arm and repo, checks within each repo's denominator) | the CI's lower bound > 0 and F1 is worse on no repo |
| Q2: the learning loop (the gate) | `metrics.py compare --on <F1 states> --frozen <F0 states>` with the pre-registered detector file, `--order time --window 0`, on E1+E2 (primary) and E1 only; `--detector-set union` as sensitivity | compare's own rule: the 90% CI of RR_frozen - RR_on lies above 0, ER_on < ER_frozen, the on arm's tampering is no higher, its median cost at most 1.25 x frozen's |

Also reported: hidden pass@1 and pass^k for tickets and repos, first-pass
verify per ticket attempt (beside metrics.py's per-issue number), costs
reported and booked (the median per ticket attempt against the $20 kill
line; per merged and passing ticket; per passing check; A0 per repo pass),
tampering (the metrics rule for every arm, a broad rule and the harness's
own check), hidden escapes, false blocks, the rates of questions, retries,
dod-failed, needs-human and voids, and the per-check pass rate.

Pre-registered limits: `learned_check_catches` is 0 by construction (no
import-edge seeds, no reviewer with domain knowledge; that criterion comes
from a product repo); the 30-day merge rate is not measured; where a correct
fix touches guarded paths the gate restores them (real factory behaviour,
visible in the shadow scores); with few ticket clusters, a real repeat-rate
gain below about 0.15 to 0.2 will likely read as no gain.

Infrastructure voids (sandbox start, a network failure or overload before
the first assistant turn, an npm network failure in the runtime setup, a
runner crash) are retried at most twice per session and never booked; a
third makes the attempt infra-failed (excluded, counted, flagged above 5%
per arm). Model outcomes are booked as on GitHub.

## Privacy

- Results stay private: the report is headed PRIVATE, and no number from the
  private repos is published.
- The public harness holds no repo name, task text, check name, hidden-test
  detail, user path or e-mail address. Everything repo-specific is in the
  private `eval.yaml` and the files it names.
- The runner reads only configured paths under `read_roots`, never a path
  with a `forbidden_path_parts` entry, never a `never_bind` file, and writes
  only under its home and the results directory.
- No grader file is ever placed where an agent can read it; canaries
  (strings that exist only in the hidden tests) are checked in every input
  the runner hands an agent.
- `privacy-check` scans the added and changed files of this repository for
  user paths, e-mail addresses, key prefixes, a private denylist and the
  canaries.

## How to run

In WSL, with a venv holding PyYAML and jsonschema and a toolchain dir
holding Linux `node`, `npm` and `claude` (the private runbook has the exact
setup):

```bash
PY=~/.cadence-eval/venv/bin/python
H=<this checkout>/eval/harness/run_eval.py
C=<private folder>/eval.yaml

$PY $H doctor --config $C                      # binaries, sandbox probes
$PY $H prepare --config $C                     # seeds, warm cache, calibration
$PY $H run --config $C --run-id dry1 --agent stub --trials 1 --epochs 2
$PY $H report --config $C --run-id dry1
$PY $H privacy-check --denylist <private>/denylist.txt --canaries <private>/canaries.txt --base <ref>
$PY $H preregister --config $C --out <private folder>/preregistration.json
$PY $H run --config $C --run-id live1 --agent live --confirm-live --budget-usd <N>
```

A live session starts only with `--agent live --confirm-live`, a passing
`doctor --live` (key file at `<home>/secrets/anthropic.key`, mode 600), a
preregistration that matches the current hashes, and `--budget-usd`;
otherwise `run` exits 2 before any session. Before each session the booked
total plus the caps in flight plus this cap must fit the budget; otherwise
nothing new starts and `run` exits 3. `--resume` continues a stopped run:
a ticket that did not finish restarts from its snapshot, and a session it
had already run is replayed from the cache, never paid for twice.

Exit codes: 0 ok, 1 a check failed, 2 bad input or refused, 3 stopped on
budget.

## Layout

```
eval/
  README.md            this page
  harness/
    run_eval.py        the CLI
    config.py          eval.yaml, the private files, the path rules
    workflow.py        the template's pieces, the whitelist, rendering, the verdict
    sandbox.py         the five bubblewrap profiles
    seed.py            prepare: plugin, pinned tools, seeds, calibration
    chain.py           a chain: origin, state, ghstore, ledger, merges, resets, invariants
    steps.py           one ticket, step by step
    learn.py           harvest, learn-record, retro-plan, retro-publish, retro-failed
    agent.py           live and stub sessions, voids, the budget, the session cache
    autopilot.py       arm A0
    ghshim.py          the read-only gh shim
    score.py           hidden scoring
    report.py          the decision rules and the private report
    clock.py           the logical clock
    privacy.py         privacy-check and the canary guard
    schemas/           the private files' and the records' JSON schemas
tests/test_eval_*.py   unit tests; test_eval_e2e_stub.py runs the whole pipeline on
                       tests/fixtures/eval_synthetic (POSIX only, free)
```

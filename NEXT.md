# Next — Cadence

**Updated:** 2026-10-03 · **State:** v0.3.0-rc.2 on `main`, release **held**. Factory mode: phase 1a live in the sandbox, phase 1b (learning loop) built and smoke-tested live; the DoD retry, the retro fallback and SHA pins built, not yet run live; on `factory-1e`, a green run on a failed gate and the learn chain in build runs, now proven live; on `factory-1f`, nested guarded paths (found preparing the product repo), not yet run live; on `factory-1g`, a key check in every model job (live finding in the product repo: an empty `ANTHROPIC_API_KEY` was booked at the cap), not yet run live.

> Update this as the **last commit before you switch away**, not when you return.

## Run it

```bash
/plugin marketplace add nik190799/cadence
/plugin install cadence@cadence
```

Local validation mirrors `.github/workflows/ci.yml`:

```bash
python -c "import json; json.load(open('plugins/cadence/.claude-plugin/plugin.json'))"
python -m pytest tests/ -v
```

## Where I stopped

On 2026-09-30 the decision was made to build **factory mode** inside Cadence
instead of a new project: a labelled GitHub issue becomes a spec you approve,
the agent team builds it, the Definition of Done gate blocks or passes it, and
every failure climbs note → pattern → check. Design: [docs/FACTORY.md](docs/FACTORY.md).
Full reasoning, 24 angles and sources: the
[Cadence Factory decision doc](https://claude.ai/code/artifact/2e1da873-1f03-4602-b3ab-7618ce6e2d56).

The `factory` branch runs end to end in the sandbox (phase 1a, below) and
now carries the learning loop (phase 1b, [docs/LEARNING.md](docs/LEARNING.md)),
which has passed tests, an offline simulation and a live smoke test in the
sandbox (2026-10-02), but no live end-to-end demo yet.

## Do not

- **Do not merge or push anything to `main` until the directory review resolves.**
  The Claude plugin directory tracks `main` and polls it about every 6 hours; any
  new commit becomes a new version that needs its own scan and review. This
  includes PR #5 (verify fix) and Dependabot PRs. Other branches are safe.
- **Do not tag `v0.3.0` or automate the marketplace release.** Still held for the
  directory review.
- **Do not merge `factory` into `main` before the 2026-11-13 gate.** Skill stubs on
  `main` would reach every installed user.
- **Do not publish eval results that use the private assessment repos**; their hidden tests must stay private.
- **Do not charge for Claude usage**, tokens or runs, ever. Users bring their own key.

## Next 3 actions — week 0 (Oct 1–4)

1. ~~**Fix the verify marker.**~~ **Done 2026-09-30** on `fix/verify-marker`
   (`3a1306d`), merged into `factory`. Both verify scripts now write
   `.last_verify_ok`, `.last_verify_sha` and `last_verify.log`; tests in
   `tests/test_verify_markers.py`. PR #5 to `main` is open but **must wait** for
   the directory review (see "Do not").
2. ~~**Check the marketplace submission.**~~ **Checked 2026-10-01.** Submitted in
   the portal (claude.ai/directory/manage) by nik190799 around 2026-09-25: security
   scan passed, in review, Publish requested. Version under review: `v0.3.0-rc.2`
   at `24b88bc`; the portal tracks `main`. Name kept as "Cadence" (decided
   2026-10-01, despite medium trademark risk from Cadence Design Systems).
   Submission-doc fixes and a `plugins/cadence/README.md` are prepared locally on
   `chore/directory-prep`, unpushed, to ship as the first update after approval.
3. ~~**Start phase 1a, the infra spine.**~~ **Done 2026-10-01; gate passed two
   weeks early.** First unattended end-to-end run in the private sandbox repo:
   issue #1 -> spec ($0.23, 12 turns) -> human `/approve` -> build ($0.21, 16 turns)
   -> verify green -> draft PR #2, claim released, ledger booked. The one live
   bug (bubblewrap missing on Ubuntu 24.04 runners) is fixed in `f71c6e0`.
   Sandbox App: `cadence-factory-nik190799`, App ID 5157542, installed on the
   sandbox only. Setup guide: `docs/factory-sandbox-setup.md`.

## Next (phase 1b, from 2026-10-02)

1. ~~**Learning ladder.**~~ **Built 2026-10-02** (`b988643`): observe, harvest,
   optional classify, ladder (note -> pattern -> check -> retire), one retro PR,
   metrics. 1146 tests; offline simulation 46/46; adversarial review found no
   blocker. **Next: the live demo** in the sandbox (plant a `src/db` layer and
   three issues whose natural fix imports it from `src/http`; the third should be
   caught by a learned rule). **Installed in the sandbox and smoke-tested
   2026-10-02** (sandbox `993ee0e`, `e1a6d23`): the sweep reported PR #2 as due,
   learn runs 36971373021 and 36971546373 harvested it once (`no-record`: it
   predates the loop), booked learn records and metrics, and planned 0
   transitions with retro-publish skipped. One live fix: `retro-plan` checks out
   under `repo/`, so its runtime setup must point there.
   **Live demo run 2026-10-02 (sandbox issues #3, #5, #8; maintainer actions by
   Claude for nik190799, except the first `/approve` and `/cadence-forbid`):**
   - #3: the agent's loader in `src/domain` imported `../db/client`; a
     `/cadence-forbid src/domain -> src/db` on PR #4, then close: harvest booked a
     reviewer-command finding with the real line; the ladder kept it a note.
   - #5: the same import again (PR #6, closed unmerged, no command): the ladder
     promoted the class to check `L-f356387a`, proved on #5's real line, and
     retro PR #7 opened with only `.cadence/`, `docs/PATTERNS.md` and a fixture.
     Merged; `decisions/retro-pr-7.json` says landed.
   - #8: the intake spec cited `L-f356387a`, said the issue's design would fail
     the gate, and redesigned with an injected query function; the build's domain
     file has no imports (PR #9, `cadence/verify` green, left open for review).
   - Metrics: 3 attempts, 3 observed; repeats 1 of 2 opportunities (0.5);
     `learned_check_catches` 0, because the rule prevented the repeat at spec time
     instead of catching it at the gate.
   - **Decided 2026-10-02: a spec-time prevention does not count** toward the
     Nov 13 criterion "a retro rule that later caught a real repeat". No kill
     criterion is widened, and it is not added to `learned_check_catches` or
     to the repeat or escape rates. "Cited and absent" is not proof of
     prevention: nothing shows what the agent would have done otherwise, and a
     model writes the spec, so it would control the count. Prevention is
     measured by the rules-on vs rules-frozen eval (RR_frozen − RR_on), which
     already exists. It is now recorded for audit only (built on `factory-1d`,
     not yet run live): `observe` stores `lessons_cited` (the active base lessons
     the approved spec names, checked against `gate`'s `spec_sha256`; `null`
     when unknown); `metrics.py report` adds an informational `lessons_cited`
     block (cited and absent / cited and present); the retro PR body shows it
     on one line marked informational. See "Lessons cited" in
     [docs/LEARNING.md](docs/LEARNING.md).
   - Live bug fixed: `retro-publish` (and `classify` in a dispatch) never ran,
     because GitHub's implicit `success()` skips a job when any upstream job was
     skipped (classify is skipped whenever labelling is off). Both now use
     `!cancelled()` plus an explicit result check; a test pins every job that
     must survive a skipped upstream job.
   - Also seen: Git Bash rewrites a leading `/approve` into a Windows path
     (`MSYS_NO_PATHCONV=1` fixes it); route.py correctly refused the mangled
     comment.
   Review follow-ups:
   - ~~a retro result that fails `verify.sh` fails retro-plan with no PR, and every
     later learn run fails the same way~~ **Closed 2026-10-02** on `factory-1c`:
     retro-plan demotes the plan's checks to patterns and runs `verify.sh` once
     more; a plan that still fails is recorded by the new `retro-failed` job
     (`retro/failed/<plan_sha>.json` on `cadence/state`) and skipped until `main`
     or the plan changes;
   - ~~a directory-index import (`from "../db"`) gets the right class but no
     check~~ **Closed 2026-10-02:** `check_boundaries.py` resolves relative TS/JS
     and Python imports, and a check is proven on up to three samples;
   - `queue: max` on the learn jobs, `gh pr merge --match-head-commit` with the
     App token and real job-output sizes are unproven until it runs live.
2. ~~**One retry on a failed gate.**~~ **Built 2026-10-02** on `factory-1c`, not
   yet run live: `retry.on_dod_fail` (default 1) retries a gate that failed at
   format, lint, boundaries or test once, in the same run (same `/approve`, spec
   and claim; never a new dispatch), from the first patch, with the failed step
   and a cleaned excerpt of the verify log. `retry-gate` checks two `per_run_usd`
   for the run against the daily cap first. Both attempts are observed and
   booked; the retry as `<run>.retry1`. See the wiring in
   [docs/FACTORY.md](docs/FACTORY.md).
3. **Hardening before any real repo:** ~~pin every action to a commit SHA~~
   **done 2026-10-02** in both workflow templates (and `cadence.yml.tmpl` reads
   only); Anthropic identity federation for GitHub Actions is researched in
   [docs/factory-auth.md](docs/factory-auth.md), with no workflow change yet.
   Next: run the retry and a demoted retro plan live in the sandbox.
4. ~~**Show the gate on the PR.**~~ **Done 2026-10-02** (`a64f9ae`): publish posts a
   `cadence/verify` check, green only on the exact tree verify tested. Checked live
   in the sandbox with a scratch run (both outcomes); the sandbox workflow has it
   (`aa1d5b6`). Sandbox PR #2 was reviewed and merged on 2026-10-02.

**2026-10-02 sandbox:** hardening installed (`a1e16fd`); sweep and learn smoke runs green with every action pinned; the DoD retry is not yet proven live.

**2026-10-02, later:**
- Sandbox PR #9 (issue #8, the build that followed L-f356387a) reviewed and merged (`cebd221`).
- `lessons_cited` (`d001039`) installed in the sandbox (`dd2932a`); learn smoke green; metrics unchanged.
- DoD retry, decline path proven live: issue #10 asked for a change to an existing (guarded) test. The intake warned the approver; approved anyway, the agent changed nothing and explained why, the gate failed at `empty`, and retry-gate declined (empty is not retryable) with the reason on the issue. Cost $0.34. The grant path (agent-retry, verify-retry) is still unproven: next test is a fixture the sandbox's `.gitignore` (`*.log`) keeps out of the patch, run after the UTC daily cap resets.
- Rulesets for `cadence/state` and `cadence/retro`: GitHub refuses them on a private repo without GitHub Pro (HTTP 403). Not set.
- Code-of-conduct contact: names the maintainer, @nik190799; a project (non-personal) address is still to choose (`chore/directory-prep`, local, ships with the release).

**2026-10-03 (UTC), retry grant path, second try:** issue #11 asked for a parser whose test reads `test/fixtures/settlement.log`, a name the sandbox's `.gitignore` (`*.log`) keeps out of the patch. The intake flagged it in the spec; the agent added a one-file `.gitignore` exception, the fixture travelled in the patch, and the first attempt passed (PR #12, `cadence/verify` green, $0.61). So the retry was not needed. `lessons_cited` worked live: the observation records `['L-f356387a']`, absent from the attempt. **Finding:** with this intake, the natural first-attempt gate failures we can stage get caught at spec time; the retry's grant path (agent-retry, verify-retry, `.retry1` booking) stays unproven live. Options: wait for a natural failure, or add an eval-sandbox-only fault-injection switch that fails the first gate once (needs a product decision).

**2026-10-02, two live findings in the sandbox; fixed 2026-10-03 on `factory-1e`** (tests and an offline simulation of the job graph; not yet run live):
- **A failed gate turned the run red.** Run 37018582265: the agent produced an empty diff, `verify`'s apply step exited 1, and the run concluded `failure`, so GitHub mailed "Run failed" although the factory had handled it (`dod-failed`, reason on the issue). Fix: `verify` and `verify-retry` stay green whenever the gate reaches a verdict and output `verdict` (pass or fail), an expression in the job's `outputs:` over step outcomes and the `ok` markers of the paths and apply steps, which run before any agent code; the paths and apply steps record a failure and exit 0; only the `verify.sh` step has `continue-on-error`. `retry-gate`, `publish`, `ledger` (`dod`) and `observe` read result and verdict together: a red `verify` job still means "verify did not finish", and the run stays red when the factory breaks. `observe` maps the verdict back to the job-result words `signals.py` always read, so `signals.py` (the detector version) is unchanged. `publish` gains the fixed word `config`.
- **Learning lagged by hours.** GitHub ran the hourly schedule only at 09:44, 16:16, 20:47 and 00:29 UTC. Fix: a build run runs the learn chain itself once `ledger` has booked the attempt, whatever the verdict, as a `stage=learn` dispatch would. Only a human-started build that passed route and the gate gets there; spec runs, label events and plain comments never do; no trigger or dispatch added. `harvest` needs `route`, `ledger` and `reconcile` and uses `!cancelled()` (a cancelled run no longer starts learning); classify's spend is booked as `<run>.learn` so it never collides with the build's own record. Cost: the build run, and the issue's queue, stay open until the chain is done.
- **Next:** install `factory-1e` in the sandbox and see both live: a staged gate failure that ends green with `dod-failed`, and a build whose run ends with harvest, learn-record and retro-plan.

**2026-10-03, both sandbox findings fixed and proven live** (`33073be`, sandbox `6463ed9`): issue #10 re-approved; the agent again changed nothing, so the gate failed at `empty`. Run 37096711149 concluded **success** (no "Run failed" email), #10 got `dod-failed` with the reason, retry-gate declined, ledger booked `dod: fail` ($0.13), and harvest, learn-record and retro-plan ran in the same run (`learn/37096711149-1.json`, 6 observations seen). This also proves job outputs can read `steps.<id>.outcome` on GitHub. The leftover `scratch-verify-check` workflow in the sandbox is disabled.

**2026-10-03, nested guarded paths, found preparing the product repo** (on `factory-1f`; tests only, not yet run live): `learning.guarded_paths` and `learning.test_roots` took only top-level directory names (the paths step checked `^[A-Za-z0-9_.-]{1,64}$`), and a private product repo keeps its tests in `server/tests`, so its existing tests would have been neither restored before the gate nor flagged: an agent could weaken one to pass. Now each entry is a relative directory path (`server/tests`, `web/src/__tests__`): 1 to 6 segments of `[A-Za-z0-9_.-]{1,64}`, no `.` or `..`, no leading or trailing `/`, no glob, at most 16 per list; `.github`, `.cadence`, `scripts` and `tool` stay always guarded; a test root is a guarded path or lies under one, and never under those four (new: `test_roots: [tool]` used to be accepted). `ledger.py` checks first (`route`'s caps step refuses a bad list before any spend), then the paths step in bash, now one script in `route`, `verify` and `verify-retry`. The apply step leaves out new files under a guarded path unless under a test root, by their literal names (`git --literal-pathspecs rm`; a new `tool/[ab].py` no longer takes `tool/a.py` with it), then restores existing files. `observe` flags the same operations (`guarded:server/tests:modify`), named after the deepest guarded path or test root, also guards the four fixed roots when the config omits them, and counts a file under a test root as a test (missing-test, `edit:test-added`, failing tests); `ladder.py`, `metrics.py` (test tampering) and `observation.schema.json` read nested roots. The agent and agent-retry prompts name the configured lists from `route`'s validated outputs instead of a hard-coded `tests/, test/, ...`. **Next:** give the product repo `guarded_paths: [server/tests, tests, .github, .cadence, scripts, tool]` and `test_roots: [server/tests, tests]`, install `factory-1f`, and see a weakened `server/tests` test restored and flagged live.

**2026-10-03, first real product repo** (private, open beta). Setup PR (`19f1bcb`): workflow rendered from `a312862` with the product repo's runtime, gate = ci.yml's server/web/cli checks + the resolve_pass() guard, $2/run, $6/day, `server/tests` guarded (nested guarded paths, `a312862`, were built for this). Local `verify.sh` and the product repo's own CI pass on the branch. Variables and labels set. Starter issues #2-#6: regression tests. **Owner steps before the first ticket:** merge #1, install the App on a private product repo, add the three secrets (a new App private key and possibly a new Anthropic key, since both files were deleted). Then label one issue `factory`.

**2026-10-03, live finding in the product repo: an empty API key spent the day's budget** (fixed on `factory-1g`; tests only, not yet run live). In a private product repo the `ANTHROPIC_API_KEY` secret existed but was empty. Three spec runs failed inside `anthropics/claude-code-action` ("Environment variable validation failed: Either ANTHROPIC_API_KEY, CLAUDE_CODE_OAUTH_TOKEN, or workload identity federation ... is required"). The model was never called, yet `ledger` booked each run at the full per-run cap ($2 each, $6 total, the whole $6 day), so the first real build was refused; the issue got no explanation (the run was red, the issue just kept its `factory` label). Fix:
- **Check first.** `intake`, `agent`, `agent-retry` and `classify` start with one identical step, "Check the Anthropic key is set": the secret reaches it through `env` only, and it reads only the key's length and whether it holds whitespace (never prints, compares or writes the value). A missing, empty or whitespace-holding key writes `key=missing`, prints an `::error::` naming the secret, the fix (`gh secret set ANTHROPIC_API_KEY --repo <owner>/<repo>`) and that a secret can be checked only by its length, and fails the job. The model step runs only on `key=ok`.
- **Book $0, only for that.** Each model job outputs `preflight: no-key` only when that step failed with `key=missing` and the model step was skipped (tier A: written before any model or agent code runs). `ledger` (the run and the retry) and `learn-record` (classify) pass `ledger.py record --preflight no-key` only for a failed job with that exact output and no result file: $0, `cost_source` `preflight:no-key`. `ledger.py` refuses `--preflight` with any outcome but `failure`, with a cost, turns, a result file, a PR or a published sha. Every other unreported cost still books the cap.
- **Say so once.** `publish` (which now also runs when `intake` failed its key check) posts one fixed comment: the factory could not run because `ANTHROPIC_API_KEY` is missing or empty, how to set it, how to check it by length, that no budget was spent, and how to try again; then labels `needs-human` (which also stops the reconciler's spec retry). "The build agent did not finish" is not posted on top. A retry whose key check failed is told in the `dod-failed` report instead (the first attempt did spend).
- Docs: the key check in [docs/FACTORY.md](docs/FACTORY.md); how to check a secret by its length in [docs/factory-sandbox-setup.md](docs/factory-sandbox-setup.md).
- **Next (owner):** set the product repo's key from your own terminal (`gh secret set ANTHROPIC_API_KEY --repo <owner>/<repo>`, paste at the prompt), install `factory-1g` there, and check the next model run prints a length of about 100. Today's three $2 records stay on `cadence/state` (records are never changed), so the day's budget recovers at 00:00 UTC.

## Priority to the Nov 13 gate (updated 2026-10-03)

The phase-1 plan has five steps to the 2026-11-13 kill-or-continue call. The
build steps landed early: the pipeline, intake and approval, caps and ledger,
the reconciler, the learning loop, and a retro PR carrying a self-tested check
are all live, and real-product tickets started ahead of plan. The proving
steps have not started, so they come first now. Targets are flexible, since
AI-built work lands early; Nov 13 is fixed.

| # | Priority | Plan step | Done when | Target (flexible) |
|---|---|---|---|---|
| 1 | Eval harness: the factory against a plain agent run, and rules-on against rules-frozen, on the private assessment repos (same tickets, same model, 3 trials each), scored by their hidden tests | Weeks 1–2 gate; weeks 4–5 | Both comparisons produce numbers for every kill-criteria metric | Start now |
| 2 | One public, measured result | Week 3 gate | A write-up with real numbers that names no private repo (assessment results stay private) | ~Oct 19–23 |
| 3 | Outside proof: replay 10–20 closed issues from one outside public repo, and reach 3 outside repos with a committed `.cadence/cadence.yaml` (needs backlog item 1) | Weeks 4–5 | Replay results recorded; 3 outside repos | ~Nov 6 |
| 4 | 20+ real tickets on a product repo, toward a learned rule that catches a real repeat | Week 3; weeks 4–5 | 20+ tickets, with repeat and escape rates measured | ~Nov 6 |
| 5 | Week-0 leftover: a trademark check on the "Cadence" name | Week 0 | The opinion is recorded | Soon |

Product changes that feed none of these wait until after the gate (see the
backlog below).

## Product backlog: generic fixes for every factory user (added 2026-10-03)

Findings from real repos become product changes that help every factory user,
never one-repo tweaks. This is AI-built work: order and the "done when" check
matter more than dates. The targets are flexible and items land as soon as
they are ready, often well ahead of them. Fixed outside dates still hold, such
as the 2026-11-13 gate.

**Now** (needed for the gate, or for safety):

| # | Item | What every user gets | Done when | Target (flexible) |
|---|---|---|---|---|
| 1 | `/cadence-factory-setup` | One command reads the repo's CI and writes the gate, protects existing tests (nested folders included), sets caps, runs a health check, proposes starter issues and opens the setup PR | A fresh repo goes from install to its first draft PR with no hand edits | Done 2026-10-03: a fresh Flutter repo went from install to its first merged factory PR (a DST bug fix with a regression test, $0.48) with no hand edits to generated files; the lessons (pin the toolchain, a drifted formatter, LF scripts) are in the skills |
| 5 | Privacy check on this repo | CI refuses a commit or commit message that names a private repo or a personal email address | The check runs on every push | Done 2026-10-03: CI job `privacy` on every push and PR, plus a pre-push hook; the deny-list comes from a secret or `gh` at check time, never from the repo |

**After Nov 13** (parked: useful, but outside phase 1):

| # | Item | What every user gets |
|---|---|---|
| 2 | Ticket timing | Per ticket: time on the spec, waiting for approval, build to PR, review to merge; shown in the retro PR body and the weekly summary |
| 3 | Cap advice | The report compares the per-run cap with the repo's real build costs and suggests a cap that lets more builds run at once under the same daily limit. The owner changes it; the factory never writes `factory.yaml` |
| 4 | Autonomy level 2 | Config lets the factory approve its own specs for low-risk labels (tests, docs) once the repo's record qualifies (merged unchanged, no escapes). Off by default |
| 6 | Status issue per repo | A pinned "Factory status" issue, updated by every run and the hourly sweep: runs in progress, recent tickets with timing and cost, quality numbers, failures with reasons, learned rules and tuning advice. Per repo and per rule, never per developer |
| 7 | Fleet page | One private page across all of a user's repos, built by a Cadence command from each repo's ledger |
| 8 | Hosted portal | Live runs, team and organization views and tamper-evident evidence. Needs a server, so it waits for the hosted phase and its gates |

What the live runs showed: a ticket's machine time is about 6.5 minutes (spec
about 2, build to PR about 4.5), and most of the elapsed time is waiting for
approval and review. The daily-cap check reserves the full per-run cap for
every build in flight, so a cap far above real build costs limits how many
builds can run at once.

## Fleet review (weekly on Fridays, or after every 20 real tickets)

Look across every connected repo from the same six angles:

- **Speed:** ticket timing, where the time goes.
- **Cost:** per ticket, and caps against real spend.
- **Quality:** merged unchanged, repeat rate, escapes.
- **Safety:** guard hits, refused patches, blocked or failed jobs.
- **Setup effort:** time and hand edits needed to connect a repo.
- **Privacy:** nothing private in public places.

The output is generic backlog items only, added to the table above. Repo
names and per-repo numbers stay in the private review notes, never in this
file.

## Kill criteria (2026-11-13)

Stop if the rules-on vs rules-frozen eval shows no gain, or if two of these miss:
agent PRs merged within 30 days ≥ 50%; median cost per ticket ≤ $20; ≥ 3 outside
public repos with a committed `.cadence/cadence.yaml`; ≥ 1 retro rule that later
caught a real repeat.

## Known broken

- `docs/case-studies/flutter-sandbox.md` links to `agent_teams_sandbox`, which was
  never pushed (404). Push it or drop the link.

## Still worth doing (release-independent)

- Reposition the README as spec-driven development, and lead with the compliance
  report.
- The npx CLI for Codex, Gemini CLI and Cline users.
- Cost routing per role, and context budgets in the Definition of Done.

## Dogfood targets

Phase 1: the three private assessment TypeScript repos (hidden tests vs the
autopilot baseline), then two private products (Flutter and Python), then one outside public repo replayed in a fork.

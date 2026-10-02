# Next — Cadence

**Updated:** 2026-10-02 · **State:** v0.3.0-rc.2 on `main`, release **held**. Factory mode: phase 1a live in the sandbox, phase 1b (learning loop) built and smoke-tested live; the DoD retry, the retro fallback and SHA pins built, not yet run live.

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
   weeks early.** First unattended end-to-end run in `nik190799/cadence-eval-sandbox`:
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
   under `repo/`, so its runtime setup must point there. Review follow-ups:
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
autopilot baseline), then `a private product` (Flutter) and `a private product repo`
(Python), then one outside public repo replayed in a fork.

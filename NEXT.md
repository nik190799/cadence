# Next — Cadence

**Updated:** 2026-09-30 · **State:** v0.3.0-rc.2 on `main`, release **held**. Factory mode started on the `factory` branch.

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

The `factory` branch holds a scaffold only: two skill stubs, a workflow
skeleton, three tool stubs and the eval plan. Nothing is functional yet.

## Do not

- **Do not merge or push anything to `main` until the directory review resolves.**
  The Claude plugin directory tracks `main` and polls it about every 6 hours; any
  new commit becomes a new version that needs its own scan and review. This
  includes PR #5 (verify fix) and Dependabot PRs. Other branches are safe.
- **Do not tag `v0.3.0` or automate the marketplace release.** Still held for the
  directory review.
- **Do not merge `factory` into `main` before the 2026-11-13 gate.** Skill stubs on
  `main` would reach every installed user.
- **Do not publish eval results that use the `intern-assessment` repos** while
  hiring runs; their hidden tests must stay private.
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

1. **Learning ladder:** retro findings from runs -> notes/patterns -> a check that
   must fire on its sample, delivered as a PR to `.cadence/`.
2. **One retry on a failed gate,** feeding the verify log back to the agent
   (TODO in the publish job).
3. **Hardening before any real repo:** pin every action to a commit SHA; research
   Anthropic identity federation for GitHub Actions to replace the stored API key.

## Kill criteria (2026-11-13)

Stop if the rules-on vs rules-frozen eval shows no gain, or if two of these miss:
agent PRs merged within 30 days ≥ 50%; median cost per ticket ≤ $20; ≥ 3 outside
public repos with a committed `.cadence/cadence.yaml`; ≥ 1 retro rule that later
caught a real repeat.

## Known broken

- `docs/case-studies/flutter-sandbox.md` links to `agent_teams_sandbox`, which was
  never pushed (404). Push it or drop the link.
- `templates/.github/workflows/cadence.yml.tmpl` has no `permissions:` block and
  pins actions by tag, not SHA.

## Still worth doing (release-independent)

- Reposition the README as spec-driven development, and lead with the compliance
  report.
- The npx CLI for Codex, Gemini CLI and Cline users.
- Cost routing per role, and context budgets in the Definition of Done.

## Dogfood targets

Phase 1: the three `intern-assessment` TypeScript repos (hidden tests vs the
autopilot baseline), then `personal-assistant` (Flutter) and `backroom`
(Python), then one outside public repo replayed in a fork.

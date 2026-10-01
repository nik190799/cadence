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

- **Do not tag `v0.3.0` or automate the marketplace release.** Still held for the
  marketplace submission.
- **Do not merge `factory` into `main` before the 2026-11-13 gate.** Skill stubs on
  `main` would reach every installed user.
- **Do not publish eval results that use the `intern-assessment` repos** while
  hiring runs; their hidden tests must stay private.
- **Do not charge for Claude usage**, tokens or runs, ever. Users bring their own key.

## Next 3 actions — week 0 (Oct 1–4)

1. **Fix the verify marker.** `scripts/verify.sh` never writes
   `.cadence/.last_verify_ok`, which `compliance_report.py` needs before any
   control can read "implemented". Small, and it unblocks the audit packet.
2. **Check the marketplace submission.** The guide offered the now-retired Console
   form; if that was used, redo it at claude.ai/directory/manage. Run a trademark
   check on "Cadence" first (Uber's Cadence workflow engine is a CNCF project).
3. **Start phase 1a, the infra spine:** per-user GitHub App identity with loop
   guards, the agent/verify/publish job split, `claim.py`, and `ledger.py` with a
   hard cap. Specs are in each stub's docstring.

## Kill criteria (2026-11-13)

Stop if the rules-on vs rules-frozen eval shows no gain, or if two of these miss:
agent PRs merged within 30 days ≥ 50%; median cost per ticket ≤ $20; ≥ 3 outside
public repos with a committed `.cadence/cadence.yaml`; ≥ 1 retro rule that later
caught a real repeat.

## Known broken

- `verify.sh` does not write `.cadence/.last_verify_ok` (see action 1).
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

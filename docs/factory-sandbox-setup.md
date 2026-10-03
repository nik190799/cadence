---
layout: default
title: Factory sandbox setup
---

# Factory sandbox setup

Setup for the first live run of [factory mode](FACTORY.md) in
`nik190799/cadence-eval-sandbox` (private). You do every step yourself in
the GitHub UI, the Claude Console or your own terminal. No key or token
ever goes into a chat, an issue, a commit or a workflow file.

## 1. Register the GitHub App

1. Open <https://github.com/settings/apps/new> (your account, not an
   organization).
2. Fill in:
   - **GitHub App name:** `cadence-factory-nik190799`
   - **Homepage URL:** `https://github.com/nik190799/cadence`
   - **Webhook:** clear **Active**. Leave the webhook URL empty.
3. **Repository permissions**, set exactly these:

   | Permission | Access | Used for |
   |---|---|---|
   | Actions | Read and write | Reading run status; `workflow_dispatch` retries |
   | Contents | Read and write | Claim refs, `cadence/issue-N`, `cadence/state`, `cadence/retro` |
   | Issues | Read and write | Labels, comments and issue events (reconciler) |
   | Pull requests | Read and write | Opening the draft PR and the retro PR |
   | Metadata | Read-only | Required; selected automatically |

   Leave every other repository permission at **No access**. In
   particular, do not grant **Workflows**: without it, the App cannot push
   changes to `.github/workflows/`, by design.
4. **Organization permissions** and **Account permissions:** none.
5. **Subscribe to events:** none.
6. **Where can this GitHub App be installed?** **Only on this account**.
7. Click **Create GitHub App**.
8. On the App's **General** page, note the **App ID** (a number). The
   App's bot login is its slug plus `[bot]`: `cadence-factory-nik190799[bot]`.
   Confirm the slug at `https://github.com/apps/cadence-factory-nik190799`.

## 2. Generate the private key

1. On the same **General** page, under **Private keys**, click
   **Generate a private key**. A `.pem` file downloads.
2. Keep that file only until step 5. Do not move it into any repository.

## 3. Install the App on the sandbox only

1. In the App settings, open **Install App** and click **Install** next to
   `nik190799`.
2. Choose **Only select repositories** and pick
   `nik190799/cadence-eval-sandbox`. Nothing else.
3. Click **Install**.

## 4. Create a dedicated Anthropic API key

1. In the Claude Console (platform.claude.com, formerly
   console.anthropic.com), create a workspace for the sandbox, for example
   `cadence-sandbox`.
2. Set a low monthly spend limit on that workspace.
3. Create an API key in that workspace. Use it nowhere else.

## 5. Add the secrets and variables to the sandbox repo

In `nik190799/cadence-eval-sandbox`: **Settings → Secrets and variables →
Actions**.

**Secrets** (tab **Secrets**, **New repository secret**):

| Name | Value |
|---|---|
| `CADENCE_APP_ID` | The App ID from step 1.8 |
| `CADENCE_APP_PRIVATE_KEY` | The whole `.pem` file, including the `BEGIN` and `END` lines |
| `ANTHROPIC_API_KEY` | The key from step 4 |

Or from your own terminal, which keeps the values out of the clipboard
for the key file:

```bash
R=nik190799/cadence-eval-sandbox
gh secret set CADENCE_APP_ID --repo "$R"            # prompts for the value
gh secret set CADENCE_APP_PRIVATE_KEY --repo "$R" < path/to/the-downloaded.private-key.pem
gh secret set ANTHROPIC_API_KEY --repo "$R"         # prompts for the value
```

Then delete the downloaded `.pem` (or keep it only in a password
manager).

**Variables** (tab **Variables**, **New repository variable**):

| Name | Value | Why |
|---|---|---|
| `CADENCE_BOT_LOGIN` | `cadence-factory-nik190799[bot]` | The App's identity for the loop guard and for runs the App dispatches |
| `CADENCE_PLUGIN_REF` | `factory` | The `cadence-intake` skill exists only on the `factory` branch until it merges |

```bash
gh variable set CADENCE_BOT_LOGIN --repo "$R" --body 'cadence-factory-nik190799[bot]'
gh variable set CADENCE_PLUGIN_REF --repo "$R" --body factory
```

**`CADENCE_EVAL_SANDBOX`: eval repos only.** It lets the learning loop
merge its own retro PR, and only when `.cadence/factory.yaml` also says
`learning.mode: eval-sandbox` and the repository is private (three switches
in three places). It exists for the phase 1c rules-on arm, whose scripted
runs have no human to merge. Do **not** set it in
`cadence-eval-sandbox` for the live demo below (a human merges the retro
PR there), and never in a real repository.

```bash
# Phase 1c eval repos (on arm) only:
gh variable set CADENCE_EVAL_SANDBOX --repo "$EVAL_REPO" --body true
```

## 6. Create the labels

```bash
gh label create factory     --repo "$R" --color 5319E7 --description "Cadence factory: write a spec"
gh label create spec-ready  --repo "$R" --color 0E8A16 --description "Cadence factory: spec posted, waiting for /approve"
gh label create building    --repo "$R" --color FBCA04 --description "Cadence factory: build in progress"
gh label create pr-open     --repo "$R" --color 1D76DB --description "Cadence factory: draft PR open"
gh label create dod-failed  --repo "$R" --color D93F0B --description "Cadence factory: Definition of Done failed"
gh label create needs-human --repo "$R" --color B60205 --description "Cadence factory: stuck, needs a person"
```

Or create the same six names under **Issues → Labels → New label**.

## 7. Add the factory files to the sandbox

The sandbox must already have `/cadence-init` applied (`.cadence/cadence.yaml`,
`scripts/verify.sh`, `tool/check_boundaries.py`). From a checkout of
`nik190799/cadence` on the `factory` branch, copy into the sandbox's
default branch:

| From `plugins/cadence/` | To the sandbox |
|---|---|
| `templates/.github/workflows/cadence-factory.yml.tmpl` | `.github/workflows/cadence-factory.yml` |
| `templates/tool/route.py`, `intake_sanitize.py`, `claim.py`, `ledger.py`, `reconcile.py` | `tool/` |
| `templates/tool/signals.py`, `ladder.py`, `metrics.py`, `emit_rule.py`, `check_boundaries.py` (the learning loop; the last two replace the `/cadence-init` copies) | `tool/` |
| `schemas/retro.schema.json`, `observation.schema.json`, `classify.schema.json`, `lessons.schema.json`, `retro-plan.schema.json`, `metrics.schema.json`, `cadence-yaml.schema.json` | `.cadence/` (the tools look there first) |
| `templates/factory.yaml.tmpl` | `.cadence/factory.yaml` |
| the last section of `templates/docs/PATTERNS.md.tmpl` (`## Learned patterns (factory)` and the line under it) | the end of `docs/PATTERNS.md` |

From a checkout of the `factory` branch, in the sandbox clone:

```bash
C=path/to/cadence/plugins/cadence
cp "$C/templates/.github/workflows/cadence-factory.yml.tmpl" .github/workflows/cadence-factory.yml
for t in route intake_sanitize claim ledger reconcile signals ladder metrics emit_rule check_boundaries; do
  cp "$C/templates/tool/$t.py" tool/
done
for s in retro observation classify lessons retro-plan metrics cadence-yaml; do
  cp "$C/schemas/$s.schema.json" .cadence/
done
```

Then add the project's runtime setup (for a Node project, `actions/setup-node`
and `npm ci`) at each "Add your stack's runtime setup here" comment: in
`agent`, `agent-retry`, `verify`, `verify-retry` and `retro-plan` (which
checks out under `repo/`). In `verify` and `verify-retry`, give each of those
steps `if: steps.apply.outputs.ok == 'true'`, so it is skipped once the gate
has already failed (an empty or rejected patch). A setup step that fails there
fails the job, which every later job reads as "verify did not finish".

In `.cadence/factory.yaml`, set sandbox-sized caps, for example
`per_run_usd: 2.00` and `daily_usd: 6.00`, and keep the `learning:` block
with its defaults: `mode: "on"` (quoted), `classify: false`. The sandbox
keeps its tests in `test/`, which the default `guarded_paths` and
`test_roots` already cover. A project whose tests live deeper lists the
directory itself, as a relative path (1 to 6 segments, no `.` or `..`, no
glob, at most 16 per list). For tests in `server/tests`:

```yaml
learning:
  mode: "on"
  guarded_paths: [server/tests, tests, .github, .cadence, scripts, tool]
  test_roots: [server/tests, tests]
```

Existing files under a guarded path are restored before the gate; new
files there are kept only under a test root. Keep `tests` guarded: it
holds `tests/fixtures/retro/`.

The learning loop adds two things the project's own tooling must accept:

- **`tests/fixtures/retro/`** holds the real failing samples that prove
  each learned check (a file such as
  `tests/fixtures/retro/1a2b3c4d/src/domain/invoice.ts` that imports
  `src/db` on purpose, with a `// @ts-nocheck` header). The boundary
  checker skips it; exclude it from lint, type-check and test discovery too
  (for example `ignorePatterns` in ESLint, `exclude` in `tsconfig.json`
  and in the Vitest or Jest config, `--ignore=tests/fixtures/retro` for
  pytest). Otherwise `verify.sh` fails on the retro PR.
- **Generated files must be git-ignored.** The retro job runs `verify.sh`
  and then refuses any file outside its allowlist, so build output
  (`node_modules/`, `dist/`, `coverage/`) must be in `.gitignore`.

Commit and push to the default branch: issue events always run the
workflow from there.

## 8. Check everything in the GitHub UI

- **App → Permissions & events:** exactly the five repository permissions
  above, no organization or account permissions, webhook inactive.
- **<https://github.com/settings/installations> → the App:** repository
  access lists only `cadence-eval-sandbox`.
- **Sandbox → Settings → Secrets and variables → Actions:** three secrets,
  two variables (GitHub shows the names only).
- **Sandbox → Settings → Actions → General:** leave **Workflow
  permissions** at read-only, and leave **Allow GitHub Actions to create
  and approve pull requests** unticked (the App opens the PRs).
- **Sandbox → Settings → Rules / Branches:** no rule that stops the App
  from creating or deleting `cadence/*` branches, and `cadence/verify` not
  listed as a required check (a commit a human pushes to a factory branch
  never gets one). Factory PRs show it beside your CI.
- **Recommended: rulesets for the factory's own branches** (**Settings →
  Rules → Rulesets → New branch ruleset**, enforcement **Active**, targets
  added under **Include by pattern**):

  | Ruleset | Targets | Rules | Bypass list (**Always allow**) |
  |---|---|---|---|
  | `cadence state` | `cadence/state` | Restrict creations, Restrict updates, Restrict deletions, Block force pushes | the App |
  | `cadence retro` | `cadence/retro` | Restrict creations, Restrict updates, Restrict deletions | the App, Repository admin |

  Then only the App writes the ledger, which every metric is computed
  from. `cadence/retro` keeps force pushes, because the retro job
  force-pushes it with a lease; the admin bypass lets a maintainer push a
  commit that deletes an entry from the retro PR (which rejects that
  lesson). After a human push, the factory leaves the branch alone until
  the PR is merged or closed.
- **Sandbox → Issues → Labels:** the six labels.
- **Smoke test with no model spend:** **Actions → cadence-factory → Run
  workflow**, stage `reconcile`. The `reconcile` job should mint the App
  token and finish green, and its "Is a learn run due?" step should print
  `{"learn_due": ...}`. Then run stage `learn`: `harvest`, `learn-record`
  and `retro-plan` should finish green (`classify` and `retro-publish`
  skip while there is nothing to label or propose), and `cadence/state`
  should gain a `learn/` marker.

## 9. Live demo of the learning loop (phase 1b)

The sandbox is TypeScript with one seed boundary rule (`src/domain` must
not import `src/http`). The demo plants a second boundary that no rule
knows yet, and shows the loop learning it from a human and enforcing it.

1. **Plant the layer.** Add `src/db/client.ts` (a small query helper) on
   the default branch, and nothing in `.cadence/cadence.yaml` about it.
2. **Issue 1: a note.** Open an issue whose natural fix in `src/domain/`
   reads data (for example "orders: load an order by id"), add `factory`,
   `/approve` the spec. When the agent's draft PR imports `src/db` from
   `src/domain`, comment on the PR, on its own line:

   ```text
   /cadence-forbid src/domain -> src/db
   ```

   then close the PR without merging. The next sweep or the next build's
   learn chain harvests it (or at once: **Run workflow**, stage `learn`). The
   class `import-edge:src/domain->src/db` is now seeded, seen on one
   issue: a note, so no retro PR yet (`promote_after: 2`).
3. **Issue 2: a check.** A second issue that tempts the same edge (for
   example "invoices: load an invoice by id"). If the agent imports
   `src/db` from `src/domain` again, the learn chain at the end of that
   build run (or the next learn run) opens a retro PR
   from `cadence/retro` with one check `L-xxxxxxxx`: the rule in
   `.cadence/cadence.yaml`, its lesson in `.cadence/lessons.yaml`, a line
   in `docs/PATTERNS.md`, and the fixture
   `tests/fixtures/retro/xxxxxxxx/src/domain/<file>.ts` built from run 2's
   real line. Review it and merge it.
4. **Issue 3: the payoff.** A third such issue. Either the agent avoids
   the edge (the spec now cites the lesson: no repeat), or `verify` blocks
   it at the boundaries step (`learned_check_catches` = 1).
5. **Measure.** Download the newest `reports/metrics-<date>.json` from
   `cadence/state`, or run
   `python tool/metrics.py report --state-dir <a checkout of cadence/state> --repo-root . --out metrics.json`.
   At this size the repeat rate reports `insufficient` (fewer than 30
   opportunities); that is expected.

## Never

- Paste the private key, the API key or any token into a chat (with
  Claude or anyone), an issue, a commit, a workflow file or a log.
- Share or commit the `.pem`. If it leaks, open the App's **General**
  page, delete that key, generate a new one and update the secret.
- Install the App on any other repository, or on **All repositories**.
- Grant the App **Workflows**, **Administration**, **Secrets** or any
  account permission, or turn its webhook on.
- Reuse a personal or production Anthropic key. Use the dedicated key
  with its spend limit.
- Enable the workflow on a real repository before every action in it is
  pinned to a full commit SHA.
- Set `CADENCE_EVAL_SANDBOX` or `learning.mode: eval-sandbox` anywhere but
  a private eval repository: together they let the retro PR merge itself.

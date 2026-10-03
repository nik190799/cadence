---
layout: default
title: Factory setup
---

# Factory setup

How to connect a repository to [factory mode](FACTORY.md). Use the setup
skill; the manual steps it automates are kept as
[Appendix A](#appendix-a-manual-setup). Either way, you set every secret
yourself, in the GitHub UI or your own terminal: no key or token ever goes
into a chat, an issue, a commit or a workflow file.

## With the setup skill

In Claude Code, from a checkout of the repository, with the Cadence plugin
installed:

```text
/cadence-factory-setup
```

The skill
(`plugins/cadence/skills/cadence-factory-setup/SKILL.md`) does, asking
before every write to GitHub:

1. Creates a worktree on a new branch off **origin's** default branch (a
   local default branch can hold commits that were never pushed).
2. Reads the repository's own CI, markers and test folders, and proposes
   the gate commands for `.cadence/cadence.yaml`, one command per line
   (`scripts/verify.sh` runs each line on its own), mirroring CI.
3. Proposes a stack profile, `.cadence/factory-stack.yaml`, and writes
   `.cadence/factory.yaml`: the budget (`per_run_usd`, `daily_usd`),
   `max_turns`, `retry`, `autonomy: pr-only`, and the learning loop's
   `guarded_paths` and `test_roots`, nested ones included (`server/tests`).
4. Renders `.github/workflows/cadence-factory.yml` with
   `render_factory_workflow.py` (below), copies the factory tools, the
   schemas and `scripts/verify.sh`, and runs `verify.sh` locally.
5. Creates the six labels and the variables `CADENCE_BOT_LOGIN` and
   `CADENCE_PLUGIN_REF` with `gh`, and opens a setup PR.
6. Walks you through the GitHub App: reuse one you already own (install it
   on this repository) or register one with the permissions in
   [A.1](#1-register-the-github-app).
7. Prints the secret commands for **you** to run, in PowerShell and bash
   (below). It never reads, prints or sets a key.
8. After you merge: a no-spend smoke test (`stage=reconcile`), then your
   first ticket.

### The stack profile and the renderer

The workflow template has five runtime setup slots: `agent` and
`agent-retry` (so the agent can run the tests), `verify` and
`verify-retry` (the Definition of Done gate) and `retro-plan` (which runs
`verify.sh` on a learned check). What goes in them differs per stack and
per folder. `plugins/cadence/templates/tool/render_factory_workflow.py`
fills them from a small profile:

```yaml
# .cadence/factory-stack.yaml: a Python service in server/ and a Vite web app in web/
python:
  version: "3.12"
  requirements: [server/requirements-dev.txt]
node:
  version: "20"
  dirs: [web]
  lockfiles: [web/package-lock.json]
```

```bash
python <plugin>/templates/tool/render_factory_workflow.py \
  --profile .cadence/factory-stack.yaml --out .github/workflows/cadence-factory.yml
```

A single-package TypeScript repo needs only `node: {version: "20", dirs:
["."]}`; anything else (Go, a system package, a build step) goes under
`custom:` as a step, and a `uses:` there must be pinned to a 40-character
commit SHA with its `tag`. The tool:

- names every step it adds `Stack: ...`;
- in `verify` and `verify-retry`, gives each step
  `if: steps.apply.outputs.ok == 'true'`, so nothing runs once the gate has
  failed on an empty or refused patch, and puts the same steps in both;
- in `retro-plan`, which checks the repository out under `repo/`, points
  each step there and runs it only when the ladder plan changed;
- sets up a Python other than the template's 3.12 after the template's own,
  with the factory tools' PyYAML and jsonschema;
- refuses unpinned actions, a second SHA for one action, `${{ }}`, secrets,
  `continue-on-error`, checkout and artifact actions, and anything else that
  would weaken the workflow; then checks that every template step is
  unchanged. Nothing is written on a refusal.

The dependency installs in `verify` run on the patched tree: like
`verify.sh` itself, they run agent code, in a job with no secrets. They run
before `verify.sh`, outside the one step that may fail without failing the
job, so a patch that breaks its own install (a `package.json` out of step
with its lockfile, a requirement that does not exist) ends the gate as
"verify did not finish" (a red run, reported as `timeout`, not retried)
rather than as a failed gate. To
change the runtime later, edit the profile, render again and open a PR;
never edit the slots by hand.

### Secrets: what works

You add three secrets: `CADENCE_APP_ID`, `CADENCE_APP_PRIVATE_KEY` and
`ANTHROPIC_API_KEY`. In real setups a hidden prompt (`gh secret set NAME`
with no value) and clipboard pipelines silently stored **empty** secrets,
twice. Two ways worked every time:

1. **The GitHub web form:** Settings, Secrets and variables, Actions, New
   repository secret.
2. **A file read by a small script:** save the key with a text editor into a
   file outside any repository, run the lines below, then delete the file.
   They print only the length and store nothing when the value is empty or
   holds whitespace.

PowerShell (full paths; a missing file stores nothing):

```powershell
$R = 'OWNER/REPO'
gh secret set CADENCE_APP_ID --repo $R --body 'APP_ID'          # the App ID number
$p = $null; $p = [IO.File]::ReadAllText('C:\path\to\app.private-key.pem')
if ($p -notmatch '-----BEGIN [A-Z ]*PRIVATE KEY-----') { 'Not stored: that file is not a PEM private key.' }
else { gh secret set CADENCE_APP_PRIVATE_KEY --repo $R --body $p; if ($LASTEXITCODE -eq 0) { "CADENCE_APP_PRIVATE_KEY stored: $($p.Length) characters." } }
$k = $null; $k = ([IO.File]::ReadAllText('C:\path\to\anthropic-key.txt')).Trim()
if ($k.Length -eq 0 -or $k -match '\s') { "Not stored: empty or holds whitespace (length $($k.Length))." }
else { gh secret set ANTHROPIC_API_KEY --repo $R --body $k; if ($LASTEXITCODE -eq 0) { "ANTHROPIC_API_KEY stored: $($k.Length) characters." } }
Remove-Variable p, k
```

bash:

```bash
R=OWNER/REPO
gh secret set CADENCE_APP_ID --repo "$R" --body 'APP_ID'           # the App ID number
pem=/path/to/app.private-key.pem
if grep -q -- '-----BEGIN [A-Z ]*PRIVATE KEY-----' "$pem"; then
  gh secret set CADENCE_APP_PRIVATE_KEY --repo "$R" < "$pem" && echo "CADENCE_APP_PRIVATE_KEY stored: $(wc -c < "$pem") bytes."
else echo "Not stored: $pem is not a PEM private key."; fi
k=$(tr -d '\r\n' < /path/to/anthropic-key.txt)
case "$k" in
  ''|*[[:space:]]*) echo "Not stored: empty or holds whitespace (length ${#k})." ;;
  *) printf '%s' "$k" | gh secret set ANTHROPIC_API_KEY --repo "$R" && echo "ANTHROPIC_API_KEY stored: ${#k} characters." ;;
esac
unset k pem
```

PowerShell never pipes a value into `gh` here: a pipeline whose file read
fails still starts `gh` with empty input, which stores an empty secret,
and Windows PowerShell 5.1 can put a byte-order mark in front of piped
text, which breaks the `.pem`. Both values go in as `--body`.

**Check a secret by its length.** GitHub never shows a secret's value
again, to anyone: the UI and `gh secret list` give only its name and when
it was last updated, and a secret that exists can still be empty. So the
only check is its length, which never reveals the key. Every model job
(`intake`, `agent`, `agent-retry`, `classify`) prints it in its first step,
"Check the Anthropic key is set", for example
`ANTHROPIC_API_KEY is set: 108 characters, no whitespace.` An Anthropic API
key is about 100 characters, with no spaces or line breaks. When the length
is 0 (the secret is missing or empty) or the key holds whitespace (a line
break pasted with it, say), that step fails the job before the model is
called: the ledger books the run at $0, and the factory comments on the
issue with the fix and labels it `needs-human`. It cannot tell a wrong or
revoked key, which still fails inside `claude-code-action` and is booked at
the full per-run cap. (Seen live in a product repository, 2026-10-03: the
secret existed but was empty, three spec runs failed, and before the key
check each was booked at the full $2 cap.)

Factory lite, a design for running with `GITHUB_TOKEN` and one secret
instead of three, is in [factory-lite.md](factory-lite.md).

## Appendix A: manual setup

What the skill automates, for reference or for a setup without Claude Code.
`OWNER/REPO` is the repository; `cadence-factory-<owner>` is an example App
name.

### 1. Register the GitHub App

1. Open <https://github.com/settings/apps/new> (your account, not an
   organization). To reuse an App you already own instead, skip to step 3
   and add this repository to its installation.
2. Fill in:
   - **GitHub App name:** `cadence-factory-<owner>`
   - **Homepage URL:** the repository's URL
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
   App's bot login is its slug plus `[bot]`: `cadence-factory-<owner>[bot]`.
   Confirm the slug at `https://github.com/apps/<slug>`.

### 2. Generate the private key

1. On the same **General** page, under **Private keys**, click
   **Generate a private key**. A `.pem` file downloads. A new key does not
   revoke the older ones.
2. Keep that file only until step 5. Do not move it into any repository.

### 3. Install the App on this repository only

1. In the App settings, open **Install App** and click **Install** (or
   **Configure**, for an App already installed elsewhere) next to your
   account.
2. Choose **Only select repositories** and pick `OWNER/REPO` (and the
   repositories it already serves, if you reuse it).
3. Click **Install** or **Save**.

### 4. Create a dedicated Anthropic API key

1. In the Claude Console (platform.claude.com, formerly
   console.anthropic.com), create a workspace for the repository, for
   example `cadence-factory`.
2. Set a low monthly spend limit on that workspace.
3. Create an API key in that workspace. Use it nowhere else.

### 5. Add the secrets and variables

**Secrets** (**Settings → Secrets and variables → Actions**, tab
**Secrets**): `CADENCE_APP_ID` (the App ID from step 1.8),
`CADENCE_APP_PRIVATE_KEY` (the whole `.pem` file, including the `BEGIN` and
`END` lines) and `ANTHROPIC_API_KEY` (the key from step 4). Use the web form
or the file scripts in [Secrets: what works](#secrets-what-works), not a
prompt or the clipboard. Then delete the downloaded `.pem` (or keep it only
in a password manager).

**Variables** (tab **Variables**):

| Name | Value | Why |
|---|---|---|
| `CADENCE_BOT_LOGIN` | `<slug>[bot]` | The App's identity for the loop guard, for runs the App dispatches and for the learning loop |
| `CADENCE_PLUGIN_REF` | `factory` | The plugin ref the workflow loads its skills from; the factory skills are on the `factory` branch until it merges (a commit SHA pins it) |

```bash
R=OWNER/REPO
gh variable set CADENCE_BOT_LOGIN --repo "$R" --body '<slug>[bot]'
gh variable set CADENCE_PLUGIN_REF --repo "$R" --body factory
```

**`CADENCE_EVAL_SANDBOX`: eval repos only.** It lets the learning loop
merge its own retro PR, and only when `.cadence/factory.yaml` also says
`learning.mode: eval-sandbox` and the repository is private (three switches
in three places). It exists for the phase 1c rules-on arm, whose scripted
runs have no human to merge. Never set it in a real repository, nor in a
sandbox where a human merges the retro PR.

```bash
# Phase 1c eval repos (on arm) only:
gh variable set CADENCE_EVAL_SANDBOX --repo "$EVAL_REPO" --body true
```

### 6. Create the labels

```bash
gh label create factory     --repo "$R" --color 5319E7 --description "Cadence factory: write a spec"
gh label create spec-ready  --repo "$R" --color 0E8A16 --description "Cadence factory: spec posted, waiting for /approve"
gh label create building    --repo "$R" --color FBCA04 --description "Cadence factory: build in progress"
gh label create pr-open     --repo "$R" --color 1D76DB --description "Cadence factory: draft PR open"
gh label create dod-failed  --repo "$R" --color D93F0B --description "Cadence factory: Definition of Done failed"
gh label create needs-human --repo "$R" --color B60205 --description "Cadence factory: stuck, needs a person"
```

Or create the same six names under **Issues → Labels → New label**.

### 7. Add the factory files

The repository must already have `/cadence-init` applied
(`.cadence/cadence.yaml`, `scripts/verify.sh`, `tool/check_boundaries.py`).
Work on a branch off origin's default branch and open a PR. From a checkout
of `nik190799/cadence` on the `factory` branch:

| From `plugins/cadence/` | To the repository |
|---|---|
| `templates/.github/workflows/cadence-factory.yml.tmpl`, rendered (below) | `.github/workflows/cadence-factory.yml` |
| `templates/tool/route.py`, `intake_sanitize.py`, `claim.py`, `ledger.py`, `reconcile.py` | `tool/` |
| `templates/tool/signals.py`, `ladder.py`, `metrics.py`, `emit_rule.py`, `check_boundaries.py` (the learning loop; the last two replace the `/cadence-init` copies) | `tool/` |
| `schemas/retro.schema.json`, `observation.schema.json`, `classify.schema.json`, `lessons.schema.json`, `retro-plan.schema.json`, `metrics.schema.json`, `cadence-yaml.schema.json` | `.cadence/` (the tools look there first) |
| `templates/factory.yaml.tmpl` | `.cadence/factory.yaml` |
| the last section of `templates/docs/PATTERNS.md.tmpl` (`## Learned patterns (factory)` and the line under it) | the end of `docs/PATTERNS.md` |

```bash
C=path/to/cadence/plugins/cadence
for t in route intake_sanitize claim ledger reconcile signals ladder metrics emit_rule check_boundaries; do
  cp "$C/templates/tool/$t.py" tool/
done
for s in retro observation classify lessons retro-plan metrics cadence-yaml; do
  cp "$C/schemas/$s.schema.json" .cadence/
done
```

**The runtime setup.** Render the workflow from a stack profile
([above](#the-stack-profile-and-the-renderer)) rather than editing it:

```bash
python "$C/templates/tool/render_factory_workflow.py" \
  --profile .cadence/factory-stack.yaml --out .github/workflows/cadence-factory.yml
```

By hand, the same rules apply: add the project's runtime setup (for a Node
project, `actions/setup-node` pinned to a commit SHA and `npm ci`) at each
"Add your stack's runtime setup here" comment: in `agent`, `agent-retry`,
`verify`, `verify-retry` and `retro-plan` (which checks out under `repo/`,
so `cache-dependency-path: repo/package-lock.json` and `working-directory:
repo`). In `verify` and `verify-retry`, give each of those steps
`if: steps.apply.outputs.ok == 'true'`, so it is skipped once the gate has
already failed (an empty or rejected patch). A setup step that fails there
fails the job, which every later job reads as "verify did not finish".

**The gate commands.** In `.cadence/cadence.yaml`, mirror the repository's
CI, one command per line (`cd web && npm run lint`): `verify.sh` runs each
line on its own with `bash -c`, and the factory restores `.cadence/` from
the default branch before every gate.

**The config.** In `.cadence/factory.yaml`, set small caps to start, for
example `per_run_usd: 2.00` and `daily_usd: 6.00`, and keep the `learning:`
block with its defaults: `mode: "on"` (quoted), `classify: false`. The
default `guarded_paths` and `test_roots` cover tests in `tests/` and
`test/`. A project whose tests live deeper lists the directory itself, as a
relative path (1 to 6 segments, no `.` or `..`, no glob, at most 16 per
list). For tests in `server/tests`:

```yaml
learning:
  mode: "on"
  guarded_paths: [server/tests, tests, .github, .cadence, scripts, tool]
  test_roots: [server/tests]
```

Existing files under a guarded path are restored before the gate; new
files there are kept only under a test root. Keep `tests` guarded: it holds
`tests/fixtures/retro/`. Leave it out of `test_roots` unless the project's
tests live there, so an agent cannot add files next to the fixtures.

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

Run `bash scripts/verify.sh` locally; it must pass. Then merge the PR:
issue events always run the workflow from the default branch.

### 8. Check everything in the GitHub UI

- **App → Permissions & events:** exactly the five repository permissions
  above, no organization or account permissions, webhook inactive.
- **<https://github.com/settings/installations> → the App:** repository
  access lists only the repositories you chose.
- **Repository → Settings → Secrets and variables → Actions:** three
  secrets, two variables (GitHub shows the names only; the first model run
  prints the key's length, as above).
- **Settings → Actions → General:** leave **Workflow permissions** at
  read-only, and leave **Allow GitHub Actions to create and approve pull
  requests** unticked (the App opens the PRs).
- **Settings → Rules / Branches:** no rule that stops the App from creating
  or deleting `cadence/*` branches, and `cadence/verify` not listed as a
  required check (a commit a human pushes to a factory branch never gets
  one). Factory PRs show it beside your CI.
- **Optional: rulesets for the factory's own branches** (**Settings →
  Rules → Rulesets → New branch ruleset**, enforcement **Active**, targets
  added under **Include by pattern**). On a private repository GitHub
  refuses rulesets without GitHub Pro or Team (HTTP 403).

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
- **Issues → Labels:** the six labels.
- **Smoke test with no model spend:** **Actions → cadence-factory → Run
  workflow**, stage `reconcile`. The `reconcile` job should mint the App
  token and finish green, and its "Is a learn run due?" step should print
  `{"learn_due": ...}`. Then run stage `learn`: `harvest`, `learn-record`
  and `retro-plan` should finish green (`classify` and `retro-publish`
  skip while there is nothing to label or propose), and `cadence/state`
  should gain a `learn/` marker.

## Appendix B: live demo of the learning loop (phase 1b)

Run in a private TypeScript sandbox with one seed boundary rule
(`src/domain` must not import `src/http`). The demo plants a second
boundary that no rule knows yet, and shows the loop learning it from a human
and enforcing it.

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
- Install the App on **All repositories**.
- Grant the App **Workflows**, **Administration**, **Secrets** or any
  account permission, or turn its webhook on.
- Reuse a personal or production Anthropic key. Use the dedicated key
  with its spend limit.
- Enable the workflow on a real repository before every action in it is
  pinned to a full commit SHA (the renderer refuses anything else).
- Hand-edit the rendered workflow's runtime setup slots: change the profile
  and render again.
- Set `CADENCE_EVAL_SANDBOX` or `learning.mode: eval-sandbox` anywhere but
  a private eval repository: together they let the retro PR merge itself.

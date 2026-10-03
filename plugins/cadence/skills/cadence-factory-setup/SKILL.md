---
name: cadence-factory-setup
description: Connect the current repository to Cadence factory mode (labelled issue -> spec -> /approve -> agent build -> Definition of Done gate -> draft PR, plus the learning loop). Detects the stack, writes .cadence/factory.yaml and the gate commands, renders the factory workflow for the repo's runtime, opens a setup PR from a branch off origin's default branch, and prints the secret commands for the user to run. Use when the user runs /cadence-factory-setup or asks to set up, install or connect the Cadence factory on a repo.
argument-hint: "[--per-run-usd <n>] [--daily-usd <n>] [--app <existing-app-slug>]"
---

# /cadence-factory-setup

You are connecting the user's repository to Cadence factory mode
(`docs/FACTORY.md` in the cadence repo). Done by hand this took about an
hour per repository, mostly on secrets, the GitHub App and the per-stack
runtime steps. Follow the steps in order. The result is:

- a **setup PR** from a new branch off **origin's** default branch, which
  the user reviews and merges (nothing is pushed to the default branch);
- the labels and two repository variables, created with `gh`;
- the exact commands the **user** runs to add three secrets, and the GitHub
  App steps they do in the browser;
- a no-spend smoke test and the first ticket.

`PLUGIN` below is this plugin's directory (`${CLAUDE_PLUGIN_ROOT}`): the
templates, tools and schemas are copied from there.

Optional arguments: `--per-run-usd` and `--daily-usd` prefill the budget
(Step 5; asked otherwise), and `--app <slug>` names a GitHub App the user
already owns (Step 9, path A).

## Never

- **Never read, print, store or set a key.** That covers the Anthropic API
  key, the App private key (`.pem`) and any token. Do not ask for one in
  chat, do not `cat`/`Get-Content` a key file, do not echo an environment
  variable that may hold one, and do not run `gh secret set` yourself: print
  the commands (Step 11) and let the user run them in their own terminal.
  If the user pastes a key into the chat anyway, do not repeat it: tell them
  to revoke it and create a new one.
- Never commit on, or push to, the user's default branch or their current
  branch. Work in a separate worktree (Step 1).
- Never enable auto-merge, set `CADENCE_EVAL_SANDBOX`, or write
  `learning.mode: eval-sandbox`: those let the factory merge its own PRs and
  exist only for private eval repos.
- Never use or suggest a shared, Cadence-owned GitHub App. The user owns
  the App, the key and the bill.
- Never edit the five runtime setup slots of the rendered workflow by hand:
  change `.cadence/factory-stack.yaml` and render again (Step 6).
- Ask before every write to GitHub (labels, variables, push, PR, a workflow
  dispatch). Reading is fine.

## Step 0: Preconditions

Run, read the output, and stop with a clear message if one fails:

```bash
gh auth status                                     # logged in to github.com
gh repo view --json nameWithOwner,defaultBranchRef,visibility,viewerPermission
python3 -c "import yaml; print(yaml.__version__)"  # PyYAML 6
```

On Windows, `python3` can be the Microsoft Store stub: use `python` there
wherever this skill says `python3`, and run the bash snippets in Git Bash.

- `viewerPermission` must be `ADMIN`: repository variables need it, and only
  an admin can add secrets and install an App. With less, explain what the
  admin will have to do and continue only with the PR.
- Note the visibility. On a private repo, rulesets need GitHub Pro or Team
  (Step 12 is optional there), and Actions minutes count against the plan.
- `git remote get-url origin` must point at that repository. Set
  `R=<owner>/<repo>` and `DEFAULT=<default branch>` for the steps below.

## Step 1: A setup branch off origin's default branch

The local default branch can hold commits that were never pushed. The setup
must not carry them, so start from origin, in a worktree that leaves the
user's checkout alone:

```bash
git fetch origin
git log --oneline "origin/$DEFAULT..$DEFAULT" 2>/dev/null   # unpushed commits, if any
git worktree add -b cadence-factory-setup ../<repo>-factory-setup "origin/$DEFAULT"
```

If the log shows commits, tell the user they are not part of the setup. Do
all later file work in the worktree. (The branch name is outside
`cadence/`, which the factory uses for its own branches.)

## Step 2: Detect the stack, folders, test roots and CI commands

Read, do not run yet. The repository's own CI is the source of truth: the
factory gate should run what CI runs.

1. `.github/workflows/*.yml`: for each job, the runtime (`setup-python`
   and its version, `setup-node` with `node-version` or
   `node-version-file`, `cache-dependency-path`), `working-directory`, the
   install commands (`pip install -r ...`, `npm ci`) and the format, lint,
   type-check and test commands. Note steps that need secrets, services
   (databases, browsers) or the network beyond package installs.
2. Markers at the root and one or two levels down (`server/`, `web/`,
   `apps/*`, `packages/*`): `pyproject.toml`, `requirements*.txt`,
   `package.json` (its `scripts`), lockfiles, `.nvmrc`, `.python-version`,
   `go.mod`, `Cargo.toml`, `pom.xml`.
3. Test folders: `git ls-files | grep -E '(^|/)(tests?|__tests__|spec)/'`
   and test file names (`test_*.py`, `*.test.ts`, `*_test.go`). Tests can be
   nested, such as `server/tests` or `web/src/__tests__`.
4. What is already there: `.cadence/cadence.yaml` (from `/cadence-init`),
   `.cadence/factory.yaml`, `scripts/verify.sh`, `tool/`.

Show the user a short table: folder, stack, runtime version, install
command, test folder, the CI commands found.

## Step 3: The gate commands (`.cadence/cadence.yaml`)

If `.cadence/cadence.yaml` does not exist on origin's default branch, run
`/cadence-init`'s steps inside the worktree first (same setup PR) and show
the user the file list before writing.

Set `commands.format`, `commands.lint` and `commands.test` to mirror the
repository's CI. `scripts/verify.sh` runs each entry with `bash -c`, one
line at a time, so:

- **one command per entry, on one line**; never a multi-line YAML string;
- run a folder's command from its folder: `cd web && npm run lint`;
- leave out what cannot run on a plain runner (secrets, databases, browser
  end-to-end suites) and tell the user which CI steps were left out.

For a Python service whose tests live in `server/tests` and a Vite web app
in `web/`:

```yaml
commands:
  format:
    - "cd server && ruff format --check ."
  lint:
    - "cd server && ruff check ."
    - "cd web && npm run lint"
  test:
    - "cd server && python -m pytest -q"
    - "cd web && npm test -- --run"
```

The factory restores `.cadence/` from the default branch before every gate,
so the agent cannot change these commands. Two things the project's own
tooling must accept (fix them in the same PR, with the user's OK):

- `tests/fixtures/retro/` will hold the deliberately failing samples that
  prove learned checks: exclude it from lint, type-check and test discovery
  (pytest `--ignore=tests/fixtures/retro`, ESLint `ignorePatterns`,
  `exclude` in `tsconfig.json` and the Vitest or Jest config).
- Generated files must be git-ignored (`node_modules/`, `dist/`,
  `coverage/`, `.venv/`), and so must the verify evidence:
  `.cadence/.last_verify_ok`, `.cadence/.last_verify_sha`,
  `.cadence/last_verify.log`.

## Step 4: The stack profile (`.cadence/factory-stack.yaml`)

The workflow's five runtime setup slots (`agent`, `agent-retry`, `verify`,
`verify-retry`, `retro-plan`) are filled from this profile. Mirror the
CI's setup. The three common shapes:

```yaml
# A Python service in server/ and a Node web app in web/.
python:
  version: "3.12"
  requirements: [server/requirements-dev.txt]
node:
  version: "20"
  dirs: [web]
  lockfiles: [web/package-lock.json]
```

```yaml
# A single-package TypeScript repo.
node:
  version_file: .nvmrc      # or version: "20"
  dirs: ["."]
```

```yaml
# Python only, on another version than the template's 3.12.
python:
  version: "3.11"
  requirements: [requirements-dev.txt]
  editable: ["."]
```

- **Pin the toolchain to the version the code passes on today** (the one in
  CI, or the developer's local version when CI says "latest"). A gate on
  "latest stable" can start failing on a release day with no code change.
  A Flutter app, for example, as custom steps:

  ```yaml
  custom:
    - name: Set up Flutter
      uses: subosito/flutter-action@1a449444c387b1966244ae4d4f8c696479add0b2
      tag: v2
      with: {flutter-version: 3.32.4, channel: stable, cache: true}
      slots: [agent, agent-retry, verify, verify-retry, retro-plan]
    - name: Get Dart packages
      run: flutter pub get
      slots: [agent, agent-retry, verify, verify-retry, retro-plan]
  ```

- Versions are quoted strings (`"3.10"`, never `3.10`, which YAML reads as
  3.1).
- `node.package_manager` is `npm` (default), `pnpm` or `yarn` (through
  corepack); `node.install` overrides the install command.
- Anything else (Go, Java, a system package, a build step) goes in
  `custom:` as a step. A `uses:` must be `owner/repo@<40-character commit
  SHA>` with `tag:` set to the release it is. Resolve a tag on the action's
  own repository, never guess:
  `gh api repos/actions/setup-go/commits/v5 --jq .sha`.
  Reuse a SHA the template already pins (`actions/setup-python`); for
  `actions/setup-node` the tool's default is
  `49933ea5288caeca8642d1e84afbd3f7d6820020` (v4).

Show the profile and the commands to the user and get an OK.

## Step 5: The factory config (`.cadence/factory.yaml`)

Copy `PLUGIN/templates/factory.yaml.tmpl` to `.cadence/factory.yaml` and
set, with the user:

- `budget.per_run_usd` and `budget.daily_usd`: start small, for example
  `2.00` and `6.00` (a few tickets a day). `per_run_usd` is passed to Claude
  Code as `--max-budget-usd`; a run that reports no cost is booked at the
  full per-run cap. Suggest a spend limit on the Anthropic key too.
- `max_turns`: 60 (the default) unless the user wants less.
- `retry.on_dod_fail`: 1 (one retry on a failed gate) or 0.
- `autonomy: pr-only`. It is the only supported value: a human merges.
- `learning.mode: "on"` (quoted) and `learning.classify: false`.
- `learning.guarded_paths`: every test folder found in Step 2, plus `tests`
  (always: the retro fixtures live in `tests/fixtures/retro`), plus
  `.github`, `.cadence`, `scripts`, `tool`. Existing files there are
  restored from the default branch before the gate, so an agent cannot
  weaken a test to pass.
- `learning.test_roots`: the folders where the repository really keeps
  its tests, where an agent may **add** a test. Leave `tests` out unless the
  repository's tests live there: then an agent cannot add files under
  `tests/`, where the fixtures are.
- Each entry: a relative directory path of 1 to 6 segments of
  `[A-Za-z0-9_.-]`, no `.` or `..`, no glob, at most 16 per list; every test
  root lies inside a guarded path and outside `.github`, `.cadence`,
  `scripts` and `tool`. For tests in `server/tests` and `web/src/__tests__`:

  ```yaml
  learning:
    mode: "on"
    guarded_paths: [server/tests, web/src/__tests__, tests, .github, .cadence, scripts, tool]
    test_roots: [server/tests, web/src/__tests__]
  ```

- If test files have unusual names, extend `learning.test_globs`.

Check it with the factory's own validation once the tools are copied
(Step 7):

```bash
python3 - <<'PY'
import sys; from pathlib import Path; sys.path.insert(0, "tool")
import ledger
ledger.load_config(Path(".cadence/factory.yaml")); ledger.load_learning(Path(".cadence/factory.yaml"))
print("factory.yaml OK")
PY
```

## Step 6: Render the workflow

```bash
python3 "$PLUGIN/templates/tool/render_factory_workflow.py" \
  --profile .cadence/factory-stack.yaml \
  --out .github/workflows/cadence-factory.yml --repo-root .
```

The tool puts the profile's steps into the five slots, gives every verify
and verify-retry step `if: steps.apply.outputs.ok == 'true'`, points the
retro-plan steps at `repo/`, and refuses unpinned actions and anything that
would weaken the workflow (exit 2, nothing written). Read its summary:

- a **refusal**: fix the profile and render again;
- a **warning** about a missing file: a path in the profile is wrong.

Never hand-edit the rendered workflow. To change the runtime later, edit the
profile, render again and open a PR.

## Step 7: Copy the tools, schemas and verify script

From `PLUGIN` into the worktree (show diffs and ask before replacing a file
the user may have changed, as `/cadence-init` does):

| From `PLUGIN/` | To |
|---|---|
| `templates/tool/{route,intake_sanitize,claim,ledger,reconcile,signals,ladder,metrics,emit_rule,check_boundaries}.py` | `tool/` |
| `templates/scripts/verify.sh` (executable: after `git add`, `git update-index --chmod=+x scripts/verify.sh`) | `scripts/verify.sh` |
| `schemas/{retro,observation,classify,lessons,retro-plan,metrics,cadence-yaml}.schema.json` | `.cadence/` |
| the last section of `templates/docs/PATTERNS.md.tmpl` (`## Learned patterns (factory)` and the line under it) | the end of `docs/PATTERNS.md` (create the file if missing) |

The renderer stays in the plugin; `.cadence/factory-stack.yaml` is
committed so the next render is reproducible.

## Step 8: Run the gate locally

Install the runtime the profile names (for example
`python3 -m pip install -r server/requirements-dev.txt` and
`npm ci --prefix web`), then, in the worktree (Git Bash on Windows):

```bash
bash scripts/verify.sh
```

It must pass on the default branch's code: the factory runs exactly this on
every attempt. Read the output, not only the exit code: every configured
command must appear as `$ <command>`. A step that says "no commands
configured" although `cadence.yaml` has some means `verify.sh` could not
read the config (on Windows, a `python3` that is the Microsoft Store stub
does this): fix the interpreter and run it again. If a command fails because of the environment, fix the
command; if the code fails its own checks, stop and tell the user, since
every ticket would fail the gate.

The common case is a format check that fails on most files, usually because
the formatter's style changed between versions. Do not reformat the
project inside the setup PR. Offer the user two ways, and say which you
chose in the PR body:

- leave the format command out (`format: []`, with a comment giving the
  exact command) and add it back after a one-time formatting commit;
- or pin the toolchain to the version the code was formatted with (Step 4).

Lint and test failures are different: they mean the code is broken today.
Stop and tell the user. `verify.sh` writes its evidence under
`.cadence/`; those files are git-ignored (Step 3).

## Step 9: The GitHub App (the user does this, in the browser)

The factory pushes branches and opens PRs as the user's own GitHub App, so
its pushes start the repository's CI and its identity is not a person's.
Ask which applies:

**A. Reuse an App the user already owns** (from another repository):

1. Open <https://github.com/settings/installations>, then the App's
   **Configure**. Under **Repository access**, keep **Only select
   repositories** and add this repository. Save.
2. On the App's settings page (<https://github.com/settings/apps>, the
   App, **General**), confirm the permissions below and that the webhook is
   inactive. Note the **App ID** and the slug (the bot login is
   `<slug>[bot]`).
3. The private key: if the user no longer has the `.pem` file, **Generate
   a private key** on the same page (older keys keep working). Keep the file
   only until Step 11.

**B. Register a new App:** <https://github.com/settings/apps/new>

1. Name `cadence-factory-<owner>`, homepage the repository URL, **Webhook:
   Active unticked** (no server, no URL).
2. **Repository permissions**, exactly:

   | Permission | Access | Used for |
   |---|---|---|
   | Actions | Read and write | Reading run status; the reconciler's `workflow_dispatch` spec retries |
   | Contents | Read and write | Claim refs, `cadence/issue-N`, `cadence/state`, `cadence/retro` |
   | Issues | Read and write | Labels, comments and issue events |
   | Pull requests | Read and write | The draft PR and the retro PR |
   | Metadata | Read-only | Required, selected automatically |

   Nothing else: no **Workflows** (so the App can never change
   `.github/workflows/`), no **Administration**, no **Secrets**, no
   organization or account permissions, no events.
3. **Only on this account**, **Create GitHub App**. Note the **App ID**
   and the slug.
4. **Generate a private key** (a `.pem` downloads), then **Install App**,
   **Only select repositories**, this repository only.

## Step 10: Labels and variables (ask first)

```bash
gh label create factory     --repo "$R" --force --color 5319E7 --description "Cadence factory: write a spec"
gh label create spec-ready  --repo "$R" --force --color 0E8A16 --description "Cadence factory: spec posted, waiting for /approve"
gh label create building    --repo "$R" --force --color FBCA04 --description "Cadence factory: build in progress"
gh label create pr-open     --repo "$R" --force --color 1D76DB --description "Cadence factory: draft PR open"
gh label create dod-failed  --repo "$R" --force --color D93F0B --description "Cadence factory: Definition of Done failed"
gh label create needs-human --repo "$R" --force --color B60205 --description "Cadence factory: stuck, needs a person"
gh variable set CADENCE_BOT_LOGIN  --repo "$R" --body '<slug>[bot]'
gh variable set CADENCE_PLUGIN_REF --repo "$R" --body '<ref>'
```

- `CADENCE_BOT_LOGIN` is the App's bot login from Step 9: the loop guard,
  the reconciler's spec retries and the learning loop recognise the
  factory by it.
- `CADENCE_PLUGIN_REF` is the branch, tag or commit of `nik190799/cadence`
  the workflow loads the plugin from at run time (the default is `main`).
  Use the ref that carries the factory skills, today `factory`; a commit
  SHA pins it.

## Step 11: Commit, push and open the setup PR (ask first)

```bash
git status --short   # only what this setup wrote; caches from Step 8 must be ignored, not added
git add .cadence .github/workflows/cadence-factory.yml tool scripts docs/PATTERNS.md .gitignore
git add <the lint, type-check or test config changed in Step 3, if any>
git commit -m "chore: set up Cadence factory mode"
git push -u origin cadence-factory-setup
gh pr create --repo "$R" --base "$DEFAULT" --head cadence-factory-setup \
  --title "Set up Cadence factory mode" --body-file <a file with the body below>
```

If the push is refused for the workflow file, the `gh` token lacks the
`workflow` scope: the user runs `gh auth refresh -s workflow`.

The PR body lists: every file written; the stack profile and the
renderer's summary; the gate commands and the CI steps left out; the
budget; and what the user still has to do (the App, the three secrets,
merging, the smoke test).

Then print the secret commands for the user, filled in with `R`. Print both
shells; lead with PowerShell on Windows. **Do not run them.**

**The reliable ways to set a secret.** In real setups a hidden prompt
(`gh secret set NAME` with no value) and clipboard pipelines silently stored
**empty** secrets, twice. Use either:

1. the GitHub web form: **Settings, Secrets and variables, Actions, New
   repository secret**, paste the value, **Add secret**; or
2. a file read by a small script: save the key with a text editor into a
   file outside any repository, run the lines below, then delete the file.
   The script prints only the length, and stores nothing when the value is
   empty or holds whitespace.

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

bash (Git Bash, macOS, Linux):

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

(PowerShell never pipes a value into `gh` here: a pipeline whose file read
fails still starts `gh` with empty input, which stores an empty secret,
and Windows PowerShell 5.1 can put a byte-order mark in front of piped
text, which breaks the `.pem`. Both values go in as `--body`.)

Then: `gh secret list --repo "$R"` shows the three names (GitHub never
shows a value again, to anyone). **A secret can be checked only by its
length:** an Anthropic API key is about 100 characters with no spaces. The
first step of every model job, "Check the Anthropic key is set", prints it
(`ANTHROPIC_API_KEY is set: 108 characters, no whitespace.`) and stops the
job before any spend, booked at $0, when it is missing, empty or holds
whitespace. Afterwards, delete the key file and the `.pem` (or keep them
only in a password manager).

Recommend a **dedicated** Anthropic key: in the Claude Console, a workspace
for this repository with a low monthly spend limit, and a key used nowhere
else.

## Step 12: Optional hardening

- **Settings, Actions, General:** keep **Workflow permissions** read-only
  and leave **Allow GitHub Actions to create and approve pull requests**
  unticked (the App opens the PRs).
- **Rulesets** (**Settings, Rules, Rulesets**) so only the App writes the
  ledger: `cadence/state` with Restrict creations, updates and deletions
  and Block force pushes, bypass: the App; `cadence/retro` with Restrict
  creations, updates and deletions, bypass: the App and Repository admin.
  On a private repository this needs GitHub Pro or Team (GitHub answers 403
  otherwise); it is optional.
- Do not make `cadence/verify` a required check: a commit a human pushes to
  a factory branch never gets one.

## Step 13: The no-spend smoke test (after the merge and the secrets)

Issue events and dispatches run the workflow from the default branch, so
this works only once the setup PR is merged. With the user's OK:

```bash
gh workflow run cadence-factory.yml --repo "$R" -f stage=reconcile
gh run list --repo "$R" --workflow cadence-factory.yml --limit 1
gh run watch <run-id> --repo "$R"
```

The `reconcile` job should finish green: "Mint the App token" proves
`CADENCE_APP_ID`, the private key and the installation (a failure there
means one of them is wrong), and "Is a learn run due?" prints
`{"learn_due": ...}`. Then `-f stage=learn`: `harvest`, `learn-record`
and `retro-plan` finish green and `cadence/state` gains a `learn/` marker.
Neither calls the model.

## Step 14: The first ticket

1. Pick a small, well-specified issue: a regression test for a known fix,
   a small bug with a clear expected behaviour.
2. A user with write access adds the `factory` label. Within minutes the
   spec arrives as a comment and the label becomes `spec-ready`. Check the
   intake job's log: "ANTHROPIC_API_KEY is set: ~100 characters".
3. Read the spec. If it is right, reply with exactly `/approve` on its own.
   From Git Bash, `gh` would turn `/approve` into a Windows path:
   `MSYS_NO_PATHCONV=1 gh issue comment <n> --repo "$R" --body "/approve"`.
4. The build ends in a draft PR (`pr-open`, with the `cadence/verify`
   check and the cost in its body) or in `dod-failed` / `needs-human` with
   the reason on the issue. Review and merge like any PR; the learning loop
   reads your edits and review comments.

## Finish

Print a short summary: the setup PR link, the files written, the budget,
and the user's remaining steps in order (App, three secrets, merge, smoke
test, first ticket). Remove the worktree only when the user says so
(`git worktree remove ../<repo>-factory-setup`).

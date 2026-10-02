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
   | Contents | Read and write | Claim refs, `cadence/issue-N`, `cadence/state` |
   | Issues | Read and write | Labels, comments and issue events (reconciler) |
   | Pull requests | Read and write | Opening the draft PR |
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

| From `plugins/cadence/templates/` | To the sandbox |
|---|---|
| `.github/workflows/cadence-factory.yml.tmpl` | `.github/workflows/cadence-factory.yml` |
| `tool/route.py`, `tool/intake_sanitize.py`, `tool/claim.py`, `tool/ledger.py`, `tool/reconcile.py` | `tool/` |
| `factory.yaml.tmpl` | `.cadence/factory.yaml` |

In `.cadence/factory.yaml`, set sandbox-sized caps, for example
`per_run_usd: 2.00` and `daily_usd: 6.00`. Commit and push to the default
branch: issue events always run the workflow from there.

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
  from creating or deleting `cadence/*` branches.
- **Sandbox → Issues → Labels:** the six labels.
- **Smoke test with no model spend:** **Actions → cadence-factory → Run
  workflow**, stage `reconcile`. The `reconcile` job should mint the App
  token and finish green.

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

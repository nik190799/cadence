---
layout: default
title: Factory auth
---

# Factory auth

> **Research only (2026-10-02).** Can GitHub Actions OIDC replace the
> stored `ANTHROPIC_API_KEY` secret that the [factory](FACTORY.md) model
> jobs use? Nothing in the workflow template changes because of this page.
> Every source was read on 2026-10-02 and is listed under
> [Sources](#sources). A claim marked **Unverified** has no source yet and
> needs a test in the sandbox before anyone relies on it.

## Summary and recommendation

- **The feature exists and is generally available.** Anthropic's Workload
  Identity Federation (WIF) exchanges a GitHub Actions OIDC token for a
  short-lived Claude API token. It became GA on 2026-06-17 and covers the
  Claude API, the SDKs and Claude Code. `anthropics/claude-code-action`
  supports it at the commit the factory pins (v1, `97c5347`) through four
  inputs plus `id-token: write`.
- **Technically it can replace the key** in all four model jobs: `intake`,
  `agent`, `agent-retry` and `classify`.
- **Do not switch yet.** In this factory, WIF moves risk around more than
  it removes it:
  1. `id-token: write` is granted per job, not per step. In `agent`, every
     step after the agent could request fresh GitHub OIDC tokens until the
     job ends, and the template already treats those steps as
     agent-controlled. Today those steps hold nothing.
  2. While the agent runs, the action keeps the GitHub JWT and the
     exchanged Claude token in files under `$RUNNER_TEMP`. The env scrub
     that keeps the key away from tool subprocesses today covers
     environment variables and `/proc`, not files. **Unverified:** whether
     the agent's Bash can read those files. Assume it can.
  3. It breaks a tested invariant: model jobs hold read-only tokens.
- **Now:** keep the key and harden it without touching the workflow. Use a
  service-account key with an expiry, in the factory's own workspace with
  spend and rate limits, and keep the Claude GitHub App off factory repos.
- **Next:** pilot WIF on `classify` only, which has no shell and an
  enum-only output. Then decide on `intake`. Move `agent` and
  `agent-retry` only after the diff packaging moves out of the job that
  holds `id-token: write` (see [next steps](#recommendation-and-next-steps)).

## What the factory holds today

| Credential | Jobs | How it is held |
|---|---|---|
| `ANTHROPIC_API_KEY` (repository secret) | `intake`, `agent`, `agent-retry`, `classify` | Passed only to the `claude-code-action` step, as the `anthropic_api_key` input. No other step in those jobs sees it |
| `GITHUB_TOKEN` | every job | Read-only in model jobs. The action gets it as `github_token`, so it never runs its own OIDC exchange for a Claude GitHub App token (see [claude-code-action](#claude-code-action)) |
| `CADENCE_APP_ID`, `CADENCE_APP_PRIVATE_KEY` | `gate`, `publish`, `ledger`, `release`, `reconcile`, `learn-record`, `retro-publish`, `retro-failed` | `actions/create-github-app-token` mints an installation token, which is revoked when the job ends. WIF does not cover the App key; it stays a stored secret |

What protects the Anthropic key today:

- **Env scrub.** `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` on the action step
  strips Anthropic and cloud-provider credentials from the environment of
  the Bash tool, hooks and MCP stdio servers. On Linux it also runs Bash
  in its own PID namespace, so Bash cannot read other processes'
  environments through `/proc`. It needs bubblewrap, which each model job
  installs ([env-vars][cc-env]).
- **Fewer tools in two jobs.** `intake` and `classify` give the model no
  Bash and deny `Read(//proc/**)`.
- **No OIDC.** No factory job has `id-token: write`, so no step can request
  a GitHub OIDC token.
- **Spend caps.** `--max-budget-usd` caps each model call, `ledger.py`
  enforces the daily caps, and the workspace has a monthly spend limit
  ([sandbox setup](factory-sandbox-setup.md), step 4).

What WIF would fix: the key is a repository secret, and GitHub warns that
every user with write access to a repository can read all of its secrets
([GitHub secure use][gh-secure]). Anyone who can push a branch can push a
workflow that uses the key. A leaked key works until someone revokes it.

## What exists

All sources were read on 2026-10-02.

### Anthropic Workload Identity Federation

- **Status.** GA announced on 2026-06-17 for Claude API endpoints, the
  first-party SDKs and Claude Code ([announcement][blog]). The Admin API
  endpoints that manage issuers, rules and service accounts sit under the
  SDKs' `beta.organization` namespace ([WIF Admin API][wif-admin]).
- **Three resources.** You create them in the Claude Console (**Settings →
  Workload identity → Connect workload**, GitHub Actions tile), which needs
  the admin, owner or primary owner role ([WIF][wif]):
  - a service account (`svac_...`), a non-human identity that acts in
    the workspaces it is a member of;
  - a federation issuer (`fdis_...`): for GitHub,
    `https://token.actions.githubusercontent.com` with JWKS by
    `discovery`;
  - a federation rule (`fdrl_...`): a JWT from issuer X whose claims look
    like Y may act as service account Z in workspace W with scope S.
- **Token exchange.** `POST https://api.anthropic.com/v1/oauth/token` uses
  the RFC 7523 `jwt-bearer` grant ([RFC 7523][rfc7523]):
  - the body carries `assertion`, `federation_rule_id`, `organization_id`,
    `service_account_id` and `workspace_id` (required when the rule
    covers more than one workspace);
  - the response is an `sk-ant-oat01-...` bearer token with `expires_in`
    and `scope`;
  - the request names the rule by id, and Anthropic does no implicit rule
    search ([WIF reference][wif-ref]).
- **Rule matching.** Every matcher that is set must pass (AND), and at
  least one of `subject_prefix`, `claims` or `condition` is required
  ([WIF reference][wif-ref]):
  - `subject_prefix`: an exact match on `sub`, or a prefix match with a
    trailing `*`; case-sensitive;
  - `audience`: an exact match on one element of `aud`;
  - `claims`: exact string values of top-level claims;
  - `condition`: a CEL expression over `claims`.
- **Scopes** ([WIF reference][wif-ref]):
  - `workspace:inference`: Messages (with token counting), Models and the
    OpenAI-compatible endpoint;
  - `workspace:developer`: the same access as a workspace API key;
  - `workspace:manage_tunnels` and `org:admin`.

  A token never exceeds both the rule's scope and the service account's
  role.
- **Lifetime.** The rule's `token_lifetime_seconds` is 60 to 86400,
  default 3600; the Console wizard prefills 600. A minted token lives for
  the rule's lifetime or twice the JWT's remaining lifetime, whichever is
  shorter, and never less than 60 seconds ([WIF][wif]). GitHub's JWTs
  expire about five minutes after issue ([WIF for GitHub
  Actions][wif-gha]). So a token minted from a fresh GitHub JWT lives
  about ten minutes at most.
- **JWT checks** ([WIF reference][wif-ref]):
  - asymmetric signatures with a `kid` only;
  - `sub`, `iat` and `exp` are required;
  - the JWT's own lifetime is at most the issuer's maximum (1 hour by
    default);
  - 30 seconds of clock skew;
  - a `jti` is single use per issuer by default; a replay is denied as
    `jti_reused`.
- **Denials** are an opaque `401 Authentication failed`. The reason, for
  example `match_subject_prefix`, shows in the Console's authentication
  history ([WIF reference][wif-ref]).
- **Credential order in the SDKs** ([WIF reference][wif-ref]):
  1. constructor arguments;
  2. `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN`;
  3. `ANTHROPIC_PROFILE`;
  4. the federation variables: `ANTHROPIC_FEDERATION_RULE_ID`,
     `ANTHROPIC_ORGANIZATION_ID`, `ANTHROPIC_SERVICE_ACCOUNT_ID`,
     `ANTHROPIC_WORKSPACE_ID`, and `ANTHROPIC_IDENTITY_TOKEN_FILE` or
     `ANTHROPIC_IDENTITY_TOKEN`;
  5. the active profile.

  A variable set to an empty string still takes its place in this order.
  With a profile, the SDK caches the access token in
  `<config_dir>/credentials/<profile>.json` (mode `0600`).
- **Limits and billing.**
  - A minted token gets the rule's workspace's rate limits and usage
    attribution, the same as an API key ([WIF][wif]).
  - Workspaces have monthly spend limits and per-minute rate limits
    ([workspaces][workspaces]).
  - The Default Workspace cannot have limits, and every service account
    is implicitly a member of it ([workspaces][workspaces], [WIF][wif]).
  - **Unverified:** the docs show no spend limit per service account.
    Treat the workspace as the only server-side cap.
- **Revocation.**
  - An archived rule fails every new exchange ([WIF
    reference][wif-ref]).
  - **Unverified:** whether tokens already minted stop working before
    they expire. The docs do not say.

### claude-code-action

- **Inputs** at the pinned commit `97c53473391bff1901034d4b454b5bac7ab7a029`
  (v1 on 2026-10-01; [action.yml][cca-action]):
  - `anthropic_federation_rule_id`, `anthropic_organization_id`,
    `anthropic_service_account_id` and `anthropic_workspace_id`;
  - `anthropic_oidc_audience`, default `https://api.anthropic.com`.

  Support landed in [#1378][cca-1378] (2026-06-02). [#1407][cca-1407]
  (2026-07-22) shares one exchanged token across the `claude` processes
  the action starts (plugin installs and the main run), because each
  GitHub JWT can be exchanged only once.
- **Precedence.** The job needs `id-token: write`. Do not set
  `anthropic_api_key` or `claude_code_oauth_token` as well: a static
  credential wins, the action logs a warning, and federation is not used
  ([setup][cca-setup], [workload-identity.ts][cca-wif]).
- **What the action does** ([workload-identity.ts][cca-wif]):
  - calls `core.getIDToken(audience)` and masks the JWT in the log;
  - writes the JWT to
    `$RUNNER_TEMP/claude-workload-identity/identity-token` (mode `0600`)
    and exports `ANTHROPIC_IDENTITY_TOKEN_FILE`;
  - writes a federation profile under the same directory and points
    `ANTHROPIC_CONFIG_DIR` and `ANTHROPIC_PROFILE` at it, so the CLI
    caches the exchanged token on disk there;
  - rewrites the JWT every 4 minutes;
  - deletes the whole directory when the step ends.
- **The session cannot mint tokens itself.** The action removes
  `ACTIONS_ID_TOKEN_REQUEST_URL` and `ACTIONS_ID_TOKEN_REQUEST_TOKEN` from
  the environment it passes to Claude Code ([#1011][cca-1011],
  [parse-sdk-options.ts][cca-sdkopts]).
- **A different OIDC exchange.** This one has nothing to do with WIF.
  - When no `github_token` input is given, the action requests an OIDC
    token with audience `claude-code-github-action`.
  - It posts that token to
    `https://api.anthropic.com/api/github/github-app-token-exchange` and
    gets back a Claude GitHub App installation token. The action's own
    default permission set is contents, pull requests and issues write
    ([token.ts][cca-token]).
  - The factory always passes `github_token`, so this exchange never
    runs. Today it could not run anyway, because no job may request an
    OIDC token.
  - The template comment "without it the action asks for an OIDC token"
    refers to this exchange.
- **Claude Code** selects federation credentials when
  `ANTHROPIC_FEDERATION_RULE_ID` and `ANTHROPIC_ORGANIZATION_ID` are both
  set. They rank below `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`,
  `apiKeyHelper` and `CLAUDE_CODE_OAUTH_TOKEN`. Bare mode does not read
  them ([Claude Code authentication][cc-auth]).
- **Docs disagree on one input.** Claude Code's GitHub Actions page calls
  `anthropic_service_account_id` optional ([Claude Code GitHub
  Actions][cc-gha]), but the WIF reference lists `service_account_id` as
  required in the exchange. Set it.

### The Claude API itself

- **Three methods** ([authentication][auth]):
  - an API key, sent as `Authorization: Bearer` or the legacy
    `x-api-key`;
  - WIF;
  - App Attest, for iOS and macOS apps.

  API keys and WIF grant the same access.
- **Key types.**
  - personal keys;
  - service account keys, which act as a service account and stop
    working when that account is archived;
  - workspace keys (legacy).

  A key can expire after 3 hours, 1 day, 7 days, 30 days, a custom
  duration, or never. A key that lives at least 14 days gets a warning
  email 7 days before it expires ([authentication][auth]).
- **Direct calls.** A `run:` step can call the API with WIF through the
  SDK and the `ANTHROPIC_*` variables, or with curl as in Anthropic's
  guide ([WIF for GitHub Actions][wif-gha]). The factory calls the model
  only through `claude-code-action`, so this matters only if a later job
  calls the API directly.

### Bedrock, Vertex and Foundry

- **How it works.** The action has `use_bedrock`, `use_vertex` and
  `use_foundry`. An earlier step does its own OIDC exchange
  ([cloud providers][cca-cloud], [Claude Code with cloud
  providers][cc-gha-cloud]):
  - `aws-actions/configure-aws-credentials` with `role-to-assume`;
  - `google-github-actions/auth` with `workload_identity_provider`.
- **Same OIDC exposure.** These need `id-token: write` too, so the
  [first risk](#id-token-write-in-a-job-where-the-agent-runs-bash) below
  applies unchanged.
- **AWS.**
  - By default, the AWS action exports `AWS_ACCESS_KEY_ID`,
    `AWS_SECRET_ACCESS_KEY` and `AWS_SESSION_TOKEN` as environment
    variables for every later step ([configure-aws-credentials][aws]).
  - A role session lasts 1 hour by default (15 minutes to 12 hours).
  - The scrub strips cloud credentials from Claude's subprocesses
    ([env-vars][cc-env]), but not from the job's later steps.
- **Google.**
  - By default, the Google action writes a credentials file into
    `$GITHUB_WORKSPACE` and tells you to ignore `gha-creds-*.json`
    ([auth][gcp]).
  - In `agent`, "Package the diff" adds untracked files to the patch, so
    that file would land in `change.patch` unless it is excluded.
  - **Unverified:** what the file holds in workload identity mode.
- **Billing.** Billing and model ids move to the cloud account. The
  factory rule "users bring their own Anthropic API key" would need a new
  option. **Unverified:** whether Claude Code's `total_cost_usd`, which
  the ledger books, matches the cloud bill.
- **Verdict:** the same `id-token` exposure plus a second cloud account.
  Not worth it for the factory.

### GitHub Actions OIDC basics

- **The permission.** `id-token: write` only lets a job fetch an OIDC
  token. It grants no write access to anything else ([OIDC
  reference][gh-oidc]). It is set per workflow or per job, never per
  step.
- **Any step can request a token.** In a job with `id-token: write`, the
  runner exposes `ACTIONS_ID_TOKEN_REQUEST_URL` and
  `ACTIONS_ID_TOKEN_REQUEST_TOKEN` ([OIDC reference][gh-oidc]). Any step
  can request a token for any audience, and the request endpoint stays
  valid for the whole job ([WIF for GitHub Actions][wif-gha]).
- **Claims** ([OIDC reference][gh-oidc]):
  - `iss`: `https://token.actions.githubusercontent.com`;
  - `aud`: the repository owner's URL by default; the requester can set
    another audience;
  - `sub`;
  - the repository: `repository`, `repository_id`, `repository_owner`,
    `repository_owner_id`;
  - the ref: `ref`, `ref_type`, `sha`;
  - the workflow: `workflow`, `workflow_ref`, `workflow_sha`, and
    `job_workflow_ref` for reusable workflows;
  - the run: `environment`, `event_name`, `run_id`, `run_attempt`,
    `actor`, `runner_environment`, `check_run_id`;
  - `jti` and `exp`.

  Ids are strings, for example `"repository_id": "74"` ([OIDC
  concepts][gh-oidc-concept]).
- **`sub` formats** ([OIDC reference][gh-oidc]):
  - a branch: `repo:OWNER/REPO:ref:refs/heads/BRANCH`;
  - a job that references an environment: `repo:OWNER/REPO:environment:NAME`;
  - pull requests: `repo:OWNER/REPO:pull_request`;
  - the immutable form, for repositories created after 2026-07-15 (older
    ones can opt in): `repo:OWNER@OWNER_ID/REPO@REPO_ID:...`.

  The sandbox repo was created on 2026-10-01 (`gh api
  repos/nik190799/cadence-eval-sandbox --jq .created_at`), so its `sub`
  uses the immutable form. The examples in Anthropic's GitHub guide use
  the old form, and a rule copied from them fails with
  `match_subject_prefix`.
- **Which ref each trigger runs on.** `issues`, `issue_comment` and
  `schedule` always run on the default branch. `workflow_dispatch` runs
  on whichever branch was dispatched ([events][gh-events]). So a factory
  run on `main` gets `sub` `...:ref:refs/heads/main` when no environment
  is set, and a dispatch on another branch does not.
- **Custom `sub`.** The `sub` template can be customized per repository
  or organization through the REST API, for example to include
  `job_workflow_ref` ([OIDC reference][gh-oidc], [REST][gh-rest-oidc]).
- **Environments** ([deployments and environments][gh-envs], [manage
  environments][gh-manage-envs]):
  - a job that references an environment gets its name in `sub` and
    creates a deployment record;
  - deployment branch rules limit which refs may use the environment;
  - environment secrets reach only the jobs that reference it;
  - on GitHub Free, environments cannot be configured for private
    repositories, and deployment branches in private repositories need
    Pro or Team.

## How it would work, per job

The sequence for one model job, using `agent` as the example:

1. The job declares `permissions: {contents: read, id-token: write}` and no
   secrets.
2. Checkout, the plugin fetch and bubblewrap run as today.
3. The action step starts with the federation inputs from repository
   variables and `github_token: github.token`, so the Claude App exchange
   does not run.
4. The action requests a GitHub JWT with audience
   `https://api.anthropic.com`. It writes the JWT to
   `$RUNNER_TEMP/claude-workload-identity/identity-token`, writes a
   federation profile next to it, and starts the 4-minute JWT refresh.
5. The action starts Claude Code with `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1`
   and without `ACTIONS_ID_TOKEN_REQUEST_*`.
6. Claude Code posts the JWT and the rule id to `/v1/oauth/token`.
   Anthropic checks:
   - the signature, against GitHub's JWKS;
   - `iss` and `aud`;
   - the rule's `sub` prefix and claims;
   - the workspace;
   - the `jti`.

   It returns a token that lives about ten minutes at most and is scoped
   to the rule's workspace. The CLI caches it in the profile's
   credentials file.
7. Before the token expires (an advisory refresh at 120 seconds left, a
   mandatory one at 30), Claude Code exchanges the newest JWT. A 60-minute
   run makes several exchanges, and each one shows in the Console's
   authentication history.
8. `--max-budget-usd` and `--max-turns` still apply on the client. Spend
   shows on the workspace.
9. When the step ends, the action deletes the directory.
10. The later steps of the same job ("Package the diff", "Keep the result
    for the ledger", the uploads) still get `ACTIONS_ID_TOKEN_REQUEST_*`.
    `ledger` reads the cost from `claude-result.json` as today.

| Job | The model's tools | Its output | Fit for WIF |
|---|---|---|---|
| `classify` | Read, Glob, Grep, Skill, Edit under `learn/out`; no Bash | One JSON file of enums, checked by `signals.py apply-classified` | First candidate. The later steps only run `cp` and `jq` |
| `intake` | Read, Glob, Grep, Skill, Edit under `cadence/output`; no Bash | A free-text spec, posted on the issue | Needs a Read deny on the token directory and `sk-ant-` redaction in `publish` |
| `agent`, `agent-retry` | Bash, Read, Write, Edit, Agent and more | `change.patch` | Not until the packaging leaves the job (see [Risks](#risks)) |

## Setup steps

A proposal only. None of this is done, and the template does not change
in this phase.

### Claude Console

1. Use the factory's own workspace (in the sandbox, `cadence-sandbox` from
   [sandbox setup](factory-sandbox-setup.md) step 4). Set its monthly
   spend limit and its rate limits. Never point a rule at the Default
   Workspace: it cannot have limits.
2. Open **Settings → Workload identity → Connect workload → GitHub
   Actions**. Set the issuer to `https://token.actions.githubusercontent.com`
   with JWKS by `discovery`, then use **Verify issuer**.
3. Create a service account, for example `cadence-factory-classify`, with
   the `developer` organization role, and add it to the workspace. Use one
   service account per job class if you want usage per job class.
4. Create the federation rule, the trust policy below. Set the lifetime
   to 600 and the scope to `workspace:inference`. **Unverified:** that
   Claude Code needs nothing outside `workspace:inference`. If it fails
   with a 403, use `workspace:developer`.
5. Note the `fdrl_...`, `svac_...`, organization and `wrkspc_...` ids.
   They are identifiers, not secrets. The wizard waits 15 minutes for a
   first successful exchange.

### Trust policy (federation rule)

In the Admin API shape ([WIF for GitHub Actions][wif-gha]):

```json
{
  "name": "cadence-factory-classify",
  "issuer_id": "fdis_...",
  "match": {
    "subject_prefix": "repo:OWNER@OWNER_ID/REPO@REPO_ID:ref:refs/heads/main",
    "audience": "https://api.anthropic.com",
    "claims": {
      "repository_id": "REPO_ID",
      "repository_owner_id": "OWNER_ID",
      "ref": "refs/heads/main",
      "workflow_ref": "OWNER/REPO/.github/workflows/cadence-factory.yml@refs/heads/main",
      "runner_environment": "github-hosted"
    }
  },
  "target": { "type": "service_account", "service_account_id": "svac_..." },
  "workspace_id": "wrkspc_...",
  "oauth_scope": "workspace:inference",
  "token_lifetime_seconds": 600
}
```

- **Exact `sub`.** There is no trailing `*`, so `sub` must match exactly.
  Read the ids with `gh api repos/OWNER/REPO --jq '.owner.id, .id'`. A
  repository created before 2026-07-15 that has not opted in uses
  `repo:OWNER/REPO:ref:refs/heads/main` instead.
- **This workflow only.** `workflow_ref` pins the rule to this workflow
  file on `main`, so other workflows in the repository (CI on a push to
  `main`, say) cannot use it. A dispatch on another branch fails both the
  `sub` and the `ref` check.
- **Not per job.** The rule cannot tell `classify` apart from any other
  job in `cadence-factory.yml` that holds `id-token: write`. Give the
  permission to one job only. Where the GitHub plan allows environments,
  use a per-job environment instead: `subject_prefix`
  `repo:OWNER@OWNER_ID/REPO@REPO_ID:environment:cadence-classify`, keep
  `claims.ref`, and add a deployment branch rule for `main`.
- **One workspace.** Never set `applies_to_all_workspaces`: a token could
  then be minted for the Default Workspace, which has no limits.

### Workflow diff (proposal, not applied)

For `classify`, the lowest-risk job:

{% raw %}
```diff
   classify:
     permissions:
       contents: read
+      id-token: write # WIF: this job may request a GitHub OIDC token
     steps:
       ...
       - name: Classify review comments
         id: claude
         uses: anthropics/claude-code-action@97c53473391bff1901034d4b454b5bac7ab7a029 # v1
         env:
           CLAUDE_CODE_SUBPROCESS_ENV_SCRUB: "1"
         with:
-          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
+          anthropic_federation_rule_id: ${{ vars.CADENCE_ANTHROPIC_FEDERATION_RULE_ID }}
+          anthropic_organization_id: ${{ vars.CADENCE_ANTHROPIC_ORGANIZATION_ID }}
+          anthropic_service_account_id: ${{ vars.CADENCE_ANTHROPIC_SERVICE_ACCOUNT_ID }}
+          anthropic_workspace_id: ${{ vars.CADENCE_ANTHROPIC_WORKSPACE_ID }}
           github_token: ${{ github.token }} # keep: without it the action swaps the OIDC token for a Claude App token
           ...
           claude_args: |
             ...
-            --disallowedTools "Bash,NotebookEdit,WebFetch,WebSearch,Agent,Task,mcp__*,Edit(/${{ runner.temp }}/_runner_file_commands/**),Read(//proc/**)"
+            --disallowedTools "Bash,NotebookEdit,WebFetch,WebSearch,Agent,Task,mcp__*,Edit(/${{ runner.temp }}/_runner_file_commands/**),Read(//proc/**),Read(/${{ runner.temp }}/claude-workload-identity/**)"
```
{% endraw %}

- **Read deny.** The new rule keeps the model's Read, Glob and Grep away
  from the JWT and the cached token. Claude Code applies Read deny rules
  to Grep and Glob "best-effort" ([permissions][cc-perm]).
- **Tests.** In `tests/test_factory_workflow.py`,
  `test_classify_holds_only_the_model_key_and_no_shell` pins `classify`'s
  permissions to `{contents: read}` and its secrets to
  `ANTHROPIC_API_KEY`. `test_model_jobs_hold_no_push_token_or_app_secret`
  requires every permission of `agent` and `intake` to be `read`. Both
  would have to allow `id-token: write` in named jobs only, and also
  assert that:
  - those jobs reference no secret;
  - `github_token` is always passed;
  - the Read deny rule is present.

## Risks

### `id-token: write` in a job where the agent runs Bash

These four points were checked against the action source at the pinned
commit and the Claude Code docs. Each needs a sandbox test.

- **The agent session.** The action deletes `ACTIONS_ID_TOKEN_REQUEST_URL`
  and `ACTIONS_ID_TOKEN_REQUEST_TOKEN` from the environment it gives
  Claude Code ([#1011][cca-1011]). So the Bash tool does not inherit
  them, scrub or no scrub.
  - The scrub removes Anthropic and cloud-provider credentials and any
    other variable Claude Code recognizes as a credential
    ([env-vars][cc-env]). **Unverified:** whether that covers these two
    variables, `ANTHROPIC_IDENTITY_TOKEN_FILE` or `ANTHROPIC_CONFIG_DIR`.
  - The PID namespace stops Bash from reading the action process's
    environment through `/proc` ([env-vars][cc-env]).
- **Files.** The JWT and the cached `sk-ant-oat01-...` token sit in
  `$RUNNER_TEMP/claude-workload-identity/` for the whole step, mode
  `0600`, owned by the same user that runs Bash
  ([workload-identity.ts][cca-wif], [WIF reference][wif-ref]). The scrub
  docs describe environment and PID isolation, not file isolation.
  **Unverified:** whether Bash can read them; assume it can. An attacker
  who reads them gets:
  - a Claude token for up to about ten minutes;
  - a JWT that has not been exchanged yet. Exchanging it first makes the
    real run fail with `jti_reused`.

  Today the key is never on disk, so `intake`'s and `classify`'s Read
  tool cannot reach it.
- **Later steps.** Every step after the agent in `agent` and
  `agent-retry` gets `ACTIONS_ID_TOKEN_REQUEST_*` in its environment until
  the job ends.
  - These steps run git in the agent's working tree, and the agent can
    write `.git/config`. git runs the command named in `core.fsmonitor`
    when it refreshes the index ([git-config][git-config]).
    **Unverified:** that `git add --intent-to-add` and `git diff` in
    "Package the diff" trigger it. The template already treats the whole
    job as agent-controlled.
  - Code running there could request GitHub OIDC tokens for any audience.
  - With the Anthropic audience, it could swap them for Claude tokens,
    capped only by the workspace's monthly limit and not by
    `--max-budget-usd`.
  - With audience `claude-code-github-action`, it could get a Claude
    GitHub App token with write access, if that App is installed on the
    repository ([token.ts][cca-token]). That would break "the agent job
    never holds a push token". **Unverified:** whether the exchange
    refuses a repository where the App is not installed.
  - It could also use any other cloud trust that accepts this repository's
    `sub`.
- **Log output.** The action masks the JWT ([workload-identity.ts][cca-wif])
  but never sees the exchanged token. If full output is on (it switches
  on in debug mode, per [security][cca-security]), a Read of the
  credentials file would print the token to the log unmasked.

### Claim scoping per job

- **Without an environment,** every job in `cadence-factory.yml` that
  holds `id-token: write` gets the same `sub`, `ref` and `workflow_ref`.
  A rule cannot tell them apart. `check_run_id` differs per job but
  cannot be known ahead of time.
- **Per-job environments** give each job class its own `sub`, and so its
  own rule, service account and workspace. Costs:
  - each job then creates a deployment record;
  - required reviewers would block unattended runs, so do not add them;
  - on GitHub Free, a private repository cannot have environments at all
    ([manage environments][gh-manage-envs]).
- **A reusable workflow** for the model jobs would add `job_workflow_ref`
  to the token as another way to tell them apart ([OIDC
  reference][gh-oidc]). **Unverified:** how `job_workflow_ref` reads in
  jobs that do not use a reusable workflow.

### The invariant "model jobs hold only `ANTHROPIC_API_KEY`"

- **What changes.** With WIF, a model job holds no secret but gains
  `id-token: write`, which is a capability to mint credentials. The
  tested invariant (read-only tokens, secrets a subset of
  `ANTHROPIC_API_KEY`) would become "no secrets, and `id-token: write`
  only in these named jobs".
- **Why it stays narrow.** The current invariant also protects the jobs
  that hold the App token. `id-token: write` must never appear in `gate`,
  `publish`, `ledger`, `release`, `reconcile`, `learn-record`,
  `retro-publish` or `retro-failed`, or in `verify` and
  `observe`, which run agent code.

### Spend limits per identity

- **Client caps still apply.** `--max-budget-usd` and the ledger's daily
  caps work as today, but only for the token the action uses.
- **The server-side cap is per workspace.** It is a monthly spend limit
  plus rate limits ([workspaces][workspaces]); the docs show none per
  service account.
- **Per-pool caps.** Separate workspaces for build and learn would give
  each ledger pool its own monthly backstop.
- **Versus a leaked key.** A leaked key spends until someone revokes it.
  A leaked federated token spends for about ten minutes, unless the
  attacker can keep minting JWTs, as in the later steps of `agent`.

### Outage and fallback

- **Two endpoints.** Every exchange needs both GitHub's token endpoint
  and `api.anthropic.com/v1/oauth/token`.
- **Short tokens.** A token lives about ten minutes, so a 60-minute build
  needs several exchanges. If an exchange fails after the cached token
  runs out (the mandatory refresh at 30 seconds left), the SDK raises an
  error ([WIF][wif]). The run then fails like any agent failure: the
  claim is released and the cost so far is booked.
- **No automatic fallback.** A fallback key would keep the stored secret
  that WIF is meant to remove. The action also ignores federation
  whenever a key is set ([setup][cca-setup]). Falling back is a manual
  revert: add the key back and restore `anthropic_api_key`.
- **Debugging.** A misconfigured rule shows only as an opaque 401. The
  reason is in the Console's authentication history
  ([WIF reference][wif-ref]).

## Recommendation and next steps

1. **Now, no workflow change.** Keep `ANTHROPIC_API_KEY`, and:
   - replace it with a service-account key in the factory workspace that
     expires after 30 days, and rotate it on a calendar
     ([authentication][auth]);
   - keep the workspace's monthly spend limit and set its rate limits;
   - do not install the Claude GitHub App on factory repositories;
   - never add `id-token: write` to a factory job without the checks in
     steps 2 to 4.
2. **Sandbox canary.** Run a throwaway workflow with `id-token: write` and
   WIF. Its prompt asks Claude Code to report only `READABLE` or
   `NOT_READABLE` for `$RUNNER_TEMP/claude-workload-identity/*` and
   whether `ACTIONS_ID_TOKEN_REQUEST_TOKEN`, `ANTHROPIC_IDENTITY_TOKEN_FILE`
   and `ANTHROPIC_CONFIG_DIR` are set in Bash. A later `run:` step reports
   whether `ACTIONS_ID_TOKEN_REQUEST_TOKEN` is set. Never print a token.
   This settles the **Unverified** items in the first risk above.
3. **Pilot on `classify`** in the sandbox with the rule and the diff
   above, behind `learning.classify`.
   - Confirm in the Console's authentication history that only `classify`
     runs exchange.
   - Confirm the cost still reaches the ledger.
   - Confirm `workspace:inference` is enough.
4. **Then `intake`,** with the Read deny rule and `sk-ant-` redaction in
   `publish`'s spec sanitizing.
5. **`agent` and `agent-retry` last,** and only when all of these hold:
   - the canary shows the token directory cannot be read from Bash, or
     the exposure is accepted and bounded by a per-job workspace;
   - no step after the agent in a job with `id-token: write` runs git
     in the agent's tree. For example, upload the tree without `.git` and
     build the diff in a fresh job;
   - the Claude GitHub App is not installed on the repository.
6. **Delete the stored key** only when no job references it, as in
   Anthropic's migration steps ([WIF][wif]).

## Sources

All read on 2026-10-02.

- Anthropic: [Workload Identity Federation][wif], [WIF with GitHub
  Actions][wif-gha], [WIF reference][wif-ref], [WIF Admin API][wif-admin],
  [Authentication][auth], [Workspaces][workspaces], [WIF GA
  announcement][blog]
- Claude Code: [Authentication][cc-auth], [Environment variables][cc-env],
  [Permissions][cc-perm], [GitHub Actions][cc-gha], [GitHub Actions with
  cloud providers][cc-gha-cloud]
- claude-code-action at `97c53473391bff1901034d4b454b5bac7ab7a029`:
  [action.yml][cca-action], [docs/setup.md][cca-setup],
  [docs/cloud-providers.md][cca-cloud], [docs/security.md][cca-security],
  [base-action/src/workload-identity.ts][cca-wif],
  [base-action/src/parse-sdk-options.ts][cca-sdkopts],
  [src/github/token.ts][cca-token]; pull requests [#1011][cca-1011],
  [#1378][cca-1378], [#1407][cca-1407]
- GitHub: [OpenID Connect reference][gh-oidc], [OpenID Connect
  concepts][gh-oidc-concept], [REST API for Actions OIDC][gh-rest-oidc],
  [Events that trigger workflows][gh-events], [Deployments and
  environments][gh-envs], [Managing environments][gh-manage-envs],
  [Secure use reference][gh-secure]
- Others: [aws-actions/configure-aws-credentials][aws],
  [google-github-actions/auth][gcp], [git-config][git-config], [RFC
  7523][rfc7523]

[wif]: https://platform.claude.com/docs/en/manage-claude/workload-identity-federation
[wif-gha]: https://platform.claude.com/docs/en/manage-claude/wif-providers/github-actions
[wif-ref]: https://platform.claude.com/docs/en/manage-claude/wif-reference
[wif-admin]: https://platform.claude.com/docs/en/manage-claude/wif-admin-api
[auth]: https://platform.claude.com/docs/en/manage-claude/authentication
[workspaces]: https://platform.claude.com/docs/en/manage-claude/workspaces
[blog]: https://claude.com/blog/workload-identity-federation
[cc-auth]: https://code.claude.com/docs/en/authentication
[cc-env]: https://code.claude.com/docs/en/env-vars
[cc-perm]: https://code.claude.com/docs/en/permissions
[cc-gha]: https://code.claude.com/docs/en/github-actions
[cc-gha-cloud]: https://code.claude.com/docs/en/github-actions-cloud-providers
[cca-action]: https://github.com/anthropics/claude-code-action/blob/97c53473391bff1901034d4b454b5bac7ab7a029/action.yml
[cca-setup]: https://github.com/anthropics/claude-code-action/blob/97c53473391bff1901034d4b454b5bac7ab7a029/docs/setup.md
[cca-cloud]: https://github.com/anthropics/claude-code-action/blob/97c53473391bff1901034d4b454b5bac7ab7a029/docs/cloud-providers.md
[cca-security]: https://github.com/anthropics/claude-code-action/blob/97c53473391bff1901034d4b454b5bac7ab7a029/docs/security.md
[cca-wif]: https://github.com/anthropics/claude-code-action/blob/97c53473391bff1901034d4b454b5bac7ab7a029/base-action/src/workload-identity.ts
[cca-sdkopts]: https://github.com/anthropics/claude-code-action/blob/97c53473391bff1901034d4b454b5bac7ab7a029/base-action/src/parse-sdk-options.ts
[cca-token]: https://github.com/anthropics/claude-code-action/blob/97c53473391bff1901034d4b454b5bac7ab7a029/src/github/token.ts
[cca-1011]: https://github.com/anthropics/claude-code-action/pull/1011
[cca-1378]: https://github.com/anthropics/claude-code-action/pull/1378
[cca-1407]: https://github.com/anthropics/claude-code-action/pull/1407
[gh-oidc]: https://docs.github.com/en/actions/reference/security/oidc
[gh-oidc-concept]: https://docs.github.com/en/actions/concepts/security/openid-connect
[gh-rest-oidc]: https://docs.github.com/en/rest/actions/oidc
[gh-events]: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
[gh-envs]: https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments
[gh-manage-envs]: https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments
[gh-secure]: https://docs.github.com/en/actions/reference/security/secure-use
[aws]: https://github.com/aws-actions/configure-aws-credentials
[gcp]: https://github.com/google-github-actions/auth
[git-config]: https://git-scm.com/docs/git-config
[rfc7523]: https://www.rfc-editor.org/rfc/rfc7523

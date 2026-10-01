---
name: cadence-factory-setup
description: Factory mode (design stub, not functional yet). Guided setup that connects a repo to the Cadence factory - the user's own GitHub App, an Anthropic API key secret, a daily budget and an autonomy level. Use only when the user runs /cadence-factory-setup.
argument-hint: "[--budget-usd <n>] [--autonomy pr-only]"
---

# /cadence-factory-setup

> **Status: design stub on the `factory` branch.** The steps below are
> the contract; the implementation lands in phase 1a. See
> `docs/FACTORY.md`.

Run after `/cadence-init`. The result is a setup PR the user reviews
and merges; nothing is pushed to the default branch directly.

## Contract

1. **Require `/cadence-init` first.** If `.cadence/cadence.yaml` is
   missing, stop and say so.
2. **The user's own GitHub App.** Walk the user through registering a
   GitHub App they own (webhook inactive, so no server), with only the
   permissions the factory needs: contents, pull requests, issues
   (read/write) and actions (write, for `workflow_dispatch`). Store the
   App ID and private key as Actions secrets. Never ask the user to
   paste the private key into the chat.
3. **The user's own model key.** Ask the user to add
   `ANTHROPIC_API_KEY` as an Actions secret themselves. Recommend a
   dedicated key with its own spend limit. Cadence never sees, stores
   or pays for model usage.
4. **Budget and autonomy.** Write `.cadence/factory.yaml` with a per-run
   cap (`--max-budget-usd`), a daily cap, `max_turns`, and
   `autonomy: pr-only` as the default. Auto-merge is opt-in, per path,
   and never covers tests, CI config or `.cadence/`.
5. **Scaffold the workflow.** Render
   `templates/.github/workflows/cadence-factory.yml.tmpl` and the
   factory tools (`ledger.py`, `claim.py`, `reconcile.py`) into the
   repo.
6. **Open a setup PR** listing every file written and every secret the
   user still has to add.

## Never

- Ask for or handle passwords, private keys or API keys in chat
- Use a shared Cadence-owned GitHub App
- Enable auto-merge by default

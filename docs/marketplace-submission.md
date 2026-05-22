---
layout: default
title: Marketplace submission
---

# Submitting Cadence to a Claude Code marketplace

> ⚠️ **Corrected in v0.2.0.** A previous version of this doc described
> opening a PR against `anthropics/claude-plugins-official` — that
> repo accepts contributions from Anthropic team members only, and
> the auto-bot closes external PRs immediately. The correct path for
> third parties is a web form that lands plugins in the *community*
> marketplace.

There are two Anthropic-curated marketplaces:

| Marketplace | Who can add plugins | Install path |
|---|---|---|
| **`anthropics/claude-plugins-official`** | Anthropic team members only (no third-party submissions) | `/plugin install <name>` after enabling |
| **`anthropics/claude-plugins-community`** | Third parties via web form | `/plugin install <name>@claude-community` |

The path below is for the **community marketplace** — that's where
Cadence belongs.

## Pre-submission checklist

Before opening the form, confirm:

- [ ] Plugin is in a **public Git repo** with a current release tag
- [ ] `claude plugin validate <plugin-root>` passes (we run the same
      check)
- [ ] `README.md` includes installation and usage instructions
- [ ] License file present and matches the manifest's `license:` field
- [ ] You have a recent commit SHA on hand (review pipeline pins to it)

For Cadence specifically, these are all green as of `v0.2.0` (sha
`4ff09907`) — the canonical reference URL to paste into the form is
`https://github.com/nik190799/cadence`.

## Submit

Open one of these (both route to the same review queue):

- [claude.ai/settings/plugins/submit](https://claude.ai/settings/plugins/submit)
- [platform.claude.com/plugins/submit](https://platform.claude.com/plugins/submit)

Fill in:

| Field | Value (for Cadence) |
|---|---|
| Plugin name | `cadence` |
| Description | Copy from `.claude-plugin/marketplace.json` plugins[0].description |
| Repository URL | `https://github.com/nik190799/cadence` |
| Plugin path (if asked) | `plugins/cadence` |
| Latest release / ref | `v0.2.0` |
| Latest commit SHA | `4ff099073c960aee4573a9b18f926156d95640ef` |
| License | `Apache-2.0` |
| Author | `Nikhil Kadalge` |
| Category | `development` |

## What happens next

1. Anthropic runs automated safety screening + revalidates the plugin
2. If approved, an entry lands in
   [`anthropics/claude-plugins-community`'s `marketplace.json`](https://github.com/anthropics/claude-plugins-community/blob/main/.claude-plugin/marketplace.json)
3. CI auto-bumps the pinned SHA as new commits land on the referenced
   branch
4. Catalog syncs nightly — your plugin becomes installable via:

   ```
   /plugin marketplace add anthropics/claude-plugins-community
   /plugin install cadence@claude-community
   ```

No SLA on review timing is published in the docs. Plan for "days, not
hours."

## Until then — self-hosted marketplace still works

Cadence's own repo ships its `.claude-plugin/marketplace.json`, so any
user can install today with no waiting:

```
/plugin marketplace add nik190799/cadence
/plugin install cadence@cadence
```

When the community-marketplace entry goes live, update the README to
prefer the community-marketplace install command (one fewer step for
users).

## What you cannot do automatically

The submission form is a web UI on claude.ai that requires you to be
logged into your Anthropic account. There is no API; submission is
deliberately a human action by the plugin owner. The Cadence repo's
automation cannot submit itself — that's by design.

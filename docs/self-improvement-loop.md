---
layout: default
title: Self-improvement loop
---

# Self-improvement loop

Cadence isn't a static set of rules. It's a system that gets better the
more you use it, with two distinct loops: a local one for your project
and a community one for the canonical framework.

## Local loop (automatic, every user)

```
team run ─▶ /cadence-retro ─▶ Reviewer drafts findings ─▶ Lead approves
              │                                              │
              ▼                                              ▼
          new ADRs / new PATTERNS sections          FRAMEWORK_CHANGELOG.md
          new boundary rules / new DoD lines              (append-only)
```

Every team run ends with `/cadence-retro`. The Reviewer subagent walks
the four-layer mapping table for each observed issue and proposes
specific patches. The human Lead approves, defers, or rejects. Approved
patches land immediately. The framework on YOUR codebase is now
smarter than it was an hour ago.

## Community loop (opt-in, via GitHub)

```
local finding worth sharing
            │
            ▼
GitHub issue (Framework Finding template) at
github.com/nik190799/cadence/issues
            │
            ▼
maintainers triage ─▶ recurring issues → new core ADR / boundary rule
            │
            ▼
next plugin release (SemVer) ships the improvement
            │
            ▼
all users see the upgrade notice
            │
            ▼
/cadence-init --upgrade ─▶ three-way diff against project overrides
                            ─▶ row-by-row approval before any write
```

No telemetry, no central server, no privacy concerns. GitHub is the
improvement engine.

## When to upstream a finding

You should open a [Framework Finding](https://github.com/nik190799/cadence/issues/new?template=framework_finding.md)
when:

- The same issue has come up in **two or more retrospectives** on your
  project — it's not a one-off
- A reasonable other team would likely hit it too — it's not specific
  to your domain
- You can describe it concretely enough for someone else to write a
  fix (proposed layer + concrete action)

When to NOT upstream:

- A project-specific naming convention (keep it in your local PATTERNS)
- A finding tightly coupled to your stack's quirks (consider a stack
  recipe PR instead)
- Something you're not sure about — sit on it, see if it recurs

## What the maintainers do with findings

Triage:

1. **Cluster** — look for similar findings; if many teams hit it, it's
   a strong signal
2. **Pick a layer** — same four-layer mapping the Reviewer uses
3. **Write the upstream ADR / lint / boundary rule / DoD line**
4. **Tag the release** with all related findings in the changelog
5. **Close issues** with links to the release notes

## How a user picks up community improvements

```bash
# Update the plugin
claude plugins update cadence

# Apply new core rules to your local framework
/cadence-init --upgrade
```

`/cadence-init --upgrade` does a three-way diff: old core vs new core
vs your project overrides. You see exactly what's changing and approve
each row. No silent rule injection.

## What stays local forever

Your project's local ADRs, your `PATTERNS.md` overrides, your
`cadence.yaml` boundary rules — these belong to your project and are
never touched by upgrades. Cadence's upgrade flow only merges
**new core rules**; your local extensions are sacred.

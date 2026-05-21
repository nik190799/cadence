---
layout: default
title: Architecture
---

# Architecture

Cadence is structured as four layers with a fifth concept (the
retrospective loop) that wires them together.

## The four layers

### Layer 1 — Patterns

`docs/PATTERNS.md` is your codebase's source of truth on how things
are done. Every rule in it cites an ADR (Architecture Decision Record)
in `docs/ADR/`. ADRs use the [Nygard format](https://www.cognitect.com/blog/2011/11/15/documenting-architecture-decisions):
Status, Context, Decision, Consequences.

When a teammate (human or agent) writes new code, this is what they
align to. When they violate it, the Reviewer cites the section.

### Layer 2 — Process

Three documents:

- **`DEFINITION_OF_DONE.md`** — the checklist every PR must satisfy.
  Each line is either (auto) — enforced mechanically by Layer 3 — or
  (manual) — cited by the Reviewer with the rule it references.
- **`ROLE_SPECS.md`** — what each subagent owns, what handoff signal
  they emit, what they're forbidden to touch.
- **`TEAM_PROTOCOL.md`** — coordination phrases, conflict resolution,
  cadence of status checks.

### Layer 3 — Automated gates

Mechanical, ungaslightable enforcement. Driven by `cadence.yaml`:

```yaml
commands:
  format: ["prettier --check ."]
  lint:   ["eslint ."]
  test:   ["npm test"]
boundaries:
  - where: "src/features/**"
    forbidden: ["src/data/sources/**"]
    reason: "Features must read via narrow providers"
```

`scripts/verify.{sh,ps1}` orchestrates format → lint → boundary check
→ test. `tool/check_boundaries.py` is language-agnostic — it scans
`import`/`require`/`include`/`use`/`from` lines for forbidden path
patterns and works across stacks via line-pattern matching.

### Layer 4 — Launch

`TEAM_LAUNCH_TEMPLATE.md` is the prompt that kicks off a team run.
It has placeholders for feature description, requirements, acceptance
criteria, and out-of-scope. `/cadence-launch <feature>` fills the
placeholders interactively and optionally spawns the four subagents
defined in `ROLE_SPECS.md`.

## The retrospective loop

After every team run, the Reviewer subagent walks a structured table:

| Field | Example |
|---|---|
| What happened | "Feature engineer used write-then-reread for list mutations" |
| Auto-catchable? | "No — judgment call" |
| Rule existed? | "No" |
| Proposed fix | "Write ADR-NNNN + update PATTERNS.md §4" |
| Fix layer | "Layer 1 (Patterns)" |

Each finding maps to exactly one of the four fix layers. The Reviewer
prefers higher numbers descending into lower — if Layer 3 (automation)
can catch it, automate; only fall back to docs (Layer 1) or process
(Layer 2) when automation can't. Layer 4 (launch ambiguity) is the
last resort because launch prompts drift across runs.

The Lead (the human) approves or defers each proposed fix. Approved
fixes land immediately as new ADRs, PATTERNS sections, boundary rules,
or DoD lines. Every change is logged in `FRAMEWORK_CHANGELOG.md`.

## Why this layout

- **Higher numbers → cheaper enforcement.** Lints and CI are
  zero-cognitive-load; docs require humans to read.
- **Every rule is traceable.** PATTERNS cites ADRs; DoD cites
  PATTERNS or ADRs; FRAMEWORK_CHANGELOG cites the triggering
  retrospective. You can always answer "why this rule?"
- **Self-improving by construction.** The retrospective protocol is
  mandatory; the changelog is append-only; the corpus of ADRs grows
  monotonically with experience.

## How this maps to industry standards

- **NIST SSDF v1.1** — practice IDs (PO, PS, PW, RV) annotate the
  artifacts that satisfy them; `/cadence-compliance --standard ssdf`
  rolls them up
- **ISO/IEC 25010:2023** — DoD checklist items carry quality-
  characteristic tags (security, reliability, maintainability, etc.)
- **ADR (Nygard)** — directly used for the ADR format
- **Conventional Commits + SemVer** — Reviewer enforces commit shape
- **Keep a Changelog 1.1.0** — `FRAMEWORK_CHANGELOG.md` format

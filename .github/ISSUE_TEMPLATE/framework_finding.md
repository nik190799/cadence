---
name: Framework finding
about: You ran Cadence on a real project and hit a recurring issue the framework didn't catch. This is THE most valuable contribution shape.
title: "finding: "
labels: ["framework-finding", "needs-triage"]
---

> Framework findings are how Cadence improves. The retrospective protocol
> turns them into ADRs, boundary rules, or DoD lines that make the
> framework better for everyone.

## What happened

<!-- one sentence: the recurring issue you observed -->

## Was it auto-catchable?

- [ ] Yes — a lint, schema, or boundary rule could have caught it
- [ ] No — judgment call, no mechanical detection possible

<!-- if yes, sketch what kind of check would catch it -->

## Was there an existing rule?

- [ ] No rule existed
- [ ] A rule existed but wasn't strict enough — which: `<ADR-NNNN>` / `<PATTERNS.md §X>`
- [ ] A rule existed and was followed — but the rule itself is wrong

## Proposed fix

Which of the four layers does the fix belong in? (Cadence prefers
higher numbers descending into lower — automate if possible.)

- [ ] **Layer 3 (Automated)** — add a lint, schema, or boundary rule
- [ ] **Layer 1 (Patterns)** — write a new ADR, update `PATTERNS.md`
- [ ] **Layer 2 (Process)** — update DoD / ROLE_SPECS / TEAM_PROTOCOL
- [ ] **Layer 4 (Launch)** — update the launch template

### Concrete action

<!-- name files, give example diffs if you have them -->

## Context (helps triage)

- Cadence version: <!-- -->
- Stack: <!-- TypeScript / Python / Go / Rust / Java / Dart-Flutter / other -->
- How often does this issue recur in your team's work? <!-- once / sometimes / always -->
- Sample of the offending code or pattern (anonymized if needed):

```
<!-- paste here -->
```

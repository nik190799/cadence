---
layout: default
title: Cadence
---

# Cadence

A stack-agnostic, self-improving AI development framework as a Claude
Code plugin.

Four layers (Patterns / Process / Automated gates / Launch) plus a
retrospective loop that makes the framework grow as your team uses it.

## Why Cadence

AI agents amplify whatever standards you give them. Without standards,
they amplify chaos. With handwritten standards, they amplify yesterday's
decisions even when those have aged. Cadence is the **discipline of
making the standards explicit, mechanical, and self-improving**.

A short tour:

- **Patterns** ([architecture.md](architecture.md)) — your codebase
  conventions captured as ADRs and a `PATTERNS.md`
- **Process** — Definition of Done, role specs, team protocol
- **Automated gates** — `cadence.yaml`-driven verify pipeline that runs
  format / lint / boundary check / test from your project's own tooling
- **Launch** — a launch template that fills in placeholders and
  (optionally) spawns four coordinated subagents
- **Retrospective loop** — every team run ends by mapping observed
  issues to one of the four layers, then patching that layer

## Get started

- [Quickstart](quickstart.md) — install, init, launch, verify, retro in
  five minutes
- [Architecture](architecture.md) — the four-layer model in detail
- [Self-improvement loop](self-improvement-loop.md) — how the framework
  grows
- [Cross-tool portability](cross-tool-portability.md) — non-Claude-Code
  use

## Standards alignment

- [NIST SSDF v1.1 mapping](standards/nist-ssdf.md)
- [ISO/IEC 25010:2023 mapping](standards/iso-25010.md)

## Recipes

Common stacks with worked-out `cadence.yaml` examples:

- [TypeScript](recipes/typescript.md)
- [Python](recipes/python.md)
- [Go](recipes/go.md)
- [Rust](recipes/rust.md)
- [Java](recipes/java.md)
- [Dart / Flutter](recipes/dart-flutter.md)

## Case studies

- [Flutter sandbox](case-studies/flutter-sandbox.md) — a real Flutter +
  Firebase project where Cadence was prototyped

## Source

[github.com/nik190799/cadence](https://github.com/nik190799/cadence) —
Apache 2.0.

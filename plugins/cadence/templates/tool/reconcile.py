#!/usr/bin/env python3
"""Cadence factory reconciler: STUB, NOT FUNCTIONAL (factory branch, phase 1a).

Runs hourly from the factory workflow's schedule. Scheduled runs and
webhooks can be delayed or dropped, so the reconciler re-derives the
true state from GitHub instead of trusting that every event arrived.

Contract:
    1. Read issues labelled ``factory``, ``spec-ready``, ``approved``,
       ``building`` or ``dod-failed``, their PRs and check results.
    2. Derive each issue's real stage from that state, never from what an
       agent reported about itself.
    3. Re-dispatch stuck work with ``workflow_dispatch`` (stale claims,
       approved issues with no run, runs that died without a ledger record).
    4. Make no model call. In phase 2 this grows into the coordinator's
       sweep, which calls a model only when the derived state changed.

Exit codes:
    0   ok (including "nothing to do")
    1   a GitHub API call failed
    2   bad input, or not implemented yet
"""

from __future__ import annotations

import argparse
import sys


def sweep(dry_run: bool) -> int:
    raise NotImplementedError("phase 1a")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="report, do not dispatch")
    parser.parse_args(argv)
    print("reconcile.py is a stub (factory branch, phase 1a)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

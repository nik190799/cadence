#!/usr/bin/env python3
"""Cadence factory issue claim: STUB, NOT FUNCTIONAL (factory branch, phase 1a).

Guarantees that only one run works on an issue at a time. Labels and
assignees cannot do this, because GitHub offers no compare-and-swap on
them; an atomic ref push can.

Contract:
    acquire  Create ``refs/heads/cadence/claim/<issue>`` with
             ``git push --force-with-lease=refs/heads/cadence/claim/<issue>:``
             (empty expected value), which fails if the ref already exists.
             Exit 1 if another run holds the claim.
    release  Delete the claim ref after the run publishes or gives up.
    stale    List claims older than a timeout, for the reconciler.

Workflows must never trigger on pushes to ``cadence/**`` branches, or the
claim itself would start new runs.

Exit codes:
    0   ok
    1   claim held by another run
    2   bad input, or not implemented yet
"""

from __future__ import annotations

import argparse
import sys


def acquire(issue: int) -> bool:
    raise NotImplementedError("phase 1a")


def release(issue: int) -> None:
    raise NotImplementedError("phase 1a")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("acquire", "release"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--issue", type=int, required=True)
    sub.add_parser("stale", help="list claims older than the timeout")
    parser.parse_args(argv)
    print("claim.py is a stub (factory branch, phase 1a)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

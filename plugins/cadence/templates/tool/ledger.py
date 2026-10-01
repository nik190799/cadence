#!/usr/bin/env python3
"""Cadence factory cost ledger: STUB, NOT FUNCTIONAL (factory branch, phase 1a).

Keeps model spend bounded and every run accounted for.

Contract:
    check   Before dispatching a run, sum today's recorded spend and the
            worst case of the runs already in flight (per-run cap x count).
            Exit non-zero if starting one more run could exceed the daily
            cap in ``.cadence/factory.yaml``.
    record  After a run (always, even on failure or timeout), append one
            JSON record: issue, run_id, run_attempt, outcome, DoD result,
            total_cost_usd, num_turns. A run that reports no cost is booked
            at its full per-run cap, never at zero.

Records are append-only files named ``<run_id>-<run_attempt>.json`` on a
state branch, so parallel runs never write the same file. Cadence never
pays for or proxies model usage; this only reads what the user's own run
reports.

Exit codes:
    0   ok
    1   over budget (check) or write failed (record)
    2   bad input, or not implemented yet
"""

from __future__ import annotations

import argparse
import sys


def check(daily_cap_usd: float, per_run_cap_usd: float) -> bool:
    raise NotImplementedError("phase 1a")


def record(run_id: str, run_attempt: int, issue: int) -> None:
    raise NotImplementedError("phase 1a")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="refuse to dispatch if the daily cap could be exceeded")
    sub.add_parser("record", help="append this run's cost and outcome")
    parser.parse_args(argv)
    print("ledger.py is a stub (factory branch, phase 1a)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Cadence factory cost ledger: keeps model spend bounded and every run accounted for.

Cadence never pays for, resells or proxies model usage. The ledger only
reads what the user's own run reports (the Claude Code result) and books
it against caps the user set in ``.cadence/factory.yaml``. It is pure
filesystem: the workflow syncs the records directory with a state branch
separately.

Config (``--config``, default ``.cadence/factory.yaml``)::

    budget:
      per_run_usd: 5.00   # hard cap for one run (--max-budget-usd)
      daily_usd: 25.00    # cap for one UTC day
    max_turns: 60         # optional; a positive integer when present
    autonomy: pr-only     # not read by the ledger

    Both budget numbers must be present, finite and > 0, and
    ``per_run_usd <= daily_usd``. Anything else exits 2.

Records (``--records-dir``, default ``.cadence/runs``):
    One JSON file per run attempt, ``<run_id>-<run_attempt>.json``,
    created with exclusive create so a record is never overwritten. Each
    holds: issue, run_id, run_attempt, outcome, dod, total_cost_usd
    (number or null), booked_usd, cost_source ("reported" or "cap"),
    num_turns (or null), per_run_cap_usd, recorded_at (ISO 8601 UTC,
    whole seconds) and, only when a reported cost exceeds the cap,
    ``"over_cap": true``.

Contract:
    record  After every run attempt (success, failure, cancel or
            timeout), write its record. Cost comes from ``--cost-usd``,
            else from ``--result-json``: a Claude Code result that is a
            JSON object, a JSON array of messages, or a JSON-lines
            stream. The LAST object carrying ``total_cost_usd`` wins.
            Missing or corrupt files are tolerated. A run with no known
            cost is booked at the full per-run cap, never at zero. A
            reported cost above the cap is booked as reported.
    check   Before dispatching a run, print
            ``{spent_today, in_flight, per_run_usd, daily_usd,
            worst_case, allowed, unreadable}`` where
            ``worst_case = spent_today + in_flight * per_run_usd +
            per_run_usd`` (the runs already going, plus the one about to
            start). ``spent_today`` sums ``booked_usd`` over records
            whose ``recorded_at`` falls on the same UTC date as now. A
            record that cannot be read or lacks a valid ``booked_usd`` or
            ``recorded_at`` is counted in ``unreadable`` and booked at the
            full per-run cap on every day until a human repairs it.

Exit codes:
    0   ok (record written; check allows one more run)
    1   over budget (check), or a record for this run attempt already
        exists (record)
    2   bad input or config (missing/invalid factory.yaml, bad
        arguments, IO error), or an internal error (never exit 1)

Usage:
    python tool/ledger.py check --in-flight 2
    python tool/ledger.py record --run-id 123 --run-attempt 1 --issue 42 \\
        --outcome success --dod pass --result-json claude-result.json
    python tool/ledger.py record --run-id 123 --run-attempt 2 --issue 42 \\
        --outcome timeout
    python tool/ledger.py --config .cadence/factory.yaml \\
        --records-dir .cadence/runs check --now 1790000000

``--config`` and ``--records-dir`` are accepted before or after the
subcommand. ``--now`` (epoch seconds) pins the clock for tests and
replays; it defaults to the current time.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import re
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

try:
    import yaml
except ImportError:
    print(
        "ERROR: PyYAML is required. Install with: pip install pyyaml",
        file=sys.stderr,
    )
    sys.exit(2)


DEFAULT_CONFIG = Path(".cadence") / "factory.yaml"
DEFAULT_RECORDS_DIR = Path(".cadence") / "runs"

OUTCOMES: tuple[str, ...] = ("success", "failure", "cancelled", "timeout")
DOD_RESULTS: tuple[str, ...] = ("pass", "fail", "skipped", "unknown")

EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_BAD_INPUT = 2

# Run ids become file names, so keep them to a path-safe alphabet. Always
# used with fullmatch: ``$`` would also accept a trailing newline.
_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class LedgerError(Exception):
    """Bad input or config. ``main`` reports it and exits 2."""


class DuplicateRecord(Exception):
    """A record for this run_id + run_attempt already exists. Exit 1."""


@dataclass(frozen=True)
class Config:
    per_run_usd: float
    daily_usd: float
    max_turns: int | None
    autonomy: str | None


@dataclass(frozen=True)
class ReportedUsage:
    total_cost_usd: float | None
    num_turns: int | None


# --- small helpers ---------------------------------------------------------


def _is_number(value: Any) -> bool:
    """True for a finite int or float. YAML/JSON booleans are not numbers.

    An integer too large for a float (JSON and YAML allow any number of
    digits) is not a usable amount either.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _is_count(value: Any) -> bool:
    """True for a non-negative integer (not a bool)."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _dec(value: float) -> Decimal:
    """Exact decimal for summing money, so ten $0.10 runs book $1.00."""
    return Decimal(str(value))


def to_utc(epoch: float) -> datetime:
    """Whole-second UTC datetime for ``epoch``.

    Flooring (not rounding) keeps a record and a check made in the same
    second on the same UTC day, and works for any epoch on every OS.
    """
    try:
        return _EPOCH + timedelta(seconds=math.floor(epoch))
    except (OverflowError, ValueError) as exc:
        raise LedgerError(f"--now {epoch!r} is not a usable timestamp") from exc


def iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_utc(text: Any) -> datetime | None:
    """Parse ``recorded_at``; None if it is not an ISO 8601 timestamp.

    Accepts a trailing ``Z`` on Python 3.10 too. A timestamp without an
    offset is taken as UTC.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    raw = text.strip()
    if raw[-1] in "Zz":
        raw = raw[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(raw)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        # Converting can leave datetime's range, e.g. year 9999 at -05:00.
        return moment.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


# --- config ----------------------------------------------------------------


def validate_config(raw: Any, source: str = "factory.yaml") -> Config:
    """Return the validated config or raise LedgerError naming the problem."""
    if not isinstance(raw, dict):
        raise LedgerError(f"{source} must be a YAML mapping")
    budget = raw.get("budget")
    if not isinstance(budget, dict):
        raise LedgerError(
            f"{source} needs a 'budget' mapping with per_run_usd and daily_usd"
        )

    numbers: dict[str, float] = {}
    for key in ("per_run_usd", "daily_usd"):
        if key not in budget or budget[key] is None:
            raise LedgerError(f"{source}: budget.{key} is missing")
        value = budget[key]
        if not _is_number(value):
            raise LedgerError(
                f"{source}: budget.{key} must be a number in US dollars "
                f"(got {value!r})"
            )
        if value <= 0:
            raise LedgerError(f"{source}: budget.{key} must be > 0 (got {value!r})")
        numbers[key] = float(value)

    if numbers["per_run_usd"] > numbers["daily_usd"]:
        raise LedgerError(
            f"{source}: budget.per_run_usd ({numbers['per_run_usd']}) must not "
            f"exceed budget.daily_usd ({numbers['daily_usd']}); no run could "
            "ever start"
        )

    max_turns = raw.get("max_turns")
    if max_turns is not None and not (_is_count(max_turns) and max_turns > 0):
        raise LedgerError(
            f"{source}: max_turns must be a positive integer (got {max_turns!r})"
        )

    autonomy = raw.get("autonomy")
    return Config(
        per_run_usd=numbers["per_run_usd"],
        daily_usd=numbers["daily_usd"],
        max_turns=max_turns,
        autonomy=None if autonomy is None else str(autonomy),
    )


def load_config(path: Path) -> Config:
    if not path.is_file():
        raise LedgerError(
            f"factory config not found: {path}. Run /cadence-factory-setup "
            "or pass --config."
        )
    try:
        with path.open("r", encoding="utf-8-sig") as fh:
            raw = yaml.safe_load(fh)
    except (yaml.YAMLError, ValueError, OverflowError, RecursionError) as exc:
        # PyYAML raises ValueError for date-like scalars such as 2001-13-45.
        raise LedgerError(f"malformed YAML in {path}: {exc}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise LedgerError(f"could not read {path}: {exc}") from exc
    return validate_config(raw, str(path))


# --- reading a Claude Code result -----------------------------------------


def _dicts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _json_objects(text: str) -> Iterator[dict[str, Any]]:
    """The JSON objects in a result, in order.

    Tries the whole text as one JSON value (object or array) first, then
    falls back to JSON lines, skipping lines that do not parse.

    Lines are split on ``\\n`` only. ``str.splitlines`` would also split on
    U+2028, U+2029 and U+0085, which JSON allows unescaped inside strings
    (Node's JSON.stringify writes them raw), and would drop the final
    result. Objects are yielded one at a time so a long stream is never
    held in memory as a whole.
    """
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        pass
    else:
        yield from _dicts(value)
        return
    for line in io.StringIO(text, newline="\n"):
        line = line.strip()
        if not line:
            continue
        try:
            yield from _dicts(json.loads(line))
        except (ValueError, RecursionError):
            continue


def parse_result(text: str) -> ReportedUsage:
    """Cost and turns from the LAST object that carries ``total_cost_usd``.

    If that object's cost is not a finite, non-negative number the cost
    is unknown (and will be booked at the cap); an earlier, smaller
    figure is never used in its place.
    """
    last: dict[str, Any] | None = None
    for obj in _json_objects(text):
        if "total_cost_usd" in obj:
            last = obj
    if last is None:
        return ReportedUsage(total_cost_usd=None, num_turns=None)
    cost = last.get("total_cost_usd")
    turns = last.get("num_turns")
    return ReportedUsage(
        total_cost_usd=float(cost) if _is_number(cost) and cost >= 0 else None,
        num_turns=turns if _is_count(turns) else None,
    )


def read_result_json(path: Path) -> ReportedUsage:
    """Like ``parse_result`` but tolerant of a missing or unreadable file."""
    try:
        text = path.read_bytes().decode("utf-8-sig", errors="replace")
        return parse_result(text)
    except OSError as exc:
        print(f"WARN: could not read result {path}: {exc}", file=sys.stderr)
    except MemoryError:
        # The record must still be written, so the cost becomes unknown
        # (booked at the cap) instead of the run going unrecorded.
        print(f"WARN: result {path} is too large to read", file=sys.stderr)
    return ReportedUsage(total_cost_usd=None, num_turns=None)


# --- record ----------------------------------------------------------------


def record_path(records_dir: Path, run_id: str, run_attempt: int) -> Path:
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        raise LedgerError(
            f"run id {run_id!r} must be 1-128 characters of letters, digits, "
            "'.', '_' or '-', starting with a letter or digit"
        )
    if not (_is_count(run_attempt) and run_attempt >= 1):
        raise LedgerError(f"run attempt must be an integer >= 1 (got {run_attempt!r})")
    return records_dir / f"{run_id}-{run_attempt}.json"


def build_record(
    *,
    config: Config,
    run_id: str,
    run_attempt: int,
    issue: int,
    outcome: str,
    dod: str,
    usage: ReportedUsage,
    now: float,
) -> dict[str, Any]:
    if outcome not in OUTCOMES:
        raise LedgerError(f"outcome must be one of {', '.join(OUTCOMES)}")
    if dod not in DOD_RESULTS:
        raise LedgerError(f"dod must be one of {', '.join(DOD_RESULTS)}")
    if not (_is_count(issue) and issue >= 1):
        raise LedgerError(f"issue must be an integer >= 1 (got {issue!r})")

    cap = config.per_run_usd
    cost = usage.total_cost_usd
    record: dict[str, Any] = {
        "issue": issue,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "outcome": outcome,
        "dod": dod,
        "total_cost_usd": cost,
        "booked_usd": cap if cost is None else cost,
        "cost_source": "cap" if cost is None else "reported",
        "num_turns": usage.num_turns,
        "per_run_cap_usd": cap,
        "recorded_at": iso_utc(to_utc(now)),
    }
    if cost is not None and cost > cap:
        record["over_cap"] = True
    return record


def write_record(records_dir: Path, record: dict[str, Any]) -> Path:
    """Write ``record`` with exclusive create. Never overwrites."""
    path = record_path(records_dir, record["run_id"], record["run_attempt"])
    payload = json.dumps(record, indent=2) + "\n"
    records_dir.mkdir(parents=True, exist_ok=True)
    try:
        fh = path.open("x", encoding="utf-8", newline="\n")
    except FileExistsError as exc:
        raise DuplicateRecord(str(path)) from exc
    try:
        with fh:
            fh.write(payload)
    except OSError:
        # We created this file, so removing a half-written copy is safe and
        # lets a retry write the whole record.
        path.unlink(missing_ok=True)
        raise
    return path


# --- check -----------------------------------------------------------------


def _booking(record: Any) -> tuple[date, Decimal] | None:
    """(UTC day, booked amount) of a record, or None if it is not usable."""
    if not isinstance(record, dict):
        return None
    booked = record.get("booked_usd")
    if not _is_number(booked) or booked < 0:
        return None
    moment = parse_iso_utc(record.get("recorded_at"))
    if moment is None:
        return None
    return moment.date(), _dec(booked)


def tally_day(
    records_dir: Path, day: date, per_run_usd: float
) -> tuple[Decimal, list[Path]]:
    """Spend booked on ``day`` (UTC) and the records that could not be read.

    Each unreadable record is booked at ``per_run_usd``: its day is unknown,
    so it is assumed to be today.
    """
    if not records_dir.exists():
        return Decimal(0), []
    if not records_dir.is_dir():
        raise LedgerError(f"records dir {records_dir} is not a directory")

    spent = Decimal(0)
    unreadable: list[Path] = []
    for path in sorted(records_dir.glob("*.json")):
        if not path.is_file():
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            record = None
        booking = _booking(record)
        if booking is None:
            unreadable.append(path)
            spent += _dec(per_run_usd)
        elif booking[0] == day:
            spent += booking[1]
    return spent, unreadable


def check_budget(
    config: Config, records_dir: Path, in_flight: int, now: float
) -> tuple[dict[str, Any], list[Path]]:
    if not _is_count(in_flight):
        raise LedgerError(f"--in-flight must be an integer >= 0 (got {in_flight!r})")
    spent, unreadable = tally_day(
        records_dir, to_utc(now).date(), config.per_run_usd
    )
    per_run = _dec(config.per_run_usd)
    worst = spent + in_flight * per_run + per_run
    allowed = worst <= _dec(config.daily_usd)
    report = {
        "spent_today": float(spent),
        "in_flight": in_flight,
        "per_run_usd": config.per_run_usd,
        "daily_usd": config.daily_usd,
        "worst_case": float(worst),
        "allowed": allowed,
        "unreadable": len(unreadable),
    }
    return report, unreadable


# --- CLI -------------------------------------------------------------------


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1 (got {value})")
    return value


def _count(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {text!r}") from None
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0 (got {value})")
    return value


def _finite_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError(f"must be finite (got {text!r})")
    return value


def _cost(text: str) -> float:
    value = _finite_float(text)
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0 (got {text!r})")
    return value


def _build_parser() -> argparse.ArgumentParser:
    # Accept --config / --records-dir after the subcommand too. SUPPRESS
    # keeps the top-level value unless the option is given again there.
    paths = argparse.ArgumentParser(add_help=False)
    paths.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    paths.add_argument("--records-dir", type=Path, default=argparse.SUPPRESS)

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"path to factory.yaml (default: {DEFAULT_CONFIG.as_posix()})",
    )
    parser.add_argument(
        "--records-dir",
        type=Path,
        default=DEFAULT_RECORDS_DIR,
        help=f"directory of run records (default: {DEFAULT_RECORDS_DIR.as_posix()})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser(
        "record",
        parents=[paths],
        help="write this run attempt's cost and outcome (never overwrites)",
    )
    rec.add_argument("--run-id", required=True, help="workflow run id")
    rec.add_argument("--run-attempt", required=True, type=_positive_int)
    rec.add_argument("--issue", required=True, type=_positive_int)
    rec.add_argument("--outcome", required=True, choices=OUTCOMES)
    rec.add_argument("--dod", choices=DOD_RESULTS, default="unknown")
    rec.add_argument(
        "--result-json",
        type=Path,
        help="Claude Code result (object, array or JSON lines); "
        "missing or corrupt files are tolerated",
    )
    rec.add_argument(
        "--cost-usd",
        type=_cost,
        help="reported cost in USD; overrides --result-json",
    )
    rec.add_argument(
        "--turns", type=_count, help="turns used; overrides --result-json"
    )
    rec.add_argument(
        "--now", type=_finite_float, help="epoch seconds (default: current time)"
    )

    chk = sub.add_parser(
        "check",
        parents=[paths],
        help="exit 1 if starting one more run could exceed the daily cap",
    )
    chk.add_argument(
        "--in-flight",
        type=_count,
        default=0,
        help="runs already dispatched and not yet recorded (default: 0)",
    )
    chk.add_argument(
        "--now", type=_finite_float, help="epoch seconds (default: current time)"
    )
    return parser


def _cmd_record(args: argparse.Namespace, config: Config, now: float) -> int:
    usage = ReportedUsage(total_cost_usd=args.cost_usd, num_turns=args.turns)
    if args.result_json is not None and (
        usage.total_cost_usd is None or usage.num_turns is None
    ):
        parsed = read_result_json(args.result_json)
        usage = ReportedUsage(
            total_cost_usd=(
                usage.total_cost_usd
                if usage.total_cost_usd is not None
                else parsed.total_cost_usd
            ),
            num_turns=usage.num_turns if usage.num_turns is not None else parsed.num_turns,
        )

    record = build_record(
        config=config,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        issue=args.issue,
        outcome=args.outcome,
        dod=args.dod,
        usage=usage,
        now=now,
    )
    path = write_record(args.records_dir, record)

    if record["cost_source"] == "cap":
        print(
            f"WARN: no cost reported for run {args.run_id} attempt "
            f"{args.run_attempt}; booked the full per-run cap "
            f"${config.per_run_usd:.2f}",
            file=sys.stderr,
        )
    if record.get("over_cap"):
        print(
            f"WARN: reported cost ${record['total_cost_usd']:.2f} exceeds the "
            f"per-run cap ${config.per_run_usd:.2f}; booked as reported",
            file=sys.stderr,
        )
    print(f"recorded {path}", file=sys.stderr)
    print(json.dumps(record))
    return EXIT_OK


def _cmd_check(args: argparse.Namespace, config: Config, now: float) -> int:
    report, unreadable = check_budget(config, args.records_dir, args.in_flight, now)
    for path in unreadable:
        print(
            f"WARN: unreadable ledger record {path}; booked at the full "
            f"per-run cap ${config.per_run_usd:.2f} until it is repaired",
            file=sys.stderr,
        )
    print(json.dumps(report))
    if report["allowed"]:
        return EXIT_OK
    print(
        f"over budget: worst case ${report['worst_case']:.2f} "
        f"(spent today ${report['spent_today']:.2f} + {report['in_flight']} "
        f"in flight + 1 new, at ${config.per_run_usd:.2f} each) exceeds the "
        f"daily cap ${config.daily_usd:.2f}. Not dispatching.",
        file=sys.stderr,
    )
    return EXIT_BLOCKED


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    now = args.now if args.now is not None else time.time()
    try:
        config = load_config(args.config)
        if args.command == "record":
            return _cmd_record(args, config, now)
        return _cmd_check(args, config, now)
    except DuplicateRecord as exc:
        print(
            f"ERROR: ledger record already exists: {exc}. Records are never "
            "overwritten; a retried run gets a new --run-attempt.",
            file=sys.stderr,
        )
        return EXIT_BLOCKED
    except LedgerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT
    except Exception:  # noqa: BLE001 - see below
        # An uncaught exception would exit 1, which this contract reserves
        # for "over budget" and "record already exists". A workflow that
        # treats an existing record as done would then lose the run's cost.
        traceback.print_exc()
        print("ERROR: internal error in ledger.py (see traceback)", file=sys.stderr)
        return EXIT_BAD_INPUT


if __name__ == "__main__":
    sys.exit(main())

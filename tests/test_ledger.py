"""Tests for the factory cost ledger (tool/ledger.py).

The ledger is the factory's spend guard: ``check`` refuses to dispatch a
run that could push the UTC day past its cap, and ``record`` books every
run attempt, at the full per-run cap whenever the run reports no cost.
These tests pin both halves, plus the config template the setup skill
renders.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = REPO_ROOT / "plugins" / "cadence" / "templates" / "tool" / "ledger.py"
TEMPLATE_PATH = REPO_ROOT / "plugins" / "cadence" / "templates" / "factory.yaml.tmpl"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, name
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ledger = _load_module("cadence_ledger", LEDGER_PATH)


GOOD_CONFIG = """\
budget:
  per_run_usd: 5.00
  daily_usd: 25.00
max_turns: 60
autonomy: pr-only
"""


def _epoch(iso: str) -> int:
    """Epoch seconds for a naive ISO timestamp read as UTC."""
    return int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp())


NOON = _epoch("2026-10-01T12:00:00")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    config = tmp_path / ".cadence" / "factory.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(GOOD_CONFIG, encoding="utf-8")
    return tmp_path


def _records(project: Path) -> Path:
    return project / ".cadence" / "runs"


def _main(project: Path, *args: str) -> int:
    return ledger.main(
        [
            *args,
            "--config",
            str(project / ".cadence" / "factory.yaml"),
            "--records-dir",
            str(_records(project)),
        ]
    )


def _record(
    project: Path,
    *extra: str,
    run_id: str = "100",
    attempt: int = 1,
    now: int = NOON,
) -> int:
    return _main(
        project,
        "record",
        "--run-id",
        run_id,
        "--run-attempt",
        str(attempt),
        "--issue",
        "7",
        "--outcome",
        "success",
        "--now",
        str(now),
        *extra,
    )


def _read_record(project: Path, run_id: str = "100", attempt: int = 1) -> dict:
    path = _records(project) / f"{run_id}-{attempt}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _check(project: Path, capsys, *, in_flight: int = 0, now: int = NOON):
    capsys.readouterr()  # drop output from earlier calls
    rc = _main(project, "check", "--in-flight", str(in_flight), "--now", str(now))
    return rc, json.loads(capsys.readouterr().out)


# --- 1. Config validation ---------------------------------------------------


@pytest.mark.parametrize(
    "text,needle",
    [
        ("max_turns: 60\n", "budget"),
        ("budget: 5\n", "budget"),
        ("budget:\n  daily_usd: 25\n", "per_run_usd"),
        ("budget:\n  per_run_usd: 5\n", "daily_usd"),
        ("budget:\n  per_run_usd: -5\n  daily_usd: 25\n", "per_run_usd"),
        ("budget:\n  per_run_usd: 5\n  daily_usd: -25\n", "daily_usd"),
        ("budget:\n  per_run_usd: 0\n  daily_usd: 25\n", "per_run_usd"),
        ("budget:\n  per_run_usd: 30\n  daily_usd: 25\n", "exceed"),
        ("budget:\n  per_run_usd: '5'\n  daily_usd: 25\n", "number"),
        ("budget:\n  per_run_usd: true\n  daily_usd: 25\n", "number"),
        ("budget:\n  per_run_usd: .nan\n  daily_usd: 25\n", "number"),
        ("budget:\n  per_run_usd: 5\n  daily_usd: 25\nmax_turns: 0\n", "max_turns"),
        ("- budget\n", "mapping"),
        ("budget: [unclosed\n", "malformed"),
        # an integer too large for a float used to crash with exit 1
        ("budget:\n  per_run_usd: 5\n  daily_usd: 1" + "0" * 400 + "\n", "number"),
        # PyYAML raises ValueError (not YAMLError) for an impossible date
        ("budget:\n  per_run_usd: 2001-13-45\n  daily_usd: 25\n", "malformed"),
    ],
)
def test_bad_config_exits_2(project, capsys, text, needle):
    (project / ".cadence" / "factory.yaml").write_text(text, encoding="utf-8")
    assert _main(project, "check") == 2
    assert needle in capsys.readouterr().err
    # record refuses too, and writes nothing
    assert _record(project, "--cost-usd", "1") == 2
    assert not _records(project).exists()


def test_missing_config_exits_2(tmp_path, capsys):
    rc = ledger.main(
        ["--config", str(tmp_path / "nope.yaml"), "--records-dir", str(tmp_path), "check"]
    )
    assert rc == 2
    assert "not found" in capsys.readouterr().err


def test_per_run_equal_to_daily_is_valid():
    config = ledger.validate_config({"budget": {"per_run_usd": 10, "daily_usd": 10}})
    assert config.per_run_usd == 10.0
    assert config.daily_usd == 10.0


def test_template_parses_and_passes_validation(tmp_path, capsys):
    config = ledger.load_config(TEMPLATE_PATH)
    assert config.per_run_usd == 5.0
    assert config.daily_usd == 25.0
    assert config.max_turns == 60
    assert config.autonomy == "pr-only"
    assert config.retry_on_dod_fail == 1

    rc = ledger.main(
        [
            "--config",
            str(TEMPLATE_PATH),
            "--records-dir",
            str(tmp_path / "runs"),
            "check",
            "--now",
            str(NOON),
        ]
    )
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["allowed"] is True


# --- 2. record with --cost-usd; duplicates ----------------------------------


def test_record_with_cost_writes_expected_json(project):
    rc = _record(project, "--cost-usd", "1.25", "--turns", "12", "--dod", "pass")
    assert rc == 0
    assert _read_record(project) == {
        "issue": 7,
        "run_id": "100",
        "run_attempt": 1,
        "outcome": "success",
        "dod": "pass",
        "total_cost_usd": 1.25,
        "booked_usd": 1.25,
        "cost_source": "reported",
        "num_turns": 12,
        "per_run_cap_usd": 5.0,
        "recorded_at": "2026-10-01T12:00:00Z",
    }


def test_record_defaults_dod_to_unknown(project):
    assert _record(project, "--cost-usd", "1") == 0
    assert _read_record(project)["dod"] == "unknown"


def test_duplicate_record_exits_1_and_keeps_original(project, capsys):
    assert _record(project, "--cost-usd", "1.25") == 0
    path = _records(project) / "100-1.json"
    original = path.read_bytes()

    assert _record(project, "--cost-usd", "0.01", "--now", str(NOON + 60)) == 1
    assert "already exists" in capsys.readouterr().err
    assert path.read_bytes() == original

    # A retry is a new attempt and gets its own file.
    assert _record(project, "--cost-usd", "0.01", attempt=2) == 0
    assert _read_record(project, attempt=2)["booked_usd"] == 0.01
    assert path.read_bytes() == original


def test_unsafe_run_id_is_rejected(project, capsys):
    assert _record(project, "--cost-usd", "1", run_id="../escape") == 2
    assert "run id" in capsys.readouterr().err
    assert not (project / ".cadence" / "escape-1.json").exists()


@pytest.mark.parametrize("run_id", ["abc\n", "abc\r", "a/b", "a\\b", ".x", "_x", "a b"])
def test_run_id_must_match_in_full(project, capsys, run_id):
    # A '$' anchor would accept "abc\n" and create a file name with a newline.
    assert _record(project, "--cost-usd", "1", run_id=run_id) == 2
    assert "run id" in capsys.readouterr().err
    assert not _records(project).exists() or not any(_records(project).iterdir())


def test_bad_record_arguments_exit_2(project):
    for extra in (["--cost-usd", "-1"], ["--cost-usd", "nan"], ["--outcome", "maybe"]):
        with pytest.raises(SystemExit) as exc_info:
            _record(project, *extra)
        assert exc_info.value.code == 2
    with pytest.raises(SystemExit) as exc_info:
        _record(project, attempt=0)
    assert exc_info.value.code == 2


# --- 3. no cost info -> book the cap ---------------------------------------


@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled", "timeout"])
def test_record_without_cost_books_the_cap(project, capsys, outcome):
    assert _record(project, "--outcome", outcome) == 0
    record = _read_record(project)
    assert record["outcome"] == outcome
    assert record["total_cost_usd"] is None
    assert record["booked_usd"] == 5.0
    assert record["cost_source"] == "cap"
    assert record["num_turns"] is None
    assert "over_cap" not in record
    assert "booked the full per-run cap" in capsys.readouterr().err


# --- 4. --result-json forms -------------------------------------------------


def _record_from_result(project: Path, tmp_path: Path, content: str | bytes) -> dict:
    result = tmp_path / "result.json"
    if isinstance(content, bytes):
        result.write_bytes(content)
    else:
        result.write_text(content, encoding="utf-8")
    assert _record(project, "--result-json", str(result)) == 0
    return _read_record(project)


def test_result_json_object(project, tmp_path):
    record = _record_from_result(
        project,
        tmp_path,
        json.dumps({"type": "result", "total_cost_usd": 0.42, "num_turns": 9}),
    )
    assert record["total_cost_usd"] == 0.42
    assert record["booked_usd"] == 0.42
    assert record["cost_source"] == "reported"
    assert record["num_turns"] == 9


def test_result_json_array_last_result_wins(project, tmp_path):
    messages = [
        {"type": "system", "subtype": "init"},
        {"type": "result", "total_cost_usd": 0.10, "num_turns": 2},
        {"type": "assistant", "message": {"content": "working"}},
        {"type": "result", "total_cost_usd": 1.75, "num_turns": 20},
        {"type": "user", "message": {"content": "tool output"}},
    ]
    record = _record_from_result(project, tmp_path, json.dumps(messages))
    assert record["total_cost_usd"] == 1.75
    assert record["num_turns"] == 20
    assert record["cost_source"] == "reported"


def test_result_json_lines_last_result_wins(project, tmp_path):
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "result", "total_cost_usd": 0.30, "num_turns": 3}),
        '{"type": "assistant", "truncated',  # corrupt line is skipped
        "",
        json.dumps({"type": "result", "total_cost_usd": 2.5, "num_turns": 31}),
        json.dumps({"type": "user"}),
    ]
    record = _record_from_result(project, tmp_path, "\n".join(lines) + "\n")
    assert record["total_cost_usd"] == 2.5
    assert record["num_turns"] == 31


def test_result_json_with_bom(project, tmp_path):
    content = b"\xef\xbb\xbf" + json.dumps({"total_cost_usd": 0.5}).encode("utf-8")
    record = _record_from_result(project, tmp_path, content)
    assert record["total_cost_usd"] == 0.5
    assert record["num_turns"] is None


@pytest.mark.parametrize(
    "content",
    [
        '{"type": "result", "total_cost_usd": 1.0',  # truncated
        "",
        b"\x00\xff\xfe garbage \x80",
        "not json at all",
        json.dumps({"type": "result", "num_turns": 4}),  # no cost key
        json.dumps({"total_cost_usd": -1.0}),
        json.dumps({"total_cost_usd": "1.00"}),
        json.dumps({"total_cost_usd": True}),
        json.dumps(42),
        # the last cost-bearing object is broken: never fall back to an
        # earlier, smaller figure
        json.dumps([{"total_cost_usd": 0.01}, {"total_cost_usd": None}]),
        # an integer too large for a float used to crash with exit 1 and
        # leave the run unrecorded
        '{"total_cost_usd": 1' + "0" * 400 + "}",
        json.dumps({"total_cost_usd": 1e400}),  # parses as inf
    ],
)
def test_corrupt_result_json_books_the_cap(project, tmp_path, content):
    record = _record_from_result(project, tmp_path, content)
    assert record["total_cost_usd"] is None
    assert record["booked_usd"] == 5.0
    assert record["cost_source"] == "cap"


def test_result_json_lines_keep_unicode_line_separators(project, tmp_path):
    # JSON allows U+2028, U+2029 and U+0085 unescaped inside strings, and
    # Node's JSON.stringify writes them raw. Splitting on them dropped the
    # final result, so the earlier $0.05 was booked instead of $4.80.
    final = json.dumps(
        {"type": "result", "total_cost_usd": 4.8, "num_turns": 55,
         "result": "a b c\x85d"},
        ensure_ascii=False,
    )
    lines = [json.dumps({"type": "result", "total_cost_usd": 0.05, "num_turns": 1}), final]
    record = _record_from_result(project, tmp_path, ("\r\n".join(lines) + "\r\n").encode("utf-8"))
    assert record["total_cost_usd"] == 4.8
    assert record["num_turns"] == 55


def test_result_too_large_for_memory_books_the_cap(project, tmp_path, monkeypatch):
    def out_of_memory(text):
        raise MemoryError

    monkeypatch.setattr(ledger, "parse_result", out_of_memory)
    record = _record_from_result(project, tmp_path, json.dumps({"total_cost_usd": 1.0}))
    assert record["booked_usd"] == 5.0
    assert record["cost_source"] == "cap"


def test_internal_error_exits_2_never_1(project, capsys, monkeypatch):
    # Exit 1 means "duplicate record" or "over budget"; a crash must not
    # be mistaken for either.
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(ledger, "build_record", boom)
    monkeypatch.setattr(ledger, "check_budget", boom)
    assert _record(project, "--cost-usd", "1") == 2
    assert _main(project, "check", "--now", str(NOON)) == 2
    assert "internal error" in capsys.readouterr().err


def test_missing_result_json_books_the_cap(project, tmp_path):
    assert _record(project, "--result-json", str(tmp_path / "absent.json")) == 0
    record = _read_record(project)
    assert record["booked_usd"] == 5.0
    assert record["cost_source"] == "cap"


def test_flags_override_result_json(project, tmp_path):
    result = tmp_path / "result.json"
    result.write_text(
        json.dumps({"total_cost_usd": 3.0, "num_turns": 30}), encoding="utf-8"
    )
    assert _record(project, "--result-json", str(result), "--cost-usd", "0.75") == 0
    record = _read_record(project)
    assert record["total_cost_usd"] == 0.75
    assert record["num_turns"] == 30  # not given as a flag, so from the result

    assert (
        _record(project, "--result-json", str(result), "--turns", "4", attempt=2) == 0
    )
    record = _read_record(project, attempt=2)
    assert record["total_cost_usd"] == 3.0
    assert record["num_turns"] == 4


# --- 5. reported cost above the cap ----------------------------------------


def test_reported_cost_above_cap_is_booked_as_reported(project, capsys):
    assert _record(project, "--cost-usd", "7.5") == 0
    record = _read_record(project)
    assert record["over_cap"] is True
    assert record["total_cost_usd"] == 7.5
    assert record["booked_usd"] == 7.5
    assert record["cost_source"] == "reported"
    assert "exceeds the per-run cap" in capsys.readouterr().err


def test_cost_at_cap_is_not_over_cap(project):
    assert _record(project, "--cost-usd", "5") == 0
    assert "over_cap" not in _read_record(project)


# --- 6. check ---------------------------------------------------------------


def test_check_empty_dir_allows(project, capsys):
    rc, report = _check(project, capsys)
    assert rc == 0
    assert report == {
        "spent_today": 0.0,
        "in_flight": 0,
        "per_run_usd": 5.0,
        "daily_usd": 25.0,
        "worst_case": 5.0,
        "allowed": True,
        "unreadable": 0,
    }
    _records(project).mkdir(parents=True)
    rc, report = _check(project, capsys)
    assert rc == 0
    assert report["spent_today"] == 0.0


def test_check_blocks_near_the_cap_with_runs_in_flight(project, capsys):
    for run_id in ("1", "2", "3"):
        assert _record(project, "--cost-usd", "4", run_id=run_id) == 0

    # 12 spent + 1 in flight * 5 + 5 for the new run = 22 <= 25
    rc, report = _check(project, capsys, in_flight=1)
    assert rc == 0
    assert report["spent_today"] == 12.0
    assert report["worst_case"] == 22.0
    assert report["allowed"] is True

    # 12 + 2 * 5 + 5 = 27 > 25
    rc, report = _check(project, capsys, in_flight=2)
    assert rc == 1
    assert report["worst_case"] == 27.0
    assert report["allowed"] is False


def test_check_allows_worst_case_exactly_at_daily_cap(project, capsys):
    assert _record(project, "--cost-usd", "10") == 0
    rc, report = _check(project, capsys, in_flight=2)  # 10 + 10 + 5 = 25
    assert rc == 0
    assert report["worst_case"] == 25.0
    assert report["allowed"] is True


def test_check_counts_capped_records(project, capsys):
    # Four runs with no reported cost book 4 * 5 = 20: one more is the limit.
    for run_id in ("1", "2", "3", "4"):
        assert _record(project, "--outcome", "timeout", run_id=run_id) == 0
    rc, report = _check(project, capsys)
    assert rc == 0
    assert report["spent_today"] == 20.0
    rc, report = _check(project, capsys, in_flight=1)
    assert rc == 1


def test_check_sums_money_exactly(project, capsys):
    for n in range(10):
        assert _record(project, "--cost-usd", "0.1", run_id=f"r{n}") == 0
    _, report = _check(project, capsys)
    assert report["spent_today"] == 1.0
    assert report["worst_case"] == 6.0


def test_check_ignores_yesterdays_records(project, capsys):
    yesterday = NOON - 24 * 3600
    assert _record(project, "--cost-usd", "20", run_id="old", now=yesterday) == 0
    rc, report = _check(project, capsys, in_flight=3)
    assert rc == 0
    assert report["spent_today"] == 0.0
    assert report["worst_case"] == 20.0


def test_unreadable_record_counts_as_full_cap(project, capsys):
    records = _records(project)
    records.mkdir(parents=True)
    (records / "999-1.json").write_text('{"booked_usd": 1.0, "recorded', encoding="utf-8")
    rc, report = _check(project, capsys)
    assert rc == 0
    assert report["unreadable"] == 1
    assert report["spent_today"] == 5.0
    assert report["worst_case"] == 10.0


@pytest.mark.parametrize(
    "content",
    [
        json.dumps({"recorded_at": "2026-10-01T12:00:00Z"}),  # no booked_usd
        json.dumps({"booked_usd": "1.0", "recorded_at": "2026-10-01T12:00:00Z"}),
        json.dumps({"booked_usd": -1.0, "recorded_at": "2026-10-01T12:00:00Z"}),
        json.dumps({"booked_usd": 1.0}),  # no recorded_at
        json.dumps({"booked_usd": 1.0, "recorded_at": "yesterday-ish"}),
        json.dumps([1, 2, 3]),
        # these used to crash check with a traceback (exit 1)
        json.dumps({"booked_usd": 1.0, "recorded_at": "9999-12-31T23:00:00-05:00"}),
        json.dumps({"booked_usd": 1.0, "recorded_at": "0001-01-01T00:00:00+05:00"}),
        '{"booked_usd": 1' + "0" * 400 + ', "recorded_at": "2026-10-01T12:00:00Z"}',
    ],
)
def test_invalid_record_counts_as_full_cap(project, capsys, content):
    records = _records(project)
    records.mkdir(parents=True)
    (records / "bad-1.json").write_text(content, encoding="utf-8")
    _, report = _check(project, capsys)
    assert report["unreadable"] == 1
    assert report["spent_today"] == 5.0


def test_unreadable_records_can_block(project, capsys):
    records = _records(project)
    records.mkdir(parents=True)
    for n in range(5):
        (records / f"junk-{n}.json").write_bytes(b"\x00")
    rc = _main(project, "check", "--now", str(NOON))  # 5 * 5 + 5 = 30 > 25
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert rc == 1
    assert report["unreadable"] == 5
    assert report["allowed"] is False
    assert captured.err.count("unreadable ledger record") == 5
    assert "over budget" in captured.err


def test_check_rejects_negative_in_flight(project):
    with pytest.raises(SystemExit) as exc_info:
        _main(project, "check", "--in-flight", "-1")
    assert exc_info.value.code == 2


# --- 7. UTC day boundary ----------------------------------------------------


def test_day_boundary_splits_records(project, capsys):
    last_second = _epoch("2026-10-01T23:59:59")
    midnight = _epoch("2026-10-02T00:00:00")
    assert _record(project, "--cost-usd", "3", run_id="late", now=last_second) == 0
    assert _record(project, "--cost-usd", "4", run_id="early", now=midnight) == 0
    assert _read_record(project, "late")["recorded_at"] == "2026-10-01T23:59:59Z"
    assert _read_record(project, "early")["recorded_at"] == "2026-10-02T00:00:00Z"

    _, report = _check(project, capsys, now=last_second)
    assert report["spent_today"] == 3.0
    _, report = _check(project, capsys, now=_epoch("2026-10-01T00:00:00"))
    assert report["spent_today"] == 3.0
    _, report = _check(project, capsys, now=midnight)
    assert report["spent_today"] == 4.0
    _, report = _check(project, capsys, now=_epoch("2026-10-02T23:59:59"))
    assert report["spent_today"] == 4.0
    _, report = _check(project, capsys, now=_epoch("2026-10-03T00:00:00"))
    assert report["spent_today"] == 0.0


def test_fractional_now_never_rounds_into_the_next_day(project):
    # datetime.fromtimestamp() would round this up to 00:00:00 the next day.
    midnight = _epoch("2026-10-02T00:00:00")
    almost_midnight = midnight - 5e-7
    assert almost_midnight < midnight
    assert _record(project, "--cost-usd", "3", now=almost_midnight) == 0
    assert _read_record(project)["recorded_at"] == "2026-10-01T23:59:59Z"


def test_recorded_at_with_offset_is_read_as_utc(project, capsys):
    records = _records(project)
    records.mkdir(parents=True)
    # 2026-10-01T20:30:00-04:00 is 2026-10-02T00:30:00Z: the next UTC day.
    (records / "tz-1.json").write_text(
        json.dumps({"booked_usd": 2.0, "recorded_at": "2026-10-01T20:30:00-04:00"}),
        encoding="utf-8",
    )
    _, report = _check(project, capsys, now=_epoch("2026-10-01T23:00:00"))
    assert report["spent_today"] == 0.0
    _, report = _check(project, capsys, now=_epoch("2026-10-02T12:00:00"))
    assert report["spent_today"] == 2.0


# --- CLI wiring -------------------------------------------------------------


def test_path_options_work_before_the_subcommand(project, capsys):
    rc = ledger.main(
        [
            "--config",
            str(project / ".cadence" / "factory.yaml"),
            "--records-dir",
            str(_records(project)),
            "record",
            "--run-id",
            "55",
            "--run-attempt",
            "1",
            "--issue",
            "3",
            "--outcome",
            "failure",
            "--now",
            str(NOON),
        ]
    )
    assert rc == 0
    assert _read_record(project, "55")["booked_usd"] == 5.0


def test_cli_uses_default_paths_relative_to_cwd(project):
    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(LEDGER_PATH), *args],
            cwd=project,
            capture_output=True,
            text=True,
            check=False,
        )

    proc = run(
        "record",
        "--run-id",
        "77",
        "--run-attempt",
        "1",
        "--issue",
        "9",
        "--outcome",
        "success",
        "--cost-usd",
        "21",
        "--now",
        str(NOON),
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["booked_usd"] == 21.0
    assert (project / ".cadence" / "runs" / "77-1.json").is_file()

    proc = run("check", "--now", str(NOON))
    assert proc.returncode == 1, proc.stderr  # 21 + 5 > 25
    assert json.loads(proc.stdout)["allowed"] is False
    assert "over budget" in proc.stderr

    proc = run("check", "--now", str(NOON + 24 * 3600))
    assert proc.returncode == 0, proc.stderr


# --- 8. Learning loop: record flags, learn records, the learn pool ----------

PUB_SHA = "a" * 40
BASE_SHA = "b" * 40


def test_record_new_flags_are_written_only_when_given(project):
    assert (
        _record(
            project,
            "--cost-usd", "1",
            "--stage", "build",
            "--pr", "12",
            "--published-sha", PUB_SHA,
            "--base-sha", BASE_SHA,
        )
        == 0
    )
    record = _read_record(project)
    assert record["stage"] == "build"
    assert record["pr"] == 12
    assert record["published_sha"] == PUB_SHA
    assert record["base_sha"] == BASE_SHA
    assert record["issue"] == 7

    assert _record(project, "--cost-usd", "1", "--stage", "spec", attempt=2) == 0
    record = _read_record(project, attempt=2)
    assert record["stage"] == "spec"
    assert not {"pr", "published_sha", "base_sha"} & set(record)


def test_learn_record_names_no_issue(project):
    rc = _main(
        project,
        "record",
        "--stage", "learn",
        "--run-id", "300",
        "--run-attempt", "1",
        "--outcome", "success",
        "--dod", "skipped",
        "--cost-usd", "0.12",
        "--now", str(NOON),
    )
    assert rc == 0
    record = _read_record(project, "300")
    assert record["issue"] is None
    assert record["stage"] == "learn"
    assert record["booked_usd"] == 0.12


def test_issue_is_required_unless_learn(project, capsys):
    rc = _main(
        project,
        "record",
        "--run-id", "301",
        "--run-attempt", "1",
        "--outcome", "success",
        "--now", str(NOON),
    )
    assert rc == 2
    assert "--issue is required" in capsys.readouterr().err
    assert not (_records(project) / "301-1.json").exists()


@pytest.mark.parametrize(
    "flags",
    [
        ["--published-sha", "A" * 40],
        ["--published-sha", "a" * 39],
        ["--base-sha", "not-a-sha"],
        ["--pr", "0"],
        ["--stage", "deploy"],
    ],
)
def test_bad_new_record_flags_exit_2(project, flags):
    with pytest.raises(SystemExit) as exc_info:
        _record(project, "--cost-usd", "1", *flags)
    assert exc_info.value.code == 2


LEARN_CONFIG = """\
budget:
  per_run_usd: 5.00
  daily_usd: 25.00
learning:
  budget:
    per_run_usd: 0.50
    daily_usd: 1.00
"""


def _learn_check(project: Path, capsys, *, in_flight: int = 0):
    capsys.readouterr()
    rc = _main(
        project, "check", "--pool", "learn", "--in-flight", str(in_flight), "--now", str(NOON)
    )
    return rc, json.loads(capsys.readouterr().out)


def _learn_record(project: Path, run_id: str, cost: str) -> None:
    rc = _main(
        project,
        "record",
        "--stage", "learn",
        "--run-id", run_id,
        "--run-attempt", "1",
        "--outcome", "success",
        "--cost-usd", cost,
        "--now", str(NOON),
    )
    assert rc == 0


def test_unreported_learn_cost_is_booked_at_the_learn_cap(project, capsys):
    # classify runs under learning.budget.per_run_usd, so a learn run that
    # reports no cost is booked at that cap, not the $5 build cap (which
    # would empty the learn pool and a build's worth of the daily budget).
    (project / ".cadence" / "factory.yaml").write_text(LEARN_CONFIG, encoding="utf-8")
    rc = _main(
        project,
        "record",
        "--stage", "learn",
        "--run-id", "l9",
        "--run-attempt", "1",
        "--outcome", "failure",
        "--dod", "skipped",
        "--now", str(NOON),
    )
    assert rc == 0
    record = _read_record(project, "l9")
    assert record["cost_source"] == "cap"
    assert record["booked_usd"] == 0.5
    assert record["per_run_cap_usd"] == 0.5
    rc, report = _learn_check(project, capsys)  # 0.50 + 0.50 <= 1.00
    assert rc == 0
    assert report["learn_spent_today"] == pytest.approx(0.5)
    # A build record with no cost still books the build cap.
    assert _record(project, run_id="b9") == 0
    assert _read_record(project, "b9")["booked_usd"] == 5.0


def test_learn_pool_caps_learn_spend(project, capsys):
    (project / ".cadence" / "factory.yaml").write_text(LEARN_CONFIG, encoding="utf-8")
    rc, report = _learn_check(project, capsys)
    assert rc == 0
    assert report["pool"] == "learn"
    assert report["learn_spent_today"] == 0.0
    assert report["learn_per_run_usd"] == 0.5
    assert report["learn_daily_usd"] == 1.0
    assert report["worst_case"] == 0.5

    _learn_record(project, "l1", "0.40")
    rc, report = _learn_check(project, capsys)  # 0.40 + 0.50 <= 1.00
    assert rc == 0
    _learn_record(project, "l2", "0.20")
    rc, report = _learn_check(project, capsys)  # 0.60 + 0.50 > 1.00
    assert rc == 1
    assert report["learn_spent_today"] == pytest.approx(0.6)
    assert report["allowed"] is False

    # Build records count toward the global cap only.
    assert _record(project, "--cost-usd", "3", run_id="b1") == 0
    assert _check(project, capsys)[1]["spent_today"] == pytest.approx(3.6)


def test_learn_pool_respects_the_global_cap(project, capsys):
    (project / ".cadence" / "factory.yaml").write_text(LEARN_CONFIG, encoding="utf-8")
    for run_id in ("1", "2", "3", "4"):
        assert _record(project, "--cost-usd", "5", run_id=run_id) == 0
    # 20 spent + 0 in flight + 0.50 <= 25: allowed
    assert _learn_check(project, capsys)[0] == 0
    # 20 + 1 build in flight * 5 + 0.50 > 25: blocked by the global cap
    rc, report = _learn_check(project, capsys, in_flight=1)
    assert rc == 1
    assert report["worst_case"] == 25.5
    assert report["learn_spent_today"] == 0.0


def test_build_pool_report_is_unchanged(project, capsys):
    rc, report = _check(project, capsys)
    assert rc == 0
    assert "pool" not in report
    assert "learn_spent_today" not in report


def test_load_learning_defaults(tmp_path):
    path = tmp_path / "factory.yaml"
    path.write_text(GOOD_CONFIG, encoding="utf-8")
    learning = ledger.load_learning(path)
    assert learning == ledger.LearningConfig()
    assert learning.mode == "on"
    assert learning.promote_after == 2
    assert learning.repeat_window == 10
    assert learning.area_depth == 2
    assert learning.guarded_paths == ("tests", "test", ".github", ".cadence", "scripts", "tool")
    assert learning.test_roots == ("tests", "test")
    assert learning.per_run_usd == 0.25
    assert learning.daily_usd == 1.0
    assert learning.classify is False
    assert learning.classify_effective is False


def test_load_learning_default_learn_cap_stays_inside_a_small_budget(tmp_path):
    path = tmp_path / "factory.yaml"
    path.write_text("budget:\n  per_run_usd: 0.1\n  daily_usd: 0.2\n", encoding="utf-8")
    learning = ledger.load_learning(path)
    assert learning.daily_usd == 0.2
    assert learning.per_run_usd == 0.2


def test_load_learning_reads_yaml_on_as_mode_on(tmp_path):
    # PyYAML reads a bare `on` as the boolean true.
    path = tmp_path / "factory.yaml"
    path.write_text(GOOD_CONFIG + "learning:\n  mode: on\n  classify: true\n", encoding="utf-8")
    learning = ledger.load_learning(path)
    assert learning.mode == "on"
    assert learning.classify_effective is True


def test_eval_sandbox_never_classifies(tmp_path):
    path = tmp_path / "factory.yaml"
    path.write_text(
        GOOD_CONFIG + "learning:\n  mode: eval-sandbox\n  classify: true\n  model: claude-x-1\n",
        encoding="utf-8",
    )
    learning = ledger.load_learning(path)
    assert learning.classify is True
    assert learning.classify_effective is False
    assert learning.model == "claude-x-1"


@pytest.mark.parametrize(
    "block,needle",
    [
        ("  mode: off\n", "mode"),
        ("  mode: auto\n", "mode"),
        ("  promote_after: 0\n", "promote_after"),
        ("  area_depth: 5\n", "area_depth"),
        ("  repeat_window: -1\n", "repeat_window"),
        ("  window_days: true\n", "window_days"),
        ("  guarded_paths: ['..']\n", "guarded_paths"),
        ("  guarded_paths: ['a/b']\n", "guarded_paths"),
        ("  guarded_paths: tests\n", "guarded_paths"),
        ("  test_roots: [spec]\n", "test_roots"),
        ("  classify: 'yes'\n", "classify"),
        ("  model: 'bad model'\n", "model"),
        ("  budget:\n    per_run_usd: 2\n    daily_usd: 1\n", "per_run_usd"),
        ("  budget:\n    daily_usd: 30\n", "daily_usd"),
        ("  budget:\n    per_run_usd: 0\n", "per_run_usd"),
        ("  promote_aftr: 3\n", "unknown keys"),
        ("  - not a mapping\n", "mapping"),
    ],
)
def test_invalid_learning_block_exits_2(project, capsys, block, needle):
    (project / ".cadence" / "factory.yaml").write_text(
        GOOD_CONFIG + "learning:\n" + block, encoding="utf-8"
    )
    with pytest.raises(ledger.LedgerError):
        ledger.load_learning(project / ".cadence" / "factory.yaml")
    assert _main(project, "check", "--now", str(NOON)) == 2
    assert needle in capsys.readouterr().err


# --- retry.on_dod_fail (the one automatic retry on a failed gate) -------------


@pytest.mark.parametrize(
    "block,expected",
    [
        ("", 1),
        ("retry:\n", 1),
        ("retry: {}\n", 1),
        ("retry:\n  on_dod_fail: 1\n", 1),
        ("retry:\n  on_dod_fail: 0\n", 0),
    ],
)
def test_retry_on_dod_fail_defaults_to_one(tmp_path, block, expected):
    path = tmp_path / "factory.yaml"
    path.write_text(GOOD_CONFIG + block, encoding="utf-8")
    assert ledger.load_config(path).retry_on_dod_fail == expected


@pytest.mark.parametrize(
    "block,needle",
    [
        ("retry:\n  on_dod_fail: true\n", "on_dod_fail"),
        ("retry:\n  on_dod_fail: false\n", "on_dod_fail"),
        ("retry:\n  on_dod_fail: 2\n", "on_dod_fail"),
        ("retry:\n  on_dod_fail: -1\n", "on_dod_fail"),
        ("retry:\n  on_dod_fail: '1'\n", "on_dod_fail"),
        ("retry:\n  on_dod_fail: 1.0\n", "on_dod_fail"),
        ("retry:\n  on_dod_fail:\n", "on_dod_fail"),
        ("retry: 1\n", "mapping"),
        ("retry: [1]\n", "mapping"),
        ("retry:\n  on_dod_fail: 1\n  max: 2\n", "unknown keys"),
    ],
)
def test_invalid_retry_block_exits_2(project, capsys, block, needle):
    (project / ".cadence" / "factory.yaml").write_text(GOOD_CONFIG + block, encoding="utf-8")
    with pytest.raises(ledger.LedgerError):
        ledger.load_config(project / ".cadence" / "factory.yaml")
    assert _main(project, "check", "--now", str(NOON)) == 2
    assert needle in capsys.readouterr().err
    # record refuses too, and writes nothing
    assert _record(project, "--cost-usd", "1") == 2
    assert not _records(project).exists()


def test_retry_setting_leaves_the_check_math_unchanged(project, capsys):
    """The workflow counts a run in its retry twice in --in-flight; check
    itself adds exactly one per_run_usd, whatever retry says."""
    rc_on, on = _check(project, capsys, in_flight=2)
    (project / ".cadence" / "factory.yaml").write_text(
        GOOD_CONFIG + "retry:\n  on_dod_fail: 0\n", encoding="utf-8"
    )
    rc_off, off = _check(project, capsys, in_flight=2)
    assert rc_on == rc_off == 0
    assert on == off
    assert on["worst_case"] == 15.0  # nothing spent, 2 slots in flight and this run, at $5

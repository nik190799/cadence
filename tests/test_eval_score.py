"""Hidden scoring: the output parser and the check results.

The harness prints ``PASS  <check>[  (<detail>)]``, ``FAIL  ...``,
``INFO  <key>  <text>`` and ``== <p> pass, <f> fail ==``, possibly in
colour. No summary means a harness error; a denominator check that was not
printed counts as FAIL; "npm install failed" fails every check; a ticket
passes when its owned checks and every regression check pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval" / "harness"))

import score as scoring  # noqa: E402
from config import schema_errors  # noqa: E402

DEN = ["builds", "lists items", "rejects bad input", "keeps tests"]
NORMAL = """\
INFO  node  v22.0.0
PASS  builds
PASS  lists items  (3 rows)
FAIL  rejects bad input  (expected 400, got 200)
PASS  keeps tests
== 3 pass, 1 fail ==
"""


def _record(text: str, *, exit_code: int | None = 1, timed_out: bool = False) -> dict:
    parsed = scoring.parse_output(text)
    return scoring.score_record(parsed, exit_code=exit_code, timed_out=timed_out, denominator=DEN, repo="r",
                                tree="a" * 40, harness_sha="b" * 64, tries=1)


def test_a_normal_run() -> None:
    parsed = scoring.parse_output(NORMAL)
    assert parsed.summary == {"pass": 3, "fail": 1}
    assert parsed.checks["lists items"] == {"result": "PASS", "detail": "3 rows"}
    assert parsed.checks["rejects bad input"]["detail"] == "expected 400, got 200"
    assert parsed.info == [{"key": "node", "text": "v22.0.0"}]
    rec = _record(NORMAL)
    assert rec["status"] == "ok" and rec["pass"] == 3 and rec["fail"] == 1
    assert rec["fraction"] == 0.75 and rec["repo_pass"] is False
    assert schema_errors("score", rec) == []


def test_ansi_colours_are_stripped() -> None:
    text = "\x1b[32mPASS  builds\x1b[0m\n\x1b[1;31mFAIL  keeps tests  (edited)\x1b[0m\n\x1b[2m== 1 pass, 1 fail ==\x1b[0m\n"
    parsed = scoring.parse_output(text)
    assert parsed.checks == {"builds": {"result": "PASS", "detail": None},
                             "keeps tests": {"result": "FAIL", "detail": "edited"}}
    assert parsed.summary == {"pass": 1, "fail": 1}


def test_a_crash_has_no_summary() -> None:
    text = "PASS  builds\nTypeError: cannot read properties of undefined\n    at run.mjs:10:5\n"
    rec = _record(text, exit_code=1)
    assert rec["status"] == "harness-error"
    assert rec["checks"]["lists items"] == {"result": "FAIL", "detail": None, "printed": False, "in_denominator": True}
    assert rec["pass"] == 1 and rec["repo_pass"] is False


def test_a_missing_summary_with_exit_zero_is_still_an_error() -> None:
    rec = _record("PASS  builds\nPASS  lists items\nPASS  rejects bad input\nPASS  keeps tests\n", exit_code=0)
    assert rec["status"] == "harness-error" and rec["repo_pass"] is False


def test_a_timeout() -> None:
    assert _record("", exit_code=None, timed_out=True)["status"] == "timeout"


def test_install_failed_fails_every_check() -> None:
    rec = _record("ERROR: npm install failed\n" + NORMAL)
    assert rec["install_failed"] is True and rec["pass"] == 0
    assert all(c["result"] == "FAIL" for n, c in rec["checks"].items() if c["in_denominator"])


def test_extra_checks_are_kept_but_not_counted() -> None:
    rec = _record(NORMAL.replace("== 3 pass", "PASS  bonus\n== 4 pass"), exit_code=1)
    assert rec["checks"]["bonus"]["in_denominator"] is False and rec["pass"] == 3


def test_a_check_printed_twice_keeps_its_worst_result() -> None:
    parsed = scoring.parse_output("PASS  builds\nFAIL  builds  (flaky)\nPASS  builds\n== 0 pass, 1 fail ==\n")
    assert parsed.checks["builds"]["result"] == "FAIL"


def test_all_pass_with_exit_zero_is_a_repo_pass() -> None:
    text = "".join(f"PASS  {n}\n" for n in DEN) + "== 4 pass, 0 fail ==\n"
    assert _record(text, exit_code=0)["repo_pass"] is True
    assert _record(text, exit_code=1)["repo_pass"] is False


def test_ticket_pass_needs_owned_and_regression_checks() -> None:
    rec = _record(NORMAL)
    assert scoring.ticket_pass(rec, ["lists items"], ["builds", "keeps tests"]) is True
    assert scoring.ticket_pass(rec, ["rejects bad input"], ["builds"]) is False
    assert scoring.ticket_pass(rec, [], ["builds", "rejects bad input"]) is False
    assert scoring.ticket_pass(_record("boom", exit_code=2), ["builds"], []) is None


def test_the_check_map() -> None:
    checks = {"repos": {"r": {"denominator": DEN, "labels": {
        "builds": {"kind": "regression"},
        "lists items": {"kind": "owned", "tickets": ["r-1"]},
        "rejects bad input": {"kind": "owned", "tickets": ["r.T2"]},
        "keeps tests": {"kind": "noticing"},
    }}}}
    cmap = scoring.check_map(checks, "r")
    assert cmap.owned(("r-1", "r.T1")) == ["lists items"]
    assert cmap.owned(("r-2", "r.T2")) == ["rejects bad input"]
    assert cmap.regression() == ["builds"] and cmap.noticing() == ["keeps tests"]


@pytest.mark.parametrize(
    "want, ok",
    [({}, True), ({"exit": 1}, True), ({"exit": 0}, False), ({"exit": 1, "pass": 3, "fail": 1}, True),
     ({"exit": 1, "pass": 4}, False)],
)
def test_expectation_matching(want: dict, ok: bool) -> None:
    assert scoring.matches(_record(NORMAL, exit_code=1), want) is ok

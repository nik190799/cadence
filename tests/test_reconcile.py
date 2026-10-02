"""Tests for the factory reconciler (tool/reconcile.py).

The reconciler is split so that no test needs the network:

- ``plan`` is pure, so its rules are pinned with hand-built snapshots;
- ``execute`` and ``collect`` take any client, so they run against
  ``FakeClient``;
- ``Client`` takes a runner, so its gh and claim.py command lines run
  against ``FakeGitHub``, a small in-memory stand-in for the repository,
  which also drives ``main`` end to end.
"""

from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL_DIR = REPO_ROOT / "plugins" / "cadence" / "templates" / "tool"
RECONCILE_PATH = TOOL_DIR / "reconcile.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, name
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


reconcile = _load_module("cadence_reconcile", RECONCILE_PATH)

Issue = reconcile.Issue
Run = reconcile.Run
ClaimInfo = reconcile.ClaimInfo
Snapshot = reconcile.Snapshot
Action = reconcile.Action
Settings = reconcile.Settings

NOW = 1_790_000_000
SETTINGS = Settings(claim_timeout_minutes=120, spec_retry_minutes=60)
SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 64

SPEC_MARK = reconcile.SPEC_RETRY_MARKER
STUCK_MARK = reconcile.STUCK_BUILD_MARKER


def _ago(minutes: float) -> int:
    return int(NOW - minutes * 60)


def _issue(
    number: int,
    *labels: str,
    updated_min_ago: float | None = 180,
    title: str | None = None,
    markers: frozenset[str] | None = frozenset(),
    has_open_pr: bool | None = False,
    labeler_can_write: bool | None = True,
) -> Issue:
    return Issue(
        number=number,
        title=title if title is not None else f"Issue number {number}",
        labels=frozenset(labels),
        updated_at=None if updated_min_ago is None else _ago(updated_min_ago),
        markers=markers,
        has_open_pr=has_open_pr,
        labeler_can_write=labeler_can_write,
    )


def _run(
    run_id: int | None = 900,
    *,
    title: str = "cadence-factory #1",
    event: str = "workflow_dispatch",
    status: str = "completed",
    created_min_ago: float | None = 10,
) -> Run:
    return Run(
        id=run_id,
        event=event,
        status=status,
        created_at=None if created_min_ago is None else _ago(created_min_ago),
        display_title=title,
    )


def _claim(
    issue: int, run_id: str | None = "555", sha: str = SHA_A, age: int = 200
) -> ClaimInfo:
    return ClaimInfo(issue=issue, run_id=run_id, sha=sha, age_minutes=age)


def _snapshot(**overrides: Any) -> Snapshot:
    base: dict[str, Any] = {
        "issues": (),
        "stale_claims": (),
        "claimed_issues": frozenset(),
        "run_status": {},
        "runs": (),
    }
    base.update(overrides)
    return Snapshot(**base)


def _kinds(actions: list[Action]) -> list[tuple[str, int]]:
    return [(action.kind, action.issue) for action in actions]


def _plan(**overrides: Any) -> list[Action]:
    return reconcile.plan(_snapshot(**overrides), NOW, SETTINGS)


# --- Pure helpers ------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2026-10-01T12:00:00Z", 1790856000),
        ("2026-10-01T12:00:00+00:00", 1790856000),
        ("2026-10-01T14:00:00+02:00", 1790856000),
        ("2026-10-01T12:00:00", 1790856000),  # no offset: UTC
        ("1970-01-01T00:00:00Z", 0),
        ("", None),
        ("   ", None),
        ("yesterday", None),
        ("2026-13-45T00:00:00Z", None),
        (None, None),
        (1790856000, None),
    ],
)
def test_parse_time(text, expected):
    assert reconcile.parse_time(text) == expected


def test_iso_utc_round_trips_with_parse_time():
    assert reconcile.iso_utc(1790856000) == "2026-10-01T12:00:00Z"
    assert reconcile.parse_time(reconcile.iso_utc(NOW)) == NOW


@pytest.mark.parametrize(
    "repo, ok",
    [
        ("octo/app", True),
        ("Octo-Org/my.app_2", True),
        ("octo", False),
        ("octo/app/extra", False),
        ("../app", False),
        ("octo/..", False),
        ("./app", False),
        ("octo/app?x=1", False),
        ("octo/ app", False),
        ("", False),
    ],
)
def test_valid_repo(repo, ok):
    assert reconcile.valid_repo(repo) is ok


def test_parse_json_stream_reads_concatenated_pages():
    # gh api --paginate prints every page as its own JSON value.
    assert reconcile.parse_json_stream('[1, 2][3]\n[]  [4]\n') == [[1, 2], [3], [], [4]]
    assert reconcile.parse_json_stream('{"a": 1}{"a": 2}') == [{"a": 1}, {"a": 2}]
    assert reconcile.parse_json_stream("") == []
    assert reconcile.parse_json_stream("  \n") == []
    assert reconcile.parse_json_stream('﻿[1]') == [[1]]
    with pytest.raises(ValueError):
        reconcile.parse_json_stream("[1] oops")


def test_page_items_flattens_lists_and_keyed_pages():
    assert reconcile.page_items([[{"a": 1}, 7], [{"b": 2}]]) == [{"a": 1}, {"b": 2}]
    pages = [{"workflow_runs": [{"id": 1}]}, {"workflow_runs": [{"id": 2}]}, ["x"]]
    assert reconcile.page_items(pages, "workflow_runs") == [{"id": 1}, {"id": 2}]
    assert reconcile.page_items([{"other": []}], "workflow_runs") == []


def test_issue_from_api_reads_labels_and_skips_pull_requests():
    issue = reconcile.issue_from_api(
        {
            "number": 7,
            "title": "Add export",
            "labels": [{"name": "factory"}, {"name": "spec-ready"}, "bare", {"x": 1}],
            "updated_at": "2026-10-01T12:00:00Z",
        }
    )
    assert issue == Issue(
        number=7,
        title="Add export",
        labels=frozenset({"factory", "spec-ready", "bare"}),
        updated_at=1790856000,
    )
    assert issue.markers is None and issue.has_open_pr is None
    assert reconcile.issue_from_api({"number": 8, "pull_request": {}}) is None
    for junk in (None, [], {"number": 0}, {"number": True}, {"number": "9"}):
        assert reconcile.issue_from_api(junk) is None
    sparse = reconcile.issue_from_api({"number": 9, "title": None, "labels": None})
    assert sparse.title == "" and sparse.labels == frozenset() and sparse.updated_at is None


def test_run_from_api_treats_missing_fields_as_running():
    run = reconcile.run_from_api(
        {
            "id": 11,
            "event": "issues",
            "status": "completed",
            "created_at": "2026-10-01T12:00:00Z",
            "display_title": "Add export",
        }
    )
    assert run == Run(11, "issues", "completed", 1790856000, "Add export")
    assert not run.active
    blank = reconcile.run_from_api({})
    assert blank == Run(None, "", "", None, "")
    assert blank.active


def test_parse_claim_lines():
    text = (
        json.dumps({"issue": 4, "run_id": "123", "sha": SHA_A, "age_minutes": 200})
        + "\n\n"
        + json.dumps({"issue": 5, "run_id": None, "sha": SHA_C, "age_minutes": "x"})
        + "\n"
    )
    assert reconcile.parse_claim_lines(text) == [
        ClaimInfo(4, "123", SHA_A, 200),
        ClaimInfo(5, None, SHA_C, 0),
    ]
    assert reconcile.parse_claim_lines("") == []


@pytest.mark.parametrize(
    "line",
    [
        "not json",
        "[1]",
        json.dumps({"issue": 0, "run_id": "1", "sha": SHA_A}),
        json.dumps({"issue": 4, "run_id": "1", "sha": "abc"}),
        json.dumps({"issue": 4, "run_id": 5, "sha": SHA_A}),
        json.dumps({"issue": 4, "run_id": "1", "sha": SHA_A.upper()}),
    ],
)
def test_parse_claim_lines_rejects_bad_lines(line):
    with pytest.raises(ValueError):
        reconcile.parse_claim_lines(line)


def test_markers_in():
    comments = [
        {"body": "hello"},
        {"body": f"{SPEC_MARK}\nretrying"},
        {"body": None},
        {"user": "x"},
    ]
    assert reconcile.markers_in(comments) == frozenset({SPEC_MARK})
    assert reconcile.markers_in([{"body": f"x {STUCK_MARK} y {SPEC_MARK}"}]) == frozenset(
        {SPEC_MARK, STUCK_MARK}
    )
    assert reconcile.markers_in([]) == frozenset()


@pytest.mark.parametrize(
    "stderr, stdout, expected",
    [
        ("gh: Not Found (HTTP 404)\n", "", 404),
        ("gh: Server Error (HTTP 502)", "", 502),
        ("", '{"message": "Not Found", "status": "404"}', 404),
        ("connection refused", "", None),
        ("", "not json", None),
        ("", '{"status": 404}', None),
    ],
)
def test_http_status(stderr, stdout, expected):
    assert reconcile.http_status(stderr, stdout) == expected


TITLES = {1: "Add export", 2: "Fix login", 3: "Add export"}


@pytest.mark.parametrize(
    "run, expected",
    [
        # The documented run name names its issue, whatever the event.
        (_run(title="cadence-factory #2", event="workflow_dispatch"), {2}),
        (_run(title="cadence-factory #42", event="issues"), {42}),
        (_run(title="cadence-factory: spec for #2", event="workflow_dispatch"), {2}),
        # Issue events without a run name are titled after their issue.
        (_run(title="Fix login", event="issues"), {2}),
        (_run(title="  Fix login ", event="issue_comment"), {2}),
        (_run(title="Add export", event="issue_comment"), {1, 3}),
        (_run(title="Some other issue", event="issue_comment"), set()),
        (_run(title="", event="issues"), set()),
        # The sweep itself is for no issue.
        (_run(title="cadence-factory", event="schedule"), set()),
        (_run(title="cadence-factory #sweep", event="schedule"), set()),
        # So are the documented "#sweep" and "#learn" run names, however the
        # run was started (a stage=reconcile or stage=learn dispatch, or a
        # learn chain in the hourly sweep).
        (_run(title="cadence-factory #sweep", event="workflow_dispatch"), set()),
        (_run(title="cadence-factory #learn", event="workflow_dispatch"), set()),
        (_run(title="cadence-factory #learn", event="schedule"), set()),
        (_run(title=" cadence-factory #learn ", event="workflow_dispatch"), set()),
        # Only those exact titles: anything longer is matched as before.
        (_run(title="cadence-factory #learning", event="workflow_dispatch"), None),
        (_run(title="cadence-factory #sweep 2", event="workflow_dispatch"), None),
        # Anything else that names no issue could be for any of them.
        (_run(title="cadence-factory", event="workflow_dispatch"), None),
        (_run(title="", event=""), None),
    ],
)
def test_run_targets(run, expected):
    targets = reconcile.run_targets(run, TITLES)
    assert targets == (None if expected is None else frozenset(expected))


def test_comments_carry_their_markers_and_the_next_step():
    spec = reconcile.spec_retry_comment(5)
    assert spec.startswith(SPEC_MARK + "\n")
    stuck = reconcile.stuck_build_comment(5)
    assert stuck.startswith(STUCK_MARK + "\n")
    assert "`/approve`" in stuck and "cadence/issue-5" in stuck and "close" in stuck
    # route.py ignores /approve while `building` is set and without `spec-ready`.
    assert "remove the `building` label, add `spec-ready`" in stuck
    assert STUCK_MARK not in spec and SPEC_MARK not in stuck


# --- Planner: rule 1, stale claims -------------------------------------------


def test_stale_claim_whose_run_completed_is_released():
    actions = _plan(
        stale_claims=(_claim(4, "555", SHA_A, age=200),),
        claimed_issues=frozenset({4}),
        run_status={"555": reconcile.RUN_COMPLETED},
    )
    assert _kinds(actions) == [(reconcile.ACTION_RELEASE, 4)]
    assert actions[0].sha == SHA_A
    assert "200 min" in actions[0].detail and "completed" in actions[0].detail


def test_stale_claim_whose_run_is_not_found_is_released():
    actions = _plan(
        stale_claims=(_claim(4, "555"),), run_status={"555": reconcile.RUN_MISSING}
    )
    assert _kinds(actions) == [(reconcile.ACTION_RELEASE, 4)]
    assert "not found" in actions[0].detail


@pytest.mark.parametrize(
    "claim, run_status, runs",
    [
        # The run is still going.
        (_claim(4, "555"), {"555": reconcile.RUN_ALIVE}, ()),
        # Its run could not be looked up.
        (_claim(4, "555"), {}, ()),
        # The claim names no run.
        (_claim(4, None), {}, ()),
        # A 404 means nothing if the runs listing failed: it may be permissions.
        (_claim(4, "555"), {"555": reconcile.RUN_MISSING}, None),
        # The listing shows the run running, whatever the lookup said.
        (
            _claim(4, "555"),
            {"555": reconcile.RUN_MISSING},
            (_run(555, status="in_progress", title="cadence-factory #4"),),
        ),
    ],
)
def test_stale_claim_is_kept_unless_its_run_is_known_gone(claim, run_status, runs):
    assert _plan(stale_claims=(claim,), run_status=run_status, runs=runs) == []


def test_completed_listed_run_does_not_keep_a_claim():
    actions = _plan(
        stale_claims=(_claim(4, "555"),),
        run_status={"555": reconcile.RUN_COMPLETED},
        runs=(_run(555, status="completed", title="cadence-factory #4"),),
    )
    assert _kinds(actions) == [(reconcile.ACTION_RELEASE, 4)]


def test_no_release_when_the_claims_could_not_be_listed():
    assert _plan(stale_claims=None, run_status={"555": reconcile.RUN_COMPLETED}) == []


def test_releases_are_sorted_by_issue():
    actions = _plan(
        stale_claims=(_claim(9, "2", SHA_B), _claim(3, "1", SHA_A)),
        run_status={"1": reconcile.RUN_COMPLETED, "2": reconcile.RUN_MISSING},
    )
    assert _kinds(actions) == [
        (reconcile.ACTION_RELEASE, 3),
        (reconcile.ACTION_RELEASE, 9),
    ]
    assert [a.sha for a in actions] == [SHA_A, SHA_B]


# --- Planner: rule 2, spec retry ---------------------------------------------


def test_factory_issue_without_spec_is_retried():
    actions = _plan(issues=(_issue(5, "factory", updated_min_ago=90),))
    assert _kinds(actions) == [(reconcile.ACTION_RETRY_SPEC, 5)]
    assert "90 min" in actions[0].detail


@pytest.mark.parametrize("label", sorted(reconcile.PAST_SPEC_LABELS))
def test_spec_retry_skips_issues_past_the_spec_step(label):
    actions = _plan(issues=(_issue(5, "factory", label),))
    assert reconcile.ACTION_RETRY_SPEC not in [action.kind for action in actions]


@pytest.mark.parametrize(
    "issue",
    [
        _issue(5),  # no factory label
        _issue(5, "bug"),
        _issue(5, "factory", updated_min_ago=60),  # exactly the window: not older
        _issue(5, "factory", updated_min_ago=5),
        _issue(5, "factory", updated_min_ago=-30),  # clock skew
        _issue(5, "factory", updated_min_ago=None),
        _issue(5, "factory", markers=frozenset({SPEC_MARK})),  # retried once already
        _issue(5, "factory", markers=None),  # comments could not be read
        _issue(5, "factory", labeler_can_write=False),  # labelled without write access
        _issue(5, "factory", labeler_can_write=None),  # labeller could not be checked
    ],
)
def test_spec_retry_needs_label_age_and_no_marker(issue):
    assert _plan(issues=(issue,)) == []


def test_spec_retry_fires_just_past_the_window():
    assert _kinds(_plan(issues=(_issue(5, "factory", updated_min_ago=61),))) == [
        (reconcile.ACTION_RETRY_SPEC, 5)
    ]


def test_stuck_build_marker_does_not_block_a_spec_retry():
    issue = _issue(5, "factory", markers=frozenset({STUCK_MARK}))
    assert _kinds(_plan(issues=(issue,))) == [(reconcile.ACTION_RETRY_SPEC, 5)]


@pytest.mark.parametrize(
    "run",
    [
        _run(title="cadence-factory #5", created_min_ago=30),  # named, in the window
        _run(title="cadence-factory #5", created_min_ago=59),
        _run(title="Issue number 5", event="issues", created_min_ago=30),  # by title
        _run(title="cadence-factory", created_min_ago=30),  # could be for any issue
        # Still running, however old.
        _run(title="cadence-factory #5", status="queued", created_min_ago=600),
        _run(title="cadence-factory", status="in_progress", created_min_ago=600),
        # Unknown creation time counts as inside the window.
        _run(title="cadence-factory #5", created_min_ago=None),
    ],
)
def test_spec_retry_waits_for_a_run_that_may_be_for_the_issue(run):
    assert _plan(issues=(_issue(5, "factory"),), runs=(run,)) == []


@pytest.mark.parametrize(
    "run",
    [
        _run(title="cadence-factory #5", created_min_ago=61),  # before the window
        _run(title="cadence-factory", created_min_ago=120),
        _run(title="cadence-factory #6", created_min_ago=10),  # another issue
        _run(title="cadence-factory #6", status="in_progress"),
        _run(title="Some other issue", event="issue_comment", created_min_ago=10),
        _run(title="cadence-factory", event="schedule", status="in_progress"),
        # A learn run (dispatched or hourly) or a dispatched sweep is for no issue.
        _run(title="cadence-factory #learn", status="in_progress", created_min_ago=10),
        _run(title="cadence-factory #learn", event="schedule", status="in_progress"),
        _run(title="cadence-factory #sweep", status="in_progress", created_min_ago=10),
    ],
)
def test_spec_retry_ignores_runs_that_are_not_for_the_issue(run):
    actions = _plan(issues=(_issue(5, "factory"),), runs=(run,))
    assert _kinds(actions) == [(reconcile.ACTION_RETRY_SPEC, 5)]


def test_spec_retry_needs_the_issue_and_run_listings():
    issue = _issue(5, "factory")
    assert _plan(issues=(issue,), runs=None) == []
    assert _plan(issues=None) == []


def test_spec_retry_does_not_depend_on_claims():
    issue = _issue(5, "factory")
    actions = _plan(issues=(issue,), stale_claims=None, claimed_issues=None)
    assert _kinds(actions) == [(reconcile.ACTION_RETRY_SPEC, 5)]


# --- Planner: rule 3, stuck builds -------------------------------------------


def test_stuck_build_is_flagged_with_a_comment():
    actions = _plan(issues=(_issue(7, "factory", "building", updated_min_ago=180),))
    assert _kinds(actions) == [(reconcile.ACTION_FLAG_STUCK, 7)]
    assert actions[0].post_comment is True
    assert "cadence/issue-7" in actions[0].detail and "180 min" in actions[0].detail


def test_stuck_build_with_marker_gets_the_label_only():
    issue = _issue(7, "building", markers=frozenset({STUCK_MARK}))
    actions = _plan(issues=(issue,))
    assert _kinds(actions) == [(reconcile.ACTION_FLAG_STUCK, 7)]
    assert actions[0].post_comment is False
    assert "already posted" in actions[0].detail


@pytest.mark.parametrize("label", sorted(reconcile.PAST_BUILD_LABELS))
def test_stuck_build_skips_issues_with_a_result_or_a_human(label):
    assert _plan(issues=(_issue(7, "building", label),)) == []


@pytest.mark.parametrize(
    "issue",
    [
        _issue(7, "factory", "spec-ready"),  # no building label
        _issue(7, "building", updated_min_ago=120),  # exactly the timeout
        _issue(7, "building", updated_min_ago=30),
        _issue(7, "building", updated_min_ago=None),
        _issue(7, "building", has_open_pr=True),
        _issue(7, "building", has_open_pr=None),  # PRs could not be read
        _issue(7, "building", markers=None),  # comments could not be read
    ],
)
def test_stuck_build_needs_label_age_no_pr_and_known_comments(issue):
    assert _plan(issues=(issue,)) == []


def test_stuck_build_fires_just_past_the_timeout():
    assert _kinds(_plan(issues=(_issue(7, "building", updated_min_ago=121),))) == [
        (reconcile.ACTION_FLAG_STUCK, 7)
    ]


def test_stuck_build_is_not_flagged_while_claimed():
    issue = _issue(7, "building")
    assert _plan(issues=(issue,), claimed_issues=frozenset({7})) == []
    assert _plan(issues=(issue,), claimed_issues=None) == []


def test_claim_released_this_sweep_is_flagged_next_sweep_not_now():
    issue = _issue(7, "building")
    snapshot = _snapshot(
        issues=(issue,),
        stale_claims=(_claim(7, "555"),),
        claimed_issues=frozenset({7}),
        run_status={"555": reconcile.RUN_COMPLETED},
    )
    assert _kinds(reconcile.plan(snapshot, NOW, SETTINGS)) == [
        (reconcile.ACTION_RELEASE, 7)
    ]
    after = replace(snapshot, stale_claims=(), claimed_issues=frozenset(), run_status={})
    assert _kinds(reconcile.plan(after, NOW, SETTINGS)) == [
        (reconcile.ACTION_FLAG_STUCK, 7)
    ]


@pytest.mark.parametrize(
    "run",
    [
        _run(title="cadence-factory #7", status="in_progress", created_min_ago=300),
        _run(title="cadence-factory #7", status="queued"),
        _run(title="cadence-factory #7", status="waiting"),
        _run(title="cadence-factory #7", status=""),  # unknown status: running
        _run(title="cadence-factory", status="in_progress"),  # could be for #7
        _run(title="Issue number 7", event="issue_comment", status="in_progress"),
    ],
)
def test_stuck_build_waits_for_a_running_run(run):
    assert _plan(issues=(_issue(7, "building"),), runs=(run,)) == []


@pytest.mark.parametrize(
    "run",
    [
        _run(title="cadence-factory #7", status="completed", created_min_ago=5),
        _run(title="cadence-factory #8", status="in_progress"),
        _run(title="cadence-factory", event="schedule", status="in_progress"),
        _run(title="cadence-factory #learn", status="in_progress"),
        _run(title="cadence-factory #sweep", status="in_progress"),
    ],
)
def test_stuck_build_ignores_finished_or_unrelated_runs(run):
    actions = _plan(issues=(_issue(7, "building"),), runs=(run,))
    assert _kinds(actions) == [(reconcile.ACTION_FLAG_STUCK, 7)]


def test_stuck_build_needs_the_run_listing():
    assert _plan(issues=(_issue(7, "building"),), runs=None) == []


# --- Planner: everything else ------------------------------------------------


@pytest.mark.parametrize(
    "issue",
    [
        _issue(3, "factory", "spec-ready"),  # waiting for /approve
        _issue(3, "factory", "pr-open"),
        _issue(3, "factory", "dod-failed"),
        _issue(3, "factory", "needs-human"),
        _issue(3, "factory", "building", "needs-human"),
        _issue(3),
    ],
)
def test_settled_issues_get_no_action(issue):
    assert _plan(issues=(issue,)) == []


def test_empty_snapshot_plans_nothing():
    assert _plan() == []
    assert reconcile.plan(Snapshot(), NOW, SETTINGS) == []


def test_mixed_snapshot_orders_releases_then_retries_then_flags():
    snapshot = _snapshot(
        issues=(
            _issue(9, "building"),
            _issue(2, "factory"),
            _issue(8, "factory"),
            _issue(1, "building"),
            _issue(4, "factory", "spec-ready"),
        ),
        stale_claims=(_claim(6, "77"), _claim(5, "76", SHA_B)),
        claimed_issues=frozenset({5, 6}),
        run_status={"76": reconcile.RUN_MISSING, "77": reconcile.RUN_COMPLETED},
        runs=(_run(1, title="cadence-factory #8", created_min_ago=20),),
    )
    before = repr(snapshot)
    actions = reconcile.plan(snapshot, NOW, SETTINGS)
    assert _kinds(actions) == [
        (reconcile.ACTION_RELEASE, 5),
        (reconcile.ACTION_RELEASE, 6),
        (reconcile.ACTION_RETRY_SPEC, 2),
        (reconcile.ACTION_FLAG_STUCK, 1),
        (reconcile.ACTION_FLAG_STUCK, 9),
    ]
    # Pure: same answer twice, snapshot untouched.
    assert reconcile.plan(snapshot, NOW, SETTINGS) == actions
    assert repr(snapshot) == before


def test_settings_change_the_windows():
    issue = _issue(5, "factory", updated_min_ago=20)
    assert _plan(issues=(issue,)) == []
    short = Settings(claim_timeout_minutes=120, spec_retry_minutes=15)
    actions = reconcile.plan(_snapshot(issues=(issue,)), NOW, short)
    assert _kinds(actions) == [(reconcile.ACTION_RETRY_SPEC, 5)]


def test_gates():
    assert reconcile.spec_retry_gate(_issue(1, "factory"), NOW, SETTINGS)
    assert not reconcile.spec_retry_gate(_issue(1, "factory", "building"), NOW, SETTINGS)
    assert reconcile.stuck_build_gate(_issue(1, "building"), NOW, SETTINGS)
    assert not reconcile.stuck_build_gate(
        _issue(1, "building", updated_min_ago=100), NOW, SETTINGS
    )


# --- Executor ----------------------------------------------------------------


class FakeClient:
    """Records every call. Methods named in ``fail`` raise CallFailed."""

    def __init__(self, *, fail: tuple[str, ...] = (), release_result: str = "released"):
        self.fail = set(fail)
        self.release_result = release_result
        self.calls: list[tuple[Any, ...]] = []
        # read side, for collect()
        self.claims: list[ClaimInfo] = []
        self.statuses: dict[str, str] = {}
        self.issues: dict[str, list[Issue]] = {}
        self.runs: dict[Any, list[Run]] = {}
        self.markers: dict[int, frozenset[str]] = {}
        self.open_prs: set[int] = set()
        self.labelers_without_write: set[int] = set()

    def _call(self, name: str, *args: Any) -> None:
        self.calls.append((name, *args))
        if name in self.fail:
            raise reconcile.CallFailed(f"{name} broke")

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]

    # write side
    def release_claim(self, issue: int, sha: str) -> str:
        self._call("release_claim", issue, sha)
        return self.release_result

    def post_comment(self, issue: int, body: str) -> None:
        self._call("post_comment", issue, body)

    def add_labels(self, issue: int, labels) -> None:
        self._call("add_labels", issue, list(labels))

    def dispatch_spec(self, issue: int) -> None:
        self._call("dispatch_spec", issue)

    # read side
    def stale_claims(self, timeout_minutes: float, now: int) -> list[ClaimInfo]:
        self._call("stale_claims", timeout_minutes, now)
        if timeout_minutes == 0:
            return list(self.claims)
        return [c for c in self.claims if c.age_minutes > timeout_minutes]

    def run_status(self, run_id: str) -> str:
        self._call("run_status", run_id)
        return self.statuses[run_id]

    def list_issues(self, label: str) -> list[Issue]:
        self._call(f"list_issues:{label}", label)
        return list(self.issues.get(label, ()))

    def list_runs(self, *, created_since: int | None = None, status: str | None = None):
        key = status if status is not None else "recent"
        self._call(f"list_runs:{key}", created_since)
        return list(self.runs.get(key, ()))

    def issue_markers(self, issue: int) -> frozenset[str]:
        self._call("issue_markers", issue)
        return self.markers.get(issue, frozenset())

    def has_open_pr(self, issue: int) -> bool:
        self._call("has_open_pr", issue)
        return issue in self.open_prs

    def factory_labeler_can_write(self, issue: int) -> bool:
        self._call("factory_labeler_can_write", issue)
        return issue not in self.labelers_without_write


RELEASE = Action(reconcile.ACTION_RELEASE, 4, "claim is old", sha=SHA_A)
RETRY = Action(reconcile.ACTION_RETRY_SPEC, 5, "no spec")
FLAG = Action(reconcile.ACTION_FLAG_STUCK, 7, "stuck", post_comment=True)
FLAG_LABEL_ONLY = Action(reconcile.ACTION_FLAG_STUCK, 7, "stuck", post_comment=False)


def _execute(actions, client, *, dry_run=False):
    lines: list[dict] = []
    failed = reconcile.execute(actions, client, dry_run=dry_run, emit=lines.append)
    return failed, lines


def test_dry_run_reports_every_action_and_calls_nothing():
    client = FakeClient()
    failed, lines = _execute([RELEASE, RETRY, FLAG], client, dry_run=True)
    assert not failed
    assert client.calls == []
    assert [line["action"] for line in lines] == [
        reconcile.ACTION_RELEASE,
        reconcile.ACTION_RETRY_SPEC,
        reconcile.ACTION_FLAG_STUCK,
    ]
    for line in lines:
        assert set(line) == {"action", "issue", "detail", "executed"}
        assert line["executed"] is False
        assert line["detail"].endswith("dry run")


@pytest.mark.parametrize(
    "result, executed, words",
    [
        ("released", True, "released"),
        ("absent", True, "already released"),
        ("kept", False, "newer run"),
    ],
)
def test_release_outcomes(result, executed, words):
    client = FakeClient(release_result=result)
    failed, lines = _execute([RELEASE], client)
    assert not failed  # a refused release is the lease working, not a failure
    assert client.calls == [("release_claim", 4, SHA_A)]
    assert lines == [
        {
            "action": reconcile.ACTION_RELEASE,
            "issue": 4,
            "detail": lines[0]["detail"],
            "executed": executed,
        }
    ]
    assert lines[0]["detail"].startswith("claim is old; ") and words in lines[0]["detail"]


def test_release_failure_is_reported():
    client = FakeClient(fail=("release_claim",))
    failed, lines = _execute([RELEASE], client)
    assert failed
    assert lines[0]["executed"] is False
    assert "failed: release_claim broke" in lines[0]["detail"]


def test_spec_retry_posts_the_marker_before_dispatching():
    client = FakeClient()
    failed, lines = _execute([RETRY], client)
    assert not failed
    assert client.names() == ["post_comment", "dispatch_spec"]
    assert client.calls[0][1] == 5 and client.calls[0][2].startswith(SPEC_MARK)
    assert client.calls[1] == ("dispatch_spec", 5)
    assert lines[0]["executed"] is True


def test_spec_retry_does_not_dispatch_without_its_marker():
    client = FakeClient(fail=("post_comment",))
    failed, lines = _execute([RETRY], client)
    assert failed
    assert client.names() == ["post_comment"]
    assert lines[0]["executed"] is False


def test_spec_retry_dispatch_failure_says_the_marker_is_posted():
    client = FakeClient(fail=("dispatch_spec",))
    failed, lines = _execute([RETRY], client)
    assert failed
    assert client.names() == ["post_comment", "dispatch_spec"]
    assert lines[0]["executed"] is False
    assert "marker posted" in lines[0]["detail"]


def test_stuck_build_comments_then_labels():
    client = FakeClient()
    failed, lines = _execute([FLAG], client)
    assert not failed
    assert client.names() == ["post_comment", "add_labels"]
    assert client.calls[0][2].startswith(STUCK_MARK)
    assert client.calls[1] == ("add_labels", 7, ["needs-human"])
    assert lines[0]["executed"] is True


def test_stuck_build_with_comment_already_posted_only_labels():
    client = FakeClient()
    failed, _ = _execute([FLAG_LABEL_ONLY], client)
    assert not failed
    assert client.calls == [("add_labels", 7, ["needs-human"])]


def test_stuck_build_is_not_labelled_when_the_comment_fails():
    # The label is what stops the next sweep, so it must not land alone.
    client = FakeClient(fail=("post_comment",))
    failed, lines = _execute([FLAG], client)
    assert failed
    assert client.names() == ["post_comment"]
    assert lines[0]["executed"] is False


def test_stuck_build_label_failure_is_reported():
    client = FakeClient(fail=("add_labels",))
    failed, lines = _execute([FLAG], client)
    assert failed
    assert "comment posted" in lines[0]["detail"]
    failed, lines = _execute([FLAG_LABEL_ONLY], FakeClient(fail=("add_labels",)))
    assert failed and "comment posted" not in lines[0]["detail"]


def test_one_failed_action_does_not_stop_the_rest():
    client = FakeClient(fail=("release_claim",))
    failed, lines = _execute([RELEASE, RETRY, FLAG], client)
    assert failed
    assert client.names() == [
        "release_claim",
        "post_comment",
        "dispatch_spec",
        "post_comment",
        "add_labels",
    ]
    assert [line["executed"] for line in lines] == [False, True, True]


def test_unknown_action_is_an_internal_error():
    with pytest.raises(ValueError):
        _execute([Action("explode", 1, "x")], FakeClient())


# --- Collect -----------------------------------------------------------------


def _full_client() -> FakeClient:
    client = FakeClient()
    client.claims = [
        _claim(4, "555", SHA_A, age=200),
        _claim(6, "555", SHA_B, age=300),  # same run: looked up once
        _claim(8, None, SHA_C, age=500),  # names no run
        _claim(9, "reconciler", SHA_A, age=500),  # not a run number
        _claim(10, "556", SHA_B, age=5),  # fresh
    ]
    client.statuses = {"555": reconcile.RUN_COMPLETED}
    client.issues = {
        "factory": [
            _issue(1, "factory", markers=None, has_open_pr=None),
            _issue(2, "factory", "building", markers=None, has_open_pr=None),
            _issue(3, "factory", "spec-ready", markers=None, has_open_pr=None),
        ],
        "building": [
            _issue(2, "factory", "building", markers=None, has_open_pr=None),
            _issue(11, "building", updated_min_ago=10, markers=None, has_open_pr=None),
        ],
    }
    client.runs = {
        "recent": [_run(1, title="cadence-factory #1"), _run(2, title="x", event="issues")],
        "in_progress": [_run(2, title="x", event="issues", status="in_progress")],
        "queued": [_run(3, status="queued"), _run(None, status="queued")],
    }
    client.markers = {1: frozenset({SPEC_MARK})}
    client.open_prs = {2}
    return client


def test_collect_reads_what_the_rules_need():
    client = _full_client()
    notes: list[str] = []
    snapshot, failures = reconcile.collect(client, NOW, SETTINGS, note=notes.append)
    assert failures == []

    assert [c.issue for c in snapshot.stale_claims] == [4, 6, 8, 9]
    assert snapshot.claimed_issues == frozenset({4, 6, 8, 9, 10})
    assert snapshot.run_status == {"555": reconcile.RUN_COMPLETED}
    assert client.names().count("run_status") == 1
    assert len(notes) == 2 and "issue 8" in notes[0] and "issue 9" in notes[1]

    # Issues merged by number, in order; details only where a rule could act.
    by_number = {issue.number: issue for issue in snapshot.issues}
    assert sorted(by_number) == [1, 2, 3, 11]
    assert by_number[1].markers == frozenset({SPEC_MARK})
    assert by_number[1].has_open_pr is None  # spec rule: PRs not needed
    assert by_number[2].markers == frozenset() and by_number[2].has_open_pr is True
    assert by_number[3].markers is None and by_number[3].has_open_pr is None
    assert by_number[11].markers is None  # building, but too recent
    assert ("issue_markers", 3) not in client.calls
    assert [c for c in client.calls if c[0] == "has_open_pr"] == [("has_open_pr", 2)]

    # Runs: the window plus every active status, de-duplicated by id.
    queried = [c for c in client.calls if c[0].startswith("list_runs:")]
    assert queried[0] == ("list_runs:recent", NOW - 3600)
    assert sorted(c[0].split(":")[1] for c in queried[1:]) == sorted(
        reconcile.ACTIVE_RUN_STATUSES
    )
    assert [run.id for run in snapshot.runs] == [1, 2, 3, None]


def test_collect_passes_both_claim_timeouts():
    client = _full_client()
    reconcile.collect(client, NOW, SETTINGS, note=lambda _: None)
    assert [c for c in client.calls if c[0] == "stale_claims"] == [
        ("stale_claims", 120, NOW),
        ("stale_claims", 0, NOW),
    ]


@pytest.mark.parametrize(
    "broken, unknown",
    [
        # No stale claims listed means no run to look up either.
        ("stale_claims", {"stale_claims", "claimed_issues", "run_status"}),
        ("run_status", {"run_status"}),
        ("list_issues:factory", {"issues"}),
        ("list_issues:building", {"issues"}),
        ("list_runs:recent", {"runs"}),
        ("list_runs:waiting", {"runs"}),
        ("issue_markers", {"markers"}),
        ("has_open_pr", {"has_open_pr"}),
    ],
)
def test_collect_leaves_unreadable_parts_unknown(broken, unknown):
    client = _full_client()
    client.fail = {broken}
    snapshot, failures = reconcile.collect(client, NOW, SETTINGS, note=lambda _: None)
    assert failures and all(f"{broken} broke" == f for f in failures)
    assert (snapshot.stale_claims is None) == ("stale_claims" in unknown)
    assert (snapshot.claimed_issues is None) == ("claimed_issues" in unknown)
    assert (snapshot.run_status == {}) == ("run_status" in unknown)
    assert (snapshot.issues is None) == ("issues" in unknown)
    assert (snapshot.runs is None) == ("runs" in unknown)
    if snapshot.issues is not None:
        issue_2 = next(i for i in snapshot.issues if i.number == 2)
        assert (issue_2.markers is None) == ("markers" in unknown)
        assert (issue_2.has_open_pr is None) == ("has_open_pr" in unknown)


def test_collect_with_only_the_all_claims_listing_failing():
    client = _full_client()
    original = client.stale_claims

    def flaky(timeout_minutes, now):
        if timeout_minutes == 0:
            raise reconcile.CallFailed("listing broke")
        return original(timeout_minutes, now)

    client.stale_claims = flaky
    snapshot, failures = reconcile.collect(client, NOW, SETTINGS, note=lambda _: None)
    assert failures == ["listing broke"]
    assert snapshot.stale_claims is not None  # rule 1 can still run
    assert snapshot.claimed_issues is None  # rule 3 cannot


# --- Client: command lines ---------------------------------------------------


@dataclass
class Scripted:
    """A runner that answers each call with the next scripted result."""

    results: list[Any]
    calls: list[tuple[list[str], str | None]] = field(default_factory=list)

    def __call__(self, args, input_text=None):
        assert isinstance(args, list) and all(isinstance(a, str) for a in args)
        self.calls.append((list(args), input_text))
        result = self.results.pop(0)
        if isinstance(result, tuple):
            return reconcile.CmdResult(*result)
        return reconcile.CmdResult(0, result, "")


CLAIM_TOOL = Path("tool") / "claim.py"
CLONE = Path("work")


def _client(*results: Any) -> tuple[Any, Scripted]:
    runner = Scripted(list(results))
    client = reconcile.Client(
        "octo/app",
        claim_tool=CLAIM_TOOL,
        clone=CLONE,
        remote="origin",
        runner=runner,
        python="python3",
    )
    return client, runner


def _query(path: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(path).query)


def test_list_issues_pages_and_drops_pull_requests():
    page1 = json.dumps([{"number": 1, "title": "a", "labels": [{"name": "factory"}]}])
    page2 = json.dumps([{"number": 2, "pull_request": {}}, {"number": 3, "title": "c"}])
    client, runner = _client(page1 + page2)
    issues = client.list_issues("factory")
    assert [issue.number for issue in issues] == [1, 3]
    args, stdin = runner.calls[0]
    assert args[:3] == ["gh", "api", "--paginate"] and stdin is None
    assert urlsplit(args[3]).path == "repos/octo/app/issues"
    assert _query(args[3]) == {"state": ["open"], "labels": ["factory"], "per_page": ["100"]}


def test_list_runs_filters_by_window_or_status():
    page = {"total_count": 1, "workflow_runs": [{"id": 5, "status": "queued"}]}
    client, runner = _client(json.dumps(page) + json.dumps(page), json.dumps(page))
    assert [run.id for run in client.list_runs(created_since=NOW - 3600)] == [5, 5]
    assert [run.status for run in client.list_runs(status="queued")] == ["queued"]
    first = runner.calls[0][0][3]
    assert urlsplit(first).path == "repos/octo/app/actions/workflows/cadence-factory.yml/runs"
    assert _query(first)["created"] == [">=" + reconcile.iso_utc(NOW - 3600)]
    assert _query(runner.calls[1][0][3]) == {"status": ["queued"], "per_page": ["100"]}


@pytest.mark.parametrize(
    "result, expected",
    [
        (json.dumps({"id": 5, "status": "completed"}), reconcile.RUN_COMPLETED),
        (json.dumps({"id": 5, "status": "in_progress"}), reconcile.RUN_ALIVE),
        (json.dumps({"id": 5, "status": "queued"}), reconcile.RUN_ALIVE),
        (json.dumps({"id": 5}), reconcile.RUN_ALIVE),
        ((1, '{"message": "Not Found"}', "gh: Not Found (HTTP 404)\n"), reconcile.RUN_MISSING),
    ],
)
def test_run_status(result, expected):
    client, runner = _client(result)
    assert client.run_status("5") == expected
    assert runner.calls[0][0] == ["gh", "api", "repos/octo/app/actions/runs/5"]


@pytest.mark.parametrize(
    "result",
    [
        (1, "", "gh: Forbidden (HTTP 403)"),
        (1, "", "gh: Server Error (HTTP 500)"),
        (1, "", "error connecting to api.github.com"),
        (0, "[]", ""),
        (0, "<html>", ""),
    ],
)
def test_run_status_other_failures_raise(result):
    client, _ = _client(result)
    with pytest.raises(reconcile.CallFailed):
        client.run_status("5")


@pytest.mark.parametrize("run_id", ["reconciler", "../5", "5/../../x", "0", ""])
def test_run_status_refuses_ids_that_are_not_run_numbers(run_id):
    client, runner = _client()
    with pytest.raises(reconcile.CallFailed):
        client.run_status(run_id)
    assert runner.calls == []


def test_issue_markers_and_open_prs():
    comments = json.dumps([{"body": "hi"}]) + json.dumps([{"body": STUCK_MARK}])
    client, runner = _client(comments, json.dumps([]), json.dumps([{"number": 12}]))
    assert client.issue_markers(7) == frozenset({STUCK_MARK})
    assert client.has_open_pr(7) is False
    assert client.has_open_pr(7) is True
    assert runner.calls[0][0] == [
        "gh",
        "api",
        "--paginate",
        "repos/octo/app/issues/7/comments?per_page=100",
    ]
    pulls = runner.calls[1][0][3]
    assert urlsplit(pulls).path == "repos/octo/app/pulls"
    assert _query(pulls) == {
        "state": ["open"],
        "head": ["octo:cadence/issue-7"],
        "per_page": ["100"],
    }


def test_writes_send_json_on_stdin():
    client, runner = _client("{}", "[]", "")
    client.post_comment(7, 'body with "quotes" and\nnewlines')
    client.add_labels(7, ["needs-human"])
    client.dispatch_spec(7)
    (comment, comment_in), (label, label_in), (dispatch, dispatch_in) = runner.calls
    assert comment == [
        "gh", "api", "--method", "POST", "--input", "-",
        "repos/octo/app/issues/7/comments",
    ]
    assert json.loads(comment_in) == {"body": 'body with "quotes" and\nnewlines'}
    assert label[-1] == "repos/octo/app/issues/7/labels"
    assert json.loads(label_in) == {"labels": ["needs-human"]}
    assert dispatch == [
        "gh", "workflow", "run", "cadence-factory.yml", "--repo", "octo/app",
        "-f", "issue=7", "-f", "stage=spec",
    ]
    assert dispatch_in is None


def test_write_failures_raise_with_gh_message():
    client, _ = _client((1, "", "gh: Resource not accessible by integration (HTTP 403)"))
    with pytest.raises(reconcile.GhError) as caught:
        client.post_comment(7, "x")
    assert caught.value.http_status == 403
    assert "Resource not accessible" in str(caught.value)
    client, _ = _client((1, "", "could not create workflow dispatch event: HTTP 422"))
    with pytest.raises(reconcile.GhError):
        client.dispatch_spec(7)


def test_gh_output_that_is_not_json_fails():
    client, _ = _client("Bad credentials, please log in")
    with pytest.raises(reconcile.GhError):
        client.list_issues("factory")


def test_stale_claims_runs_claim_py_and_parses_its_lines():
    line = json.dumps({"issue": 4, "run_id": "555", "sha": SHA_A, "age_minutes": 130})
    client, runner = _client((0, line + "\n", "1 claim(s), 1 older than 120 minutes"))
    assert client.stale_claims(120.0, NOW) == [ClaimInfo(4, "555", SHA_A, 130)]
    args, stdin = runner.calls[0]
    assert args == [
        "python3", str(CLAIM_TOOL), "stale",
        "--repo", str(CLONE), "--remote", "origin",
        "--timeout-minutes", "120.0", "--now", str(NOW),
    ]
    assert stdin is None


@pytest.mark.parametrize(
    "result", [(2, "", "ERROR: git ls-remote failed"), (0, "garbage\n", "")]
)
def test_stale_claims_failures_raise(result):
    client, _ = _client(result)
    with pytest.raises(reconcile.CallFailed):
        client.stale_claims(120, NOW)


@pytest.mark.parametrize(
    "result, expected",
    [
        ((0, json.dumps({"issue": 4, "released": True, "sha": SHA_A}) + "\n", ""), "released"),
        ((0, json.dumps({"issue": 4, "released": False, "sha": None}) + "\n", ""), "absent"),
        ((1, json.dumps({"issue": 4, "held_by": "9"}) + "\n", "not released"), "kept"),
    ],
)
def test_release_claim_outcomes(result, expected):
    client, runner = _client(result)
    assert client.release_claim(4, SHA_A) == expected
    assert runner.calls[0][0] == [
        "python3", str(CLAIM_TOOL), "release",
        "--repo", str(CLONE), "--remote", "origin",
        "--issue", "4", "--run-id", "reconciler", "--force", "--sha", SHA_A,
    ]


def test_release_claim_git_failure_raises():
    client, _ = _client((2, "", "ERROR: git push failed"))
    with pytest.raises(reconcile.CallFailed, match="git push failed"):
        client.release_claim(4, SHA_A)


def test_run_command_never_uses_a_shell(monkeypatch):
    seen: dict[str, Any] = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, "out", "err")

    monkeypatch.setattr(reconcile.subprocess, "run", fake_run)
    result = reconcile.run_command(["gh", "api", "x?a=1&b=2"], None)
    assert result == reconcile.CmdResult(0, "out", "err")
    assert seen["args"] == ["gh", "api", "x?a=1&b=2"]
    assert not seen.get("shell", False)
    assert seen["env"]["GH_PROMPT_DISABLED"] == "1"
    assert seen["stdin"] == subprocess.DEVNULL


def test_run_command_runs_a_real_program_and_passes_stdin():
    code = "import sys; data = sys.stdin.read(); print(data.upper()); sys.exit(3)"
    result = reconcile.run_command([sys.executable, "-c", code], "abc")
    assert result.returncode == 3
    assert result.stdout.strip() == "ABC"


def test_run_command_reports_a_missing_program():
    result = reconcile.run_command(["cadence-no-such-program-xyz"], None)
    assert result.returncode == 127
    assert "could not run" in result.stderr


# --- main, end to end against a fake GitHub ----------------------------------


@dataclass
class FakeGitHub:
    """An in-memory repository that answers the gh and claim.py calls the
    reconciler makes. ``fail`` holds substrings: a call whose command line
    contains one fails with HTTP 500."""

    now: int = NOW
    issues: dict[int, dict[str, Any]] = field(default_factory=dict)
    comments: dict[int, list[str]] = field(default_factory=dict)
    runs: list[dict[str, Any]] = field(default_factory=list)
    pulls: list[dict[str, Any]] = field(default_factory=list)
    claims: dict[int, dict[str, Any]] = field(default_factory=dict)
    # Issue events (who added which label) and collaborator permissions.
    events: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    permissions: dict[str, str] = field(default_factory=lambda: {"maintainer": "write"})
    fail: set[str] = field(default_factory=set)
    log: list[str] = field(default_factory=list)

    def add_issue(
        self,
        number: int,
        *labels: str,
        updated_min_ago: float = 180,
        labeler: dict[str, str] | None = None,
    ) -> None:
        self.issues[number] = {
            "number": number,
            "title": f"Issue {number}",
            "labels": [{"name": label} for label in labels],
            "updated_at": reconcile.iso_utc(int(self.now - updated_min_ago * 60)),
        }
        actor = labeler or {"login": "maintainer", "type": "User"}
        self.events[number] = [
            {
                "event": "labeled",
                "label": {"name": label},
                "actor": actor,
                "created_at": reconcile.iso_utc(int(self.now - updated_min_ago * 60)),
            }
            for label in labels
        ]

    def add_run(self, run_id: int, title: str, status: str, created_min_ago: float) -> None:
        self.runs.append(
            {
                "id": run_id,
                "event": "workflow_dispatch",
                "status": status,
                "display_title": title,
                "created_at": reconcile.iso_utc(int(self.now - created_min_ago * 60)),
            }
        )

    def labels(self, number: int) -> set[str]:
        return {label["name"] for label in self.issues[number]["labels"]}

    def writes(self) -> list[str]:
        return [entry for entry in self.log if not entry.startswith("read ")]

    # The runner -------------------------------------------------------------

    def __call__(self, args, input_text=None):
        assert isinstance(args, list)
        line = " ".join(args)
        if any(marker in line for marker in self.fail):
            return reconcile.CmdResult(1, "", "gh: Server Error (HTTP 500)")
        if args[0] == "gh" and args[1] == "api":
            return self._api(args[2:], input_text)
        if args[0] == "gh" and args[1:3] == ["workflow", "run"]:
            fields = dict(arg.split("=", 1) for arg in args if "=" in arg)
            self.log.append(f"dispatch {fields['issue']} {fields['stage']}")
            title = f"cadence-factory #{fields['issue']}"
            self.add_run(len(self.runs) + 1000, title, "queued", 0)
            return reconcile.CmdResult(0, "", "")
        if args[1].endswith("claim.py"):
            return self._claim(args[2], args[3:])
        raise AssertionError(f"unexpected command {args}")

    def _ok(self, value: Any) -> reconcile.CmdResult:
        return reconcile.CmdResult(0, json.dumps(value), "")

    def _api(self, args: list[str], input_text: str | None) -> reconcile.CmdResult:
        method = args[args.index("--method") + 1] if "--method" in args else "GET"
        target = urlsplit(args[-1])
        path, query = target.path, parse_qs(target.query)
        parts = path.split("/")
        assert parts[:3] == ["repos", "octo", "app"], path
        rest = parts[3:]

        if method == "POST":
            body = json.loads(input_text or "{}")
            number = int(rest[1])
            if rest[2] == "comments":
                self.comments.setdefault(number, []).append(body["body"])
                self.log.append(f"comment {number}")
            else:
                for name in body["labels"]:
                    self.issues[number]["labels"].append({"name": name})
                self.log.append(f"label {number} {' '.join(body['labels'])}")
            return self._ok({})

        self.log.append(f"read {path}")
        if rest == ["issues"]:
            label = query["labels"][0]
            listed = [i for i in self.issues.values() if {"name": label} in i["labels"]]
            # The issues API lists pull requests too.
            listed.append({"number": 999, "pull_request": {}, "labels": [{"name": label}]})
            half = len(listed) // 2
            pages = json.dumps(listed[:half]) + json.dumps(listed[half:])
            return reconcile.CmdResult(0, pages, "")
        if rest[0] == "issues" and rest[2] == "comments":
            return self._ok([{"body": body} for body in self.comments.get(int(rest[1]), [])])
        if rest[0] == "issues" and rest[2] == "events":
            return self._ok(self.events.get(int(rest[1]), []))
        if rest[0] == "collaborators" and rest[2] == "permission":
            if rest[1] not in self.permissions:
                return reconcile.CmdResult(1, '{"message": "Not Found"}', "gh: Not Found (HTTP 404)")
            return self._ok({"permission": self.permissions[rest[1]], "role_name": "x"})
        if rest == ["pulls"]:
            head = query["head"][0]
            return self._ok([p for p in self.pulls if p["head"] == head])
        if rest[:2] == ["actions", "runs"]:
            match = [r for r in self.runs if str(r["id"]) == rest[2]]
            if not match:
                body = '{"message": "Not Found"}'
                return reconcile.CmdResult(1, body, "gh: Not Found (HTTP 404)")
            return self._ok(match[0])
        if rest[:2] == ["actions", "workflows"]:
            runs = self.runs
            if "status" in query:
                runs = [r for r in runs if r["status"] == query["status"][0]]
            if "created" in query:
                since = reconcile.parse_time(query["created"][0][2:])
                runs = [r for r in runs if reconcile.parse_time(r["created_at"]) >= since]
            return self._ok({"total_count": len(runs), "workflow_runs": runs})
        raise AssertionError(f"unexpected api path {path}")

    def _claim(self, command: str, args: list[str]) -> reconcile.CmdResult:
        options = {
            name: value for name, value in zip(args, args[1:]) if name.startswith("--")
        }
        if command == "stale":
            timeout = float(options["--timeout-minutes"])
            now = int(options["--now"])
            out = []
            for issue, claim in sorted(self.claims.items()):
                if now - claim["committed_at"] > timeout * 60:
                    out.append(
                        json.dumps(
                            {
                                "issue": issue,
                                "run_id": claim["run_id"],
                                "sha": claim["sha"],
                                "age_minutes": (now - claim["committed_at"]) // 60,
                            }
                        )
                    )
            self.log.append(f"read claims {timeout:g}")
            return reconcile.CmdResult(0, "".join(line + "\n" for line in out), "")
        assert command == "release" and "--force" in args
        issue = int(options["--issue"])
        held = self.claims.get(issue)
        if held is None:
            return reconcile.CmdResult(0, json.dumps({"issue": issue, "released": False}), "")
        if held["sha"] != options["--sha"]:
            refused = {"issue": issue, "held_by": held["run_id"]}
            return reconcile.CmdResult(1, json.dumps(refused), "")
        del self.claims[issue]
        self.log.append(f"release {issue}")
        released = {"issue": issue, "released": True, "sha": held["sha"]}
        return reconcile.CmdResult(0, json.dumps(released), "")


def _scenario() -> FakeGitHub:
    gh = FakeGitHub()
    gh.add_issue(1, "factory")  # no spec after 3 hours: retry it
    gh.add_issue(2, "factory", "building")  # build vanished: flag it
    gh.add_issue(3, "factory", "building")  # claim of a finished run: release it
    gh.claims[3] = {"run_id": "555", "sha": SHA_A, "committed_at": NOW - 200 * 60}
    gh.add_run(555, "cadence-factory #3", "completed", 210)
    gh.add_issue(4, "factory", "building")  # still building: leave it
    gh.claims[4] = {"run_id": "556", "sha": SHA_B, "committed_at": NOW - 150 * 60}
    gh.add_run(556, "cadence-factory #4", "in_progress", 160)
    gh.add_issue(5, "factory", "spec-ready")  # waiting for /approve
    gh.add_issue(6, "factory", "building")  # PR is open
    gh.pulls.append({"number": 60, "head": "octo:cadence/issue-6"})
    gh.add_issue(7, "factory", updated_min_ago=20)  # labelled recently
    return gh


def _main(tmp_path: Path, gh: FakeGitHub, *extra: str) -> int:
    return reconcile.main(
        ["--repo", "octo/app", "--now", str(NOW), "--clone", str(tmp_path), *extra],
        runner=gh,
    )


def _out(capsys) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]


EXPECTED = [
    (reconcile.ACTION_RELEASE, 3),
    (reconcile.ACTION_RETRY_SPEC, 1),
    (reconcile.ACTION_FLAG_STUCK, 2),
]


def test_main_dry_run_plans_and_changes_nothing(tmp_path, capsys):
    gh = _scenario()
    assert _main(tmp_path, gh, "--dry-run") == 0
    lines = _out(capsys)
    assert [(line["action"], line["issue"]) for line in lines] == EXPECTED
    assert all(line["executed"] is False for line in lines)
    assert gh.writes() == []
    assert set(gh.claims) == {3, 4}


def test_main_executes_and_a_second_sweep_is_idempotent(tmp_path, capsys):
    gh = _scenario()
    assert _main(tmp_path, gh) == 0
    lines = _out(capsys)
    assert [(line["action"], line["issue"]) for line in lines] == EXPECTED
    assert all(line["executed"] is True for line in lines)
    assert gh.writes() == [
        "release 3",
        "comment 1",
        "dispatch 1 spec",
        "comment 2",
        "label 2 needs-human",
    ]
    assert set(gh.claims) == {4}
    assert gh.comments[1][0].startswith(SPEC_MARK)
    assert gh.comments[2][0].startswith(STUCK_MARK)
    assert "needs-human" in gh.labels(2)

    # Next hour: 1 is never retried again and 2 is settled. 3 lost its claim
    # last sweep and is still building with nothing running, so now it is
    # flagged. 7, labelled 80 minutes ago by now, is past the spec window.
    gh.log.clear()
    gh.now = NOW + 3600
    assert reconcile.main(
        ["--repo", "octo/app", "--now", str(NOW + 3600), "--clone", str(tmp_path)],
        runner=gh,
    ) == 0
    assert [(line["action"], line["issue"]) for line in _out(capsys)] == [
        (reconcile.ACTION_RETRY_SPEC, 7),
        (reconcile.ACTION_FLAG_STUCK, 3),
    ]
    assert gh.writes() == [
        "comment 7",
        "dispatch 7 spec",
        "comment 3",
        "label 3 needs-human",
    ]

    # And the hour after that, nothing is left to do.
    gh.log.clear()
    gh.now = NOW + 7200
    assert reconcile.main(
        ["--repo", "octo/app", "--now", str(NOW + 7200), "--clone", str(tmp_path)],
        runner=gh,
    ) == 0
    assert _out(capsys) == []
    assert gh.writes() == []


def test_main_with_nothing_to_do(tmp_path, capsys):
    gh = FakeGitHub()
    gh.add_issue(5, "factory", "spec-ready")
    assert _main(tmp_path, gh) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "0 action(s)" in captured.err


def test_main_exits_1_on_a_failed_call_after_trying_the_rest(tmp_path, capsys):
    gh = _scenario()
    gh.fail = {"issues/1/comments"}  # the spec-retry marker cannot be read
    assert _main(tmp_path, gh) == 1
    lines = _out(capsys)
    assert [(line["action"], line["issue"]) for line in lines] == [
        (reconcile.ACTION_RELEASE, 3),
        (reconcile.ACTION_FLAG_STUCK, 2),
    ]
    assert "dispatch 1 spec" not in gh.writes()


def test_main_exits_1_when_a_write_fails(tmp_path, capsys):
    gh = _scenario()
    gh.fail = {"workflow run"}
    assert _main(tmp_path, gh) == 1
    captured = capsys.readouterr()
    lines = [json.loads(line) for line in captured.out.splitlines()]
    assert [line["executed"] for line in lines] == [True, False, True]
    assert "marker posted" in lines[1]["detail"]
    assert "label 2 needs-human" in gh.writes()
    assert "ERROR: retry_spec on issue 1" in captured.err


def test_main_does_not_release_when_runs_cannot_be_listed(tmp_path, capsys):
    gh = _scenario()
    gh.fail = {"actions/workflows"}
    assert _main(tmp_path, gh) == 1
    assert _out(capsys) == []
    assert set(gh.claims) == {3, 4}


@pytest.mark.parametrize(
    "extra",
    [
        ["--repo", "octo"],
        ["--repo", "octo/.."],
        ["--repo", "octo/app/x"],
        ["--claim-timeout-minutes", "0"],
        ["--claim-timeout-minutes", "-5"],
        ["--claim-timeout-minutes", "nan"],
        ["--spec-retry-minutes", "inf"],
        ["--now", "-1"],
        ["--now", "253402300800"],
        ["--workflow", "../evil.yml"],
        ["--workflow", "cadence-factory"],
        ["--remote=--upload-pack=x"],
        ["--remote", ""],
        ["--claim-tool", "no/such/claim.py"],
    ],
)
def test_main_rejects_bad_input_without_calling_anything(extra, tmp_path, capsys):
    gh = FakeGitHub()
    argv = ["--repo", "octo/app", "--clone", str(tmp_path), *extra]
    assert reconcile.main(argv, runner=gh) == 2
    assert gh.log == []
    assert "ERROR:" in capsys.readouterr().err


def test_main_rejects_a_missing_clone(tmp_path, capsys):
    gh = FakeGitHub()
    argv = ["--repo", "octo/app", "--clone", str(tmp_path / "missing")]
    assert reconcile.main(argv, runner=gh) == 2
    assert gh.log == []


def test_main_requires_repo():
    with pytest.raises(SystemExit) as caught:
        reconcile.main([], runner=FakeGitHub())
    assert caught.value.code == 2


def test_main_internal_error_exits_2(tmp_path, capsys, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(reconcile, "plan", boom)
    assert _main(tmp_path, _scenario()) == 2
    assert "internal error" in capsys.readouterr().err


def test_cli_entry_point(tmp_path):
    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(RECONCILE_PATH), *args],
            capture_output=True,
            text=True,
            check=False,
            cwd=tmp_path,
        )

    helped = run("--help")
    assert helped.returncode == 0 and "--claim-timeout-minutes" in helped.stdout
    bad = run("--repo", "not-a-repo")
    assert bad.returncode == 2 and "OWNER/REPO" in bad.stderr
    assert bad.stdout == ""


def test_default_claim_tool_is_the_sibling_claim_py():
    assert (TOOL_DIR / "claim.py").is_file()
    assert math.isclose(Settings().claim_timeout_minutes, 120)
    assert math.isclose(Settings().spec_retry_minutes, 60)


# --- Spec retries only repeat what a user with write access asked for ---------
#
# The retry is dispatched as the factory App, and route.py lets the App's spec
# dispatch through without a permission check, so the reconciler must check
# that whoever added `factory` could have started the spec themselves.


def _labeled(login: str, *, kind: str = "User", minutes_ago: float = 100, name: str = "factory"):
    return {
        "event": "labeled",
        "label": {"name": name},
        "actor": {"login": login, "type": kind},
        "created_at": reconcile.iso_utc(_ago(minutes_ago)),
    }


@pytest.mark.parametrize(
    "events, expected",
    [
        ([], None),
        ([_labeled("alice")], "alice"),
        # The latest `factory` labelling wins, whatever the listing order.
        ([_labeled("alice", minutes_ago=50), _labeled("bob", minutes_ago=90)], "alice"),
        ([_labeled("bob", minutes_ago=90), _labeled("alice", minutes_ago=50)], "alice"),
        ([_labeled("alice"), _labeled("mallory", name="bug", minutes_ago=1)], "alice"),
        ([{"event": "unlabeled", "label": {"name": "factory"}, "actor": {"login": "x"}}], None),
        # Bots and logins that are not plain user logins name nobody.
        ([_labeled("cadence-app[bot]", kind="Bot")], None),
        ([_labeled("some-integration", kind="Bot")], None),
        ([_labeled("../../admin")], None),
        ([{"event": "labeled", "label": {"name": "factory"}}], None),  # no actor
        ([{"event": "labeled", "label": "factory", "actor": {"login": "alice"}}], None),
    ],
)
def test_factory_labeler(events, expected):
    assert reconcile.factory_labeler(events) == expected


@pytest.mark.parametrize(
    "permission, role, expected",
    [
        ("admin", "admin", True),
        ("write", "write", True),
        ("write", "maintain", True),
        ("read", "triage", False),
        ("read", "read", False),
        ("none", None, False),
    ],
)
def test_client_labeler_permission(permission, role, expected):
    events = json.dumps([_labeled("alice")])
    body = json.dumps({"permission": permission, "role_name": role})
    client, runner = _client(events, body)
    assert client.factory_labeler_can_write(7) is expected
    assert runner.calls[0][0] == [
        "gh",
        "api",
        "--paginate",
        "repos/octo/app/issues/7/events?per_page=100",
    ]
    assert runner.calls[1][0] == ["gh", "api", "repos/octo/app/collaborators/alice/permission"]


def test_client_labeler_permission_without_a_labeler_asks_nothing_more():
    client, runner = _client(json.dumps([_labeled("cadence-app[bot]", kind="Bot")]))
    assert client.factory_labeler_can_write(7) is False
    assert len(runner.calls) == 1


def test_client_labeler_permission_404_means_no_access():
    client, _ = _client(
        json.dumps([_labeled("alice")]),
        (1, '{"message": "Not Found"}', "gh: Not Found (HTTP 404)"),
    )
    assert client.factory_labeler_can_write(7) is False


def test_client_labeler_permission_other_failures_raise():
    client, _ = _client(
        json.dumps([_labeled("alice")]),
        (1, "", "gh: Server Error (HTTP 500)"),
    )
    with pytest.raises(reconcile.CallFailed):
        client.factory_labeler_can_write(7)


def test_collect_checks_the_labeler_only_for_spec_retry_candidates():
    client = _full_client()
    client.issues["factory"].append(_issue(12, "factory", markers=None, has_open_pr=None))
    client.labelers_without_write = {12}
    snapshot, failures = reconcile.collect(client, NOW, SETTINGS, note=lambda _: None)
    assert failures == []
    by_number = {issue.number: issue for issue in snapshot.issues}
    # 1 already carries the retry marker, 2 and 3 are past the spec step.
    assert [c for c in client.calls if c[0] == "factory_labeler_can_write"] == [
        ("factory_labeler_can_write", 12)
    ]
    assert by_number[12].labeler_can_write is False
    assert all(action.issue != 12 for action in reconcile.plan(snapshot, NOW, SETTINGS))


def test_collect_leaves_the_labeler_unknown_when_the_lookup_fails():
    client = _full_client()
    client.issues["factory"].append(_issue(12, "factory", markers=None, has_open_pr=None))
    client.fail = {"factory_labeler_can_write"}
    snapshot, failures = reconcile.collect(client, NOW, SETTINGS, note=lambda _: None)
    assert failures == ["factory_labeler_can_write broke"]
    issue_12 = next(i for i in snapshot.issues if i.number == 12)
    assert issue_12.labeler_can_write is None
    assert all(a.issue != 12 for a in reconcile.plan(snapshot, NOW, SETTINGS))


@pytest.mark.parametrize(
    "labeler, permissions",
    [
        # A triage user may add labels, but route.py refused their label event.
        ({"login": "trina", "type": "User"}, {"trina": "read"}),
        # An issue template applied `factory` when an outsider opened the issue.
        ({"login": "outsider", "type": "User"}, {}),
        # Another automation added it.
        ({"login": "labeler-bot[bot]", "type": "Bot"}, {}),
    ],
)
def test_main_never_retries_a_spec_nobody_with_write_access_asked_for(
    tmp_path, capsys, labeler, permissions
):
    gh = FakeGitHub()
    gh.permissions = permissions
    gh.add_issue(1, "factory", labeler=labeler)
    assert _main(tmp_path, gh) == 0
    assert _out(capsys) == []
    assert gh.writes() == []


def test_main_retries_a_spec_a_maintainer_asked_for(tmp_path, capsys):
    gh = FakeGitHub()
    gh.permissions = {"mia": "write"}
    gh.add_issue(1, "factory", labeler={"login": "mia", "type": "User"})
    assert _main(tmp_path, gh) == 0
    assert [(line["action"], line["issue"]) for line in _out(capsys)] == [
        (reconcile.ACTION_RETRY_SPEC, 1)
    ]
    assert gh.writes() == ["comment 1", "dispatch 1 spec"]

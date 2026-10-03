"""Tests for the learning metrics (tool/metrics.py).

Every number below is computed by hand from the attempt sequence the test
builds (docs/LEARNING.md, "Metrics"): opportunities O, repeats R, escapes
E, the rates RR = |R|/|O| and ER = |E|/|O|, exposure, the same-issue
exclusion, dedupe by patch, ops exclusion, completeness, the Wilson
interval, time vs issue order, detector sets, learned-check catches, the
compare bootstrap and its pass rule, and the judge-pairs shadow column.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_BUILDERS = REPO_ROOT / "tests" / "fixtures" / "ladder" / "builders.py"
_spec = importlib.util.spec_from_file_location("ladder_builders", _BUILDERS)
assert _spec is not None and _spec.loader is not None
b = importlib.util.module_from_spec(_spec)
sys.modules["ladder_builders"] = b
_spec.loader.exec_module(b)

ladder = b.load_tool("ladder")
metrics = b.load_tool("metrics")

K = b.edge_key("src/domain", "src/db")
L = ladder.lesson_id(K)
LINE = "import { db } from '../db/client';"
TEST_FILE = b.changed("tests/order.test.ts", "A", test=True)
DOMAIN_FILE = b.changed("src/domain/other.ts", "M")
G = "guarded:tests:modify"


def with_k(state, run, issue, day, *, published=False, repo=b.REPO, hits=(), verify="success", **kw):
    """An attempt that adds the K import (and a test, so no missing-test)."""
    return state.observe(
        b.observation(
            run,
            issue,
            day=day,
            edges=[b.edge("src/domain/order.ts", 1, "src/db", line=LINE)],
            files=[b.changed("src/domain/order.ts", "A"), TEST_FILE],
            published=published,
            repo=repo,
            rule_hits=hits,
            verify_result=verify,
            **kw,
        )
    )


def without_k(state, run, issue, day, *, published=False, files=None, repo=b.REPO, hits=(), **kw):
    """An attempt exposed to K (it changes src/domain) that does not repeat it."""
    return state.observe(
        b.observation(
            run,
            issue,
            day=day,
            files=files if files is not None else [DOMAIN_FILE, TEST_FILE],
            published=published,
            repo=repo,
            rule_hits=hits,
            **kw,
        )
    )


def seed(state, key=K, pr=99):
    state.findings(f"pr-{pr}-{'a' * 12}", [b.factory_finding(key, signal="reviewer-command", issue=1, pr=pr)])


def report(state, *, order="time", window=0, lessons=(), det=None, judge=None, now_day=40, since=None):
    st = ladder.read_state(state.root, ladder.Schemas(None, b.SCHEMA_DIR))
    det = det or metrics.current_detector(st, list(lessons), [])
    return metrics.build_report(
        st,
        ladder.Settings(),
        det,
        order=order,
        window=window,
        since=since,
        lessons=list(lessons),
        judge_pairs=judge,
        now=b.epoch(now_day),
    )


def validate(rep):
    errors = ladder.Schemas(None, b.SCHEMA_DIR).errors("metrics.schema.json", rep, required=True)
    assert errors == []


# --- RR, ER and share -----------------------------------------------------------------------


def test_repeat_and_escape_rates(tmp_path):
    """a1 K | a2 K published | a3 no K, published | a4 K.
    O = {(a2,K), (a3,K), (a4,K)}, R = {(a2,K), (a4,K)}, E = {(a2,K)}."""
    state = b.StateDir(tmp_path / "state")
    with_k(state, "101", 1, 0)
    with_k(state, "102", 2, 1, published=True)
    without_k(state, "103", 3, 2, published=True)
    with_k(state, "104", 4, 3)
    seed(state)
    rep = report(state)
    validate(rep)
    assert rep["attempts"] == {"scored": 4, "ops": 0, "deduped": 0}
    assert rep["repeat"] == {"opportunities": 3, "repeats": 2, "rate": 0.666667, "ci95": None, "status": "insufficient"}
    assert rep["escape"] == {"escapes": 1, "rate": 0.333333, "share": 0.5, "per_10_attempts": 2.5, "ci95": None}
    assert rep["by_family"]["import-edge"] == {"opportunities": 3, "repeats": 2, "escapes": 1, "rr": 0.666667, "er": 0.333333}
    assert rep["by_family"]["guarded"]["rr"] is None
    assert rep["by_class"] == [
        {"key": K, "rung": "note", "issues": 3, "occurrences": 3, "escaped": 1, "last_seen": b.ts(3)}
    ]
    assert rep["new_class_rate"] == 0.25  # one new class over four attempts


def test_an_unseeded_edge_is_not_a_class(tmp_path):
    state = b.StateDir(tmp_path / "state")
    with_k(state, "101", 1, 0)
    with_k(state, "102", 2, 1)
    rep = report(state)
    assert rep["repeat"]["opportunities"] == 0 and rep["repeat"]["rate"] is None


def test_exposure_needs_a_change_in_the_from_area(tmp_path):
    """a2 only touches src/http: no opportunity for K. a3 touches src/domain."""
    state = b.StateDir(tmp_path / "state")
    with_k(state, "101", 1, 0)
    without_k(state, "102", 2, 1, files=[b.changed("src/http/server.ts", "M"), TEST_FILE])
    without_k(state, "103", 3, 2)
    seed(state)
    rep = report(state)
    assert (rep["repeat"]["opportunities"], rep["repeat"]["repeats"]) == (1, 0)


def test_guarded_classes_are_always_exposed(tmp_path):
    state = b.StateDir(tmp_path / "state")
    guarded = [{"root": "tests", "op": "modify", "path": "tests/a.ts"}]
    state.observe(b.observation("101", 1, day=0, files=[TEST_FILE], guarded=guarded))
    state.observe(b.observation("102", 2, day=1, files=[b.changed("README.md", "M", source=False)]))
    state.observe(b.observation("103", 3, day=2, files=[TEST_FILE], guarded=guarded, published=True))
    rep = report(state)
    assert (rep["repeat"]["opportunities"], rep["repeat"]["repeats"], rep["escape"]["escapes"]) == (2, 1, 1)
    assert rep["test_tampering_rate"] == 0.666667


def test_tampering_counts_nested_test_roots(tmp_path):
    """Test roots may be nested (server/tests, a product repo, 2026-10-03): an edit
    under one counts as tampering whichever guarded path observe named it
    after, and the ladder reads the nested root from the observation."""
    state = b.StateDir(tmp_path / "state")
    named_by_root = [{"root": "server/tests", "op": "modify", "path": "server/tests/test_api.py"}]
    named_by_parent = [{"root": "server", "op": "delete", "path": "server/tests/conftest.py"}]
    elsewhere = [{"root": "server", "op": "modify", "path": "server/app.py"}]
    added = [{"root": "server/tests", "op": "add", "path": "server/tests/x.py"}]
    state.observe(b.observation("101", 1, day=0, files=[TEST_FILE], guarded=named_by_root))
    state.observe(b.observation("102", 2, day=1, files=[TEST_FILE], guarded=named_by_parent))
    state.observe(b.observation("103", 3, day=2, files=[TEST_FILE], guarded=elsewhere))
    state.observe(b.observation("104", 4, day=3, files=[TEST_FILE], guarded=added))
    st = ladder.read_state(state.root, ladder.Schemas(None, b.SCHEMA_DIR))
    assert {op.root for a in st.attempts for op in a.obs.guarded} == {"server/tests", "server"}
    det = metrics.current_detector(st, [], [])
    rep = metrics.build_report(
        st, ladder.Settings(test_roots=("server/tests",)), det, order="time", window=0,
        since=None, lessons=[], judge_pairs=None, now=b.epoch(40),
    )
    assert rep["test_tampering_rate"] == 0.5
    # With the default roots none of them is under a test root.
    assert report(state)["test_tampering_rate"] == 0.0
    assert ladder.settings_from(ladder.Settings(test_roots=("server/tests", "web/src/__tests__")))
    with pytest.raises(ladder.LadderError):
        ladder.settings_from(ladder.Settings(test_roots=("../tests",)))


def test_the_same_issue_never_counts_as_a_repeat(tmp_path):
    state = b.StateDir(tmp_path / "state")
    with_k(state, "101", 1, 0)
    with_k(state, "102", 1, 1, patch_sha256=b.sha256("second patch"))
    seed(state)
    rep = report(state)
    assert rep["repeat"]["opportunities"] == 0
    assert rep["attempts"]["scored"] == 2


def test_dedupe_by_patch_and_ops_exclusion(tmp_path):
    """a2 re-run with the same patch is one attempt (published if either
    was); a cancelled agent and an unapplied patch are operations."""
    state = b.StateDir(tmp_path / "state")
    with_k(state, "101", 1, 0)
    a2 = with_k(state, "102", 2, 1)
    dup = with_k(state, "102", 2, 2, attempt=2, published=True)
    dup["patch_sha256"] = a2["patch_sha256"]
    state.observe(dup)
    with_k(state, "103", 3, 3, agent_result="cancelled")
    with_k(state, "104", 4, 4, apply_status="failed")
    seed(state)
    rep = report(state)
    assert rep["attempts"] == {"scored": 2, "ops": 2, "deduped": 1}
    assert (rep["repeat"]["opportunities"], rep["repeat"]["repeats"], rep["escape"]["escapes"]) == (1, 1, 1)


def test_window_limits_prior_attempts(tmp_path):
    """With N = 1, a3's prior is only a2 (no K), so a3 is no opportunity."""
    state = b.StateDir(tmp_path / "state")
    with_k(state, "101", 1, 0)
    without_k(state, "102", 2, 1)
    with_k(state, "103", 3, 2)
    seed(state)
    assert report(state, window=0)["repeat"]["opportunities"] == 2
    assert report(state, window=1)["repeat"]["opportunities"] == 1


# --- completeness, Wilson and status -------------------------------------------------------------


def test_completeness_below_95_percent_is_incomplete_and_exits_1(tmp_path, capsys):
    state = b.StateDir(tmp_path / "state")
    for i in range(1, 5):
        with_k(state, f"10{i}", i, i)
        state.run(f"10{i}", day=i)
    state.run("199", day=2)  # a build run whose observation never arrived
    state.run("198", day=2, stage="spec")  # not a build
    state.run("197", day=2, outcome="cancelled")  # not success or failure
    seed(state)
    out = tmp_path / "metrics.json"
    rc = metrics.main(["report", "--state-dir", str(state.root), "--now", str(b.epoch(10)), "--out", str(out)])
    assert rc == 1
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["completeness"] == {"build_runs": 5, "observed": 4, "ratio": 0.8}
    assert rep["repeat"]["status"] == "incomplete"
    validate(rep)


def test_since_defaults_to_the_earliest_observation(tmp_path):
    state = b.StateDir(tmp_path / "state")
    state.run("050", day=-5)  # before any observation: not counted
    with_k(state, "101", 1, 0)
    state.run("101", day=0)
    rep = report(state)
    assert rep["since"] == b.ts(0)
    assert rep["completeness"] == {"build_runs": 1, "observed": 1, "ratio": 1.0}


def test_wilson_interval():
    lo, hi = metrics.wilson(5, 10)
    assert (lo, hi) == (pytest.approx(0.236593, abs=1e-6), pytest.approx(0.763407, abs=1e-6))
    assert metrics.wilson(0, 0) is None
    lo, hi = metrics.wilson(30, 30)
    assert lo == pytest.approx(0.886488, abs=1e-5) and hi == 1.0


def test_status_ok_and_an_interval_from_30_opportunities(tmp_path):
    """31 attempts of distinct issues all carry G: O = R = 30."""
    state = b.StateDir(tmp_path / "state")
    guarded = [{"root": "tests", "op": "modify", "path": "tests/a.ts"}]
    for i in range(1, 32):
        state.observe(b.observation(f"{100 + i}", i, day=i / 10, files=[TEST_FILE], guarded=guarded))
    rep = report(state)
    assert rep["repeat"]["status"] == "ok"
    assert rep["repeat"]["opportunities"] == 30
    assert rep["repeat"]["ci95"] == metrics.wilson(30, 30)
    assert rep["escape"]["ci95"] == metrics.wilson(0, 30)


# --- order and detector sets -------------------------------------------------------------------


def test_order_time_vs_issue(tmp_path):
    """Issue 2 (day 0) adds K; issue 1 (day 1) does not. By time issue 1
    is an opportunity that does not repeat; by issue number nothing precedes
    issue 2's K."""
    state = b.StateDir(tmp_path / "state")
    with_k(state, "101", 2, 0)
    without_k(state, "102", 1, 1)
    seed(state)
    assert report(state, order="time")["repeat"]["opportunities"] == 1
    assert report(state, order="issue")["repeat"]["opportunities"] == 0


def test_detector_set_from_a_file(tmp_path):
    state = b.StateDir(tmp_path / "state")
    with_k(state, "101", 1, 0)
    with_k(state, "102", 2, 1)
    seed(state)
    other = tmp_path / "detectors.json"
    other.write_text(
        json.dumps({"schema": "cadence.detectors/1", "import_edges": [b.edge_key("src/a", "src/b")], "families": []}),
        encoding="utf-8",
    )
    det = metrics.detector_from_file(other)
    rep = report(state, det=det)
    assert rep["repeat"]["opportunities"] == 0
    assert rep["detector_set"]["kind"] == "file"
    # Unseeded but pre-registered: the file alone decides.
    unseeded = b.StateDir(tmp_path / "unseeded")
    with_k(unseeded, "101", 1, 0)
    with_k(unseeded, "102", 2, 1)
    mine = tmp_path / "mine.json"
    mine.write_text(json.dumps({"schema": "cadence.detectors/1", "import_edges": [K], "families": ["guarded"]}), encoding="utf-8")
    rep = report(unseeded, det=metrics.detector_from_file(mine))
    assert (rep["repeat"]["opportunities"], rep["repeat"]["repeats"]) == (1, 1)
    assert rep["detector_set"]["sha256"] == metrics.detector_from_file(mine).sha256()


@pytest.mark.parametrize(
    "content",
    [
        {"schema": "cadence.detectors/2", "import_edges": [], "families": []},
        {"schema": "cadence.detectors/1", "import_edges": ["guarded:tests:add"], "families": []},
        {"schema": "cadence.detectors/1", "import_edges": [], "families": ["gate"]},
    ],
)
def test_bad_detector_files_are_refused(tmp_path, content):
    path = tmp_path / "d.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(metrics.LadderError):
        metrics.detector_from_file(path)


# --- kill-criterion support ----------------------------------------------------------------------


def _catch_state(tmp_path):
    state = b.StateDir(tmp_path / "state")
    hit = [b.rule_hit(L, "src/domain/order.ts", 1, K, "src/db/**")]
    with_k(state, "101", 1, 0)
    with_k(state, "102", 2, 1)
    with_k(state, "103", 8, 3, hits=hit)  # before the promotion
    with_k(state, "104", 7, 6, hits=hit, verify="failure")  # the catch
    with_k(state, "105", 1, 7, hits=hit, patch_sha256=b.sha256("again"))  # evidence issue
    without_k(state, "106", 9, 8)  # exposed, no repeat
    seed(state)
    return state


def test_learned_check_catches_from_lessons(tmp_path):
    state = _catch_state(tmp_path)
    lesson = b.lesson(K, "check", since="2026-09-06", issues=(1, 2), history=[("check", "2026-09-06")])
    rep = report(state, lessons=[lesson])
    assert rep["learned_check_catches"] == {"count": 1, "by_lesson": {L: 1}}
    # After promotion and outside the evidence: a4 repeated, a6 did not.
    assert rep["post_promotion_exposed_no_repeat"] == 1
    assert rep["by_class"][0]["rung"] == "check"


def test_learned_check_catches_from_decisions_and_plans(tmp_path):
    state = _catch_state(tmp_path)
    plan_sha = "1" * 64
    plan = {
        "schema": "cadence.retro-plan/1",
        "plan_sha": plan_sha,
        "base_sha": "a" * 40,
        "generated_at": b.ts(5),
        "mode": "on",
        "applied": False,
        "verify_required": True,
        "transitions": [
            {
                "lesson_id": L,
                "class_key": K,
                "from": "note",
                "to": "check",
                "reason": "promote",
                "issues": [1, 2],
                "occurrences": 2,
                "evidence": [],
                "text": ladder.check_text(K, [1, 2]),
                "sample": {
                    "run": "102-1",
                    "patch": "patches/102-1.patch",
                    "patch_sha256": "2" * 64,
                    "path": "src/domain/order.ts",
                    "line_no": 1,
                    "import_line": LINE,
                    "language": "ts",
                    "where": "src/domain/**",
                    "forbidden_pattern": "src/db/**",
                },
                "emit": None,
            }
        ],
        "skipped": [],
        "needs_human": [],
        "replay": [],
        "metrics": None,
    }
    (state.root / "retro" / "plans").mkdir(parents=True)
    (state.root / "retro" / "plans" / f"{plan_sha}.json").write_text(json.dumps(plan), encoding="utf-8")
    state.decision(70, merged=True, day=5.5, plan_sha=plan_sha, transitions=[(K, "check", True)])
    rep = report(state)
    assert rep["learned_check_catches"] == {"count": 1, "by_lesson": {L: 1}}


# --- lessons_cited (informational) ---------------------------------------------------------------

G_ID = ladder.lesson_id(G)
TESTS_MODIFIED = [{"root": "tests", "op": "modify", "path": "tests/a.ts"}]


def test_lessons_cited_absent_present_and_unknown(tmp_path):
    """a1 predates the field and a4 is null: unknown, never absent. a2 cites
    L and has no K: absent. a3 cites L and adds K anyway: present. a5 cites
    nothing. a6 cites L (absent) and G (present: it modifies tests/). a3's
    re-run with the same patch is one attempt, counted once."""
    state = b.StateDir(tmp_path / "state")
    with_k(state, "101", 1, 0)
    without_k(state, "102", 2, 1, lessons_cited=[L])
    a3 = with_k(state, "103", 3, 2, lessons_cited=[L])
    dup = with_k(state, "103", 3, 2.5, attempt=2, lessons_cited=None)
    dup["patch_sha256"] = a3["patch_sha256"]
    state.observe(dup)
    without_k(state, "104", 4, 3, lessons_cited=None)
    without_k(state, "105", 5, 4, lessons_cited=[])
    without_k(state, "106", 6, 5, lessons_cited=sorted([L, G_ID]), guarded=TESTS_MODIFIED)
    seed(state)
    rep = report(state)
    validate(rep)
    assert rep["attempts"] == {"scored": 6, "ops": 0, "deduped": 1}
    assert rep["lessons_cited"] == {
        "attempts_with_citation": 3,
        "attempts_unknown": 2,
        "cited_and_absent": {"count": 2, "by_lesson": {L: 2}},
        "cited_and_present": {"count": 2, "by_lesson": {L: 1, G_ID: 1}},
    }
    # Informational only: a cited-and-absent attempt is no catch.
    assert rep["learned_check_catches"] == {"count": 0, "by_lesson": {}}


def test_lessons_cited_follows_the_detector_set(tmp_path):
    """"Present" means the class is in C(a) as metrics computes it: an
    unseeded edge is not a class, so citing its lesson counts as absent."""
    state = b.StateDir(tmp_path / "state")
    with_k(state, "101", 1, 0, lessons_cited=[L])
    rep = report(state)
    assert rep["lessons_cited"]["cited_and_absent"] == {"count": 1, "by_lesson": {L: 1}}
    seed(state)
    rep = report(state)
    assert rep["lessons_cited"]["cited_and_present"] == {"count": 1, "by_lesson": {L: 1}}


def test_lessons_cited_with_no_observations_is_all_zero(tmp_path):
    rep = report(b.StateDir(tmp_path / "state"))
    validate(rep)
    assert rep["lessons_cited"] == {
        "attempts_with_citation": 0,
        "attempts_unknown": 0,
        "cited_and_absent": {"count": 0, "by_lesson": {}},
        "cited_and_present": {"count": 0, "by_lesson": {}},
    }


def _numbers_state(root: Path, cite) -> b.StateDir:
    """The catch scenario plus escapes, guarded repeats, costs and a PR, with
    ``cite(run)`` as each observation's lessons_cited ("absent" leaves it out)."""
    state = b.StateDir(root)
    hit = [b.rule_hit(L, "src/domain/order.ts", 1, K, "src/db/**")]
    with_k(state, "101", 1, 0, lessons_cited=cite("101"), published=True, pr=5)
    with_k(state, "102", 2, 1, lessons_cited=cite("102"), guarded=TESTS_MODIFIED)
    with_k(state, "103", 8, 3, hits=hit, lessons_cited=cite("103"))
    with_k(state, "104", 7, 6, hits=hit, verify="failure", lessons_cited=cite("104"), published=True, pr=6)
    with_k(state, "105", 1, 7, hits=hit, patch_sha256=b.sha256("again"), lessons_cited=cite("105"))
    without_k(state, "106", 9, 8, lessons_cited=cite("106"), guarded=TESTS_MODIFIED)
    without_k(state, "107", 10, 9, lessons_cited=cite("107"), published=True, pr=7)
    for i, run in enumerate(("101", "102", "103", "104", "105", "106", "107")):
        state.run(run, booked=1.0 + i, day=i)
    state.pr(5, 1, "101", day=0)
    state.harvest(5, 1, merged=True, day=2)
    seed(state)
    return state


def test_lessons_cited_never_changes_an_existing_number(tmp_path):
    """The same attempts with and without lessons_cited give the same report,
    the new block aside: it feeds no rate, catch or count."""
    values = {"101": [L], "102": None, "103": [], "104": [L, G_ID], "105": [G_ID], "106": [L], "107": None}
    without = _numbers_state(tmp_path / "without", lambda run: "absent")
    cited = _numbers_state(tmp_path / "with", lambda run: sorted(values[run]) if values[run] else values[run])
    lesson = b.lesson(K, "check", since="2026-09-06", issues=(1, 2), history=[("check", "2026-09-06")])
    base = report(without, lessons=[lesson])
    new = report(cited, lessons=[lesson])
    validate(base)
    validate(new)
    assert base.pop("lessons_cited")["attempts_unknown"] == base["attempts"]["scored"]
    assert new.pop("lessons_cited")["attempts_with_citation"] == 4
    assert new == base
    # The scenario does exercise the numbers that must not move.
    assert base["learned_check_catches"] == {"count": 1, "by_lesson": {L: 1}}
    assert base["repeat"]["repeats"] > 0 and base["escape"]["escapes"] > 0


def test_first_pass_cost_merge_and_post_pr(tmp_path):
    state = b.StateDir(tmp_path / "state")
    with_k(state, "101", 1, 0, verify="failure", published=True, pr=5)
    with_k(state, "102", 2, 1, published=True, pr=6)
    without_k(state, "103", 1, 2, verify_result="success")
    state.run("101", booked=1.0, day=0)
    state.run("102", booked=3.0, day=1)
    state.run("103", booked=2.0, day=2)
    state.run("90", booked=0.5, day=0, stage="spec")
    state.pr(5, 1, "101", day=0)
    state.pr(6, 2, "102", day=1)
    state.pr(7, 3, "777", day=39)  # too recent to judge, not harvested
    state.harvest(5, 1, merged=True, day=2)
    state.harvest(6, 2, merged=False, day=3)
    review = "review:defect:src/domain"
    state.findings("pr-5-aaaaaaaaaaaa", [b.factory_finding(review, signal="reviewer-command", issue=1, pr=5)])
    state.findings("pr-6-bbbbbbbbbbbb", [b.factory_finding(review, signal="reviewer-command", issue=2, pr=6)])
    seed(state)
    rep = report(state, now_day=40)
    validate(rep)
    assert rep["first_pass_verify"] == 0.5  # issue 1 failed first, issue 2 passed
    assert rep["merge_rate_30d"] == 0.5  # PR 5 merged in 2 days; PR 6 closed
    assert rep["cost"] == {"per_attempt_median": 2.0, "per_attempt_mean": 2.166667, "per_merged_pr": 6.5}
    assert rep["post_pr"] == {"prs_harvested": 2, "opportunities": 1, "repeats": 1, "rate": 1.0}
    # Post-PR classes never enter the headline repeat rate.
    assert all(c["key"] != review for c in rep["by_class"])


def test_judge_pairs_merge_only_in_the_shadow_column(tmp_path):
    state = b.StateDir(tmp_path / "state")
    state.observe(b.observation("101", 1, day=0, files=[TEST_FILE], guarded=[{"root": "tests", "op": "modify", "path": "tests/a"}]))
    state.observe(b.observation("102", 2, day=1, files=[TEST_FILE], guarded=[{"root": "test", "op": "modify", "path": "test/a"}]))
    pairs = tmp_path / "pairs.jsonl"
    pairs.write_text(
        json.dumps({"a": "guarded:tests:modify", "b": "guarded:test:modify", "label": "same-root-cause", "backend": "jev"})
        + "\n"
        + json.dumps({"a": "guarded:tests:modify", "b": "test:x", "label": "different", "backend": "jev"})
        + "\n",
        encoding="utf-8",
    )
    rep = report(state, judge=pairs)
    assert (rep["repeat"]["opportunities"], rep["repeat"]["repeats"]) == (1, 0)
    assert rep["judge_shadow"] == {
        "pairs": 2,
        "merged_keys": 1,
        "repeat": {"opportunities": 1, "repeats": 1, "rate": 1.0},
        "escape": {"escapes": 0, "rate": 0.0},
    }
    validate(rep)


def test_report_cli_writes_a_valid_report(tmp_path, capsys):
    state = b.StateDir(tmp_path / "state")
    with_k(state, "101", 1, 0)
    state.run("101", day=0)
    with_k(state, "102", 2, 1)
    state.run("102", day=1)
    seed(state)
    root = b.make_repo(tmp_path / "repo")
    out = tmp_path / "m.json"
    rc = metrics.main(
        ["report", "--state-dir", str(state.root), "--repo-root", str(root), "--now", str(b.epoch(3)), "--out", str(out)]
    )
    assert rc == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "insufficient" and printed["repeat_rate"] == 1.0
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["repo"] == b.REPO and rep["window"] == 10
    assert len(rep["metrics_code_sha256"]) == 64


def test_report_cli_bad_input_exits_2(tmp_path):
    assert metrics.main(["report", "--state-dir", str(tmp_path / "missing"), "--out", str(tmp_path / "m.json")]) == 2
    state = b.StateDir(tmp_path / "state")
    assert metrics.main(["report", "--state-dir", str(state.root), "--since", "yesterday", "--out", str(tmp_path / "m.json")]) == 2


# --- compare -----------------------------------------------------------------------------------------


def _arm(tmp_path: Path, name: str, repo: str, *, repeats: bool, cost: float, tamper: bool = False):
    """Issue 1 adds K; issues 2-4 are exposed and repeat K (or not)."""
    state = b.StateDir(tmp_path / name)
    with_k(state, "101", 1, 0, repo=repo)
    state.run("101", booked=cost, day=0)
    for i in (2, 3, 4):
        run = f"10{i}"
        guarded = [{"root": "tests", "op": "modify", "path": "tests/a"}] if tamper else []
        if repeats:
            with_k(state, run, i, i, repo=repo, published=True, guarded=guarded)
        else:
            without_k(state, run, i, i, repo=repo, published=True, guarded=guarded)
        state.run(run, booked=cost, day=i)
    seed(state)
    return state.root


def _tickets(tmp_path: Path, repos: list[str]) -> Path:
    tickets = {f"{repo}#{i}": f"T{i}" for repo in repos for i in (1, 2, 3, 4)}
    path = tmp_path / "tickets.json"
    path.write_text(json.dumps({"schema": "cadence.ticket-map/1", "tickets": tickets}), encoding="utf-8")
    return path


def _compare(tmp_path, *, on_cost=1.0, frozen_cost=1.0, seed=13, on_tamper=False, extra=()):
    on = _arm(tmp_path, "on", "octo/on1", repeats=False, cost=on_cost, tamper=on_tamper)
    frozen = _arm(tmp_path, "frozen", "octo/fr1", repeats=True, cost=frozen_cost)
    tickets = _tickets(tmp_path, ["octo/on1", "octo/fr1"])
    out = tmp_path / f"compare-{seed}.json"
    rc = metrics.main(
        [
            "compare", "--on", str(on), "--frozen", str(frozen), "--ticket-map", str(tickets),
            "--resamples", "200", "--seed", str(seed), "--out", str(out), *extra,
        ]
    )  # fmt: skip
    return rc, json.loads(out.read_text(encoding="utf-8"))


def test_compare_passes_when_learning_wins(tmp_path):
    rc, result = _compare(tmp_path)
    assert rc == 0 and result["pass"] is True and result["reasons"] == []
    assert result["arms"]["on"] == {"rr": 0.0, "er": 0.0, "tampering": 0.0, "cost_median": 1.0}
    assert result["arms"]["frozen"] == {"rr": 1.0, "er": 1.0, "tampering": 0.0, "cost_median": 1.0}
    assert result["diff"]["rr"] == {"point": 1.0, "ci90": [1.0, 1.0], "ci95": [1.0, 1.0]}
    assert result["schema"] == "cadence.eval-compare/1"
    assert (result["resamples"], result["seed"]) == (200, 13)


def test_compare_bootstrap_is_deterministic(tmp_path):
    """Mixed arms so the bootstrap interval has width; the same seed gives
    the same interval."""
    on = b.StateDir(tmp_path / "on")
    with_k(on, "101", 1, 0, repo="octo/on1")
    with_k(on, "102", 2, 1, repo="octo/on1")
    without_k(on, "103", 3, 2, repo="octo/on1")
    without_k(on, "104", 4, 3, repo="octo/on1")
    seed(on)
    frozen = b.StateDir(tmp_path / "frozen")
    with_k(frozen, "101", 1, 0, repo="octo/fr1")
    with_k(frozen, "102", 2, 1, repo="octo/fr1")
    with_k(frozen, "103", 3, 2, repo="octo/fr1")
    without_k(frozen, "104", 4, 3, repo="octo/fr1")
    seed(frozen)
    tickets = _tickets(tmp_path, ["octo/on1", "octo/fr1"])
    states_on = [ladder.read_state(on.root, ladder.Schemas(None, b.SCHEMA_DIR))]
    states_fr = [ladder.read_state(frozen.root, ladder.Schemas(None, b.SCHEMA_DIR))]
    det = metrics.current_detector(states_on[0], [], [])
    args = dict(order="issue", window=0, resamples=500, test_roots=("tests", "test"))
    first = metrics.compare(states_on, states_fr, metrics.load_ticket_map(tickets), det, seed=7, **args)
    again = metrics.compare(states_on, states_fr, metrics.load_ticket_map(tickets), det, seed=7, **args)
    assert first == again
    # On: O = 3 (issues 2-4), R = 1 (issue 2). Frozen: O = 3, R = 2.
    assert first["arms"]["on"]["rr"] == 0.333333 and first["arms"]["frozen"]["rr"] == 0.666667
    assert first["diff"]["rr"]["point"] == 0.333333
    lo, hi = first["diff"]["rr"]["ci95"]
    assert lo <= first["diff"]["rr"]["point"] <= hi and lo < hi


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        ({"on_cost": 2.0}, "median cost"),
        ({"on_tamper": True}, "tampering"),
    ],
)
def test_compare_pass_rule(tmp_path, kwargs, reason):
    rc, result = _compare(tmp_path, **kwargs)
    assert rc == 1 and result["pass"] is False
    assert any(reason in r for r in result["reasons"])


def test_compare_fails_when_frozen_is_not_worse(tmp_path):
    on = _arm(tmp_path, "on", "octo/on1", repeats=True, cost=1.0)
    frozen = _arm(tmp_path, "frozen", "octo/fr1", repeats=True, cost=1.0)
    tickets = _tickets(tmp_path, ["octo/on1", "octo/fr1"])
    out = tmp_path / "c.json"
    rc = metrics.main(["compare", "--on", str(on), "--frozen", str(frozen), "--ticket-map", str(tickets), "--out", str(out)])
    assert rc == 1
    reasons = json.loads(out.read_text(encoding="utf-8"))["reasons"]
    assert any("90% interval" in r for r in reasons) and any("ER_on" in r for r in reasons)


def test_compare_bad_ticket_map_exits_2(tmp_path):
    on = _arm(tmp_path, "on", "octo/on1", repeats=False, cost=1.0)
    bad = tmp_path / "tickets.json"
    bad.write_text(json.dumps({"schema": "cadence.ticket-map/1", "tickets": {"not a key": "T1"}}), encoding="utf-8")
    rc = metrics.main(["compare", "--on", str(on), "--frozen", str(on), "--ticket-map", str(bad), "--out", str(tmp_path / "c.json")])
    assert rc == 2


def test_percentile():
    assert metrics.percentile([1, 2, 3, 4], 0.5) == 2.5
    assert metrics.percentile([5], 0.95) == 5
    assert metrics.percentile([], 0.5) is None

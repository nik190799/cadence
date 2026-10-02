"""Tests for the learning ladder (tool/ladder.py).

The ladder turns factory evidence on cadence/state into one rolling retro
PR: classes climb note -> pattern -> check, and fall to retired or
suppressed, exactly as docs/LEARNING.md ("The ladder") says. These tests
pin the counting rules (distinct issues, the window, seeding), rejection
cool-down and suppression, the caps and their ranking, every retirement
rule, hysteresis and pinning, the plan hash, the guard, ``apply`` with a
stub emitter and with the real one, and the PR body. Also: a check's up to
three samples and how ``apply`` falls through them, a plan recorded as
failed on cadence/state (retro/failed/), and ``apply --verify-failed``
with its "Demoted after verify failed" PR body section.

State dirs and repos are generated per test by tests/fixtures/ladder/
builders.py; git runs with an isolated, empty global config.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
_BUILDERS = REPO_ROOT / "tests" / "fixtures" / "ladder" / "builders.py"
_spec = importlib.util.spec_from_file_location("ladder_builders", _BUILDERS)
assert _spec is not None and _spec.loader is not None
b = importlib.util.module_from_spec(_spec)
sys.modules["ladder_builders"] = b
_spec.loader.exec_module(b)

ladder = b.load_tool("ladder")
emit_rule = b.load_tool("emit_rule")

LINE = "import { db } from '../db/client';"
DB_EDGE = b.edge_key("src/domain", "src/db")
DB_LID = ladder.lesson_id(DB_EDGE)
BASE = "a" * 40
TEST_FILE = b.changed("src/domain/order.test.ts", "A", test=True)

_GIT_VARS = (
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_NOSYSTEM",
    "GIT_TERMINAL_PROMPT",
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
)


# --- fixtures and helpers -------------------------------------------------------------


@pytest.fixture
def git_env(tmp_path, monkeypatch):
    """An isolated git config, also exported so ladder's own git calls use it."""
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    env = b.git_env(tmp_path / "gitcfg")
    for name in _GIT_VARS:
        monkeypatch.setenv(name, env[name])
    for name in b._GIT_LOCATION_VARS:
        monkeypatch.delenv(name, raising=False)
    return env


def edge_attempt(
    state,
    run: str,
    issue: int,
    day: float,
    *,
    to: str = "src/db",
    path: str = "src/domain/order.ts",
    line: str | None = LINE,
    published: bool = False,
    with_patch: bool = True,
    rule_hits=(),
    pr: int | None = None,
):
    """One attempt that adds ``line`` (an import of ``to``) at line 1 of ``path``."""
    patch = b.add_patch(path, [line or "import x from 'y';"]) if with_patch else None
    obs = b.observation(
        run,
        issue,
        day=day,
        edges=[b.edge(path, 1, to, line=line)],
        files=[b.changed(path, "A"), TEST_FILE],
        published=published,
        pr=pr,
        rule_hits=rule_hits,
    )
    return state.observe(obs, patch)


def plain_attempt(state, run: str, issue: int, day: float, **kw):
    """An attempt with no headline class (a test file and a doc change)."""
    files = kw.pop("files", [TEST_FILE, b.changed("README.md", "M", source=False)])
    return state.observe(b.observation(run, issue, day=day, files=files, **kw))


def seed(state, key: str = DB_EDGE, *, issue: int = 1, pr: int = 9, signal: str = "reviewer-command"):
    state.findings(f"pr-{pr}-{'a' * 12}", [b.factory_finding(key, signal=signal, issue=issue, pr=pr)])


def make_plan(
    tmp_path: Path,
    state,
    *,
    lessons=(),
    rules=(b.SEED_RULE,),
    replay=(),
    root_hits=None,
    areas=("src/domain", "src/db", "src/http"),
    now_day: float = 10,
    **settings,
):
    root = tmp_path / "repo"
    if not root.exists():
        b.make_repo(root, rules=rules, areas=areas)
    schemas = ladder.Schemas(root, None)
    st = ladder.read_state(state.root, schemas)
    view = ladder.RepoView(
        root=root,
        lessons=[dict(l) for l in lessons],
        rules=ladder.rules_from_config(yaml.safe_load(b.cadence_yaml(rules))),
        replay=list(replay),
        root_hits=dict(root_hits or {}),
    )
    return ladder.compute_plan(ladder.Settings(**settings), st, view, base_sha=BASE, now=b.epoch(now_day))


def by_key(plan) -> dict[str, dict]:
    return {t["class_key"]: t for t in plan["transitions"]}


def skipped(plan) -> dict[str, str]:
    return {s["class_key"]: s["why"] for s in plan["skipped"]}


def needs(plan) -> set[tuple[str, str]]:
    return {(n["key"], n["why"]) for n in plan["needs_human"]}


def learned_rule(key: str = DB_EDGE) -> dict:
    frm, to = ladder.parse_edge_key(key)
    return {
        "id": ladder.lesson_id(key),
        "where": f"{frm}/**",
        "forbidden": [f"{to}/**"],
        "reason": ladder.rule_reason(key, [1, 2]),
    }


# --- shared constants ------------------------------------------------------------------


def test_lesson_id_is_emit_rule_short_id():
    for key in (DB_EDGE, "guarded:tests:modify", "test:tests/a.test.ts"):
        finding = {"id": ladder.lesson_finding_id(key)}
        assert ladder.lesson_id(key) == "L-" + emit_rule._short_id(finding)


def test_constants_match_emit_rule():
    assert ladder.LANG_FAMILY == emit_rule.LANG_FAMILY
    assert ladder.SAMPLE_LANGUAGE == emit_rule.SAMPLE_LANGUAGE
    assert ladder.RETRO_FIXTURE_PREFIX == emit_rule.RETRO_FIXTURE_PREFIX
    assert ladder.RULE_ID_RE == emit_rule.RULE_ID_RE
    assert ladder.LESSON_ID_RE == emit_rule.LESSON_ID_RE


@pytest.mark.parametrize(
    "key,expected",
    [
        ("import-edge:src/domain->src/db", ("src/domain", "src/db")),
        ("import-edge:src/a->pkg:@scope/x", ("src/a", "pkg:@scope/x")),
        ("import-edge:.->src/db", (".", "src/db")),
        ("import-edge:src/a>src/b", None),
        ("import-edge:->src/b", None),
        ("guarded:tests:modify", None),
    ],
)
def test_parse_edge_key(key, expected):
    assert ladder.parse_edge_key(key) == expected


def test_area():
    assert ladder.area("src/domain/order.ts", 2) == "src/domain"
    assert ladder.area("src/domain/deep/order.ts", 2) == "src/domain"
    assert ladder.area("main.py", 2) == "."
    assert ladder.area("src/we ird/x.ts", 2) is None


# --- counting and the window --------------------------------------------------------------


def test_same_issue_twice_is_not_promoted(tmp_path):
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 1, 1, line="import { db } from '../db/pool';")
    seed(state)
    plan = make_plan(tmp_path, state)
    assert plan["transitions"] == []


def test_two_distinct_issues_promote_a_check_from_the_newest_sample(tmp_path):
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 2, 1, published=True, pr=7)
    seed(state)
    plan = make_plan(tmp_path, state)
    t = by_key(plan)[DB_EDGE]
    assert (t["from"], t["to"], t["reason"]) == ("note", "check", "promote")
    assert t["issues"] == [1, 2]
    assert t["occurrences"] == 2
    assert t["sample"]["run"] == "102-1"
    assert t["sample"]["patch"] == "patches/102-1.patch"
    assert t["sample"]["where"] == "src/domain/**"
    assert t["sample"]["forbidden_pattern"] == "src/db/**"
    assert t["sample"]["language"] == "ts"
    assert t["text"].startswith("`src/domain/` must not import `src/db/`. Enforced by check " + DB_LID)
    assert len(t["evidence"]) == 2
    assert plan["verify_required"] is True


def test_window_takes_whichever_holds_more(tmp_path):
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 2, 1)
    for i, day in enumerate((20, 21, 22)):
        plain_attempt(state, f"20{i}", 10 + i, day)
    seed(state)
    # Last 2 attempts vs last 5 days (3 attempts): the window is 3 attempts.
    plan = make_plan(tmp_path, state, now_day=23, window_attempts=2, window_days=5)
    assert DB_EDGE not in by_key(plan)
    # 30 days hold all 5 attempts.
    plan = make_plan(tmp_path, state, now_day=23, window_attempts=2, window_days=30)
    assert by_key(plan)[DB_EDGE]["to"] == "check"
    # And 5 attempts beat 1 day.
    plan = make_plan(tmp_path, state, now_day=23, window_attempts=5, window_days=1)
    assert by_key(plan)[DB_EDGE]["to"] == "check"


def test_promote_after_is_configurable(tmp_path):
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 2, 1)
    seed(state)
    assert make_plan(tmp_path, state, promote_after=3)["transitions"] == []


# --- seeding -------------------------------------------------------------------------------


def test_unseeded_edge_never_counts(tmp_path):
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 2, 1)
    assert make_plan(tmp_path, state)["transitions"] == []


def test_rule_hit_seeds_the_edge(tmp_path):
    state = b.StateDir(tmp_path / "state")
    hit = [b.rule_hit("B-12345678", "src/domain/order.ts", 1, DB_EDGE, "src/db/**")]
    edge_attempt(state, "101", 1, 0, rule_hits=hit)
    edge_attempt(state, "102", 2, 1)
    assert by_key(make_plan(tmp_path, state))[DB_EDGE]["to"] == "check"


def test_seeding_is_retroactive(tmp_path):
    """A human removes the import on issue 2's PR after both attempts ran:
    both attempts now count."""
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 2, 1, published=True, pr=8)
    assert make_plan(tmp_path, state, now_day=2)["transitions"] == []
    seed(state, issue=2, pr=8, signal="human-edit")
    plan = make_plan(tmp_path, state, now_day=6)
    assert by_key(plan)[DB_EDGE]["issues"] == [1, 2]


def test_a_detector_finding_without_a_rule_does_not_seed(tmp_path):
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 2, 1)
    seed(state, signal="detector")
    assert make_plan(tmp_path, state)["transitions"] == []


def test_edge_covered_by_a_seed_rule_counts_but_stays_a_pattern(tmp_path):
    rule = {"where": "src/domain/**", "forbidden": ["src/db/**"], "reason": "Domain never talks to the db"}
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 2, 1)
    plan = make_plan(tmp_path, state, rules=(b.SEED_RULE, rule))
    t = by_key(plan)[DB_EDGE]
    assert (t["to"], t["reason"]) == ("pattern", "promote")
    assert t["text"] == "Code under `src/domain/` must not import `src/db` (seen in #1, #2)."


def test_package_edge_is_a_pattern_and_needs_a_human(tmp_path):
    key = b.edge_key("src/domain", "pkg:@acme/http")
    line = "import { get } from '@acme/http';"
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0, to="pkg:@acme/http", line=line)
    edge_attempt(state, "102", 2, 1, to="pkg:@acme/http", line=line)
    seed(state, key)
    plan = make_plan(tmp_path, state)
    assert by_key(plan)[key]["to"] == "pattern"
    assert (key, "package-edge") in needs(plan)


def test_without_a_stored_patch_the_edge_falls_back_to_a_pattern(tmp_path):
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0, with_patch=False)
    edge_attempt(state, "102", 2, 1, with_patch=False)
    seed(state)
    plan = make_plan(tmp_path, state)
    t = by_key(plan)[DB_EDGE]
    assert (t["to"], t["reason"], t["sample"]) == ("pattern", "emit-fallback", None)
    assert (DB_EDGE, "emit-failed") in needs(plan)


def test_a_line_that_is_not_a_strict_import_is_unsupported(tmp_path):
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0, line=None)
    edge_attempt(state, "102", 2, 1, line=None)
    seed(state)
    plan = make_plan(tmp_path, state)
    assert by_key(plan)[DB_EDGE]["to"] == "pattern"
    assert (DB_EDGE, "unsupported-language") in needs(plan)


def test_intrinsic_families_become_patterns(tmp_path):
    state = b.StateDir(tmp_path / "state")
    for run, issue in (("101", 1), ("102", 2)):
        state.observe(
            b.observation(
                run,
                issue,
                day=issue,
                files=[b.changed("src/db/db.ts", "M")],
                guarded=[{"root": "tests", "op": "modify", "path": "tests/a.test.ts"}],
                failing_tests=["tests/a.test.ts"],
            )
        )
    root = b.make_repo(tmp_path / "repo")
    (root / "tests").mkdir()
    (root / "tests" / "a.test.ts").write_text("// test\n", encoding="utf-8")
    plan = make_plan(tmp_path, state)
    texts = {k: t["text"] for k, t in by_key(plan).items()}
    assert texts["guarded:tests:modify"] == (
        "Do not modify or delete existing files under `tests/`; the gate restores them (seen in #1, #2)."
    )
    assert texts["missing-test:src/db"] == "Changes under `src/db/` must add or update a test (missed in #1, #2)."
    assert texts["test:tests/a.test.ts"] == "Changes have broken `tests/a.test.ts` (#1, #2); run it before finishing."
    assert plan["verify_required"] is True  # a test: pattern


def test_ops_and_duplicates_do_not_count(tmp_path):
    state = b.StateDir(tmp_path / "state")
    first = edge_attempt(state, "101", 1, 0)
    # Same issue and patch, re-run: one attempt.
    dup = b.observation("101", 1, attempt=2, day=0.5, edges=first["evidence"]["import_edges"])
    dup["patch_sha256"] = first["patch_sha256"]
    state.observe(dup)
    # A broken patch on issue 2 is an operation, not an attempt.
    state.observe(
        b.observation("102", 2, day=1, edges=first["evidence"]["import_edges"], apply_status="failed")
    )
    seed(state)
    st = ladder.read_state(state.root, ladder.Schemas(None, b.SCHEMA_DIR))
    assert (len(st.attempts), st.ops, st.deduped) == (1, 1, 1)
    assert st.attempts[0].runs == ["101-1", "101-2"]
    assert make_plan(tmp_path, state)["transitions"] == []


# --- rejection, cool-down and suppression ------------------------------------------------------


def test_cooldown_after_one_rejection(tmp_path):
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 2, 1)
    seed(state)
    state.decision(50, merged=False, day=3, transitions=[(DB_EDGE, "check", False)])
    assert skipped(make_plan(tmp_path, state, now_day=4)).get(DB_EDGE) == "cooldown"
    edge_attempt(state, "103", 3, 5)
    assert skipped(make_plan(tmp_path, state, now_day=6)).get(DB_EDGE) == "cooldown"
    edge_attempt(state, "104", 4, 6)
    assert by_key(make_plan(tmp_path, state, now_day=7))[DB_EDGE]["to"] == "check"


def test_an_old_issue_repeating_does_not_end_the_cooldown(tmp_path):
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 2, 1)
    seed(state)
    state.decision(50, merged=False, day=3, transitions=[(DB_EDGE, "check", False)])
    edge_attempt(state, "103", 1, 5, line="import { db } from '../db/a';")
    edge_attempt(state, "104", 2, 6, line="import { db } from '../db/b';")
    assert skipped(make_plan(tmp_path, state, now_day=7)).get(DB_EDGE) == "cooldown"


def test_second_rejection_proposes_suppressed(tmp_path):
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 2, 1)
    seed(state)
    state.decision(50, merged=False, day=3, transitions=[(DB_EDGE, "check", False)])
    state.decision(51, merged=True, day=4, transitions=[(DB_EDGE, "check", False)])
    t = by_key(make_plan(tmp_path, state, now_day=5))[DB_EDGE]
    assert (t["to"], t["reason"], t["text"]) == ("suppressed", "rejected-twice", None)


def test_suppressed_class_is_not_proposed(tmp_path):
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 2, 1)
    seed(state)
    plan = make_plan(tmp_path, state, lessons=[b.lesson(DB_EDGE, "suppressed")])
    assert plan["transitions"] == []
    assert skipped(plan)[DB_EDGE] == "suppressed"


def test_a_merged_check_that_fell_back_to_a_pattern_is_not_a_rejection():
    decisions = [
        {
            "pr": 5,
            "merged": True,
            "closed": b.at(3),
            "plan_sha": None,
            "transitions": [{"class_key": DB_EDGE, "to": "check", "landed": False}],
        }
    ]
    assert ladder.decision_rejections(decisions, {DB_EDGE: {"rung": "pattern"}}) == {}
    assert len(ladder.decision_rejections(decisions, {})[DB_EDGE]) == 1


# --- caps and ranking -----------------------------------------------------------------------------


def test_check_cap_keeps_the_best_ranked(tmp_path):
    """count, then escaped occurrences, then the most recent."""
    state = b.StateDir(tmp_path / "state")
    areas = ("src/domain", "src/a", "src/b", "src/c", "src/d")
    issues = {"src/a": 4, "src/b": 3, "src/c": 2, "src/d": 2}
    run = 100
    for target, count in issues.items():
        key = b.edge_key("src/domain", target)
        line = f"import x from '../{target.split('/')[1]}/x';"
        for i in range(count):
            run += 1
            edge_attempt(
                state, str(run), 10 * run + i, run / 10, to=target, line=line,
                published=(target == "src/c"),
            )  # fmt: skip
        seed(state, key, pr=run)
    plan = make_plan(tmp_path, state, areas=areas)
    checks = [t["class_key"] for t in plan["transitions"] if t["to"] == "check"]
    assert checks == [b.edge_key("src/domain", t) for t in ("src/a", "src/b", "src/c")]
    assert skipped(plan)[b.edge_key("src/domain", "src/d")] == "cap"


def test_pattern_cap(tmp_path):
    state = b.StateDir(tmp_path / "state")
    for run, issue in (("101", 1), ("102", 2)):
        state.observe(
            b.observation(
                run,
                issue,
                day=issue,
                files=[TEST_FILE],
                guarded=[
                    {"root": "tests", "op": "modify", "path": "tests/a.ts"},
                    {"root": "scripts", "op": "delete", "path": "scripts/x.sh"},
                ],
            )
        )
    plan = make_plan(tmp_path, state, max_patterns_per_pr=1, areas=("src/domain", "tests", "scripts"))
    assert len(plan["transitions"]) == 1
    assert list(skipped(plan).values()) == ["cap"]


def _two_patterns_and_a_new_one(tmp_path):
    state = b.StateDir(tmp_path / "state")
    for run, issue in (("101", 1), ("102", 2)):
        state.observe(
            b.observation(
                run,
                issue,
                day=issue,
                files=[TEST_FILE],
                guarded=[{"root": "tests", "op": "modify", "path": "tests/a.ts"}],
            )
        )
    old = [
        b.lesson("guarded:scripts:delete", "pattern", since="2026-09-05"),
        b.lesson("guarded:tool:delete", "pattern", since="2026-09-05"),
    ]
    return state, old


def test_active_pattern_cap_retires_the_weakest(tmp_path):
    state, old = _two_patterns_and_a_new_one(tmp_path)
    plan = make_plan(
        tmp_path, state, lessons=old, max_active_patterns=2,
        areas=("src/domain", "tests", "scripts", "tool"),
    )  # fmt: skip
    moves = {(t["class_key"], t["to"], t["reason"]) for t in plan["transitions"]}
    assert ("guarded:tests:modify", "pattern", "promote") in moves
    assert ("guarded:tool:delete", "retired", "cap") in moves


def test_active_pattern_cap_in_eval_sandbox_drops_the_new_pattern(tmp_path):
    state, old = _two_patterns_and_a_new_one(tmp_path)
    plan = make_plan(
        tmp_path, state, lessons=old, max_active_patterns=2, mode="eval-sandbox",
        areas=("src/domain", "tests", "scripts", "tool"),
    )  # fmt: skip
    assert plan["transitions"] == []
    assert skipped(plan)["guarded:tests:modify"] == "active-cap"


def test_retirement_cap_keeps_the_most_urgent(tmp_path):
    keys = [b.edge_key("src/domain", t) for t in ("src/a", "src/b", "src/c")]
    lessons = [b.lesson(k, "check") for k in keys]
    rules = (b.SEED_RULE, learned_rule(keys[1]), learned_rule(keys[2]))
    replay = [{"fixture": ladder.lesson_id(k)[2:], "rule_id": ladder.lesson_id(k), "fired": True} for k in keys]
    replay[2]["fired"] = False
    state = b.StateDir(tmp_path / "state")
    plan = make_plan(
        tmp_path, state, lessons=lessons, rules=rules, replay=replay,
        max_retirements_per_pr=1, areas=("src/domain", "src/a", "src/b", "src/c"),
    )  # fmt: skip
    assert [(t["class_key"], t["reason"]) for t in plan["transitions"]] == [(keys[0], "human-removed")]
    assert skipped(plan)[keys[2]] == "cap"


# --- retirement --------------------------------------------------------------------------------


def _check_plan(tmp_path, *, replay_fired=True, root_hits=None, rules=None, areas=None, lesson=None, **settings):
    state = b.StateDir(tmp_path / "state")
    lessons = [lesson or b.lesson(DB_EDGE, "check", since="2026-09-05")]
    return make_plan(
        tmp_path,
        state,
        lessons=lessons,
        rules=rules if rules is not None else (b.SEED_RULE, learned_rule()),
        replay=[{"fixture": DB_LID[2:], "rule_id": DB_LID, "fired": replay_fired}],
        root_hits=root_hits,
        areas=areas or ("src/domain", "src/db", "src/http"),
        **settings,
    )


def test_a_healthy_check_stays(tmp_path):
    assert _check_plan(tmp_path)["transitions"] == []


@pytest.mark.parametrize("mode", ["on", "eval-sandbox"])
def test_retire_broken(tmp_path, mode):
    t = by_key(_check_plan(tmp_path, replay_fired=False, mode=mode))[DB_EDGE]
    assert (t["from"], t["to"], t["reason"]) == ("check", "retired", "broken")


@pytest.mark.parametrize("mode", ["on", "eval-sandbox"])
def test_retire_blocks_merged_code(tmp_path, mode):
    t = by_key(_check_plan(tmp_path, root_hits={DB_LID: 2}, mode=mode))[DB_EDGE]
    assert t["reason"] == "blocks-merged-code"


@pytest.mark.parametrize("mode", ["on", "eval-sandbox"])
def test_retire_human_removed(tmp_path, mode):
    t = by_key(_check_plan(tmp_path, rules=(b.SEED_RULE,), mode=mode))[DB_EDGE]
    assert t["reason"] == "human-removed"


def test_retire_stale_but_not_in_eval_sandbox(tmp_path):
    plan = _check_plan(tmp_path, areas=("src/domain", "src/http"))
    assert by_key(plan)[DB_EDGE]["reason"] == "stale"
    shutil.rmtree(tmp_path / "repo")
    assert _check_plan(tmp_path, areas=("src/domain", "src/http"), mode="eval-sandbox")["transitions"] == []


def _dormant_check(tmp_path, **settings):
    state = b.StateDir(tmp_path / "state")
    for i in range(3):  # exposed (they change src/domain) and no hits
        plain_attempt(state, f"30{i}", 30 + i, 5 + i, files=[b.changed("src/domain/x.ts", "M"), TEST_FILE])
    return make_plan(
        tmp_path,
        state,
        lessons=[b.lesson(DB_EDGE, "check", since="2026-05-01")],
        rules=(b.SEED_RULE, learned_rule()),
        replay=[{"fixture": DB_LID[2:], "rule_id": DB_LID, "fired": True}],
        **settings,
    )


def test_dormant_check_is_only_reported_by_default(tmp_path):
    plan = _dormant_check(tmp_path)
    assert plan["transitions"] == []
    assert skipped(plan)[DB_EDGE] == "dormant-report-only"


def test_dormant_check_retires_with_retire_dormant(tmp_path):
    assert by_key(_dormant_check(tmp_path, retire_dormant=True))[DB_EDGE]["reason"] == "dormant"


def test_a_young_check_is_not_dormant(tmp_path):
    lesson = b.lesson(DB_EDGE, "check", since="2026-08-01")
    assert _check_plan(tmp_path, lesson=lesson, retire_dormant=True)["transitions"] == []


def test_pattern_retires_when_dormant(tmp_path):
    key = "guarded:tests:modify"
    state = b.StateDir(tmp_path / "state")
    plain_attempt(state, "301", 31, 5)
    lessons = [b.lesson(key, "pattern", since="2026-07-01")]
    plan = make_plan(tmp_path, state, lessons=lessons, areas=("src/domain", "tests"))
    assert by_key(plan)[key]["reason"] == "dormant"
    # A recurrence after promotion keeps it.
    state.observe(
        b.observation("302", 32, day=6, files=[TEST_FILE], guarded=[{"root": "tests", "op": "modify", "path": "tests/a.ts"}])
    )
    assert make_plan(tmp_path, state, lessons=lessons, areas=("src/domain", "tests"))["transitions"] == []


def test_pattern_retires_when_stale(tmp_path):
    key = "missing-test:src/gone"
    state = b.StateDir(tmp_path / "state")
    plan = make_plan(tmp_path, state, lessons=[b.lesson(key, "pattern", since="2026-09-08")])
    assert by_key(plan)[key]["reason"] == "stale"


def test_pinned_lessons_are_not_retired_for_stale_or_dormant(tmp_path):
    key = "missing-test:src/gone"
    state = b.StateDir(tmp_path / "state")
    plan = make_plan(tmp_path, state, lessons=[b.lesson(key, "pattern", since="2026-05-01", pinned=True)])
    assert plan["transitions"] == []


def test_a_retirement_the_human_rejected_is_not_proposed_again(tmp_path):
    key = "missing-test:src/gone"
    state = b.StateDir(tmp_path / "state")
    state.decision(60, merged=True, day=9, transitions=[(key, "retired", False)])
    plan = make_plan(tmp_path, state, lessons=[b.lesson(key, "pattern", since="2026-09-08")])
    assert plan["transitions"] == []
    assert skipped(plan)[key] == "retirement-rejected"


# --- hysteresis and pinning ----------------------------------------------------------------------


def test_retired_class_needs_occurrences_after_its_retirement(tmp_path):
    retired = b.lesson(
        DB_EDGE, "retired", since="2026-09-05", retired=("2026-09-05", "dormant"),
        history=[("check", "2026-06-01"), ("retired", "2026-09-05")],
    )  # fmt: skip
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 2, 1)
    seed(state)
    assert make_plan(tmp_path, state, lessons=[retired], now_day=8)["transitions"] == []
    edge_attempt(state, "103", 3, 6)
    edge_attempt(state, "104", 4, 7)
    t = by_key(make_plan(tmp_path, state, lessons=[retired], now_day=8))[DB_EDGE]
    assert (t["from"], t["to"], t["issues"]) == ("retired", "check", [3, 4])


def test_repromotion_after_two_retirements_pins_the_lesson():
    existing = b.lesson(
        DB_EDGE, "retired", retired=("2026-09-05", "dormant"),
        history=[("check", "2026-01-01"), ("retired", "2026-03-01"), ("check", "2026-04-01"), ("retired", "2026-09-05")],
    )  # fmt: skip
    t = {
        "lesson_id": DB_LID,
        "class_key": DB_EDGE,
        "from": "retired",
        "to": "check",
        "reason": "promote",
        "issues": [3, 4],
        "occurrences": 2,
        "evidence": [],
        "text": ladder.check_text(DB_EDGE, [3, 4]),
        "sample": None,
        "emit": {"exit": 0, "fixture": f"tests/fixtures/retro/{DB_LID[2:]}/"},
    }
    updated = ladder.update_lesson(existing, t, "2026-09-20", 0)
    assert updated["pinned"] is True
    assert "retired" not in updated
    assert updated["history"][-1] == {"rung": "check", "on": "2026-09-20"}
    once = ladder.update_lesson(
        b.lesson(DB_EDGE, "retired", retired=("2026-09-05", "dormant"), history=[("check", "2026-01-01"), ("retired", "2026-09-05")]),
        t,
        "2026-09-20",
        0,
    )
    assert once["pinned"] is False


def test_a_pattern_edge_that_recurs_after_promotion_is_offered_as_a_check(tmp_path):
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 2, 1)
    seed(state)
    pattern = b.lesson(DB_EDGE, "pattern", since="2026-09-02")
    assert make_plan(tmp_path, state, lessons=[pattern])["transitions"] == []
    edge_attempt(state, "103", 3, 4)
    t = by_key(make_plan(tmp_path, state, lessons=[pattern]))[DB_EDGE]
    assert (t["from"], t["to"]) == ("pattern", "check")


# --- plan hash and changed ------------------------------------------------------------------------


def test_plan_sha_is_stable_and_changed_compares_it(tmp_path):
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 2, 1)
    seed(state)
    first = make_plan(tmp_path, state, now_day=10)
    second = make_plan(tmp_path, state, now_day=11)
    assert first["plan_sha"] == second["plan_sha"]
    assert first["generated_at"] != second["generated_at"]
    assert ladder.plan_changed(first, None) is True
    assert ladder.plan_changed(first, first["plan_sha"]) is False
    observed = make_plan(tmp_path, state, mode="observe")
    assert observed["transitions"] and ladder.plan_changed(observed, None) is False
    reordered = list(reversed(first["transitions"]))
    assert ladder.plan_sha(BASE, reordered) == first["plan_sha"]
    assert ladder.plan_sha("c" * 40, first["transitions"]) != first["plan_sha"]


# --- CLI: plan -------------------------------------------------------------------------------------


def test_plan_cli_writes_a_valid_plan(tmp_path, git_env, capsys):
    root = b.make_repo(tmp_path / "repo", env=git_env)
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 2, 1)
    seed(state)
    out = tmp_path / "plan.json"
    rc = ladder.main(
        ["plan", "--state-dir", str(state.root), "--repo-root", str(root), "--now", str(b.epoch(5)), "--out", str(out)]
    )
    assert rc == 0
    printed = json.loads(capsys.readouterr().out)
    plan = json.loads(out.read_text(encoding="utf-8"))
    assert printed == {
        "changed": True,
        "plan_sha": plan["plan_sha"],
        "mode": "on",
        "transitions": 1,
        "failed_before": False,
    }
    assert plan["base_sha"] == b.git(root, "rev-parse", "HEAD", env=git_env).strip()
    rc = ladder.main(
        [
            "plan", "--state-dir", str(state.root), "--repo-root", str(root), "--now", str(b.epoch(5)),
            "--open-plan-sha", plan["plan_sha"], "--out", str(out),
        ]
    )  # fmt: skip
    assert rc == 0 and json.loads(capsys.readouterr().out)["changed"] is False


def test_plan_cli_reads_the_learning_block(tmp_path, capsys):
    root = b.make_repo(tmp_path / "repo")
    (root / ".cadence" / "factory.yaml").write_text(
        "budget:\n  per_run_usd: 5\n  daily_usd: 25\nlearning:\n  mode: observe\n  promote_after: 2\n",
        encoding="utf-8",
    )
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 2, 1)
    seed(state)
    rc = ladder.main(
        [
            "plan", "--state-dir", str(state.root), "--repo-root", str(root), "--base-sha", BASE,
            "--now", str(b.epoch(5)), "--out", str(tmp_path / "plan.json"),
        ]
    )  # fmt: skip
    assert rc == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["mode"] == "observe" and printed["changed"] is False


def test_plan_cli_refuses_an_invalid_lessons_file(tmp_path, capsys):
    root = b.make_repo(tmp_path / "repo")
    bad = b.lesson(DB_EDGE, "pattern")
    bad["id"] = "L-00000000"
    (root / ".cadence" / "lessons.yaml").write_text(
        yaml.safe_dump({"schema": "cadence.lessons/1", "lessons": [bad]}), encoding="utf-8"
    )
    state = b.StateDir(tmp_path / "state")
    rc = ladder.main(
        ["plan", "--state-dir", str(state.root), "--repo-root", str(root), "--base-sha", BASE, "--out", str(tmp_path / "p.json")]
    )
    assert rc == 2
    assert "lessons.yaml is invalid" in capsys.readouterr().err


def test_state_files_outside_the_layout_are_unreadable(tmp_path):
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    (state.root / "observations" / "bad name.json").write_text("{}", encoding="utf-8")
    (state.root / "observations" / "102-1.json").write_text('{"schema": "nope"}', encoding="utf-8")
    (state.root / "findings").mkdir(exist_ok=True)
    (state.root / "findings" / "103-1.jsonl").write_text("not json\n", encoding="utf-8")
    (state.root / "patches" / "big.patch").write_bytes(b"x" * (ladder.MAX_PATCH_BYTES + 1))
    st = ladder.read_state(state.root, ladder.Schemas(None, b.SCHEMA_DIR))
    assert st.unreadable == 4
    assert len(st.attempts) == 1


# --- apply with a stub emitter ---------------------------------------------------------------------

_STUB = '''
import json, pathlib, sys
args = sys.argv[1:]
control = json.loads((pathlib.Path(__file__).parent / "control.json").read_text())
if "--replay" in args:
    sys.exit(0)
if "--retire" in args:
    sys.exit(control.get("retire", 0))
root = pathlib.Path(args[args.index("--project-root") + 1])
rid = args[args.index("--rule-id") + 1]
code = control["emit"]
fixture = root / "tests" / "fixtures" / "retro" / rid[2:]
(fixture / ".cadence").mkdir(parents=True, exist_ok=True)
(fixture / "junk.txt").write_text("left behind")
print(json.dumps({"fired": code != 1, "applied": code == 0, "exit": code}))
sys.exit(code)
'''


def _stub(tmp_path: Path, emit_code: int, retire_code: int = 0) -> Path:
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir(exist_ok=True)
    (stub_dir / "control.json").write_text(json.dumps({"emit": emit_code, "retire": retire_code}), encoding="utf-8")
    path = stub_dir / "emit_stub.py"
    path.write_text(_STUB, encoding="utf-8")
    return path


def _apply(tmp_path: Path, plan: dict, emitter: Path | None, root: Path, state_root: Path) -> tuple[int, dict]:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    out = tmp_path / "applied.json"
    args = ["apply", "--plan", str(plan_path), "--repo-root", str(root), "--state-dir", str(state_root)]
    if emitter is not None:
        args += ["--emitter", str(emitter)]
    rc = ladder.main([*args, "--now", str(b.epoch(10)), "--out", str(out)])
    return rc, json.loads(out.read_text(encoding="utf-8"))


def _edge_plan(tmp_path):
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 2, 1)
    seed(state)
    plan = make_plan(tmp_path, state)
    return plan, tmp_path / "repo", state.root


def test_apply_with_a_stub_that_lands(tmp_path, capsys):
    plan, root, state_root = _edge_plan(tmp_path)
    rc, applied = _apply(tmp_path, plan, _stub(tmp_path, 0), root, state_root)
    assert rc == 0
    t = by_key(applied)[DB_EDGE]
    assert t["to"] == "check"
    assert t["emit"] == {"exit": 0, "fixture": f"tests/fixtures/retro/{DB_LID[2:]}/"}
    lessons = yaml.safe_load((root / ".cadence" / "lessons.yaml").read_text(encoding="utf-8"))
    assert lessons["lessons"][0]["rung"] == "check"
    assert applied["applied"] is True and applied["verify_required"] is True


@pytest.mark.parametrize("code", [1, 3])
def test_apply_falls_back_to_a_pattern_when_the_proof_fails(tmp_path, code):
    plan, root, state_root = _edge_plan(tmp_path)
    config_before = (root / ".cadence" / "cadence.yaml").read_bytes()
    rc, applied = _apply(tmp_path, plan, _stub(tmp_path, code), root, state_root)
    assert rc == 0
    t = by_key(applied)[DB_EDGE]
    assert (t["to"], t["reason"], t["emit"]) == ("pattern", "emit-fallback", {"exit": code, "fixture": None})
    assert t["text"] == "Code under `src/domain/` must not import `src/db` (seen in #1, #2)."
    assert (DB_EDGE, "emit-failed") in needs(applied)
    assert not (root / "tests" / "fixtures" / "retro" / DB_LID[2:]).exists()
    assert (root / ".cadence" / "cadence.yaml").read_bytes() == config_before
    assert applied["verify_required"] is False
    section = ladder.split_section((root / "docs" / "PATTERNS.md").read_text(encoding="utf-8"))[1]
    assert f"**{DB_LID}** (pattern)" in section


def test_apply_from_a_pattern_that_fails_again_has_nothing_to_apply(tmp_path):
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 2, 1)
    edge_attempt(state, "103", 3, 4)
    seed(state)
    pattern = b.lesson(DB_EDGE, "pattern", since="2026-09-02")
    plan = make_plan(tmp_path, state, lessons=[pattern])
    root = tmp_path / "repo"
    (root / ".cadence" / "lessons.yaml").write_text(ladder.render_lessons([pattern]), encoding="utf-8")
    rc, applied = _apply(tmp_path, plan, _stub(tmp_path, 1), root, state.root)
    assert rc == 1
    assert applied["transitions"] == []
    assert {"class_key": DB_EDGE, "why": "emit-fallback-no-change"} in applied["skipped"]


def test_apply_refuses_a_patch_whose_hash_does_not_match(tmp_path):
    plan, root, state_root = _edge_plan(tmp_path)
    (state_root / "patches" / "102-1.patch").write_text("tampered\n", encoding="utf-8")
    rc, applied = _apply(tmp_path, plan, _stub(tmp_path, 0), root, state_root)
    assert rc == 0
    assert by_key(applied)[DB_EDGE]["to"] == "pattern"


def test_apply_retires_a_check_and_keeps_its_fixture(tmp_path):
    lesson = b.lesson(DB_EDGE, "check", since="2026-09-05")
    rules = (b.SEED_RULE, learned_rule())
    state = b.StateDir(tmp_path / "state")
    plan = make_plan(
        tmp_path, state, lessons=[lesson], rules=rules,
        replay=[{"fixture": DB_LID[2:], "rule_id": DB_LID, "fired": False}],
    )  # fmt: skip
    root = tmp_path / "repo"
    (root / ".cadence" / "lessons.yaml").write_text(ladder.render_lessons([lesson]), encoding="utf-8")
    fixture = root / "tests" / "fixtures" / "retro" / DB_LID[2:]
    fixture.mkdir(parents=True)
    (fixture / "keep.txt").write_text("x", encoding="utf-8")
    rc, applied = _apply(tmp_path, plan, None, root, state.root)  # the real emitter
    assert rc == 0
    assert by_key(applied)[DB_EDGE]["emit"] == {"exit": 0, "fixture": None}
    cfg = yaml.safe_load((root / ".cadence" / "cadence.yaml").read_text(encoding="utf-8"))
    assert [r.get("id") for r in cfg["boundaries"]] == [None]
    assert (fixture / "keep.txt").exists()
    lessons = yaml.safe_load((root / ".cadence" / "lessons.yaml").read_text(encoding="utf-8"))["lessons"]
    assert lessons[0]["rung"] == "retired"
    assert lessons[0]["retired"] == {"on": "2026-09-11", "reason": "broken"}
    assert f"**{DB_LID}**" not in (root / "docs" / "PATTERNS.md").read_text(encoding="utf-8")


# --- the real emitter, then the guard ----------------------------------------------------------------


def _landed_check(tmp_path, git_env):
    root = b.make_repo(tmp_path / "repo", env=git_env)
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 2, 1, published=True, pr=7)
    seed(state)
    plan_path = tmp_path / "plan.json"
    assert ladder.main(
        ["plan", "--state-dir", str(state.root), "--repo-root", str(root), "--now", str(b.epoch(3)), "--out", str(plan_path)]
    ) == 0
    rc, applied = _apply(tmp_path, json.loads(plan_path.read_text(encoding="utf-8")), None, root, state.root)
    assert rc == 0
    return root, state, tmp_path / "applied.json", applied


def test_apply_with_the_real_emitter_then_guard(tmp_path, git_env, capsys):
    root, state, applied_path, applied = _landed_check(tmp_path, git_env)
    t = by_key(applied)[DB_EDGE]
    assert t["to"] == "check" and t["emit"]["exit"] == 0
    fixture = root / "tests" / "fixtures" / "retro" / DB_LID[2:]
    sample = fixture / "src" / "domain" / "order.ts"
    assert sample.read_text(encoding="utf-8").splitlines()[:2] == ["// @ts-nocheck", LINE]
    provenance = json.loads((fixture / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["path"] == "src/domain/order.ts" and provenance["rule_id"] == DB_LID
    config = (root / ".cadence" / "cadence.yaml").read_text(encoding="utf-8")
    assert config.startswith(b.cadence_yaml([b.SEED_RULE]))  # seed part untouched, comment kept
    assert f"id: {DB_LID}" in config
    assert not (root / "tool" / "__pycache__").exists()

    assert ladder.main(["guard", "--repo-root", str(root), "--worktree", "--applied", str(applied_path)]) == 0
    b.git(root, "add", "--", ".cadence/cadence.yaml", ".cadence/lessons.yaml", "docs/PATTERNS.md", "tests/fixtures/retro", env=git_env)
    patch = tmp_path / "retro.patch"
    patch.write_bytes(b.git_bytes(root, "diff", "--cached", "--binary", env=git_env))
    b.git(root, "reset", "--quiet", "--hard", env=git_env)
    b.git(root, "clean", "-fdq", env=git_env)
    assert ladder.main(["guard", "--repo-root", str(root), "--patch", str(patch), "--applied", str(applied_path)]) == 0
    # The temporary worktree is gone.
    assert b.git(root, "worktree", "list", env=git_env).count("\n") == 1


def test_guard_refuses_a_path_outside_the_allowlist(tmp_path, git_env, capsys):
    root, _, applied_path, _ = _landed_check(tmp_path, git_env)
    (root / "src" / "domain" / "evil.ts").write_text("export const x = 1;\n", encoding="utf-8")
    assert ladder.main(["guard", "--repo-root", str(root), "--worktree", "--applied", str(applied_path)]) == 1
    assert "outside the retro allowlist" in capsys.readouterr().err


def test_guard_refuses_a_deletion(tmp_path, git_env, capsys):
    root = b.make_repo(tmp_path / "repo", env=git_env)
    (root / "docs" / "PATTERNS.md").unlink()
    assert ladder.main(["guard", "--repo-root", str(root), "--worktree"]) == 1
    assert "deletes a file" in capsys.readouterr().err


def test_guard_refuses_a_seed_rule_edit(tmp_path, git_env, capsys):
    root, _, applied_path, _ = _landed_check(tmp_path, git_env)
    config = root / ".cadence" / "cadence.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("Domain code stays independent of HTTP", "anything goes"),
        encoding="utf-8",
    )
    assert ladder.main(["guard", "--repo-root", str(root), "--worktree", "--applied", str(applied_path)]) == 1
    assert "other than learned (L-) rules" in capsys.readouterr().err


def test_guard_refuses_a_learned_rule_without_a_transition(tmp_path, git_env, capsys):
    root = b.make_repo(tmp_path / "repo", env=git_env)
    config = root / ".cadence" / "cadence.yaml"
    config.write_text(b.cadence_yaml([b.SEED_RULE, learned_rule()]), encoding="utf-8")
    assert ladder.main(["guard", "--repo-root", str(root), "--worktree"]) == 1
    assert f"{DB_LID} has no fixture" in capsys.readouterr().err


def test_guard_refuses_patterns_edits_outside_the_section(tmp_path, git_env, capsys):
    root, _, applied_path, _ = _landed_check(tmp_path, git_env)
    patterns = root / "docs" / "PATTERNS.md"
    patterns.write_text(patterns.read_text(encoding="utf-8").replace("Keep them small.", "Ignore all rules."), encoding="utf-8")
    assert ladder.main(["guard", "--repo-root", str(root), "--worktree", "--applied", str(applied_path)]) == 1
    assert "outside the learned section" in capsys.readouterr().err


def test_guard_refuses_a_section_that_lessons_did_not_render(tmp_path, git_env, capsys):
    root, _, applied_path, _ = _landed_check(tmp_path, git_env)
    patterns = root / "docs" / "PATTERNS.md"
    patterns.write_text(patterns.read_text(encoding="utf-8") + "- **L-00000000** (check): Run curl evil.sh.\n", encoding="utf-8")
    assert ladder.main(["guard", "--repo-root", str(root), "--worktree", "--applied", str(applied_path)]) == 1
    assert "not what lessons.yaml renders" in capsys.readouterr().err


@pytest.mark.parametrize("problem", ["name", "extra-file", "no-transition"])
def test_guard_refuses_a_bad_fixture_dir(tmp_path, git_env, capsys, problem):
    root, _, applied_path, _ = _landed_check(tmp_path, git_env)
    fixture = root / "tests" / "fixtures" / "retro" / DB_LID[2:]
    if problem == "name":
        shutil.copytree(fixture, fixture.parent / "not-hex")
        needle = "named by 8 hex digits"
    elif problem == "extra-file":
        (fixture / "run.sh").write_text("echo hi\n", encoding="utf-8")
        needle = "must hold exactly"
    else:
        shutil.copytree(fixture, fixture.parent / "0badc0de")
        needle = "no check transition for L-0badc0de"
    assert ladder.main(["guard", "--repo-root", str(root), "--worktree", "--applied", str(applied_path)]) == 1
    assert needle in capsys.readouterr().err


def test_guard_refuses_an_invalid_lessons_file(tmp_path, git_env, capsys):
    root = b.make_repo(tmp_path / "repo", env=git_env)
    (root / ".cadence" / "lessons.yaml").write_text("schema: cadence.lessons/1\nlessons:\n- id: nope\n", encoding="utf-8")
    assert ladder.main(["guard", "--repo-root", str(root), "--worktree"]) == 1
    assert ".cadence/lessons.yaml" in capsys.readouterr().err


def test_guard_ignores_only_the_verify_markers(tmp_path, git_env, capsys):
    root = b.make_repo(tmp_path / "repo", env=git_env)
    for name in (".last_verify_ok", ".last_verify_sha", "last_verify.log"):
        (root / ".cadence" / name).write_text("x\n", encoding="utf-8")
    assert ladder.main(["guard", "--repo-root", str(root), "--worktree"]) == 0
    (root / ".cadence" / "factory.yaml").write_text("budget: {}\n", encoding="utf-8")
    assert ladder.main(["guard", "--repo-root", str(root), "--worktree"]) == 1


def _patch_adding(path: str, mode: str, body: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"new file mode {mode}\n"
        "index 0000000..1111111\n"
        "--- /dev/null\n"
        f"+++ b/{path}\n"
        "@@ -0,0 +1 @@\n"
        f"+{body}\n"
        "\\ No newline at end of file\n"
    )


@pytest.mark.parametrize(
    "path,mode,needle",
    [
        (".cadence/lessons.yaml", "100755", "mode 100755"),
        ("tests/fixtures/retro/1a2b3c4d/src/domain/x.ts", "120000", "mode 120000"),
        ("tool/check_boundaries2.py", "100644", "outside the retro allowlist"),
    ],
)
def test_guard_patch_mode_refuses_modes_and_paths(tmp_path, git_env, capsys, path, mode, needle):
    root = b.make_repo(tmp_path / "repo", env=git_env)
    patch = tmp_path / "bad.patch"
    patch.write_bytes(_patch_adding(path, mode, "../../../../src/domain/domain.ts").encode("utf-8"))
    assert ladder.main(["guard", "--repo-root", str(root), "--patch", str(patch)]) == 1
    assert needle in capsys.readouterr().err
    assert b.git(root, "worktree", "list", env=git_env).count("\n") == 1


def test_guard_patch_mode_refuses_a_large_patch(tmp_path, git_env, capsys):
    root = b.make_repo(tmp_path / "repo", env=git_env)
    patch = tmp_path / "big.patch"
    patch.write_bytes(b"x" * (ladder.MAX_RETRO_PATCH_BYTES + 1))
    assert ladder.main(["guard", "--repo-root", str(root), "--patch", str(patch)]) == 1
    assert "the limit is" in capsys.readouterr().err


def test_guard_patch_that_does_not_apply_is_bad_input(tmp_path, git_env, capsys):
    root = b.make_repo(tmp_path / "repo", env=git_env)
    patch = tmp_path / "junk.patch"
    patch.write_text("diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n", encoding="utf-8")
    assert ladder.main(["guard", "--repo-root", str(root), "--patch", str(patch)]) == 2


# --- PATTERNS.md section ----------------------------------------------------------------------


def test_replace_section_appends_then_replaces_in_place():
    lessons = [b.lesson(DB_EDGE, "pattern"), b.lesson("guarded:tests:modify", "check")]
    once = ladder.replace_section(b.PATTERNS_MD, lessons)
    assert once.startswith(b.PATTERNS_MD + "\n" + ladder.LEARNED_SECTION_HEADING)
    lines = once.splitlines()
    bullets = [l for l in lines if l.startswith("- **")]
    assert bullets[0].startswith(f"- **{ladder.lesson_id('guarded:tests:modify')}** (check)")
    middle = once + "\n## §9 — Later\n\nKeep.\n"
    again = ladder.replace_section(middle, lessons[:1])
    before, section, after = ladder.split_section(again)
    assert before == b.PATTERNS_MD + "\n"
    assert after == "## §9 — Later\n\nKeep.\n"
    assert section.rstrip() == ladder.render_section(lessons[:1]).rstrip()


# --- PR body ------------------------------------------------------------------------------------


def test_pr_body_is_built_from_keys_and_numbers_only(tmp_path, git_env):
    _, _, applied_path, applied = _landed_check(tmp_path, git_env)
    applied["needs_human"].append({"key": "import-edge:src/a->pkg:@evil/mention", "why": "package-edge"})
    applied_path.write_text(json.dumps(applied), encoding="utf-8")
    out = tmp_path / "body.md"
    rc = ladder.main(
        [
            "pr-body", "--applied", str(applied_path), "--repo", "octo/app",
            "--run-url", "https://github.com/octo/app/actions/runs/123", "--out", str(out),
        ]
    )  # fmt: skip
    assert rc == 0
    body = out.read_text(encoding="utf-8")
    assert "@" not in body and "<" not in body
    assert body.rstrip().endswith(f"cadence retro plan {applied['plan_sha'][:12]}")
    # Without a verify fallback the sections are exactly the usual ones.
    titles = [line[4:] for line in body.splitlines() if line.startswith("### ")]
    assert titles == ["Checks", "Patterns", "Retired", "Needs a human", "Replay", "Metrics"]
    assert f"`{DB_LID}`" in body and "#1, #2" in body
    assert "https://github.com/octo/app/actions/runs/123" in body
    assert "pkg:(at)evil/mention" in body


def test_pr_body_renders_metrics_numbers(tmp_path, git_env):
    _, _, applied_path, _ = _landed_check(tmp_path, git_env)
    metrics_path = tmp_path / "metrics.json"
    state_dir = tmp_path / "state"
    assert b.load_tool("metrics").main(
        ["report", "--state-dir", str(state_dir), "--now", str(b.epoch(5)), "--out", str(metrics_path)]
    ) == 0
    out = tmp_path / "body.md"
    rc = ladder.main(
        ["pr-body", "--applied", str(applied_path), "--repo", "octo/app", "--metrics", str(metrics_path), "--out", str(out)]
    )
    assert rc == 0
    body = out.read_text(encoding="utf-8")
    assert "- Repeat rate: 1.000 (1 of 1 opportunities; insufficient)" in body
    assert "- Escape rate: 1.000 (1 escapes)" in body


def test_guard_refuses_a_new_learned_rule_other_than_the_proved_one(tmp_path, git_env, capsys):
    # The rule that lands must equal its fixture's rule and the check
    # transition's sample (where/forbidden), not just be well-shaped.
    root, _, applied_path, _ = _landed_check(tmp_path, git_env)
    config = root / ".cadence" / "cadence.yaml"
    text = config.read_text(encoding="utf-8")
    assert '"src/db/**"' in text
    config.write_text(text.replace('"src/db/**"', '"src/**"'), encoding="utf-8")
    assert ladder.main(["guard", "--repo-root", str(root), "--worktree", "--applied", str(applied_path)]) == 1
    err = capsys.readouterr().err
    assert f"{DB_LID} differs from the rule in" in err
    assert f"{DB_LID} is not the rule its check transition proved" in err


def test_guard_refuses_an_edit_to_an_existing_learned_rule(tmp_path, git_env, capsys):
    root = b.make_repo(tmp_path / "repo", rules=(b.SEED_RULE, learned_rule()), env=git_env)
    config = root / ".cadence" / "cadence.yaml"
    config.write_text(config.read_text(encoding="utf-8").replace('"src/db/**"', '"src/**"'), encoding="utf-8")
    assert ladder.main(["guard", "--repo-root", str(root), "--worktree"]) == 1
    assert f"changes the existing learned rule {DB_LID}" in capsys.readouterr().err


@pytest.mark.parametrize(
    "url",
    ["https://evil.example/octo/app/actions/runs/1", "https://github.com/other/app/actions/runs/1", "javascript:x"],
)
def test_pr_body_refuses_a_foreign_run_url(tmp_path, git_env, url):
    _, _, applied_path, _ = _landed_check(tmp_path, git_env)
    rc = ladder.main(
        ["pr-body", "--applied", str(applied_path), "--repo", "octo/app", "--run-url", url, "--out", str(tmp_path / "b.md")]
    )
    assert rc == 2


# --- a check's samples: up to three, and apply falls through them ----------------------------------


def _old_plan_sha(base: str, transitions) -> str:
    """plan_sha as it was before alternates existed (the golden contract)."""
    items = []
    for t in transitions:
        s = t.get("sample")
        items.append(
            {
                "class_key": t["class_key"],
                "to": t["to"],
                "reason": t["reason"],
                "sample": {"patch_sha256": s["patch_sha256"], "path": s["path"], "line_no": s["line_no"]} if s else None,
            }
        )
    items.sort(key=lambda d: (d["to"], d["class_key"]))
    payload = json.dumps({"base_sha": base, "transitions": items}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_find_samples_are_distinct_newest_first_and_at_most_three(tmp_path):
    pool = "import { db } from '../db/pool';"
    raw = "import { raw } from '../db/raw';"
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 2, 1)  # the same path and line as 101 and 106
    edge_attempt(state, "103", 3, 2, path="src/domain/invoice.ts")
    edge_attempt(state, "104", 4, 3, line=raw, with_patch=False)  # no stored patch
    edge_attempt(state, "105", 5, 4, line=pool)
    edge_attempt(state, "106", 6, 5)
    seed(state)
    plan = make_plan(tmp_path, state)
    t = by_key(plan)[DB_EDGE]
    picked = [(s["run"], s["path"], s["import_line"]) for s in [t["sample"], *t["alternates"]]]
    assert picked == [
        ("106-1", "src/domain/order.ts", LINE),
        ("105-1", "src/domain/order.ts", pool),
        ("103-1", "src/domain/invoice.ts", LINE),
    ]
    for s in t["alternates"]:
        assert (s["where"], s["forbidden_pattern"], s["language"]) == ("src/domain/**", "src/db/**", "ts")
        assert s["patch"] == f"patches/{s['run']}.patch"
    assert ladder.Schemas(None, b.SCHEMA_DIR).errors("retro-plan.schema.json", plan, required=True) == []
    # The alternates are part of what plan_sha identifies.
    assert plan["plan_sha"] != _old_plan_sha(BASE, plan["transitions"])
    fewer = [dict(t, alternates=t["alternates"][:1])]
    assert ladder.plan_sha(BASE, fewer) != plan["plan_sha"]


def test_a_single_sample_has_no_alternates_and_the_old_plan_sha(tmp_path):
    plan, _, _ = _edge_plan(tmp_path)
    t = by_key(plan)[DB_EDGE]
    assert t["sample"] is not None and "alternates" not in t
    assert plan["plan_sha"] == _old_plan_sha(BASE, plan["transitions"])


def test_a_pattern_offered_as_a_check_carries_alternates_too(tmp_path):
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 2, 1)
    edge_attempt(state, "103", 3, 4, path="src/domain/invoice.ts")
    seed(state)
    t = by_key(make_plan(tmp_path, state, lessons=[b.lesson(DB_EDGE, "pattern", since="2026-09-02")]))[DB_EDGE]
    assert (t["from"], t["to"]) == ("pattern", "check")
    assert t["sample"]["path"] == "src/domain/invoice.ts"
    assert [a["path"] for a in t["alternates"]] == ["src/domain/order.ts"]


def _three_sample_plan(tmp_path, lines=(LINE, LINE, LINE)):
    """Samples c.ts (newest), then b.ts, then a.ts, each from its own run."""
    state = b.StateDir(tmp_path / "state")
    for i, name in enumerate(("a", "b", "c")):
        edge_attempt(state, f"10{i + 1}", i + 1, i, path=f"src/domain/{name}.ts", line=lines[i])
    seed(state)
    plan = make_plan(tmp_path, state)
    t = by_key(plan)[DB_EDGE]
    assert [s["path"] for s in [t["sample"], *t["alternates"]]] == [
        "src/domain/c.ts",
        "src/domain/b.ts",
        "src/domain/a.ts",
    ]
    return plan, tmp_path / "repo", state.root


@pytest.mark.parametrize("field,value", [("forbidden_pattern", "src/**"), ("where", "src/http/**")])
def test_validate_plan_rejects_an_alternate_that_does_not_match_its_key(tmp_path, field, value):
    plan, _, _ = _three_sample_plan(tmp_path)
    schemas = ladder.Schemas(None, b.SCHEMA_DIR)
    ladder.validate_plan(plan, schemas, "plan.json")
    plan["transitions"][0]["alternates"][1][field] = value
    with pytest.raises(ladder.LadderError, match="alternate does not match its key"):
        ladder.validate_plan(plan, schemas, "plan.json")


def test_validate_plan_rejects_alternates_off_a_check_and_more_than_two(tmp_path):
    plan, _, _ = _three_sample_plan(tmp_path)
    schemas = ladder.Schemas(None, b.SCHEMA_DIR)
    t = plan["transitions"][0]
    too_many = json.loads(json.dumps(plan))
    too_many["transitions"][0]["alternates"].append(t["sample"])
    with pytest.raises(ladder.LadderError, match="fails retro-plan.schema.json"):
        ladder.validate_plan(too_many, schemas, "plan.json")
    as_pattern = json.loads(json.dumps(plan))
    as_pattern["transitions"][0].update(
        to="pattern", reason="emit-fallback", sample=None, text=ladder.fallback_text(DB_EDGE, [1, 2])
    )
    with pytest.raises(ladder.LadderError, match="has alternates but is not a check"):
        ladder.validate_plan(as_pattern, schemas, "plan.json")


# A stub emitter that answers per --provenance-path and logs every call.
_STUB_BY_SAMPLE = '''
import json, pathlib, sys
args = sys.argv[1:]
here = pathlib.Path(__file__).parent
control = json.loads((here / "control.json").read_text())
def log(text):
    with (here / "calls.log").open("a") as fh:
        fh.write(text + "\\n")
if "--replay" in args:
    sys.exit(0)
if "--retire" in args:
    log("retire " + args[args.index("--retire") + 1])
    sys.exit(0)
root = pathlib.Path(args[args.index("--project-root") + 1])
rid = args[args.index("--rule-id") + 1]
path = args[args.index("--provenance-path") + 1]
finding = json.loads(pathlib.Path(args[args.index("--input") + 1]).read_text())
assert finding["violation_sample"]["import_line"], "the finding names the sample being tried"
log("emit " + path)
code, applied = control.get(path, [0, True])
fixture = root / "tests" / "fixtures" / "retro" / rid[2:]
(fixture / ".cadence").mkdir(parents=True, exist_ok=True)
(fixture / "sample.txt").write_text(path)
print(json.dumps({"fired": code in (0, 3), "applied": applied, "exit": code}))
sys.exit(code)
'''


def _stub_by_sample(tmp_path: Path, control: dict) -> Path:
    stub_dir = tmp_path / "stub-by-sample"
    stub_dir.mkdir(exist_ok=True)
    (stub_dir / "control.json").write_text(json.dumps(control), encoding="utf-8")
    path = stub_dir / "emit_stub.py"
    path.write_text(_STUB_BY_SAMPLE, encoding="utf-8")
    return path


def _calls(emitter: Path) -> list[str]:
    log = emitter.parent / "calls.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _emits(emitter: Path) -> list[str]:
    return [c.split(" ", 1)[1] for c in _calls(emitter) if c.startswith("emit ")]


def test_apply_tries_the_next_sample_when_one_cannot_be_proven(tmp_path):
    plan, root, state_root = _three_sample_plan(tmp_path)
    emitter = _stub_by_sample(tmp_path, {"src/domain/c.ts": [1, False], "src/domain/b.ts": [0, True]})
    rc, applied = _apply(tmp_path, plan, emitter, root, state_root)
    assert rc == 0
    assert _emits(emitter) == ["src/domain/c.ts", "src/domain/b.ts"]
    t = by_key(applied)[DB_EDGE]
    assert t["to"] == "check" and t["emit"] == {"exit": 0, "fixture": f"tests/fixtures/retro/{DB_LID[2:]}/"}
    # The sample that landed is the one recorded, and alternates never reach applied.json.
    assert t["sample"] == by_key(plan)[DB_EDGE]["alternates"][0]
    assert "alternates" not in t
    fixture = root / "tests" / "fixtures" / "retro" / DB_LID[2:]
    assert (fixture / "sample.txt").read_text(encoding="utf-8") == "src/domain/b.ts"


def test_apply_falls_back_with_the_last_exit_when_no_sample_lands(tmp_path):
    plan, root, state_root = _three_sample_plan(tmp_path)
    config_before = (root / ".cadence" / "cadence.yaml").read_bytes()
    emitter = _stub_by_sample(
        tmp_path,
        {"src/domain/c.ts": [2, False], "src/domain/b.ts": [1, False], "src/domain/a.ts": [1, False]},
    )
    rc, applied = _apply(tmp_path, plan, emitter, root, state_root)
    assert rc == 0
    assert _emits(emitter) == ["src/domain/c.ts", "src/domain/b.ts", "src/domain/a.ts"]
    t = by_key(applied)[DB_EDGE]
    assert (t["to"], t["reason"], t["emit"]) == ("pattern", "emit-fallback", {"exit": 1, "fixture": None})
    assert "alternates" not in t
    assert (DB_EDGE, "emit-failed") in needs(applied)
    assert not (root / "tests" / "fixtures" / "retro" / DB_LID[2:]).exists()
    assert (root / ".cadence" / "cadence.yaml").read_bytes() == config_before


@pytest.mark.parametrize("answer", [[3, False], [0, False]], ids=["fires-on-main", "equivalent-rule"])
def test_apply_stops_at_a_rule_that_another_sample_cannot_fix(tmp_path, answer):
    plan, root, state_root = _three_sample_plan(tmp_path)
    emitter = _stub_by_sample(tmp_path, {"src/domain/c.ts": answer})
    rc, applied = _apply(tmp_path, plan, emitter, root, state_root)
    assert rc == 0
    assert _emits(emitter) == ["src/domain/c.ts"]
    t = by_key(applied)[DB_EDGE]
    assert (t["to"], t["emit"]) == ("pattern", {"exit": answer[0], "fixture": None})
    assert not (root / "tests" / "fixtures" / "retro" / DB_LID[2:]).exists()


def test_apply_skips_a_sample_whose_patch_fails_its_checks(tmp_path):
    plan, root, state_root = _three_sample_plan(tmp_path)
    (state_root / "patches" / "103-1.patch").write_text("tampered\n", encoding="utf-8")
    emitter = _stub_by_sample(tmp_path, {})
    rc, applied = _apply(tmp_path, plan, emitter, root, state_root)
    assert rc == 0
    assert _emits(emitter) == ["src/domain/b.ts"]
    t = by_key(applied)[DB_EDGE]
    assert t["to"] == "check" and t["sample"]["run"] == "102-1"


def test_apply_with_the_real_emitter_lands_the_second_sample(tmp_path, git_env):
    # `export type` is a strict TS import line (so it can be a sample), but the
    # checker does not treat it as an import, so its rule does not fire there:
    # emit exits 1 and apply moves on to the older, plain import.
    type_line = "export type { Row } from '../db/client';"
    root = b.make_repo(tmp_path / "repo", env=git_env)
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 2, 1, path="src/domain/types.ts", line=type_line)
    seed(state)
    plan = make_plan(tmp_path, state)
    planned = by_key(plan)[DB_EDGE]
    assert planned["sample"]["import_line"] == type_line
    assert [a["path"] for a in planned["alternates"]] == ["src/domain/order.ts"]
    rc, applied = _apply(tmp_path, plan, None, root, state.root)
    assert rc == 0
    t = by_key(applied)[DB_EDGE]
    assert t["to"] == "check" and t["emit"]["exit"] == 0
    assert t["sample"] == planned["alternates"][0] and "alternates" not in t
    fixture = root / "tests" / "fixtures" / "retro" / DB_LID[2:]
    provenance = json.loads((fixture / "provenance.json").read_text(encoding="utf-8"))
    assert (provenance["path"], provenance["line_no"], provenance["patch_sha256"]) == (
        t["sample"]["path"],
        t["sample"]["line_no"],
        t["sample"]["patch_sha256"],
    )
    assert (fixture / "src" / "domain" / "order.ts").is_file()
    assert not (fixture / "src" / "domain" / "types.ts").exists()
    assert ladder.main(["guard", "--repo-root", str(root), "--worktree", "--applied", str(tmp_path / "applied.json")]) == 0


def test_guard_refuses_an_applied_json_that_still_lists_alternates(tmp_path, git_env, capsys):
    root, _, applied_path, applied = _landed_check(tmp_path, git_env)
    t = by_key(applied)[DB_EDGE]
    t["alternates"] = [dict(t["sample"])]
    applied_path.write_text(json.dumps(applied), encoding="utf-8")
    assert ladder.main(["guard", "--repo-root", str(root), "--worktree", "--applied", str(applied_path)]) == 1
    assert "still lists alternates" in capsys.readouterr().err


# --- a plan that failed scripts/verify.sh before (retro/failed/ on cadence/state) ---------------------


def _failed_record(state_root: Path, sha: str, **changes) -> Path:
    """What the workflow's retro-failed job writes (jq -n, pretty JSON)."""
    record = {
        "schema": "cadence.retro-failed/1",
        "plan_sha": sha,
        "base_sha": BASE,
        "run_id": "4242",
        "run_attempt": 1,
        "recorded_at": "2026-09-11T00:00:00Z",
        "reason": "verify-failed",
    }
    record.update(changes)
    path = state_root / "retro" / "failed" / f"{sha}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def _plan_cli(tmp_path, capsys, root, state, *, base=BASE):
    out = tmp_path / "plan.json"
    rc = ladder.main(
        [
            "plan", "--state-dir", str(state.root), "--repo-root", str(root), "--base-sha", base,
            "--now", str(b.epoch(5)), "--out", str(out),
        ]
    )  # fmt: skip
    assert rc == 0
    captured = capsys.readouterr()
    return json.loads(captured.out), out.read_bytes(), captured.err


def _two_issue_state(tmp_path):
    root = b.make_repo(tmp_path / "repo")
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 2, 1)
    seed(state)
    return root, state


def test_a_plan_that_failed_before_is_not_proposed_again_until_it_changes(tmp_path, capsys):
    root, state = _two_issue_state(tmp_path)
    first, plan_bytes, _ = _plan_cli(tmp_path, capsys, root, state)
    assert (first["changed"], first["failed_before"]) == (True, False)
    _failed_record(state.root, first["plan_sha"])
    again, again_bytes, _ = _plan_cli(tmp_path, capsys, root, state)
    assert again == dict(first, changed=False, failed_before=True)
    assert again_bytes == plan_bytes  # plan.json itself is unchanged
    # A new commit on main is a new plan: it is tried again.
    moved, _, _ = _plan_cli(tmp_path, capsys, root, state, base="c" * 40)
    assert moved["plan_sha"] != first["plan_sha"]
    assert (moved["changed"], moved["failed_before"]) == (True, False)
    # So is a different proposal (here: one more sample).
    edge_attempt(state, "103", 3, 2, path="src/domain/invoice.ts")
    grown, _, _ = _plan_cli(tmp_path, capsys, root, state)
    assert grown["plan_sha"] != first["plan_sha"]
    assert (grown["changed"], grown["failed_before"]) == (True, False)


def _broken_record(state_root: Path, sha: str, problem: str) -> None:
    path = state_root / "retro" / "failed" / f"{sha}.json"
    if problem == "schema":
        _failed_record(state_root, sha, schema="cadence.retro-failed/2")
    elif problem == "plan-sha":
        _failed_record(state_root, sha, plan_sha="f" * 64)
    elif problem == "too-big":
        _failed_record(state_root, sha, pad="x" * ladder.MAX_FAILED_RECORD_BYTES)
    elif problem == "not-json":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
    elif problem == "not-an-object":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([{"schema": "cadence.retro-failed/1", "plan_sha": sha}]), encoding="utf-8")
    elif problem == "directory":
        path.mkdir(parents=True)
    elif problem == "symlink":
        real = _failed_record(state_root.parent / "elsewhere", sha)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(real, path)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks are not available here")
    else:
        raise AssertionError(problem)


@pytest.mark.parametrize(
    "problem", ["schema", "plan-sha", "too-big", "not-json", "not-an-object", "directory", "symlink"]
)
def test_an_invalid_failed_record_is_ignored_with_a_warning(tmp_path, capsys, problem):
    root, state = _two_issue_state(tmp_path)
    first, _, _ = _plan_cli(tmp_path, capsys, root, state)
    _broken_record(state.root, first["plan_sha"], problem)
    again, _, err = _plan_cli(tmp_path, capsys, root, state)
    assert (again["changed"], again["failed_before"]) == (True, False)
    assert f"WARN: retro/failed/{first['plan_sha']}.json" in err


def test_a_failed_record_for_another_plan_changes_nothing(tmp_path, capsys):
    root, state = _two_issue_state(tmp_path)
    _failed_record(state.root, "e" * 64)
    printed, _, err = _plan_cli(tmp_path, capsys, root, state)
    assert (printed["changed"], printed["failed_before"]) == (True, False)
    assert "retro/failed" not in err  # a missing record is no warning
    assert ladder.failed_before(state.root, "not-a-sha") is False


# --- apply --verify-failed: demote the checks, drop test: patterns ----------------------------------

K_UP = b.edge_key("src/domain", "src/b")  # a pattern offered as a check again
K_OLD = b.edge_key("src/domain", "src/a")  # a check whose fixture no longer fires
GUARD_KEY = "guarded:tests:modify"
TEST_KEY = "test:tests/a.test.ts"
VF_AREAS = ("src/domain", "src/db", "src/http", "src/a", "src/b", "tests")


def _commit(root: Path, env) -> None:
    for args in (
        ("init", "--quiet"),
        ("config", "core.autocrlf", "false"),
        ("config", "commit.gpgsign", "false"),
        ("add", "-A"),
        ("commit", "--quiet", "-m", "init"),
    ):
        b.git(root, *args, env=env)


def _verify_failed_world(tmp_path, env=None):
    """One plan with every kind of move: two checks (from note and from
    pattern), a guarded: and a test: pattern, and a check retirement."""
    up = b.lesson(K_UP, "pattern", since="2026-09-02")
    old = b.lesson(K_OLD, "check", since="2026-09-05")
    rules = (b.SEED_RULE, learned_rule(K_OLD))
    root = b.make_repo(tmp_path / "repo", rules=rules, lessons=[up, old], areas=VF_AREAS)
    (root / "tests" / "a.test.ts").write_text("// test\n", encoding="utf-8")
    if env is not None:
        _commit(root, env)
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 2, 1)
    seed(state)
    for run, issue, day in (("201", 11, 0), ("202", 12, 1), ("203", 13, 4)):
        edge_attempt(state, run, issue, day, to="src/b", line="import { b } from '../b/x';")
    for run, issue in (("301", 21), ("302", 22)):
        state.observe(
            b.observation(
                run, issue, day=2, files=[TEST_FILE],
                guarded=[{"root": "tests", "op": "modify", "path": "tests/a.test.ts"}],
                failing_tests=["tests/a.test.ts"],
            )
        )  # fmt: skip
    plan = make_plan(
        tmp_path, state, lessons=[up, old], rules=rules, areas=VF_AREAS,
        replay=[{"fixture": ladder.lesson_id(K_OLD)[2:], "rule_id": ladder.lesson_id(K_OLD), "fired": False}],
    )  # fmt: skip
    moves = {(t["class_key"], t["from"], t["to"], t["reason"]) for t in plan["transitions"]}
    assert moves == {
        (DB_EDGE, "note", "check", "promote"),
        (K_UP, "pattern", "check", "promote"),
        (GUARD_KEY, "note", "pattern", "promote"),
        (TEST_KEY, "note", "pattern", "promote"),
        (K_OLD, "check", "retired", "broken"),
    }
    return plan, root, state.root


def _apply_verify_failed(tmp_path, plan, emitter, root, state_root):
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    out = tmp_path / "applied.json"
    args = ["apply", "--plan", str(plan_path), "--repo-root", str(root), "--state-dir", str(state_root)]
    if emitter is not None:
        args += ["--emitter", str(emitter)]
    rc = ladder.main([*args, "--now", str(b.epoch(10)), "--verify-failed", "--out", str(out)])
    return rc, json.loads(out.read_text(encoding="utf-8"))


def test_apply_verify_failed_runs_no_check_emit(tmp_path):
    plan, root, state_root = _verify_failed_world(tmp_path)
    emitter = _stub_by_sample(tmp_path, {})
    rc, applied = _apply_verify_failed(tmp_path, plan, emitter, root, state_root)
    assert rc == 0
    # Only the retirement reaches the emitter.
    assert _calls(emitter) == [f"retire {ladder.lesson_id(K_OLD)}"]
    assert not (root / "tests" / "fixtures" / "retro").exists()


def test_apply_verify_failed_after_a_reset_demotes_and_passes_the_guard(tmp_path, git_env, capsys):
    plan, root, state_root = _verify_failed_world(tmp_path, git_env)
    config_before = (root / ".cadence" / "cadence.yaml").read_text(encoding="utf-8")
    # As the retro-plan job: the first apply, then scripts/verify.sh fails,
    # then the repo is reset and the same plan.json is applied again.
    rc, first = _apply(tmp_path, plan, None, root, state_root)
    assert rc == 0 and first["verify_required"] is True
    b.git(root, "reset", "--quiet", "--hard", "HEAD", env=git_env)
    b.git(root, "clean", "-fdq", "--", "tests/fixtures/retro", ".cadence/lessons.yaml", "docs/PATTERNS.md", env=git_env)
    rc, applied = _apply_verify_failed(tmp_path, plan, None, root, state_root)
    assert rc == 0

    moves = {(t["class_key"], t["from"], t["to"], t["reason"]) for t in applied["transitions"]}
    assert moves == {
        (DB_EDGE, "note", "pattern", "verify-fallback"),
        (GUARD_KEY, "note", "pattern", "promote"),
        (K_OLD, "check", "retired", "broken"),
    }
    t = by_key(applied)[DB_EDGE]
    assert (t["sample"], t["emit"]) == (None, None)
    assert t["text"] == ladder.fallback_text(DB_EDGE, [1, 2])
    assert not any("alternates" in x for x in applied["transitions"])
    assert skipped(applied)[K_UP] == "verify-fallback-no-change"
    assert skipped(applied)[TEST_KEY] == "verify-failed"
    assert {(DB_EDGE, "verify-failed"), (K_UP, "verify-failed"), (TEST_KEY, "verify-failed")} <= needs(applied)
    assert (applied["applied"], applied["verify_required"], applied["plan_sha"]) == (True, False, plan["plan_sha"])
    assert ladder.Schemas(None, b.SCHEMA_DIR).errors("retro-plan.schema.json", applied, required=True) == []

    # No fixture directory, and cadence.yaml changes only by the retirement.
    assert not (root / "tests" / "fixtures" / "retro").exists()
    assert yaml.safe_load((root / ".cadence" / "cadence.yaml").read_text(encoding="utf-8")) == yaml.safe_load(
        b.cadence_yaml([b.SEED_RULE])
    )
    assert config_before != (root / ".cadence" / "cadence.yaml").read_text(encoding="utf-8")
    lessons = yaml.safe_load((root / ".cadence" / "lessons.yaml").read_text(encoding="utf-8"))["lessons"]
    assert {l["class_key"]: l["rung"] for l in lessons} == {
        DB_EDGE: "pattern",
        K_UP: "pattern",
        GUARD_KEY: "pattern",
        K_OLD: "retired",
    }
    section = ladder.split_section((root / "docs" / "PATTERNS.md").read_text(encoding="utf-8"))[1]
    assert f"**{DB_LID}** (pattern)" in section
    assert ladder.main(["guard", "--repo-root", str(root), "--worktree", "--applied", str(tmp_path / "applied.json")]) == 0

    out = tmp_path / "body.md"
    assert ladder.main(["pr-body", "--applied", str(tmp_path / "applied.json"), "--repo", "octo/app", "--out", str(out)]) == 0
    body = out.read_text(encoding="utf-8")
    assert "@" not in body and "<" not in body
    titles = [line[4:] for line in body.splitlines() if line.startswith("### ")]
    assert titles == [
        "Checks", "Patterns", "Demoted after verify failed", "Retired", "Needs a human", "Replay", "Metrics",
    ]  # fmt: skip
    demoted = body.split("### Demoted after verify failed\n\n", 1)[1].split("\n\n### ", 1)[0].splitlines()
    assert demoted == [
        f"- `{DB_LID}` `{DB_EDGE}`: scripts/verify.sh failed on the retro result with this plan's "
        "checks in place, so the check is proposed as a pattern.",
        f"- `{K_UP}`: stays a pattern because scripts/verify.sh failed on the retro result with its check in place.",
        f"- `{TEST_KEY}`: left out because scripts/verify.sh failed on the retro result.",
    ]
    assert "(note -> pattern, verify-fallback)" in body


def test_apply_verify_failed_with_nothing_left_exits_1(tmp_path, capsys):
    state = b.StateDir(tmp_path / "state")
    edge_attempt(state, "101", 1, 0)
    edge_attempt(state, "102", 2, 1)
    edge_attempt(state, "103", 3, 4)
    seed(state)
    pattern = b.lesson(DB_EDGE, "pattern", since="2026-09-02")
    plan = make_plan(tmp_path, state, lessons=[pattern])
    assert [(t["from"], t["to"]) for t in plan["transitions"]] == [("pattern", "check")]
    root = tmp_path / "repo"
    (root / ".cadence" / "lessons.yaml").write_text(ladder.render_lessons([pattern]), encoding="utf-8")
    lessons_before = (root / ".cadence" / "lessons.yaml").read_bytes()
    emitter = _stub_by_sample(tmp_path, {})
    rc, applied = _apply_verify_failed(tmp_path, plan, emitter, root, state.root)
    assert rc == 1
    assert _calls(emitter) == []
    assert applied["transitions"] == []
    assert {"class_key": DB_EDGE, "why": "verify-fallback-no-change"} in applied["skipped"]
    assert (DB_EDGE, "verify-failed") in needs(applied)
    assert (root / ".cadence" / "lessons.yaml").read_bytes() == lessons_before
    out = tmp_path / "body.md"
    assert ladder.main(["pr-body", "--applied", str(tmp_path / "applied.json"), "--repo", "octo/app", "--out", str(out)]) == 0
    assert "### Demoted after verify failed" in out.read_text(encoding="utf-8")


def test_the_demoted_section_never_carries_an_at_sign():
    applied = {
        "plan_sha": "1" * 64,
        "transitions": [],
        "skipped": [
            {"class_key": "test:tests/@scope/a.test.ts", "why": "verify-failed"},
            {"class_key": "import-edge:src/@x->src/db", "why": "verify-fallback-no-change"},
            {"class_key": "guarded:tests:modify", "why": "cap"},
        ],
        "needs_human": [],
        "replay": [],
    }
    body = ladder.render_pr_body(applied, "octo/app")
    assert "@" not in body and "<" not in body
    demoted = body.split("### Demoted after verify failed\n\n", 1)[1].split("\n\n### ", 1)[0].splitlines()
    assert demoted == [
        "- `test:tests/(at)scope/a.test.ts`: left out because scripts/verify.sh failed on the retro result.",
        "- `import-edge:src/(at)x->src/db`: stays a pattern because scripts/verify.sh failed on the retro "
        "result with its check in place.",
    ]


def test_capped_lists_keep_what_apply_added():
    plan_items = [{"class_key": f"test:t{i}", "why": "cap"} for i in range(100)]
    added = [{"class_key": "test:new", "why": "verify-failed"}]
    kept = ladder.Applier._capped(plan_items + added, 100, 100)
    assert len(kept) == 100 and kept[-1] == added[0] and kept[:99] == plan_items[:99]
    assert ladder.Applier._capped(plan_items[:3] + added, 3, 100) == plan_items[:3] + added

"""End to end, for free: the whole eval pipeline on the synthetic fixture.

POSIX only, with ``sandbox: none`` and the stub agent: the synthetic demo
app has a bash-only gate and a python fake hidden harness (set through
``hidden_command``). The run covers A0, F0 and F1 with one trial and two
epochs. The stubs modify an existing test file (a guarded path) on tickets
1 and 2, ask questions once on ticket 2, and fail the gate at test on
ticket 3, then pass on the retry. So:

- F1 lands a pattern (guarded:tests:modify, seen on two distinct issues)
  before ticket 3, and F0's retro PR stays open;
- E2 carries the lessons only in F1;
- every attempt file validates against attempt.schema.json;
- metrics.py compare writes cadence.eval-compare/1, and report.md exists.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "eval" / "harness"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "eval_synthetic"

pytestmark = [
    pytest.mark.skipif(os.name == "nt", reason="POSIX only (the eval runs in WSL or Linux)"),
    pytest.mark.skipif(any(shutil.which(t) is None for t in ("git", "bash", "jq")), reason="needs git, bash and jq"),
]


def _imports():
    for path in (str(HARNESS), str(FIXTURE)):
        if path not in sys.path:
            sys.path.insert(0, path)
    import config  # noqa: F401
    import run_eval
    import world

    return run_eval, world


@pytest.fixture(scope="module")
def ran(tmp_path_factory):
    run_eval, world = _imports()
    tmp = tmp_path_factory.mktemp("eval-e2e")
    w = world.make_world(tmp)
    cfg = str(w["config"])
    assert run_eval.main(["prepare", "--config", cfg]) == 0
    assert run_eval.main(["run", "--config", cfg, "--run-id", "e2e", "--agent", "stub",
                          "--trials", "1", "--epochs", "2"]) == 0
    assert run_eval.main(["report", "--config", cfg, "--run-id", "e2e"]) == 0
    return w, w["results"] / "e2e"


def _attempt(results: Path, chain: str, label: str) -> dict:
    return json.loads((results / "chains" / chain / label / "attempt.json").read_text(encoding="utf-8"))


def test_f1_lands_a_pattern_before_ticket_3_and_f0_keeps_it_open(ran) -> None:
    _, results = ran
    f1 = _attempt(results, "F1-t1-demo", "e1-demo-2")
    assert f1["learn"]["changed"] is True and f1["learn"]["retro_merged"] is True
    assert any(t["to"] == "pattern" and t["class_key"] == "guarded:tests:modify" for t in f1["learn"]["transitions"])
    third = _attempt(results, "F1-t1-demo", "e1-demo-3")
    assert third["retro_paths"]["before"][".cadence/lessons.yaml"] is not None
    f0 = _attempt(results, "F0-t1-demo", "e1-demo-2")
    assert f0["learn"]["changed"] is True and f0["learn"]["retro_merged"] is False
    assert _attempt(results, "F0-t1-demo", "e1-demo-3")["retro_paths"]["before"][".cadence/lessons.yaml"] is None
    pulls = [json.loads(p.read_text(encoding="utf-8"))
             for p in (results / "chains" / "F0-t1-demo" / "ghstore" / "pulls").glob("*.json")]
    retro = [p for p in pulls if p["head"]["ref"] == "cadence/retro"]
    assert len(retro) == 1 and retro[0]["state"] == "open"
    for chain in ("F0-t1-demo", "F1-t1-demo"):
        for label in ("e1-demo-1", "e1-demo-2", "e1-demo-3", "e2-demo-1", "e2-demo-2", "e2-demo-3"):
            assert _attempt(results, chain, label)["flags"]["invariant"] is False


def test_e2_carries_the_lessons_only_in_f1(ran) -> None:
    _, results = ran
    on = _attempt(results, "F1-t1-demo", "e2-demo-1")["retro_paths"]["before"]
    frozen = _attempt(results, "F0-t1-demo", "e2-demo-1")["retro_paths"]["before"]
    assert on[".cadence/lessons.yaml"] is not None
    assert frozen[".cadence/lessons.yaml"] is None
    assert on["docs/PATTERNS.md"] != frozen["docs/PATTERNS.md"]


def test_the_pipeline_ran_every_branch(ran) -> None:
    _, results = ran
    second = _attempt(results, "F1-t1-demo", "e1-demo-2")
    assert second["intake"]["reran"] is True and second["intake"]["outcome"] == "approved"
    third = _attempt(results, "F1-t1-demo", "e1-demo-3")
    assert [t["verdict"] for t in third["tries"]] == ["fail", "pass"]
    assert third["tries"][0]["step_word"] == "test"
    assert third["retry"] == {"eligible": True, "granted": True, "why": "granted"}
    assert third["publish"]["try"] == 2 and third["publish"]["cadence_verify"] == "success"
    first = _attempt(results, "F1-t1-demo", "e1-demo-1")
    assert first["publish"]["cadence_verify"] == "action_required"  # the guarded edit was restored
    assert first["hidden"]["merged"]["status"] == "ok" and first["hidden"]["ticket_pass"] is True
    assert first["ledger"]["run_ids"] == ["e1k1spec", "e1k1b"]
    ap = json.loads((results / "autopilot" / "A0-t1-demo" / "attempt.json").read_text(encoding="utf-8"))
    assert ap["hidden"]["merged"]["status"] == "ok" and ap["hidden"]["merged"]["pass"] == 3


def test_the_attempt_files_validate(ran) -> None:
    from config import schema_errors

    _, results = ran
    files = list(results.glob("chains/*/e*-*/attempt.json")) + list(results.glob("autopilot/*/attempt.json"))
    assert len(files) == 13
    for path in files:
        attempt = json.loads(path.read_text(encoding="utf-8"))
        assert schema_errors("attempt", attempt) == [], path
        if attempt["arm"] != "A0" and attempt["outcome"] != "infra-failed":
            # Every factory attempt counts in pass@1: a ticket that did not merge did not pass.
            assert isinstance(attempt["hidden"]["ticket_pass"], bool), path
            if not attempt["review"]["merged"]:
                assert attempt["hidden"]["ticket_pass"] is False, path
    run = json.loads((results / "run.json").read_text(encoding="utf-8"))
    assert schema_errors("run", run) == [] and run["status"] == "done" and run["agent_mode"] == "stub"


def test_resume_after_a_crash_replays_the_paid_sessions(tmp_path_factory, monkeypatch) -> None:
    """A run that dies mid-ticket resumes from the ticket's snapshot, and the
    sessions it already ran are replayed from the cache, never run twice."""
    run_eval, world = _imports()
    import learn

    w = world.make_world(tmp_path_factory.mktemp("eval-resume"))
    cfg = str(w["config"])
    assert run_eval.main(["prepare", "--config", cfg]) == 0
    real = learn.run_chain
    crashed = []

    def crash_once(t) -> None:
        if t.key == "e1-demo-2" and not crashed:
            crashed.append(t.key)
            raise RuntimeError("the runner died here")
        real(t)

    monkeypatch.setattr(learn, "run_chain", crash_once)
    base = ["run", "--config", cfg, "--run-id", "r", "--agent", "stub", "--trials", "1", "--epochs", "1",
            "--arms", "F1"]
    assert run_eval.main(base) == 1 and crashed == ["e1-demo-2"]
    results = w["results"] / "r"
    first = (results / "chains" / "F1-t1-demo" / "e1-demo-1" / "attempt.json").read_bytes()
    assert not (results / "chains" / "F1-t1-demo" / "e1-demo-2" / ".done").exists()
    assert run_eval.main(base) == 2  # an existing run needs --resume
    assert run_eval.main([*base, "--resume"]) == 0
    assert (results / "chains" / "F1-t1-demo" / "e1-demo-1" / "attempt.json").read_bytes() == first
    second = _attempt(results, "F1-t1-demo", "e1-demo-2")
    assert second["sessions"] and all("cached" in s["flags"] for s in second["sessions"])
    assert second["publish"]["pr"] == 101 and second["learn"]["retro_merged"] is True
    third = _attempt(results, "F1-t1-demo", "e1-demo-3")
    assert all("cached" not in s["flags"] for s in third["sessions"])
    assert third["flags"]["invariant"] is False
    assert json.loads((results / "run.json").read_text(encoding="utf-8"))["status"] == "done"


def test_compare_and_the_report(ran) -> None:
    _, results = ran
    compare = json.loads((results / "report" / "compare-e1e2.json").read_text(encoding="utf-8"))
    assert compare["schema"] == "cadence.eval-compare/1"
    assert (results / "report" / "compare-e1.json").is_file()
    report = (results / "report" / "report.md").read_text(encoding="utf-8")
    assert report.startswith("# PRIVATE")
    assert "Kill criteria" in report
    summary = json.loads((results / "report" / "summary.json").read_text(encoding="utf-8"))
    assert summary["learned_check_catches"]["label"] == "0 by construction"
    assert summary["merge_rate_30d"] == "not measured"
    assert summary["q1"]["by_repo"] == {"demo": pytest.approx(0.2)}

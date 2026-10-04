"""The command line, and the gate in front of every live session.

A live session starts only with --agent live --confirm-live, a passing
doctor --live (which needs the key file), a preregistration that matches
the current hashes, and --budget-usd; otherwise run exits 2 before any
session. A stub run never reads the key file, and its sandboxes see a
`claude` that exits 99.
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
for p in (str(HARNESS), str(FIXTURE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import agent as ag  # noqa: E402
import run_eval  # noqa: E402
import sandbox as sb  # noqa: E402
import world  # noqa: E402


@pytest.fixture()
def live_world(tmp_path: Path, monkeypatch) -> dict:
    w = world.make_world(tmp_path, sandbox="bwrap")

    def no_session(**kw):
        raise AssertionError("a live session started")

    monkeypatch.setattr(ag, "run_live", no_session)
    return w


def _run(w: dict, *extra: str) -> int:
    return run_eval.main(["run", "--config", str(w["config"]), "--run-id", "live1", "--agent", "live", *extra])


def test_live_is_refused_without_confirm_live(live_world: dict, capsys) -> None:
    assert _run(live_world, "--budget-usd", "50") == 2
    assert "--confirm-live" in capsys.readouterr().err


def test_live_is_refused_without_a_budget(live_world: dict, capsys) -> None:
    assert _run(live_world, "--confirm-live") == 2
    assert "--budget-usd" in capsys.readouterr().err


def test_live_is_refused_without_a_preregistration(live_world: dict, capsys) -> None:
    assert _run(live_world, "--confirm-live", "--budget-usd", "50") == 2
    assert "preregistration" in capsys.readouterr().err


def test_live_is_refused_when_the_preregistration_is_stale(live_world: dict, capsys) -> None:
    out = live_world["private"] / "preregistration.json"
    assert run_eval.main(["preregister", "--config", str(live_world["config"]), "--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert set(data["hashes"]) >= {"config", "tickets", "pieces", "harness", "cadence_sha", "model"}
    ticket = live_world["private"] / "tickets" / "demo-1.md"
    ticket.write_text(ticket.read_text(encoding="utf-8") + "One more line.\n", encoding="utf-8")
    assert _run(live_world, "--confirm-live", "--budget-usd", "50") == 2
    assert "tickets" in capsys.readouterr().err


def test_live_is_refused_without_the_key(live_world: dict, capsys) -> None:
    out = live_world["private"] / "preregistration.json"
    assert run_eval.main(["preregister", "--config", str(live_world["config"]), "--out", str(out)]) == 0
    assert not (live_world["home"] / "secrets" / "anthropic.key").exists()
    assert _run(live_world, "--confirm-live", "--budget-usd", "50") == 2
    err = capsys.readouterr().err
    assert "doctor --live fails" in err and "key_file" in err


def test_live_is_refused_on_a_stub_run_id(live_world: dict, capsys) -> None:
    run_json = live_world["results"] / "live1" / "run.json"
    run_json.parent.mkdir(parents=True)
    run_json.write_text(json.dumps({"agent_mode": "stub"}), encoding="utf-8")
    assert _run(live_world, "--confirm-live", "--budget-usd", "50") == 2
    assert "stub run" in capsys.readouterr().err


def test_live_needs_bwrap(tmp_path: Path, capsys) -> None:
    w = world.make_world(tmp_path, sandbox="none")
    assert _run(w, "--confirm-live", "--budget-usd", "50") == 2
    assert "bwrap" in capsys.readouterr().err


def test_preregister_writes_next_to_the_config_only(live_world: dict, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere.json"
    assert run_eval.main(["preregister", "--config", str(live_world["config"]), "--out", str(elsewhere)]) == 2
    assert not elsewhere.exists()


def test_a_stub_run_never_reads_the_key_file(tmp_path: Path, monkeypatch) -> None:
    w = world.make_world(tmp_path, sandbox="none")
    seen = []
    monkeypatch.setattr(run_eval, "key_file", lambda cfg: seen.append(cfg) or Path("/nonexistent"))
    # Not prepared: the stub run stops at "run prepare first", after the live gate.
    assert run_eval.main(["run", "--config", str(w["config"]), "--run-id", "s1", "--agent", "stub"]) == 2
    assert seen == []


def test_an_existing_run_needs_resume(tmp_path: Path, capsys) -> None:
    w = world.make_world(tmp_path, sandbox="none")
    run_json = w["results"] / "s1" / "run.json"
    run_json.parent.mkdir(parents=True)
    run_json.write_text(json.dumps({"agent_mode": "stub"}), encoding="utf-8")
    assert run_eval.main(["run", "--config", str(w["config"]), "--run-id", "s1", "--agent", "stub"]) == 2
    assert "--resume" in capsys.readouterr().err


@pytest.mark.skipif(os.name == "nt" or shutil.which("sh") is None, reason="POSIX only")
def test_the_stub_guard_claude_exits_99(tmp_path: Path) -> None:
    home = tmp_path / "home"
    sb.write_guard(home)
    box = sb.Box("none", "agent", home, tmp_path / "job", guard=True)
    res = box.run(["claude", "-p", "hello"], timeout=30)
    assert res.exit == 99
    ag.check_guard(box)


def test_f0_and_f1_of_a_trial_and_repo_launch_as_one_group() -> None:
    groups = run_eval.launch_groups(["A0", "F0", "F1"], [1, 2], ["a", "b"])
    assert groups[:4] == [[("A0", 1, "a")], [("F0", 1, "a"), ("F1", 1, "a")],
                          [("A0", 1, "b")], [("F0", 1, "b"), ("F1", 1, "b")]]
    assert len(groups) == 8
    assert run_eval.launch_groups(["F1", "F0"], [1], ["a"]) == [[("F0", 1, "a"), ("F1", 1, "a")]]
    assert run_eval.launch_groups(["A0"], [3], ["a"]) == [[("A0", 3, "a")]]


@pytest.mark.skipif(os.name == "nt" or shutil.which("sh") is None, reason="POSIX only")
def test_a_command_leaves_no_process_behind(tmp_path: Path) -> None:
    """A leftover child (a server a harness only SIGTERMed through its shell)
    dies with the command, so it cannot answer a later score."""
    marker = tmp_path / "alive"
    script = f"(sleep 2; touch {marker}) >/dev/null 2>&1 &\nexit 0\n"
    res = sb.spawn(["sh", "-c", script], env={"PATH": "/usr/bin:/bin"}, timeout=30)
    assert res.ok
    import time

    time.sleep(3)
    assert not marker.exists()


def test_privacy_check_cli(tmp_path: Path, capsys) -> None:
    deny = tmp_path / "deny.txt"
    deny.write_text("acme" + "corp\n", encoding="utf-8")
    clean = tmp_path / "clean.md"
    clean.write_text("nothing to see\n", encoding="utf-8")
    dirty = tmp_path / "dirty.md"
    dirty.write_text("the " + "acme" + "corp repo\n", encoding="utf-8")
    base = ["privacy-check", "--denylist", str(deny), "--repo", str(tmp_path)]
    assert run_eval.main([*base, "--paths", "clean.md"]) == 0
    assert run_eval.main([*base, "--paths", "dirty.md"]) == 1
    out = capsys.readouterr().out
    assert "dirty.md:1: denylist[0]" in out and "acme" + "corp" not in out

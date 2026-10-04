"""Sandboxes: what each profile may see, and what never leaks.

- the bwrap argv never binds /mnt, /init, /run, a home dir or the results;
  a bind of /mnt is refused before anything starts;
- tools and score run without a network (score may be switched online);
- the API key reaches a process only through its environment, never argv;
- no subprocess inherits the runner's environment;
- a stub sandbox's PATH starts with a guard whose `claude` exits 99;
- the live bwrap probes skip when bwrap is missing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval" / "harness"))

import sandbox as sb  # noqa: E402
from config import EvalError  # noqa: E402


def _box(tmp: Path, profile: str, mode: str = "bwrap", **kw) -> sb.Box:
    return sb.Box(mode, profile, tmp / "home", tmp / "jobs" / profile, **kw)


@pytest.mark.parametrize("profile", sb.PROFILES)
def test_the_argv_never_binds_private_places(tmp_path: Path, profile: str) -> None:
    box = _box(tmp_path, profile, binds=[sb.Bind(tmp_path / "tools", sb.OPT_TOOLS)])
    argv = box.bwrap_argv()
    joined = " ".join(argv)
    for forbidden in ("/mnt", "/init", "/run", str(Path.home()), str(tmp_path / "results")):
        assert not any(a == forbidden or a.startswith(forbidden + "/") for a in argv), forbidden
    assert "--die-with-parent" in argv and "--unshare-user" in argv and "--unshare-pid" in argv
    assert argv[argv.index("--bind") + 2] == sb.RUNNER_HOME
    assert ("--unshare-net" in argv) == (profile in ("tools", "score"))
    assert "--tmpfs /tmp" in joined


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlinks")
def test_a_symlinked_resolv_conf_is_replaced_without_binding_onto_it(tmp_path: Path) -> None:
    """WSL's /etc/resolv.conf points into /mnt/wsl, which does not exist in a
    sandbox, so a bind onto it would fail: /etc is bound entry by entry."""
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "hosts").write_text("127.0.0.1 localhost\n", encoding="utf-8")
    (etc / "ssl").mkdir()
    (etc / "resolv.conf").symlink_to("/mnt/" + "wsl/resolv.conf")
    (etc / "elsewhere").symlink_to("/run/x")
    (etc / "mtab").symlink_to("../proc/self/mounts")
    box = _box(tmp_path, "agent")
    copy = tmp_path / "home" / "etc" / "resolv.conf"
    copy.parent.mkdir(parents=True)
    copy.write_text("nameserver 10.0.0.1\n", encoding="utf-8")
    args = box.etc_args(True, etc)
    assert not any(a.startswith(("/mnt", "/run")) for a in args)
    assert args[:2] == ["--dir", "/etc"] and args[-3:] == ["--ro-bind", str(copy), "/etc/resolv.conf"]
    assert ["--ro-bind", str(etc / "hosts"), "/etc/hosts"] == args[2:5]
    assert "--symlink" in args and "/etc/mtab" in args and "/etc/elsewhere" not in args
    assert box.etc_args(False, etc) == ["--ro-bind", etc.as_posix(), "/etc"]
    (etc / "resolv.conf").unlink()
    (etc / "resolv.conf").write_text("nameserver 10.0.0.2\n", encoding="utf-8")
    assert box.etc_args(True, etc) == ["--ro-bind", etc.as_posix(), "/etc", "--ro-bind", str(copy), "/etc/resolv.conf"]


def test_score_can_be_switched_online(tmp_path: Path) -> None:
    box = _box(tmp_path, "score")
    box.net = True
    assert "--unshare-net" not in box.bwrap_argv()


@pytest.mark.parametrize("src", ["/mnt/c/" + "Users/someone/work", "/mnt", "/run/WSL", "/init"])
def test_a_bind_of_mnt_or_run_is_refused_before_anything_starts(tmp_path: Path, src: str, monkeypatch) -> None:
    started = []
    monkeypatch.setattr(sb, "spawn", lambda *a, **k: started.append(a))
    box = _box(tmp_path, "agent", binds=[sb.Bind(Path(src), "/opt/x")])
    with pytest.raises(EvalError, match="never be bound"):
        box.run(["true"])
    assert started == []


@pytest.mark.parametrize("mode", ["bwrap", "none"])
def test_the_key_reaches_the_env_never_argv(tmp_path: Path, mode: str, monkeypatch) -> None:
    seen = {}

    def fake(command, *, env, **kw):
        seen["argv"], seen["env"] = list(command), dict(env)
        return sb.Result(0, False, b"", b"", 0.0)

    monkeypatch.setattr(sb, "spawn", fake)
    monkeypatch.setenv("CADENCE_EVAL_SENTINEL", "leak")
    secret = "test-value-" + "0" * 20
    box = _box(tmp_path, "agent", mode, guard=False)
    box.secret_env = {"ANTHROPIC_API_KEY": secret, "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1"}
    box.run(["claude", "-p", "hello"])
    assert all(secret not in a for a in seen["argv"])
    assert seen["env"]["ANTHROPIC_API_KEY"] == secret
    assert "CADENCE_EVAL_SENTINEL" not in seen["env"]  # nothing inherited
    assert seen["env"]["HOME"] in (sb.RUNNER_HOME, (tmp_path / "jobs" / "agent" / "home").as_posix())


STREAM = (b'{"type":"system","subtype":"init"}\n{"type":"assistant","message":{}}\n'
          b'{"type":"result","subtype":"success","is_error":false,"total_cost_usd":0.5,"num_turns":3,"duration_ms":9}\n')


def _fake_spawn(calls: list, stream: bytes = STREAM, exit_code: int = 0, stderr: bytes = b""):
    def fake(command, *, env, stdout_path=None, **kw):
        calls.append((list(command), dict(env)))
        if stdout_path is not None:
            Path(stdout_path).write_bytes(stream)
        return sb.Result(exit_code, False, b"", stderr, 0.0)

    return fake


def test_the_key_reaches_only_the_model_session(tmp_path: Path, monkeypatch) -> None:
    import agent as ag

    calls: list = []
    monkeypatch.setattr(sb, "spawn", _fake_spawn(calls))
    box = _box(tmp_path, "agent", "none", guard=False)
    budget = ag.Budget(100)
    secret = "test-value-" + "1" * 20
    session = ag.run_live(role="build", run_id="e1k1b", box=box, cwd=tmp_path, prompt="p", args=["--max-turns", "5"],
                          model="m", timeout_s=60, exec_file=tmp_path / "exec.json", stream_file=tmp_path / "s.jsonl",
                          plugin=False, cap=5.0, budget=budget, log=lambda m: None, key=secret)
    argv, env = calls[-1]
    assert argv[:2] == ["claude", "-p"] and secret not in " ".join(argv)
    assert env["ANTHROPIC_API_KEY"] == secret and env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] == "1"
    assert session.succeeded and session.booked_usd == 0.5 and session.cost_source == "reported"
    assert budget.booked == 0.5 and budget.in_flight == 0
    box.run(["git", "diff"])  # a later step of the same job
    assert "ANTHROPIC_API_KEY" not in calls[-1][1] and box.secret_env == {}


def test_a_network_failure_before_the_first_turn_is_a_void(tmp_path: Path, monkeypatch) -> None:
    import agent as ag

    calls: list = []
    stream = b'{"type":"result","subtype":"error_during_execution","is_error":true,"total_cost_usd":0}\n'
    monkeypatch.setattr(sb, "spawn", _fake_spawn(calls, stream, 1, b"API Error: 529 overloaded"))
    box = _box(tmp_path, "agent", "none", guard=False)
    budget = ag.Budget(100)
    session = ag.run_live(role="build", run_id="r", box=box, cwd=tmp_path, prompt="p", args=[], model="m",
                          timeout_s=60, exec_file=tmp_path / "e.json", stream_file=tmp_path / "s.jsonl", plugin=False,
                          cap=5.0, budget=budget, log=lambda m: None, key="k")
    assert len(calls) == 3 and session.infra_failed and session.voids == ["network-before-first-turn"] * 3
    assert budget.booked == 0  # voids are never booked
    _, result, assistant = ag.parse_stream(STREAM)
    assert assistant and ag.is_void(sb.Result(1, False, b"", b"529 overloaded", 0), result, assistant) is None


def test_the_budget_stops_before_a_session_that_would_not_fit(tmp_path: Path) -> None:
    import agent as ag

    budget = ag.Budget(12, booked=2.0)
    budget.reserve(5)
    with pytest.raises(ag.BudgetStop):
        budget.reserve(5.01)  # 2 booked + 5 in flight + 5.01 > 12
    assert budget.stopped
    with pytest.raises(ag.BudgetStop):
        budget.reserve(0.01)  # nothing new once stopped
    budget.settle(5, 1.25)
    assert budget.booked == 3.25 and budget.in_flight == 0


def test_the_stub_path_starts_with_the_guard(tmp_path: Path) -> None:
    box = _box(tmp_path, "gate", "bwrap", guard=True)
    assert box.base_env()["PATH"].split(":")[0] == sb.OPT_GUARD
    assert "--ro-bind" in box.bwrap_argv() and sb.OPT_GUARD in box.bwrap_argv()
    none = _box(tmp_path, "gate", "none", guard=True)
    assert none.base_env()["PATH"].startswith((tmp_path / "home" / "guard").as_posix() + ":")
    live = _box(tmp_path, "agent", "bwrap", guard=False)
    assert live.base_env()["PATH"].split(":")[0] == sb.OPT_TOOLCHAIN + "/bin"


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs sh")
def test_the_guard_claude_exits_99(tmp_path: Path) -> None:
    guard = sb.write_guard(tmp_path)
    done = subprocess.run(["sh", str(guard / "claude"), "-p", "anything"], capture_output=True)
    assert done.returncode == 99


def test_paths_map_into_the_sandbox(tmp_path: Path) -> None:
    box = _box(tmp_path, "tools", binds=[sb.Bind(tmp_path / "tools", sb.OPT_TOOLS)])
    assert box.inside(box.work / "repo" / "repo") == "/home/runner/work/repo/repo"
    assert box.inside(box.temp) == sb.RUNNER_TEMP
    assert box.inside(tmp_path / "tools" / "tool" / "x.py") == "/opt/cadence-tools/tool/x.py"
    with pytest.raises(EvalError):
        box.inside(tmp_path / "elsewhere")
    none = _box(tmp_path, "tools", "none")
    assert none.inside(tmp_path / "x") == (tmp_path / "x").as_posix()
    assert none.base_env()["RUNNER_TEMP"] == none.temp.as_posix()


def test_tools_turn_git_hooks_off(tmp_path: Path) -> None:
    env = _box(tmp_path, "tools").base_env()
    assert env["GIT_CONFIG_KEY_0"] == "core.hooksPath" and env["GIT_CONFIG_VALUE_0"] == "/dev/null"
    assert "GIT_CONFIG_COUNT" not in _box(tmp_path, "agent").base_env()


@pytest.mark.skipif(os.name == "nt" or shutil.which("bwrap") is None, reason="needs bwrap (Linux)")
def test_live_bwrap_probe_hides_mnt_and_blocks_egress(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "toolchain" / "bin").mkdir(parents=True)
    (home / "venv" / "bin").mkdir(parents=True)
    box = sb.Box("bwrap", "score", home, tmp_path / "job")
    probe = ("import os, socket\nprint(os.path.exists('/mnt'))\n"
             "try:\n    socket.create_connection(('1.1.1.1', 443), timeout=3); print('egress')\n"
             "except OSError:\n    print('blocked')\n")
    res = box.run(["/usr/bin/python3", "-c", probe], timeout=60)
    if res.exit != 0 and b"bwrap" in res.stderr:
        pytest.skip("bwrap cannot create namespaces here: " + res.stderr.decode(errors="replace")[:200])
    assert res.text().split() == ["False", "blocked"]

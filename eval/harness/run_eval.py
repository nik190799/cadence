#!/usr/bin/env python3
"""The Cadence factory eval: one command per stage.

    doctor        --config C [--live]
    prepare       --config C [--write-expectations]
    preregister   --config C --out <private>/preregistration.json
    run           --config C --run-id R --agent stub|live [--confirm-live]
                  [--arms A0,F0,F1] [--trials 1-3] [--epochs 1|2] [--repos ids]
                  [--budget-usd N] [--sessions N] [--score-workers N] [--resume]
    score         --config C --run-id R [--rescore]
    report        --config C --run-id R
    privacy-check --denylist F [--canaries F] [--base REF] [--paths ...]
    clean         --config C --run-id R [--work-only]

Run it in WSL as ``<home>/venv/bin/python <worktree>/eval/harness/run_eval.py``.
A live session starts only when all of these hold, else the command exits 2
before any session: ``--agent live --confirm-live``; ``doctor --live``
passes; the preregistration matches the current hashes; ``--budget-usd``
is given. Stub runs never read the key file.

Exit codes: 0 ok, 1 a check failed, 2 bad input or refused, 3 stopped on
budget.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import re
import stat
import sys
import traceback
from collections import deque
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent as ag  # noqa: E402
import privacy  # noqa: E402
import sandbox as sb  # noqa: E402
import score as scoring  # noqa: E402
import seed as seedm  # noqa: E402
import workflow as wfm  # noqa: E402
from config import (  # noqa: E402
    DECISION_RULE_VERSION, EXIT_BAD, EXIT_BUDGET, EXIT_CHECK, EXIT_OK, Config, EvalError, load_config,
    load_private, private_hashes, read_json, require_valid, sha256_json, wall_now, write_json,
)

ARMS = ("A0", "F0", "F1")


def log(message: str) -> None:
    print(message, flush=True)


# --- doctor -----------------------------------------------------------------------------------------

PROBE = r"""
import json, os, shutil, socket, subprocess, sys
mode = sys.argv[1]
out = {}
if mode == "agent":
    out["no_mnt"] = not os.path.exists("/mnt")
    out["no_init"] = not os.path.exists("/init")
    out["no_run_wsl"] = not os.path.exists("/run/WSL")
    hidden = []
    for p in sys.argv[2:]:
        try:
            os.listdir(p)
            hidden.append(False)
        except OSError:
            hidden.append(True)
    out["private_unreadable"] = all(hidden)
    try:
        r = subprocess.run(["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "true"],
                           capture_output=True, timeout=60)
        out["nested_bwrap"] = r.returncode == 0
    except Exception:
        out["nested_bwrap"] = False
    for name in ("node", "claude"):
        found = shutil.which(name)
        out[name + "_under_opt"] = bool(found) and os.path.realpath(found).startswith("/opt/")
    try:
        socket.getaddrinfo("registry.npmjs.org", 443)
        out["dns"] = True
    except OSError:
        out["dns"] = False
if mode == "score":
    try:
        s = socket.create_connection(("1.1.1.1", 443), timeout=5)
        s.close()
        out["egress_blocked"] = False
    except OSError:
        out["egress_blocked"] = True
    for fam, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        key = "loopback_" + ("v4" if fam == socket.AF_INET else "v6")
        try:
            srv = socket.socket(fam, socket.SOCK_STREAM)
            srv.bind((host, 0))
            srv.listen(1)
            port = srv.getsockname()[1]
            cli = socket.create_connection((host, port), timeout=5)
            conn, _ = srv.accept()
            cli.close(); conn.close(); srv.close()
            out[key] = True
        except OSError:
            out[key] = False
print(json.dumps(out))
"""


def _version(argv: list[str], env_path: str) -> str | None:
    res = sb.spawn(argv, env={"PATH": env_path, "HOME": "/nonexistent", "LANG": "C.UTF-8"}, timeout=60)
    if not res.ok:
        return None
    return (res.stdout or res.stderr).decode("utf-8", "replace").strip().splitlines()[0][:100] if (res.stdout or res.stderr) else ""


def key_file(cfg: Config) -> Path:
    return cfg.home / "secrets" / "anthropic.key"


def key_accepted(path: Path) -> dict[str, Any]:
    """One free authenticated call (list one model): the key's format and the
    HTTP status, never the key. A run on a rejected key would book every
    ticket as a model failure, since a 401 is not a void."""
    import urllib.error
    import urllib.request

    try:
        key = path.read_text(encoding="utf-8").strip()
    except OSError:
        return {"prefix_ok": False, "status": None, "ok": False}
    out: dict[str, Any] = {"prefix_ok": key.startswith(privacy._KEY_PREFIX), "status": None}
    req = urllib.request.Request("https://api.anthropic.com/v1/models?limit=1",
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            out["status"] = resp.status
    except urllib.error.HTTPError as err:
        out["status"] = err.code
    except OSError as err:
        out["error"] = type(err).__name__
    out["ok"] = out["prefix_ok"] and out["status"] == 200
    return out


def doctor(cfg: Config, live: bool) -> tuple[bool, dict[str, Any]]:
    report: dict[str, Any] = {"sandbox": cfg.sandbox, "checks": {}}
    checks = report["checks"]
    path_dirs = [cfg.home / "toolchain" / "bin", cfg.home / "venv" / "bin", Path("/usr/bin"), Path("/bin")]
    env_path = ":".join(str(p) for p in path_dirs)
    for name in ("git", "bash", "jq", "python3", "node", "npm", "claude", "bwrap"):
        found = sb.which_in(path_dirs, name)
        real = os.path.realpath(found) if found else None
        checks[f"bin:{name}"] = {"found": bool(found), "under_mnt": bool(real and real.startswith("/mnt/"))}
    bad = [n for n, c in checks.items() if c.get("under_mnt")]
    ok = not bad
    for name in ("git", "bash", "jq", "python3"):
        ok = ok and checks[f"bin:{name}"]["found"]
    venv_py = cfg.home / "venv" / "bin" / "python3"
    deps = sb.spawn([str(venv_py), "-c", "import yaml, jsonschema, importlib.metadata as m; print(m.version('jsonschema'))"],
                    env={"PATH": env_path, "LANG": "C.UTF-8"}, timeout=60) if venv_py.exists() else None
    checks["venv"] = {"found": venv_py.exists(), "jsonschema": deps.text().strip() if deps and deps.ok else None}
    checks["toolchain_dir"] = (cfg.home / "toolchain" / "bin").is_dir()
    if cfg.sandbox == "bwrap":
        ok = ok and checks["bin:bwrap"]["found"] and checks["venv"]["found"] and checks["toolchain_dir"]
    if live or cfg.sandbox == "bwrap":
        ok = ok and checks["bin:node"]["found"]
    if live:
        claude = sb.which_in(path_dirs, "claude")
        version = _version([str(claude), "--version"], env_path) if claude else None
        checks["claude_version"] = {"found": version, "want": cfg.claude_code_version,
                                    "ok": bool(version and cfg.claude_code_version in version)}
        checks["model"] = {"ok": bool(cfg.model)}
        kf = key_file(cfg)
        mode_ok = kf.is_file() and not (kf.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO))
        checks["key_file"] = {"present": kf.is_file(), "private_mode": mode_ok}
        checks["key_accepted"] = key_accepted(kf) if mode_ok else {"ok": False}
        ok = ok and checks["claude_version"]["ok"] and checks["model"]["ok"] and mode_ok
        ok = ok and checks["key_accepted"]["ok"]
        ok = ok and cfg.sandbox == "bwrap"
    if cfg.sandbox == "bwrap" and checks["bin:bwrap"]["found"]:
        sb.write_guard(cfg.home)
        # The agent profile binds this copy over /etc/resolv.conf; without it the
        # DNS probe fails on WSL until prepare has run once.
        sb.copy_resolv(cfg.home)
        root = cfg.home / "work" / "_doctor"
        sb.rmtree(root)
        private_paths = [str(Path.home()), str(cfg.results_dir), str(cfg.dir)]
        agent_box = sb.Box("bwrap", "agent", cfg.home, root / "agent", guard=False)
        res = agent_box.run(["python3", "-c", PROBE, "agent", *private_paths], timeout=120)
        probe = json.loads(res.text() or "{}") if res.ok else {"error": res.stderr.decode(errors="replace")[-300:]}
        checks["probe_agent"] = probe
        need = ["no_mnt", "no_init", "no_run_wsl", "private_unreadable", "dns"]
        need += ["node_under_opt", "claude_under_opt"] if live else []
        ok = ok and all(probe.get(k) for k in need)
        checks["nested_bwrap"] = bool(probe.get("nested_bwrap"))
        if live and not probe.get("nested_bwrap") and not cfg.allow_no_subprocess_scrub:
            ok = False
        score_box = sb.Box("bwrap", "score", cfg.home, root / "score")
        res = score_box.run(["python3", "-c", PROBE, "score"], timeout=120)
        probe = json.loads(res.text() or "{}") if res.ok else {"error": res.stderr.decode(errors="replace")[-300:]}
        checks["probe_score"] = probe
        ok = ok and all(probe.get(k) for k in ("egress_blocked", "loopback_v4", "loopback_v6"))
        sb.rmtree(root)
    report["ok"] = bool(ok)
    return bool(ok), report


# --- preregistration -----------------------------------------------------------------------------------


def current_hashes(cfg: Config) -> dict[str, str]:
    private = load_private(cfg)
    wf = wfm.load(cfg.cadence_repo, cfg.cadence_sha)
    hashes = private_hashes(cfg, private)
    hashes.update({
        "pieces": sha256_json(wf.shas()),
        "harness": sha256_json(seedm.harness_hashes()),
        "cadence_sha": cfg.cadence_sha,
        "model": cfg.model or "unset",
        "claude_code_version": cfg.claude_code_version,
    })
    return hashes


def preregistration_path(cfg: Config) -> Path:
    return cfg.dir / "preregistration.json"


def preregistration_ok(cfg: Config) -> tuple[bool, str]:
    path = preregistration_path(cfg)
    if not path.is_file():
        return False, f"no {path.name} next to the config"
    data = read_json(path)
    try:
        require_valid("preregistration", data, str(path))
    except EvalError as exc:
        return False, str(exc)
    if data["decision_rule_version"] != DECISION_RULE_VERSION:
        return False, "the decision-rule version changed"
    now = current_hashes(cfg)
    diff = sorted(k for k in set(now) | set(data["hashes"]) if now.get(k) != data["hashes"].get(k))
    return (not diff), ("ok" if not diff else "changed since preregistration: " + ", ".join(diff))


# --- run --------------------------------------------------------------------------------------------------


def _parse_trials(text: str) -> list[int]:
    m = re.fullmatch(r"([1-9])(?:-([1-9]))?", text)
    if not m:
        raise EvalError(f"--trials {text!r}: use N or N-M")
    lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
    if hi < lo:
        raise EvalError(f"--trials {text!r}: empty range")
    return list(range(lo, hi + 1))


def launch_groups(arms: list[str], trials: list[int], repo_ids: list[str]) -> list[list[tuple[str, int, str]]]:
    """The chains in launch order: per (trial, repo), A0 alone, then F0 and F1
    as one group that starts together."""
    groups: list[list[tuple[str, int, str]]] = []
    for k in trials:
        for rid in repo_ids:
            if "A0" in arms:
                groups.append([("A0", k, rid)])
            pair = [(arm, k, rid) for arm in ("F0", "F1") if arm in arms]
            if pair:
                groups.append(pair)
    return groups


def _booked_so_far(results: Path) -> float:
    total = 0.0
    for path in list(results.glob("chains/*/e*-*/attempt.json")) + list(results.glob("autopilot/*/attempt.json")):
        try:
            total += float(read_json(path)["ledger"]["booked_usd"] or 0)
        except (OSError, KeyError, ValueError):
            continue
    return total


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", args.run_id):
        raise EvalError(f"--run-id {args.run_id!r}: letters, digits, '.', '_' or '-'")
    arms = [a.strip() for a in (args.arms or ",".join(ARMS)).split(",") if a.strip()]
    if any(a not in ARMS for a in arms):
        raise EvalError(f"--arms: each of {', '.join(ARMS)}")
    trials = _parse_trials(args.trials or "1-3")
    epochs = int(args.epochs or 2)
    if epochs not in (1, 2):
        raise EvalError("--epochs 1 or 2")
    repo_ids = [r.strip() for r in (args.repos or ",".join(r.id for r in cfg.repos)).split(",") if r.strip()]
    for rid in repo_ids:
        cfg.repo(rid)
    results = cfg.results(args.run_id)
    run_json = results / "run.json"
    key: str | None = None
    nested: bool | None = None
    # The live gate: everything is checked before any session.
    if args.agent == "live":
        if not args.confirm_live:
            raise EvalError("a live run needs --confirm-live", EXIT_BAD)
        if args.budget_usd is None:
            raise EvalError("a live run needs --budget-usd", EXIT_BAD)
        if cfg.sandbox != "bwrap":
            raise EvalError("a live run needs sandbox: bwrap", EXIT_BAD)
        if run_json.is_file() and read_json(run_json).get("agent_mode") != "live":
            raise EvalError(f"run id {args.run_id} is a stub run; a live run needs its own run id", EXIT_BAD)
        ok, why = preregistration_ok(cfg)
        if not ok:
            raise EvalError(f"preregistration: {why}", EXIT_BAD)
        healthy, report = doctor(cfg, live=True)
        if not healthy:
            raise EvalError("doctor --live fails: " + json.dumps(report["checks"])[:600], EXIT_BAD)
        nested = bool(report["checks"].get("nested_bwrap"))
        key = key_file(cfg).read_text(encoding="utf-8").strip()
        if not key or re.search(r"\s", key):
            raise EvalError("the key file is empty or holds whitespace", EXIT_BAD)
    else:
        if run_json.is_file() and read_json(run_json).get("agent_mode") != "stub":
            raise EvalError(f"run id {args.run_id} is a live run", EXIT_BAD)
    if run_json.is_file() and not args.resume:
        raise EvalError(f"run {args.run_id} exists; pass --resume or pick a new run id", EXIT_BAD)
    private = load_private(cfg)
    prepared = seedm.load_prepared(cfg)
    wf = wfm.load(cfg.cadence_repo, cfg.cadence_sha)
    if prepared.data.get("pieces") != wf.shas():
        raise EvalError("the workflow pieces differ from the prepared ones: run prepare again", EXIT_BAD)
    sb.write_guard(cfg.home)
    if args.agent == "stub":
        guard_root = cfg.work(args.run_id) / "_guard"
        ag.check_guard(sb.Box(cfg.sandbox, "agent", cfg.home, guard_root, guard=True))
        sb.rmtree(guard_root)
    if args.sessions:
        cfg.sessions = int(args.sessions)
    if args.score_workers:
        cfg.score_workers = int(args.score_workers)
    limit = float(args.budget_usd if args.budget_usd is not None else cfg.budget_usd)
    budget = ag.Budget(limit, booked=_booked_so_far(results) if args.resume else 0.0)
    pre = preregistration_path(cfg)
    from config import sha256_file

    pins = {
        "schema": "cadence-eval.run/1", "run_id": args.run_id, "agent_mode": args.agent, "sandbox": cfg.sandbox,
        "created_at": wall_now(), "cadence_sha": cfg.cadence_sha, "plugin_tree": prepared.data.get("plugin_tree"),
        "pieces": wf.shas(), "claude_code_version": cfg.claude_code_version, "model": cfg.model,
        "node": _version([str(cfg.home / "toolchain" / "bin" / "node"), "--version"], "/usr/bin:/bin")
        if (cfg.home / "toolchain" / "bin" / "node").exists() else None,
        "venv": _version([str(cfg.home / "venv" / "bin" / "python3"), "--version"], "/usr/bin:/bin")
        if (cfg.home / "venv" / "bin" / "python3").exists() else None,
        "harness": seedm.harness_hashes(), "config_sha256": cfg.sha256,
        "preregistration_sha256": sha256_file(pre) if pre.is_file() else None,
        "arms": arms, "trials": trials, "epochs": epochs, "repos": repo_ids, "voids": {},
        "allow_no_subprocess_scrub": cfg.allow_no_subprocess_scrub, "nested_bwrap": nested,
        "seeds": {r: prepared.seeds[r]["seed"] for r in repo_ids}, "status": "running",
    }
    require_valid("run", pins, "run.json")
    write_json(run_json, pins)
    from chain import RunEnv, run_factory_chain
    import autopilot

    env = RunEnv(cfg, private, wf, prepared, args.run_id, args.agent, budget, key=key, resume=args.resume, log=log)
    groups = launch_groups(arms, trials, repo_ids)
    failures: list[str] = []
    stopped = False

    def one(job: tuple[str, int, str]) -> None:
        arm, k, rid = job
        if budget.stopped:
            return
        if arm == "A0":
            autopilot.run(env, k, rid, args.resume)
        else:
            run_factory_chain(env, arm, k, rid, epochs, args.resume)

    # F0 and F1 of the same (trial, repo) launch together: a group starts only
    # when it has a session slot for each of its chains, in order.
    slots = max(cfg.sessions, max((len(g) for g in groups), default=1))
    free = slots
    pending = deque(groups)
    running: dict[cf.Future, tuple[str, int, str]] = {}
    with cf.ThreadPoolExecutor(max_workers=max(1, sum(len(g) for g in groups))) as pool:
        while pending or running:
            while pending and len(pending[0]) <= free and not budget.stopped:
                group = pending.popleft()
                free -= len(group)
                for job in group:
                    running[pool.submit(one, job)] = job
            if not running:
                break  # stopped on budget: nothing new is scheduled
            done, _ = cf.wait(list(running), return_when=cf.FIRST_COMPLETED)
            for fut in done:
                job = running.pop(fut)
                free += 1
                try:
                    fut.result()
                except ag.BudgetStop as exc:
                    stopped = True
                    log(f"run: {job}: {exc}; nothing new is scheduled")
                except Exception as exc:  # noqa: BLE001 - one chain's crash must not lose the others
                    failures.append(f"{job}: {exc}")
                    log(f"run: {job} failed: {exc}")
                    traceback.print_exc()
    if pending:
        stopped = True
    log("run: scoring")
    try:
        counts = scoring.score_queue(cfg, private, args.run_id, log=log)
        log(f"run: scored {counts}")
    except EvalError as exc:
        failures.append(str(exc))
    voids: dict[str, int] = {}
    for path in list(results.glob("chains/*/e*-*/attempt.json")) + list(results.glob("autopilot/*/attempt.json")):
        a = read_json(path)
        for sess in a.get("sessions") or []:
            for reason in sess.get("voids") or []:
                key = reason.split(":")[0]
                voids[key] = voids.get(key, 0) + 1
        if a.get("outcome") == "infra-failed":
            voids["infra-failed-attempts"] = voids.get("infra-failed-attempts", 0) + 1
    pins["voids"] = voids
    pins["status"] = "budget-stopped" if stopped else ("failed" if failures else "done")
    pins["updated_at"] = wall_now()
    write_json(run_json, pins)
    if pins["status"] == "done":
        # Scored: the large work trees go; the results hold everything the report needs.
        sb.rmtree(cfg.policy.check_write(cfg.work(args.run_id)))
    if stopped:
        return EXIT_BUDGET
    return EXIT_CHECK if failures else EXIT_OK


# --- commands -------------------------------------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    ok, report = doctor(cfg, args.live)
    print(json.dumps(report, indent=2, sort_keys=True))
    return EXIT_OK if ok else EXIT_CHECK


def cmd_prepare(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    private = load_private(cfg)
    seedm.prepare(cfg, private, write_expectations=args.write_expectations, log=log)
    return EXIT_OK


def cmd_preregister(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    out = Path(args.out).resolve()
    if out.parent != cfg.dir:
        raise EvalError("--out must sit next to the config (the private folder)")
    data = {"schema": "cadence-eval.preregistration/1", "created_at": wall_now(),
            "decision_rule_version": DECISION_RULE_VERSION, "hashes": current_hashes(cfg)}
    require_valid("preregistration", data, str(out))
    write_json(out, data)
    log(f"preregister: wrote {out.name}")
    return EXIT_OK


def cmd_score(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    private = load_private(cfg)
    counts = scoring.score_queue(cfg, private, args.run_id, rescore=args.rescore, log=log)
    log(f"score: {counts}")
    return EXIT_OK


def cmd_report(args: argparse.Namespace) -> int:
    import report

    cfg = load_config(args.config)
    private = load_private(cfg)
    s = report.write_report(cfg, private, args.run_id, log=log)
    log(f"report: Q1 pass {s['q1'].get('pass')}, Q2 pass {(s['q2'].get('e1e2') or {}).get('pass')}")
    return EXIT_OK


def cmd_clean(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    work = cfg.policy.check_write(cfg.work(args.run_id))
    sb.rmtree(work)
    if not args.work_only:
        sb.rmtree(cfg.policy.check_write(cfg.results(args.run_id)))
    return EXIT_OK


def cmd_privacy(args: argparse.Namespace) -> int:
    return privacy.main_check(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("doctor")
    d.add_argument("--config", required=True)
    d.add_argument("--live", action="store_true")
    p = sub.add_parser("prepare")
    p.add_argument("--config", required=True)
    p.add_argument("--write-expectations", action="store_true")
    r = sub.add_parser("preregister")
    r.add_argument("--config", required=True)
    r.add_argument("--out", required=True)
    run = sub.add_parser("run")
    run.add_argument("--config", required=True)
    run.add_argument("--run-id", required=True)
    run.add_argument("--agent", choices=("stub", "live"), required=True)
    run.add_argument("--confirm-live", action="store_true")
    run.add_argument("--arms")
    run.add_argument("--trials")
    run.add_argument("--epochs", type=int, choices=(1, 2))
    run.add_argument("--repos")
    run.add_argument("--budget-usd", type=float)
    run.add_argument("--sessions", type=int)
    run.add_argument("--score-workers", type=int)
    run.add_argument("--resume", action="store_true")
    s = sub.add_parser("score")
    s.add_argument("--config", required=True)
    s.add_argument("--run-id", required=True)
    s.add_argument("--rescore", action="store_true")
    rep = sub.add_parser("report")
    rep.add_argument("--config", required=True)
    rep.add_argument("--run-id", required=True)
    pc = sub.add_parser("privacy-check")
    pc.add_argument("--denylist", required=True)
    pc.add_argument("--canaries")
    pc.add_argument("--base")
    pc.add_argument("--paths", nargs="*")
    pc.add_argument("--repo", help="the checkout to scan (default: the current directory)")
    c = sub.add_parser("clean")
    c.add_argument("--config", required=True)
    c.add_argument("--run-id", required=True)
    c.add_argument("--work-only", action="store_true")
    return parser


COMMANDS = {
    "doctor": cmd_doctor, "prepare": cmd_prepare, "preregister": cmd_preregister, "run": cmd_run,
    "score": cmd_score, "report": cmd_report, "privacy-check": cmd_privacy, "clean": cmd_clean,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except EvalError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return exc.code
    except ag.BudgetStop as exc:
        print(f"STOPPED: {exc}", file=sys.stderr)
        return EXIT_BUDGET
    except KeyboardInterrupt:
        return EXIT_BAD
    except Exception:  # noqa: BLE001 - every unexpected error is "bad input or refused"
        traceback.print_exc()
        return EXIT_BAD


if __name__ == "__main__":
    sys.exit(main())

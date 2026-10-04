"""Arm A0, the plain agent ("autopilot"): one session per repo and trial.

On a checkout of the pinned pristine commit (no factory files), after
``npm ci``, in the agent profile without the plugin:

    claude -p "fix the failing tests and the issues in TASK.md" --model M
      --output-format stream-json --verbose --max-turns 60n
      --max-budget-usd 15n --allowedTools "Read,Write,Edit,Glob,Grep,Bash,
      Agent,Task,Skill,TodoWrite" --disallowedTools "WebFetch,WebSearch,mcp__*"

where n is the repo's ticket count (budget parity with the factory's three
per-run caps per ticket). The diff is packaged with the workflow's own
script and booked with ``ledger.py record``; for information only it is
also gated and observed on the seed (a shadow gate), and the pinned commit
plus the patch is scored by the hidden harness. Everything is accepted.
"""

from __future__ import annotations

import shutil
from typing import Any

import agent as ag
import sandbox as sb
from chain import Chain, RunEnv
from clock import iso
from config import EvalError, write_json
from steps import JobEnv, Try, run_gate, run_observe, run_piece, runtime_setup, try_record

PROMPT = "fix the failing tests and the issues in TASK.md"
ALLOWED = "Read,Write,Edit,Glob,Grep,Bash,Agent,Task,Skill,TodoWrite"
DISALLOWED = "WebFetch,WebSearch,mcp__*"


def autopilot_args(cfg: Any, n_tickets: int) -> list[str]:
    turns = int(cfg.caps["autopilot_turns_per_ticket"]) * n_tickets
    usd = float(cfg.caps["autopilot_usd_per_ticket"]) * n_tickets
    return ["--max-turns", str(turns), "--max-budget-usd", f"{usd:g}",
            "--allowedTools", ALLOWED, "--disallowedTools", DISALLOWED]


def run(env: RunEnv, trial: int, repo_id: str, resume: bool) -> dict[str, Any]:
    chain = Chain(env, "A0", trial, repo_id)
    chain.results = env.results / "autopilot" / chain.name
    done = chain.results / ".done"
    if resume and done.is_file():
        from config import read_json

        return read_json(chain.results / "attempt.json")
    chain.setup(resume=False)
    cfg = env.cfg
    n = len(chain.tickets)
    pinned, seed = chain.info.pinned, chain.info.seed
    run_id = f"ap-t{trial}"
    now = env.clock.at(1, n, 1, "build")
    work = chain.work / "ap"
    attempt: dict[str, Any] = {
        "schema": "cadence-eval.attempt/1", "run_id": env.run_id, "arm": "A0", "trial": trial, "epoch": 1,
        "repo": repo_id, "ticket": None, "issue": None, "chain": chain.name, "base_sha": pinned,
        "agent_mode": env.agent_mode, "model": cfg.model,
        "times": {"logical_start": iso(now), "logical_end": None, "wall_start": None, "wall_end": None},
        "sessions": [], "intake": {"shape": None, "reran": False, "outcome": "not-run", "spec_sha256": None},
        "tries": [], "retry": {"eligible": False, "granted": False, "why": None},
        "publish": {"outcome": None, "try": None, "published_sha": None, "pr": None, "cadence_verify": None},
        "review": {"merged": False, "merged_sha": None},
        "ledger": {"run_ids": [], "booked_usd": 0.0, "reported_usd": 0.0},
        "learn": {"harvest": None, "plan_sha": None, "changed": False, "transitions": [], "applied": False,
                  "demoted": False, "failed": False, "retro_merged": False, "retro_sha": None},
        "retro_paths": {"before": {}, "after": {}, "changed_by": []},
        "hidden": {"merged": None, "shadow_try1": None, "shadow_try2": None, "ticket_pass": None},
        "flags": {"canary": False, "invariant": False, "tool_drift": False, "install_failed": False},
        "outcome": "autopilot",
    }
    from config import wall_now

    attempt["times"]["wall_start"] = wall_now()
    root = work / "agent"
    ws = root / "work" / chain.name_part / chain.name_part
    chain.checkout(pinned, ws)
    box = chain.box("agent", root, plugin=False)
    (box.temp / "cadence" / "output").mkdir(parents=True, exist_ok=True)
    exec_file = box.temp / "claude-execution-output.json"
    cap = float(cfg.caps["autopilot_usd_per_ticket"]) * n
    trial_rec = Try(1, run_id)
    src = env.private.stubs["autopilot"].get(repo_id, "noop")
    cache = ag.SessionCache(chain.results / "sessions", enabled=env.resume)
    key = cache.key(role="autopilot", run_id=run_id, prompt=PROMPT, args=autopilot_args(cfg, n), base=pinned,
                    model=cfg.model, mode=env.agent_mode, src=src if env.agent_mode == "stub" else None)
    hit = cache.load("autopilot", key)
    setup_ok, setup_voids = True, []
    if hit is None:
        setup_ok, setup_voids = runtime_setup(chain, root, ws, cfg.timeouts_min["autopilot"] * 60)
        if not setup_ok:
            env.log(f"[{chain.name}] runtime setup (npm ci) failed ({len(setup_voids)} network void(s))")
    if hit is not None:
        session, files = hit
        if files.get("work.patch", b"").strip() and not ag.stub_build(ws, files["work.patch"]):
            raise EvalError(f"{run_id}: the cached session's diff does not apply", 1)
        if "execution.json" in files:
            exec_file.write_bytes(files["execution.json"])
            session.execution_file = exec_file
    elif not setup_ok:
        session = ag.Session("autopilot", run_id, voids=setup_voids[:3], infra_failed=len(setup_voids) >= 3)
        session.exit = None
    elif env.agent_mode == "stub":
        patch = env.stub_patch(repo_id, src)

        def action() -> int:
            if not ag.stub_build(ws, patch):
                trial_rec.stub_flag = True
            return 0

        session = ag.run_stub(role="autopilot", run_id=run_id, cost=float(env.private.stubs["cost_usd"]),
                              exec_file=exec_file, cap=cap, budget=env.budget, action=action)
    else:
        session = ag.run_live(role="autopilot", run_id=run_id, box=box, cwd=ws, prompt=PROMPT,
                              args=autopilot_args(cfg, n), model=cfg.model,
                              timeout_s=cfg.timeouts_min["autopilot"] * 60, exec_file=exec_file,
                              stream_file=work / "autopilot.stream.jsonl", plugin=False, cap=cap,
                              budget=env.budget, log=env.log, key=env.key)
    trial_rec.session = session
    job = JobEnv(chain.repo.slug, pinned, run_id, ws)
    keep = run_piece(box, env.wf.piece("agent.keep"), job,
                     env={"EXECUTION_FILE": box.inside(session.execution_file) if session.execution_file else ""},
                     timeout=300)
    result_dir = box.temp / "cadence-result" if keep.outcome == "success" else None
    trial_rec.result_dir = result_dir
    chain.results.mkdir(parents=True, exist_ok=True)
    if session.infra_failed:
        attempt["outcome"] = "infra-failed"
        trial_rec.agent_result = "failure"
    elif not setup_ok:
        trial_rec.agent_result = "failure"
    else:
        # Everything is accepted: the working tree is taken as it stands even
        # when the session ended on its turn or budget cap or the timeout (the
        # factory, by contrast, publishes nothing from a failed agent job).
        trial_rec.agent_result = "success" if session.succeeded else ("cancelled" if session.timed_out else "failure")
        pkg = run_piece(box, env.wf.piece("agent.package"), job, env={"BASE_SHA": pinned}, timeout=600)
        out = box.temp / "cadence" / "output" / "change.patch"
        if pkg.outcome == "success" and out.is_file():
            trial_rec.patch = chain.results / "change.patch"
            shutil.copy2(out, trial_rec.patch)
        else:
            trial_rec.agent_result = "failure"
    if hit is None and setup_ok and not session.infra_failed:
        files = {"execution.json": exec_file.read_bytes()} if exec_file.is_file() else {}
        if trial_rec.patch is not None:
            files["work.patch"] = trial_rec.patch.read_bytes()
        cache.save("autopilot", key, session, files)
    if result_dir is not None and (result_dir / "claude-result.json").is_file():
        shutil.copy2(result_dir / "claude-result.json", chain.results / "claude-result.json")
    # The ledger, into the autopilot's own state.
    if not session.infra_failed:
        result = chain.results / "claude-result.json"
        chain.book(run_id=run_id, issue=1, stage="build", outcome=trial_rec.agent_result, dod="skipped",
                   result=result if result.is_file() else None, now=now, message=f"ledger: autopilot run {run_id}",
                   ledger=attempt["ledger"])
    # The shadow gate and observe on the seed (information only).
    if trial_rec.patch is not None:
        g_root = work / "verify"
        g_ws = g_root / "work" / chain.name_part / chain.name_part
        chain.checkout(seed, g_ws)
        gbox = chain.box("gate", g_root)
        warm = env.cache / "npm-warm"
        if warm.is_dir():
            shutil.copytree(warm, g_root / "home" / ".npm", symlinks=True, dirs_exist_ok=True)
        (gbox.temp / "change").mkdir(parents=True, exist_ok=True)
        shutil.copy2(trial_rec.patch, gbox.temp / "change" / "change.patch")
        trial_rec.verify, trial_rec.verify_result, trial_rec.verify_log = run_gate(
            env.wf, gbox, JobEnv(chain.repo.slug, seed, run_id, g_ws),
            timeout=cfg.timeouts_min["gate"] * 60, log_dir=chain.results / "verify-log")
    if trial_rec.agent_result != "skipped" and not session.infra_failed:
        o_root = work / "observe"
        o_ws = o_root / "work" / chain.name_part / chain.name_part
        chain.checkout(seed, o_ws / "base")
        obox = chain.box("tools", o_root)
        verify_result = ("failure" if trial_rec.verify_result == "success" and trial_rec.verify.get("verdict") != "pass"
                         else trial_rec.verify_result)
        trial_rec.observe = run_observe(env.wf, obox, JobEnv(chain.repo.slug, seed, run_id, o_ws), trial_rec,
                                        issue=1, verify_result=verify_result, spec_sha256=None, now=now)
        trial_rec.observed = bool(trial_rec.observe.get("bundle_sha256"))
        if trial_rec.observe.get("_observation"):
            (chain.results / "observation.json").write_text(trial_rec.observe["_observation"], encoding="utf-8")
    attempt["sessions"] = [session.record()]
    attempt["tries"] = [try_record(trial_rec)]
    # Score the pinned commit plus the patch.
    if trial_rec.patch is not None:
        tree = chain.tree_plus(pinned, trial_rec.patch)
    else:
        tree = chain.tree_of(pinned)
    if tree is not None and not session.infra_failed:
        attempt["hidden"]["merged"] = chain.queue_score(attempt_dir=chain.results, field_name="merged", tree=tree,
                                                        epoch_time=now)
    attempt["times"]["logical_end"] = iso(now + 3600)
    attempt["times"]["wall_end"] = wall_now()
    write_json(chain.results / "attempt.json", attempt)
    done.write_text("done\n", encoding="utf-8")
    sb.rmtree(work)
    return attempt


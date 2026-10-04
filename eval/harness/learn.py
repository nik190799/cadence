"""The learn chain after each ticket, the same code in both factory arms.

harvest (150) -> learn-record (155) -> retro-plan (160) -> retro-publish or
retro-failed (170), as the workflow runs them at the end of a build run:

- harvest: ``signals.py harvest`` with the workflow's flags, reading closed
  PRs through the read-only gh shim (the chain's ghstore) and PR heads from
  a fresh full clone;
- learn-record: put the harvest on cadence/state, plus the day's metrics
  snapshot;
- retro-plan: the workflow's own Plan, Apply, Run verify, Demote, Decide and
  Guard-and-build pieces, on a fresh checkout of main, with the open retro
  PR's plan sha;
- retro-publish: ``ladder.py guard`` again, the plan recorded on
  cadence/state (create-only), ``cadence/retro`` = main + the retro patch
  with a ``Cadence-Retro-Plan`` trailer, the PR opened or updated, and the
  switch: with ``CADENCE_EVAL_SANDBOX`` (F1 only) the PR is squash-merged,
  otherwise (F0) it stays open;
- retro-failed: the workflow's jq record of a plan that failed verify.sh
  even with its checks demoted.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, TYPE_CHECKING

import sandbox as sb
import workflow as wfm
from clock import day, git_date, iso
from config import read_json, write_json
from steps import JobEnv, SKIPPED, run_piece

if TYPE_CHECKING:  # pragma: no cover
    from steps import Ticket

BOT_LOGIN = "cadence-eval[bot]"
RETRO_BRANCH = "cadence/retro"
TRAILER = "Cadence-Retro-Plan"


def _bind_paths(t: "Ticket", box: sb.Box) -> tuple[str, str, str]:
    """(shim dir, ghstore, origin) as the tools sandbox sees them."""
    if box.mode == "bwrap":
        return "/opt/ghshim", "/srv/ghstore", "/srv/origin.git"
    return ((t.env.cache / "shim").as_posix(), t.chain.ghstore.as_posix(), t.chain.origin.as_posix())


def harvest(t: "Ticket") -> dict[str, Any] | None:
    chain = t.chain
    root, ws = t.job("harvest")
    chain.checkout(t.base, ws)
    clone = root / "work" / "clone"
    chain.full_clone(clone)
    box = chain.box("tools", root, extra=[
        sb.Bind(t.env.cache / "shim", "/opt/ghshim", ro=True),
        sb.Bind(chain.ghstore, "/srv/ghstore", ro=True),
        sb.Bind(chain.origin, "/srv/origin.git", ro=True),
    ])
    shim, store, origin = _bind_paths(t, box)
    sb.git(clone, "remote", "set-url", "origin", f"file://{origin}")
    state = box.temp / "state"
    chain.state_archive(state)
    (state / "runs").mkdir(parents=True, exist_ok=True)
    box.env["PATH"] = shim + ":" + box.base_env()["PATH"]
    box.env.update({"GH_SHIM_STORE": store, "GH_SHIM_REPO": chain.repo.slug})
    tools = box.opt(sb.OPT_TOOLS)
    out = box.temp / "harvest"
    now = t.at("harvest")
    argv = wfm.tool_argv(tools, "signals.py harvest", "harvest", "--repo", chain.repo.slug,
            "--state-dir", box.inside(state), "--clone", box.inside(clone), "--bot-login", BOT_LOGIN,
            "--config", box.inside(ws / ".cadence" / "factory.yaml"), "--run-id", t.build_id,
            "--run-attempt", "1", "--out-dir", box.inside(out), "--now", str(now))
    res = box.run(argv, cwd=ws, timeout=1200)
    rdir = t.results / "learn"
    rdir.mkdir(parents=True, exist_ok=True)
    if res.exit not in (0, 1):
        t.log(f"harvest exited {res.exit}: {res.stderr.decode(errors='replace')[-500:]}")
        return None
    summary = read_json(out / "summary.json") if (out / "summary.json").is_file() else {}
    write_json(rdir / "harvest-summary.json", summary)
    t.harvest_dir = out  # type: ignore[attr-defined]
    return {"exit": res.exit, **{k: summary.get(k) for k in ("prs_harvested", "decisions", "findings", "mode") if k in summary}}


def learn_record(t: "Ticket") -> None:
    chain = t.chain
    root, ws = t.job("learn-record")
    chain.checkout(chain.main(), ws)
    box = chain.box("tools", root, extra=[sb.Bind(chain.state, "/srv/state", ro=False)])
    staged = box.temp / "staged"
    src = getattr(t, "harvest_dir", None)
    if src is not None and (Path(src) / "staged").is_dir():
        shutil.copytree(Path(src) / "staged", staged)
    else:
        staged.mkdir(parents=True)
    added = chain._put(box, staged)
    now = t.at("learn-record")
    rel = f"reports/metrics-{day(now)}.json"
    exists = (chain.state / rel).exists() or sb.git(chain.state, "cat-file", "-e", f"HEAD:{rel}", check=False).ok
    if not exists:
        tools = box.opt(sb.OPT_TOOLS)
        state = "/srv/state" if box.mode == "bwrap" else chain.state.as_posix()
        target = staged / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        res = box.run(wfm.tool_argv(tools, "metrics.py report", "report", "--state-dir", state, "--repo-root",
                                    box.inside(ws), "--out", box.inside(target), "--now", str(now)), cwd=ws, timeout=600)
        if res.exit not in (0, 1):
            t.log(f"metrics.py report exited {res.exit}; no snapshot")
            target.unlink(missing_ok=True)
    added += chain._put(box, staged)
    chain.state_commit(sorted(set(added)), f"learn: run {t.build_id}", now)


def open_plan(t: "Ticket") -> tuple[int | None, str]:
    pr = t.chain.store.open_by_head(RETRO_BRANCH)
    if pr is None:
        return None, ""
    head = pr["head"]["sha"]
    out = sb.git_out(None, "log", "-1", f"--format=%(trailers:key={TRAILER},valueonly)", head,
                     git_dir=t.chain.origin)
    plan = out.splitlines()[0].strip() if out.strip() else ""
    return pr["number"], plan if re.fullmatch(r"[0-9a-f]{64}", plan) else ""


def retro_plan(t: "Ticket") -> dict[str, Any]:
    chain, wf = t.chain, t.env.wf
    root, ws = t.job("retro-plan")
    repo = ws / "repo"
    main = chain.main()
    chain.checkout(main, repo)
    exclude = repo / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with open(exclude, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("__pycache__/\n*.py[cod]\n")
    tools_box = chain.box("tools", root)
    gate_box = chain.box("gate", root)
    warm = t.env.cache / "npm-warm"
    if warm.is_dir():
        shutil.copytree(warm, root / "home" / ".npm", symlinks=True, dirs_exist_ok=True)
    chain.state_archive(tools_box.temp / "state")
    _, plan_sha_open = open_plan(t)
    job = JobEnv(chain.repo.slug, main, t.build_id, ws)
    now = t.at("retro-plan")
    steps: dict[str, Any] = {}
    failed = False
    order = [
        ("retro-plan.plan", tools_box, {"OPEN_PLAN": plan_sha_open}),
        ("retro-plan.apply", tools_box, {}),
        ("retro-plan.verify", gate_box, {}),
        ("retro-plan.demote", tools_box, {}),
        ("retro-plan.verify2", gate_box, {}),
        ("retro-plan.decide", tools_box, None),
        ("retro-plan.build", tools_box, {}),
    ]
    for key, box, env in order:
        piece = wf.piece(key)
        sid = piece.step_id or key
        if not wfm.step_should_run(piece.condition, {"steps": steps}, failed):
            steps[sid] = SKIPPED.ctx()
            continue
        if env is None:
            out = lambda s, k: steps.get(s, {}).get("outputs", {}).get(k, "")  # noqa: E731
            env = {"APPLIED": out("apply", "applied"), "VERIFY_OK": out("verify", "ok"),
                   "DEMOTE_APPLIED": out("demote", "applied"), "VERIFY2_OK": out("verify2", "ok"),
                   "PLAN_SHA": out("plan", "plan_sha")}
        env = {"RUN_URL": "", "PYTHONDONTWRITEBYTECODE": "1", **env}
        timeout = t.cfg.timeouts_min["learn_verify"] * 60 if box is gate_box else 900
        step = run_piece(box, piece, job, env=env, timeout=timeout, tools_now=now if box is tools_box else None)
        steps[sid] = step.ctx()
        (t.results / "learn").mkdir(parents=True, exist_ok=True)
        if step.outcome == "failure":
            t.log(f"{key} failed (exit {step.exit}): {step.log[-600:]}")
            failed = True
    out = tools_box.temp / "out"
    get = lambda s, k: steps.get(s, {}).get("outputs", {}).get(k, "")  # noqa: E731
    result = {
        "job": "failure" if failed else "success",
        "changed": get("build", "changed") or "false",
        "mode": get("plan", "mode"),
        "plan_sha": get("plan", "plan_sha"),
        "failed_plan_sha": get("outcome", "failed_plan_sha"),
        "applied": get("apply", "applied") == "true",
        "demoted": steps.get("demote", {}).get("outcome") == "success" and get("demote", "applied") == "true",
        "verify_required": get("apply", "verify_required"),
        "out": out,
        "main": main,
        "steps": {k: v["outcome"] for k, v in steps.items()},
    }
    rdir = t.results / "learn"
    for name in ("plan.json", "applied.json", "plan-summary.json", "pr-body.md", "retro.patch"):
        if (out / name).is_file():
            shutil.copy2(out / name, rdir / name)
    return result


def retro_publish(t: "Ticket", plan: dict[str, Any]) -> dict[str, Any]:
    chain, wf = t.chain, t.env.wf
    root, ws = t.job("retro-publish")
    main = chain.main()
    chain.checkout(main, ws / "repo")
    box = chain.box("tools", root)
    shutil.copytree(plan["out"], box.temp / "out")
    job = JobEnv(chain.repo.slug, main, t.build_id, ws)
    plan_sha = plan["plan_sha"]
    now = t.at("retro-publish")
    step = run_piece(box, wf.piece("retro-publish.guard"), job,
                     env={"PLAN_SHA": plan_sha, "RUN_URL": "", "BASE_BRANCH": "main"}, timeout=600, tools_now=now)
    if step.outcome != "success":
        t.log(f"retro-publish: the guard refused the patch: {step.log[-600:]}")
        return {"published": False}
    applied = read_json(box.temp / "out" / "applied.json")
    # Record the plan on cadence/state, create-only.
    rel = f"retro/plans/{plan_sha}.json"
    if not (chain.state / rel).exists():
        (chain.state / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(box.temp / "out" / "applied.json", chain.state / rel)
        chain.state_commit([rel], f"retro: plan {plan_sha[:12]} (run {t.build_id})", now)
    # cadence/retro = main + the retro patch, with the trailer.
    transitions = applied.get("transitions") or []
    count = lambda rung: sum(1 for x in transitions if x.get("to") == rung)  # noqa: E731
    subject = (f"cadence retro: {len(transitions)} lessons ({count('check')} checks, "
               f"{count('pattern')} patterns, {count('retired')} retired)")
    run_url = chain.run_url(t.build_id)
    body_lines = f"Proposed by the Cadence learning loop from factory runs. Run: {run_url}"
    clone = t.work / "retro-clone"
    chain.checkout(main, clone)
    sb.git(clone, "checkout", "--quiet", "-B", RETRO_BRANCH)
    sb.git(clone, "apply", "--index", "--whitespace=nowarn", str(box.temp / "out" / "retro.patch"))
    sb.git(clone, "commit", "--quiet", "-m", subject, "-m", body_lines, "-m", f"{TRAILER}: {plan_sha}",
           date=git_date(now))
    head = sb.git_out(clone, "rev-parse", "HEAD")
    sb.git(clone, "push", "--quiet", "--force", f"file://{chain.origin.as_posix()}", f"HEAD:refs/heads/{RETRO_BRANCH}")
    body = (box.temp / "out" / "body.md").read_text(encoding="utf-8") if (box.temp / "out" / "body.md").is_file() else ""
    existing = chain.store.open_by_head(RETRO_BRANCH)
    if existing is not None:
        pr = existing["number"]
        chain.store.update_head(pr, head, iso(now), title=subject, body=body)
    else:
        pr = chain.next_pr()
        chain.store.open_pr(pr, head_ref=RETRO_BRANCH, head_sha=head, base_sha=main, title=subject, body=body,
                            draft=False, at=iso(now))
    sb.git(None, "update-ref", f"refs/pull/{pr}/head", head, git_dir=chain.origin)
    sb.rmtree(clone)
    merged_sha = None
    # The merge step's if: mode eval-sandbox, vars.CADENCE_EVAL_SANDBOX == 'true', and a PR.
    if plan.get("mode") == "eval-sandbox" and chain.sandbox_var and pr:
        message = f"* {subject}\n\n{body_lines}\n\n{TRAILER}: {plan_sha}\n"
        merged_sha = chain.merge_retro(head, subject, message, pr, now)
        chain.store.merge(pr, merged_sha, iso(now))
    chain.save()
    return {"published": True, "pr": pr, "head": head, "merged_sha": merged_sha,
            "transitions": [{k: x.get(k) for k in ("class_key", "lesson_id", "to", "reason") if k in x} for x in transitions]}


def retro_failed(t: "Ticket", plan: dict[str, Any]) -> None:
    chain, wf = t.chain, t.env.wf
    root, ws = t.job("retro-failed")
    ws.mkdir(parents=True, exist_ok=True)
    box = chain.box("tools", root)
    target = box.temp / "retro-failed.json"
    now = t.at("retro-publish")
    step = run_piece(box, wf.piece("retro-failed.record"), JobEnv(chain.repo.slug, plan["main"], t.build_id, ws),
                     env={"PLAN_SHA": plan["failed_plan_sha"], "RETRO_OUT": box.inside(target)},
                     prepend='rel="$RETRO_OUT"\n', tools_now=now, timeout=300)
    if step.outcome != "success" or not target.is_file():
        t.log(f"retro-failed: the jq record failed: {step.log[-300:]}")
        return
    rel = f"retro/failed/{plan['failed_plan_sha']}.json"
    if (chain.state / rel).exists():
        return
    (chain.state / rel).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, chain.state / rel)
    chain.state_commit([rel], f"retro: failed plan {plan['failed_plan_sha'][:12]} (run {t.build_id})", now)


def run_chain(t: "Ticket") -> None:
    a = t.attempt["learn"]
    h = harvest(t)
    a["harvest"] = h
    if h is None:
        return
    learn_record(t)
    plan = retro_plan(t)
    a["plan_sha"] = plan["plan_sha"] or None
    a["applied"] = plan["applied"]
    a["demoted"] = plan["demoted"]
    a["steps"] = plan["steps"]
    if plan["job"] != "success":
        return
    if plan["changed"] == "true":
        pub = retro_publish(t, plan)
        a["changed"] = bool(pub.get("published"))
        a["transitions"] = pub.get("transitions") or []
        a["retro_sha"] = pub.get("head")
        a["retro_pr"] = pub.get("pr")
        a["retro_merged"] = bool(pub.get("merged_sha"))
    elif plan["failed_plan_sha"]:
        a["failed"] = True
        retro_failed(t, plan)
    write_json(t.results / "learn" / "learn.json", {k: v for k, v in a.items()})

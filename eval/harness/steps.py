"""One factory ticket, step by step, as the workflow runs it.

The steps (logical minutes from the slot start): issue and intake (0-10),
the scripted /approve (15), route outputs, build (20-60), gate and observe
(65), one retry when the gate failed at format, lint, boundaries or test
(70-110), publish (115), ledger (120), review and merge (140), the learn
chain (150-170, learn.py) and the queue for hidden scoring.

Each step runs the workflow's own piece (workflow.py) in its sandbox
profile, with GITHUB_OUTPUT emulated per step and step outcomes taken from
exit codes. The runner's own git (fresh checkouts, the publish commit, the
merge) runs only on clones no agent code has touched.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

import agent as ag
import privacy
import sandbox as sb
import workflow as wfm
from clock import iso
from config import EvalError, sha256_bytes, sha256_file, write_json, write_text

if TYPE_CHECKING:  # pragma: no cover
    from chain import Chain

STEP_WORDS = (
    ("no change", "empty"), ("apply", "apply"), ("policy", "policy"), ("config", "config"),
    ("FAIL: format ", "format"), ("FAIL: lint ", "lint"), ("FAIL: boundaries ", "boundaries"),
    ("FAIL: test ", "test"), ("verify did not finish", "timeout"),
)


def step_word(failed_step: str | None) -> str:
    """publish's fixed word for a failed gate (the "Report the Definition of
    Done failure" mapping)."""
    text = failed_step or ""
    for prefix, word in STEP_WORDS:
        if text.startswith(prefix):
            return word
    return "unknown"


# --- running one piece -----------------------------------------------------------------------


@dataclass
class Step:
    outcome: str  # success | failure | skipped
    exit: int | None
    outputs: dict[str, str]
    log: str = ""
    timed_out: bool = False

    def ctx(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "conclusion": self.outcome, "outputs": dict(self.outputs)}


SKIPPED = Step("skipped", None, {})


def parse_github_output(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if "<<" in line and ("=" not in line or line.index("<<") < line.index("=")):
            key, _, delim = line.partition("<<")
            value: list[str] = []
            i += 1
            while i < len(lines) and lines[i] != delim:
                value.append(lines[i])
                i += 1
            out[key] = "\n".join(value)
        elif "=" in line:
            key, _, value_s = line.partition("=")
            out[key] = value_s
        i += 1
    return out


@dataclass
class JobEnv:
    """What every piece of one emulated job sees as GitHub's default env."""

    slug: str
    sha: str
    run_id: str
    workspace: Path


def run_piece(
    box: sb.Box,
    piece: wfm.Piece,
    job: JobEnv,
    *,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    tools_now: int | None = None,
    append: str = "",
    prepend: str = "",
    cwd: Path | None = None,
) -> Step:
    env = dict(env or {})
    for name in list(piece.env) + list(piece.job_env):
        env.setdefault(name, "")
    text = prepend + piece.text + append
    if wfm.calls_tools(text):
        tools = box.opt(sb.OPT_TOOLS)
        text = wfm.rewrite_tools(text, f"{tools}/tool", tools_now, iso(tools_now) if tools_now else None).text
    elif tools_now is not None:
        text = wfm.rewrite_tools(text, "", None, iso(tools_now)).text
    out_file = box.temp / f".gh-output-{uuid.uuid4().hex}"
    out_file.write_text("", encoding="utf-8")
    env_file = box.temp / ".gh-env"
    env_file.touch()
    if cwd is None:
        wd = piece.working_directory
        if wd is None:
            cwd = job.workspace
        elif "runner.temp" in wd:
            cwd = box.temp
        else:
            cwd = job.workspace / wd
    full = {
        "GITHUB_OUTPUT": box.inside(out_file),
        "GITHUB_ENV": box.inside(env_file),
        "GITHUB_WORKSPACE": box.inside(job.workspace),
        "GITHUB_REPOSITORY": job.slug,
        "GITHUB_SHA": job.sha,
        "GITHUB_RUN_ID": job.run_id,
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        **env,
    }
    res = box.bash(text, cwd=cwd, env=full, timeout=timeout)
    outputs = parse_github_output(out_file.read_text(encoding="utf-8", errors="replace"))
    out_file.unlink()
    log = res.stdout.decode("utf-8", "replace") + res.stderr.decode("utf-8", "replace")
    outcome = "success" if res.ok else "failure"
    return Step(outcome, res.exit, outputs, log[-20000:], res.timed_out)


# --- the ticket ---------------------------------------------------------------------------------


@dataclass
class Try:
    n: int
    run_id: str
    agent_result: str = "skipped"         # the agent job's result
    session: ag.Session | None = None
    patch: Path | None = None             # change.patch (None when the job never packaged one)
    result_dir: Path | None = None        # cadence-result/ (claude-result.json, run_attempt)
    verify_result: str = "skipped"        # the verify job's result
    verify: dict[str, str] = field(default_factory=dict)  # verdict, failed_step, tree, guarded
    verify_log: Path | None = None
    observe: dict[str, str] = field(default_factory=dict)
    observed: bool = False
    stub_flag: bool = False

    def gate(self) -> str:
        """pass | fail | unfinished | skipped, as publish reads a verify job."""
        if self.verify_result == "success":
            return {"pass": "pass", "fail": "fail"}.get(self.verify.get("verdict", ""), "unfinished")
        if self.verify_result in ("failure", "cancelled"):
            return "unfinished"
        return "skipped"


class Ticket:
    """One attempt at one ticket in one chain."""

    def __init__(self, chain: "Chain", ticket: Any, epoch: int) -> None:
        self.chain = chain
        self.env = chain.env
        self.cfg = chain.env.cfg
        self.t = ticket
        self.epoch = epoch
        self.k = ticket.issue
        self.n = len(chain.tickets)
        self.clock = chain.env.clock
        self.issue = ticket.issue
        self.prefix = f"e{epoch}k{self.k}"
        self.build_id = f"{self.prefix}b"
        self.key = f"e{epoch}-{ticket.stem}"
        self.results = chain.results / self.key
        self.work = chain.work / "t" / self.key
        sb.rmtree(self.work)
        self.work.mkdir(parents=True)
        self.results.mkdir(parents=True, exist_ok=True)
        self.base = chain.main()
        self.tries: list[Try] = []
        self.sessions: list[ag.Session] = []
        self.spec_runs: list[dict[str, Any]] = []
        self.route: dict[str, str] = {}
        self.attempt = self._blank_attempt()

    # --- helpers -----------------------------------------------------------------------------

    def at(self, step: str, which: int = 0) -> int:
        return self.clock.at(self.epoch, self.n, self.k, step, which)

    def log(self, message: str) -> None:
        self.env.log(f"[{self.chain.name} {self.key}] {message}")

    def job(self, name: str) -> tuple[Path, Path]:
        """(job root, workspace) for one emulated job."""
        root = self.work / name
        ws = root / "work" / self.chain.name_part / self.chain.name_part
        return root, ws

    def guard(self, label: str, text: str) -> None:
        try:
            privacy.guard_inputs(label, text, self.env.private.canaries)
        except EvalError:
            self.attempt["flags"]["canary"] = True
            raise

    def _blank_attempt(self) -> dict[str, Any]:
        c = self.chain
        return {
            "schema": "cadence-eval.attempt/1",
            "run_id": self.env.run_id,
            "arm": c.arm,
            "trial": c.trial,
            "epoch": self.epoch,
            "repo": c.repo.id,
            "ticket": self.t.id,
            "issue": self.issue,
            "chain": c.name,
            "base_sha": self.base,
            "agent_mode": self.env.agent_mode,
            "model": self.cfg.model,
            "times": {"logical_start": iso(self.at("spec")), "logical_end": None,
                      "wall_start": None, "wall_end": None},
            "sessions": [],
            "intake": {"shape": None, "reran": False, "outcome": "not-run", "spec_sha256": None},
            "tries": [],
            "retry": {"eligible": False, "granted": False, "why": None},
            "publish": {"outcome": None, "try": None, "published_sha": None, "pr": None, "cadence_verify": None},
            "review": {"merged": False, "merged_sha": None},
            "ledger": {"run_ids": [], "booked_usd": 0.0, "reported_usd": 0.0},
            "learn": {"harvest": None, "plan_sha": None, "changed": False, "transitions": [], "applied": False,
                      "demoted": False, "failed": False, "retro_merged": False, "retro_sha": None},
            "retro_paths": {"before": {}, "after": {}, "changed_by": []},
            "hidden": {"merged": None, "shadow_try1": None, "shadow_try2": None, "ticket_pass": None},
            "flags": {"canary": False, "invariant": False, "tool_drift": False, "install_failed": False},
            "outcome": "not-built",
        }

    # --- 1. the issue -----------------------------------------------------------------------------

    def issue_md(self, root: Path, body: str) -> Path:
        """intake_sanitize.py on the issue event, into <temp>/cadence/input/issue.md."""
        self.guard("the issue", self.t.title + "\n" + body)
        box = self.chain.box("tools", root)
        event = box.temp / "issue-event.json"
        write_json(event, {"issue": {"number": self.issue, "title": self.t.title, "body": body,
                                     "user": {"login": "eval-owner"}, "labels": []}})
        out = box.temp / "cadence" / "input" / "issue.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        tools = box.opt(sb.OPT_TOOLS)
        res = box.run(wfm.tool_argv(tools, "intake_sanitize.py", "--event-path", box.inside(event),
                                    "--out", box.inside(out)), timeout=300)
        if not res.ok:
            raise EvalError(f"intake_sanitize.py exited {res.exit}: {res.stderr.decode(errors='replace')[-300:]}", 1)
        self.guard("the sanitized issue", out.read_text(encoding="utf-8"))
        return out

    # --- route ------------------------------------------------------------------------------------------

    def route_outputs(self, stage: str) -> dict[str, str]:
        """route's caps (and, for a build, paths) pieces on a checkout of BASE."""
        root, ws = self.job(f"route-{stage}")
        self.chain.checkout(self.base, ws)
        box = self.chain.box("tools", root)
        job = JobEnv(self.chain.repo.slug, self.base, self.build_id, ws)
        caps = run_piece(box, self.env.wf.piece("route.caps"), job, timeout=300)
        if caps.outcome != "success":
            raise EvalError(f"route caps failed: {caps.log[-400:]}", 1)
        out = {"issue": str(self.issue), **{k: caps.outputs.get(k, "") for k in ("per_run_usd", "max_turns", "retry_on_dod_fail")}}
        if stage == "build":
            paths = run_piece(box, self.env.wf.piece("route.paths"), job, timeout=300)
            if paths.outputs.get("ok") != "true":
                raise EvalError(f"route: the gate would refuse the guarded paths: {paths.outputs.get('failed_step')}", 1)
            out.update({"guarded": paths.outputs.get("guarded", ""), "test_roots": paths.outputs.get("test_roots", "")})
        return out

    # --- 2. intake ------------------------------------------------------------------------------------

    def intake(self, run_id: str, role: str, body: str, shape_stub: str) -> tuple[ag.Session, Path | None]:
        root, ws = self.job(role)
        self.chain.checkout(self.base, ws)
        box = self.chain.box("agent", root, plugin=True)
        self.issue_md(root, body)
        out_dir = box.temp / "cadence" / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        intake_file = out_dir / "intake.md"
        route = self.route_outputs("spec")
        ctx = {"needs": {"route": {"outputs": route}}, "runner": {"temp": box.inside(box.temp)},
               "github": {"repository": self.chain.repo.slug}}
        prompt = wfm.render(self.env.wf.piece("intake.prompt").text, ctx).strip()
        args = wfm.claude_args(wfm.render(self.env.wf.piece("intake.claude_args").text, ctx))
        self.guard("the intake prompt", prompt)
        exec_file = box.temp / "claude-execution-output.json"
        cap = float(route["per_run_usd"] or self.cfg.caps["per_run_usd"])
        issue_md = box.temp / "cadence" / "input" / "issue.md"
        cache = self.session_cache()
        key = cache.key(role=role, run_id=run_id, prompt=prompt, args=args, base=self.base, model=self.cfg.model,
                        mode=self.env.agent_mode, issue=sha256_file(issue_md),
                        shape=shape_stub if self.env.agent_mode == "stub" else None)
        hit = cache.load(role, key)
        if hit is not None:
            session, files = hit
            if "intake.md" in files:
                intake_file.write_bytes(files["intake.md"])
            if "execution.json" in files:
                exec_file.write_bytes(files["execution.json"])
                session.execution_file = exec_file
        elif self.env.agent_mode == "stub":
            session = ag.run_stub(role=role, run_id=run_id, cost=float(self.env.private.stubs["cost_usd"]),
                                  exec_file=exec_file, cap=cap, budget=self.env.budget,
                                  action=lambda: ag.stub_intake(intake_file, self.t.title, shape_stub))
        else:
            session = ag.run_live(role=role, run_id=run_id, box=box, cwd=ws, prompt=prompt,
                                  args=[*args], model=self.cfg.model, timeout_s=self.cfg.timeouts_min["intake"] * 60,
                                  exec_file=exec_file, stream_file=self.work / f"{role}.stream.jsonl", plugin=True,
                                  cap=cap, budget=self.env.budget, log=self.log, key=self.env.key)
        if hit is None:
            cache.save(role, key, session, _files(exec_file=exec_file, intake=intake_file))
        self.sessions.append(session)
        result_dir = self._keep_result(box, ws, "intake.keep", session, run_id)
        ok = session.succeeded and intake_file.is_file()
        rdir = self.results / "intake" / role
        rdir.mkdir(parents=True, exist_ok=True)
        if intake_file.is_file():
            shutil.copy2(intake_file, rdir / "intake.md")
        if result_dir is not None and (result_dir / "claude-result.json").is_file():
            shutil.copy2(result_dir / "claude-result.json", rdir / "claude-result.json")
        if session.infra_failed:  # voids are never booked
            return session, None
        self.spec_runs.append({"run_id": run_id, "outcome": "success" if ok else ("cancelled" if session.timed_out else "failure"),
                               "result": rdir / "claude-result.json" if (rdir / "claude-result.json").is_file() else None,
                               "at": self.at("spec2" if role == "intake2" else "spec", 1), "session": session})
        return session, (intake_file if ok else None)

    def _keep_result(self, box: sb.Box, ws: Path, key: str, session: ag.Session, run_id: str) -> Path | None:
        """The workflow's "Keep the result for the ledger" (the jq over the stream)."""
        job = JobEnv(self.chain.repo.slug, self.base, run_id, ws)
        env = {"EXECUTION_FILE": box.inside(session.execution_file) if session.execution_file else ""}
        step = run_piece(box, self.env.wf.piece(key), job, env=env, timeout=300)
        out = box.temp / "cadence-result"
        return out if step.outcome == "success" and out.is_dir() else None

    # --- 3. spec ------------------------------------------------------------------------------------

    def post_spec(self, intake_file: Path, run_id: str) -> tuple[str | None, str | None]:
        """(state, comment body) from publish's own cleaning of the intake file."""
        root, ws = self.job(f"publish-{run_id}")
        ws.mkdir(parents=True, exist_ok=True)
        box = self.chain.box("tools", root)
        (box.temp / "intake").mkdir(parents=True, exist_ok=True)
        shutil.copy2(intake_file, box.temp / "intake" / "intake.md")
        job = JobEnv(self.chain.repo.slug, self.base, run_id, ws)
        step = run_piece(box, self.env.wf.piece("publish.post_spec"), job,
                         env={"RUN_URL": self.chain.run_url(run_id), "ISSUE": str(self.issue)},
                         append='\necho "state=$state" >> "$GITHUB_OUTPUT"\n', timeout=300)
        comment = box.temp / "comment.md"
        if step.outcome != "success" or not comment.is_file():
            return None, None
        return step.outputs.get("state"), comment.read_text(encoding="utf-8")

    def spec_stage(self) -> str | None:
        """Intake (and one rerun after questions). The approved spec, or None."""
        stubs = self.env.private.stubs
        shapes = self.env.private.ticket_value(stubs["intake"].get("overrides") or {}, self.t) or [stubs["intake"]["default"]]
        body = self.t.body
        for round_no, (run_id, role) in enumerate(((f"{self.prefix}spec", "intake"), (f"{self.prefix}spec2", "intake2"))):
            shape_stub = shapes[min(round_no, len(shapes) - 1)]
            session, intake_file = self.intake(run_id, role, body, shape_stub)
            if session.infra_failed:
                self.attempt["intake"]["outcome"] = "failed"
                return None
            if intake_file is None:
                self.attempt["intake"]["outcome"] = "failed"
                return None
            state, comment = self.post_spec(intake_file, run_id)
            if state is None:
                self.attempt["intake"]["outcome"] = "failed"
                return None
            self.attempt["intake"]["shape"] = "spec" if state == "spec-ready" else "questions"
            if state == "spec-ready":
                self.attempt["intake"]["outcome"] = "approved"
                return comment
            if round_no == 0:
                self.attempt["intake"]["reran"] = True
                body = body.rstrip("\n") + "\n\n" + self.env.private.replies["questions_reply"].strip() + "\n"
                continue
        self.attempt["intake"]["outcome"] = "needs-human"
        return None

    # --- 5. build ------------------------------------------------------------------------------------

    def _inputs(self, box: sb.Box, spec: str, body: str, root: Path) -> None:
        inp = box.temp / "cadence" / "input"
        inp.mkdir(parents=True, exist_ok=True)
        write_text(inp / "spec.md", spec)
        issue = self.issue_md(root, body)
        if issue != inp / "issue.md":
            shutil.copy2(issue, inp / "issue.md")
        self.guard("the spec", spec)

    def session_cache(self) -> ag.SessionCache:
        return ag.SessionCache(self.results / "sessions", enabled=self.env.resume)

    def build(self, n: int, spec: str, body: str, *, first: Try | None = None, retry_step: str = "") -> Try:
        run_id = self.build_id if n == 1 else f"{self.build_id}.retry1"
        role = "build" if n == 1 else "retry"
        prefix = "agent" if n == 1 else "agent-retry"
        trial = Try(n, run_id)
        root, ws = self.job(f"agent{n}")
        self.chain.checkout(self.base, ws)
        box = self.chain.box("agent", root, plugin=True)
        self._inputs(box, spec, body, root)
        (box.temp / "cadence" / "output").mkdir(parents=True, exist_ok=True)
        jobenv = JobEnv(self.chain.repo.slug, self.base, run_id, ws)
        ctx = {"needs": {"route": {"outputs": self.route}, "retry-gate": {"outputs": {"step": retry_step}}},
               "runner": {"temp": box.inside(box.temp)}, "github": {"repository": self.chain.repo.slug}}
        prompt = wfm.render(self.env.wf.piece(f"{prefix}.prompt").text, ctx).strip()
        args = wfm.claude_args(wfm.render(self.env.wf.piece(f"{prefix}.claude_args").text, ctx))
        self.guard("the build prompt", prompt)
        exec_file = box.temp / "claude-execution-output.json"
        cap = float(self.route.get("per_run_usd") or self.cfg.caps["per_run_usd"])
        stubs = self.env.private.stubs
        src = (self.env.private.ticket_value(stubs["build"], self.t) or stubs["build"]["default"]).get(f"try{n}", "noop")
        excerpt = self.work / "retry-input" / "verify-excerpt.txt"
        cache = self.session_cache()
        key = cache.key(role=role, run_id=run_id, prompt=prompt, args=args, base=self.base, model=self.cfg.model,
                        mode=self.env.agent_mode, spec=sha256_bytes(spec.encode()), body=sha256_bytes(body.encode()),
                        first=sha256_file(first.patch) if first is not None and first.patch is not None else None,
                        excerpt=sha256_file(excerpt) if excerpt.is_file() else None,
                        src=src if self.env.agent_mode == "stub" else None)
        hit = cache.load(role, key)
        setup_voids: list[str] = []
        if hit is not None:
            # Replay a session a stopped run already paid for: its diff on the fresh checkout.
            session, files = hit
            if files.get("work.patch", b"").strip() and not ag.stub_build(ws, files["work.patch"]):
                raise EvalError(f"{run_id}: the cached session's diff does not apply", 1)
            if "execution.json" in files:
                exec_file.write_bytes(files["execution.json"])
                session.execution_file = exec_file
        else:
            setup_ok, setup_voids = runtime_setup(self.chain, root, ws, self.cfg.timeouts_min["build"] * 60)
            if not setup_ok:
                # As on GitHub, a failed setup step fails the agent job before the model runs.
                if len(setup_voids) >= 3:
                    session = ag.Session(role, run_id, voids=setup_voids[:3], infra_failed=True)
                    trial.session = session
                    self.sessions.append(session)
                self.log(f"runtime setup (npm ci) failed for {run_id} ({len(setup_voids)} network void(s))")
                trial.agent_result = "failure"
                return trial
            if first is not None and first.patch is not None:
                (box.temp / "first").mkdir(parents=True, exist_ok=True)
                shutil.copy2(first.patch, box.temp / "first" / "change.patch")
                if excerpt.is_file():
                    shutil.copy2(excerpt, box.temp / "cadence" / "input" / "verify-excerpt.txt")
                applied = run_piece(box, self.env.wf.piece("agent-retry.apply_first"), jobenv,
                                    env={"BASE_SHA": self.base}, timeout=300)
                if applied.outcome != "success":
                    trial.agent_result = "failure"
                    self.log("agent-retry: the first attempt did not apply")
                    return trial
            if self.env.agent_mode == "stub":
                patch = self.env.stub_patch(self.chain.repo.id, src)

                def action() -> int:
                    if n == 2 and patch.strip():
                        # A stub retry's patch is the whole result: back to BASE first.
                        sb.git(ws, "checkout", "--quiet", "--", ".")
                        sb.git(ws, "clean", "-fdq")
                    if not ag.stub_build(ws, patch):
                        trial.stub_flag = True
                        self.attempt["flags"]["stub_not_applied"] = True
                    return 0

                session = ag.run_stub(role=role, run_id=run_id, cost=float(stubs["cost_usd"]), exec_file=exec_file,
                                      cap=cap, budget=self.env.budget, action=action)
            else:
                session = ag.run_live(role=role, run_id=run_id, box=box, cwd=ws, prompt=prompt, args=[*args],
                                      model=self.cfg.model, timeout_s=self.cfg.timeouts_min["build"] * 60,
                                      exec_file=exec_file, stream_file=self.work / f"{role}.stream.jsonl",
                                      plugin=True, cap=cap, budget=self.env.budget, log=self.log, key=self.env.key)
            session.voids = setup_voids + session.voids
            if len(session.voids) >= 3:  # a third void: infra-failed
                session.infra_failed, session.voids = True, session.voids[:3]
        trial.session = session
        self.sessions.append(session)
        trial.result_dir = self._keep_result(box, ws, f"{prefix}.keep", session, run_id)
        tdir = self.results / f"try{n}"
        tdir.mkdir(parents=True, exist_ok=True)
        if trial.result_dir is not None and (trial.result_dir / "claude-result.json").is_file():
            shutil.copy2(trial.result_dir / "claude-result.json", tdir / "claude-result.json")
        if session.infra_failed:
            trial.agent_result = "failure"
            return trial
        if not session.succeeded:
            trial.agent_result = "cancelled" if session.timed_out else "failure"
            if hit is None:
                cache.save(role, key, session, _files(exec_file=exec_file))
            return trial
        pkg = run_piece(box, self.env.wf.piece(f"{prefix}.package"), jobenv, env={"BASE_SHA": self.base}, timeout=600)
        patch_file = box.temp / "cadence" / "output" / "change.patch"
        if pkg.outcome != "success" or not patch_file.is_file():
            trial.agent_result = "failure"
            return trial
        if hit is None:
            cache.save(role, key, session, _files(exec_file=exec_file, work=patch_file))
        trial.agent_result = "success"
        trial.patch = tdir / "change.patch"
        shutil.copy2(patch_file, trial.patch)
        return trial

    # --- 6. the gate ---------------------------------------------------------------------------------

    def gate(self, trial: Try, base: str | None = None) -> None:
        base = base or self.base
        root, ws = self.job(f"verify{trial.n}")
        self.chain.checkout(base, ws)
        box = self.chain.box("gate", root)
        warm = self.env.cache / "npm-warm"
        if warm.is_dir():
            shutil.copytree(warm, root / "home" / ".npm", symlinks=True, dirs_exist_ok=True)
        (box.temp / "change").mkdir(parents=True, exist_ok=True)
        if trial.patch is not None:
            shutil.copy2(trial.patch, box.temp / "change" / "change.patch")
        trial.verify, trial.verify_result, trial.verify_log = run_gate(
            self.env.wf, box, JobEnv(self.chain.repo.slug, base, trial.run_id, ws),
            timeout=self.cfg.timeouts_min["gate"] * 60, log_dir=self.results / f"try{trial.n}" / "verify-log",
        )

    # --- 7. observe ------------------------------------------------------------------------------------

    def observe(self, trial: Try, *, base: str | None = None, spec: str | None = None) -> None:
        base = base or self.base
        root, ws = self.job(f"observe{trial.n}")
        self.chain.checkout(base, ws / "base")
        box = self.chain.box("tools", root)
        verify_result = (
            "failure" if trial.verify_result == "success" and trial.verify.get("verdict") != "pass"
            else trial.verify_result
        )
        spec_sha = None
        if spec is not None:
            write_text(box.temp / "cadence-input" / "spec.md", spec)
            spec_sha = sha256_bytes(spec.encode("utf-8"))
        trial.observe = run_observe(
            self.env.wf, box, JobEnv(self.chain.repo.slug, base, trial.run_id, ws), trial,
            issue=self.issue, verify_result=verify_result, spec_sha256=spec_sha, now=self.at("gate"),
        )
        trial.observed = bool(trial.observe.get("bundle_sha256"))
        if trial.observe.get("_observation"):
            write_text(self.results / f"try{trial.n}" / "observation.json", trial.observe["_observation"])

    # --- 8. retry ---------------------------------------------------------------------------------------

    def retry_gate(self, first: Try) -> tuple[bool, str, str]:
        """(granted, step word, why)."""
        a = self.attempt["retry"]
        if not (first.agent_result == "success" and first.verify_result == "success"
                and first.verify.get("verdict") == "fail" and self.route.get("retry_on_dod_fail") == "1"):
            a.update({"eligible": False, "granted": False, "why": None})
            return False, "", ""
        root, ws = self.job("retry-gate")
        ws.mkdir(parents=True, exist_ok=True)
        box = self.chain.box("tools", root)
        job = JobEnv(self.chain.repo.slug, self.base, self.build_id, ws)
        step = run_piece(box, self.env.wf.piece("retry-gate.map_step"), job,
                         env={"FAILED_STEP": first.verify.get("failed_step", ""), "ISSUE": str(self.issue)}, timeout=300)
        word = step.outputs.get("step", "other")
        a["eligible"] = True
        if word == "other":
            a.update({"granted": False, "why": "not-retryable"})
            return False, word, "not-retryable"
        # The daily check (daily_usd is far above any eval spend) always passes.
        excerpt = self.work / "retry-input" / "verify-excerpt.txt"
        excerpt.parent.mkdir(parents=True, exist_ok=True)
        log_dir = box.temp / "verify-log"
        if first.verify_log is not None and first.verify_log.is_dir():
            shutil.copytree(first.verify_log, log_dir, dirs_exist_ok=True)
        else:
            log_dir.mkdir(parents=True, exist_ok=True)
        tools = box.opt(sb.OPT_TOOLS)
        out = box.temp / "retry-input" / "verify-excerpt.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        res = box.run(wfm.tool_argv(tools, "signals.py excerpt", "excerpt", "--verify-log-dir", box.inside(log_dir),
                                    "--out", box.inside(out), isolated=True), timeout=300)
        if not res.ok:
            a.update({"granted": False, "why": "excerpt-failed"})
            return False, word, "excerpt-failed"
        self.guard("the verify excerpt", out.read_text(encoding="utf-8", errors="replace"))
        shutil.copy2(out, excerpt)
        a.update({"granted": True, "why": "granted"})
        write_json(self.results / "retry-gate.json", {"step": word, "why": "granted"})
        return True, word, "granted"

    # --- 9. publish ---------------------------------------------------------------------------------------

    def pick(self) -> dict[str, str]:
        t1 = self.tries[0] if self.tries else Try(1, self.build_id)
        t2 = self.tries[1] if len(self.tries) > 1 else Try(2, f"{self.build_id}.retry1")
        root, ws = self.job("publish")
        ws.mkdir(parents=True, exist_ok=True)
        box = self.chain.box("tools", root)
        env = {
            "VERIFY_RESULT": t1.verify_result, "VERIFY_VERDICT": t1.verify.get("verdict", ""),
            "RETRY_VERIFY_RESULT": t2.verify_result, "RETRY_VERIFY_VERDICT": t2.verify.get("verdict", ""),
            "TREE_1": t1.verify.get("tree", ""), "TREE_2": t2.verify.get("tree", ""),
            "GUARDED_1": t1.verify.get("guarded", ""), "GUARDED_2": t2.verify.get("guarded", ""),
            "FAILED_1": t1.verify.get("failed_step", ""), "FAILED_2": t2.verify.get("failed_step", ""),
            "ISSUE": str(self.issue), "RUN_URL": self.chain.run_url(self.build_id),
        }
        step = run_piece(box, self.env.wf.piece("publish.pick"), JobEnv(self.chain.repo.slug, self.base, self.build_id, ws),
                         env=env, timeout=300)
        if step.outcome != "success":
            raise EvalError(f"publish: pick failed: {step.log[-300:]}", 1)
        return step.outputs

    def publish(self, picked: dict[str, str]) -> None:
        p = self.attempt["publish"]
        if picked.get("passed") != "true":
            first = self.tries[0] if self.tries else None
            if first is None or first.agent_result in ("failure", "cancelled"):
                p["outcome"] = "needs-human"
            else:
                p["outcome"] = "dod-failed"
                p["step_word"] = step_word(picked.get("failed_step") or "verify did not finish")
            write_json(self.results / "publish.json", p)
            return
        n = int(picked["try"])
        trial = self.tries[n - 1]
        assert trial.patch is not None
        at = self.at("publish")
        clone = self.work / "publish-clone"
        self.chain.checkout(self.base, clone)
        sb.git(clone, "apply", "--index", "--whitespace=nowarn", str(trial.patch))
        run_url = self.chain.run_url(self.build_id)
        sb.git(clone, "commit", "--quiet", "-m", f"cadence: build #{self.issue}",
               "-m", f"Built by the Cadence factory from the spec approved on #{self.issue}.",
               "-m", f"Run: {run_url}", date=f"@{at} +0000")
        published = sb.git_out(clone, "rev-parse", "HEAD")
        pushed_tree = sb.git_out(clone, "rev-parse", "HEAD^{tree}")
        pr = self.chain.next_pr()
        branch = f"cadence/issue-{self.issue}"
        sb.git(clone, "push", "--quiet", "--force", f"file://{self.chain.origin.as_posix()}",
               f"HEAD:refs/heads/{branch}", f"HEAD:refs/pull/{pr}/head")
        self.chain.store.open_pr(pr, head_ref=branch, head_sha=published, base_sha=self.base,
                                 title=f"cadence: #{self.issue} {self.t.title}"[:200],
                                 body=f"Builds #{self.issue} from the spec approved on the issue. Closes #{self.issue}.\n",
                                 draft=True, at=iso(at))
        verified = picked.get("tree", "")
        p.update({"outcome": "pr-open", "try": n, "published_sha": published, "pr": pr,
                  "cadence_verify": "success" if verified and verified == pushed_tree else "action_required"})
        sb.rmtree(clone)
        write_json(self.results / "publish.json", p)

    # --- 10. ledger ---------------------------------------------------------------------------------------

    def ledger(self, spec_only: bool) -> None:
        """ledger.py record for every spec run, the build and the retry;
        signals.py finalize and put for each observed try; one commit each."""
        L = self.attempt["ledger"]
        for run in self.spec_runs:
            self.chain.book(
                run_id=run["run_id"], issue=self.issue, stage="spec", outcome=run["outcome"], dod="skipped",
                result=run["result"], now=run["at"], message=f"ledger: #{self.issue} run {run['run_id']} attempt 1",
                ledger=L,
            )
        if spec_only:
            self._copy_ledger()
            return
        pub = self.attempt["publish"]
        staged_obs: list[tuple[Try, Path]] = []
        records: list[dict[str, Any]] = []
        for trial in self.tries:
            if trial.agent_result == "skipped":
                continue
            dod = {"pass": "pass", "fail": "fail", "unfinished": "unknown", "skipped": "skipped"}[trial.gate()]
            pr_args = (pub["pr"], pub["published_sha"]) if pub.get("try") == trial.n and pub.get("pr") else (None, None)
            result = trial.result_dir / "claude-result.json" if trial.result_dir and (trial.result_dir / "claude-result.json").is_file() else None
            records.append({"run_id": trial.run_id, "outcome": trial.agent_result, "dod": dod, "result": result,
                            "pr": pr_args[0], "published_sha": pr_args[1]})
            if trial.observed:
                staged_obs.append((trial, Path(trial.observe["_bundle_file"])))
        self.chain.book_build(records=records, observed=staged_obs, issue=self.issue, base=self.base,
                              pub=pub, now=self.at("ledger"),
                              message=f"ledger: #{self.issue} run {self.build_id} attempt 1", ledger=L)
        self._copy_ledger()

    def _copy_ledger(self) -> None:
        dest = self.results / "ledger"
        dest.mkdir(parents=True, exist_ok=True)
        for run_id in self.attempt["ledger"]["run_ids"]:
            src = self.chain.state / "runs" / f"{run_id}-1.json"
            if src.is_file():
                shutil.copy2(src, dest / src.name)

    # --- the whole ticket ----------------------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        import learn
        from config import wall_now

        a = self.attempt
        a["times"]["wall_start"] = wall_now()
        a["retro_paths"]["before"] = self.chain.retro_paths()
        a["flags"]["tool_drift"] = self.chain.tool_drift()
        body = self.t.body
        built = False
        try:
            spec = self.spec_stage()
            if a["intake"]["reran"]:
                body = body.rstrip("\n") + "\n\n" + self.env.private.replies["questions_reply"].strip() + "\n"
            if spec is None:
                infra = any(s.infra_failed for s in self.sessions)
                a["outcome"] = "infra-failed" if infra else "needs-human"
                a["publish"]["outcome"] = "not-built"
                self.ledger(spec_only=True)
            else:
                a["intake"]["spec_sha256"] = sha256_bytes(spec.encode("utf-8"))
                self.route = self.route_outputs("build")
                first = self.build(1, spec, body)
                self.tries.append(first)
                if first.agent_result == "success":
                    self.gate(first)
                if first.agent_result != "skipped" and not (first.session and first.session.infra_failed):
                    self.observe(first, spec=spec)
                granted, word, _why = self.retry_gate(first)
                if granted:
                    second = self.build(2, spec, body, first=first, retry_step=word)
                    self.tries.append(second)
                    if second.agent_result == "success":
                        self.gate(second)
                    if not (second.session and second.session.infra_failed):
                        self.observe(second, spec=spec)
                if any(t.session and t.session.infra_failed for t in self.tries):
                    a["outcome"] = "infra-failed"
                    a["flags"]["infra_failed"] = True
                    self.ledger(spec_only=True)
                else:
                    picked = self.pick()
                    self.publish(picked)
                    self.ledger(spec_only=False)
                    built = True
                    before_merge = self.chain.retro_paths()
                    self.review()
                    if self.chain.retro_paths() != before_merge:
                        a["retro_paths"]["changed_by"].append("agent-merge")
                    a["outcome"] = a["publish"]["outcome"]
        finally:
            a["sessions"] = [s.record() for s in self.sessions]
            a["tries"] = [try_record(t) for t in self.tries]
        merged_main = self.chain.main()
        merged_tree = self.chain.tree_of(merged_main)
        if built:
            before_retro = self.chain.retro_paths()
            learn.run_chain(self)
            if self.chain.retro_paths() != before_retro:
                a["retro_paths"]["changed_by"].append("retro-merge")
        a["retro_paths"]["after"] = self.chain.retro_paths()
        problems = self.chain.invariants()
        if problems:
            a["flags"]["invariant"] = True
            a["flags"]["notes"] = problems[:10]
            self.log("INVARIANT: " + "; ".join(problems))
        self.queue_scores(merged_tree)
        if not a["review"]["merged"] and a["outcome"] != "infra-failed":
            # Nothing landed, so the ticket did not pass. Left at None, the
            # attempt would drop out of pass@1 and pass^k (report.py skips
            # None), counting only the merged attempts.
            a["hidden"]["ticket_pass"] = False
        a["times"]["logical_end"] = iso(self.at("retro-publish"))
        a["times"]["wall_end"] = wall_now()
        return a

    def queue_scores(self, merged_tree: str) -> None:
        """main after the merge, and each try's patch on BASE when its tree differs."""
        a = self.attempt
        epoch_time = self.at("merge")
        if a["review"]["merged"]:
            a["hidden"]["merged"] = self.chain.queue_score(attempt_dir=self.results, field_name="merged",
                                                           tree=merged_tree, epoch_time=epoch_time)
        for trial in self.tries:
            if trial.patch is None or trial.patch.stat().st_size == 0:
                continue
            tree = self.chain.tree_plus(self.base, trial.patch)
            if tree is None:
                continue
            field_name = f"shadow_try{trial.n}"
            # The same tree as the merged one is scored once (the queue dedupes by tree).
            a["hidden"][field_name] = self.chain.queue_score(attempt_dir=self.results, field_name=field_name,
                                                             tree=tree, epoch_time=epoch_time)

    # --- 11. review ---------------------------------------------------------------------------------------

    def review(self) -> None:
        pub = self.attempt["publish"]
        if pub.get("outcome") != "pr-open":
            return
        reviewer = self.env.private.reviewer
        for i, line in enumerate(reviewer.get("forbids") or []):
            self.chain.store.comment(pub["pr"], line, iso(self.at("merge") - 300), comment_id=self.chain.next_comment())
        merged = self.chain.merge_agent(pub["published_sha"], self.t.id)
        self.chain.store.merge(pub["pr"], merged, iso(self.at("merge")))
        self.attempt["review"] = {"merged": True, "merged_sha": merged}
        write_json(self.results / "review.json", self.attempt["review"])


def _files(*, exec_file: Path | None = None, work: Path | None = None, intake: Path | None = None) -> dict[str, bytes]:
    """A session's outputs for the session cache."""
    out: dict[str, bytes] = {}
    for name, p in (("execution.json", exec_file), ("work.patch", work), ("intake.md", intake)):
        if p is not None and p.is_file():
            out[name] = p.read_bytes()
    return out


# --- shared with the autopilot -------------------------------------------------------------------

NPM_NETWORK = re.compile(
    r"ENOTFOUND|ETIMEDOUT|EAI_AGAIN|ECONNRESET|ECONNREFUSED|socket hang up|network|\b(429|5\d\d)\b", re.I
)


def runtime_setup(chain: "Chain", root: Path, ws: Path, timeout: int) -> tuple[bool, list[str]]:
    """``npm ci --no-audit --no-fund`` on the base tree (gate profile), with a
    per-attempt copy of the warm cache. A network failure is a void (retried,
    at most twice); any other failure fails the job, as on GitHub."""
    if not (ws / "package.json").is_file():
        return True, []
    warm = chain.env.cache / "npm-warm"
    voids: list[str] = []
    for _ in range(3):
        home_cache = root / "home" / ".npm"
        sb.rmtree(home_cache)
        if warm.is_dir():
            shutil.copytree(warm, home_cache, symlinks=True)
        res = chain.box("gate", root).run(["npm", "ci", "--no-audit", "--no-fund"], cwd=ws, timeout=timeout)
        if res.ok:
            return True, voids
        text = res.stdout.decode("utf-8", "replace")[-4000:] + res.stderr.decode("utf-8", "replace")[-4000:]
        if not (res.timed_out or NPM_NETWORK.search(text)):
            return False, voids
        voids.append("npm-network")
    return False, voids


# --- the gate and observe, shared with the autopilot -----------------------------------------------


def run_gate(wf: wfm.Workflow, box: sb.Box, job: JobEnv, *, timeout: int, log_dir: Path) -> tuple[dict[str, str], str, Path | None]:
    """verify's paths, apply and verify pieces, then its own output expressions.
    Returns (outputs, job result, the verify-log dir)."""
    import time

    start = time.monotonic()
    steps: dict[str, Any] = {}
    job_failed = False
    timed_out = False
    for sid, key, env in (
        ("paths", "verify.paths", {}),
        ("apply", "verify.apply", None),
        ("verify", "verify.verify", {}),
    ):
        piece = wf.piece(key)
        ctx = {"steps": steps}
        if env is None:
            paths = steps.get("paths", {}).get("outputs", {})
            env = {"GUARDED": paths.get("guarded", ""), "TEST_ROOTS": paths.get("test_roots", "")}
        if timed_out or not wfm.step_should_run(piece.condition, ctx, job_failed):
            steps[sid] = SKIPPED.ctx()
            continue
        remaining = max(1.0, timeout - (time.monotonic() - start))
        step = run_piece(box, piece, job, env={"BASE_SHA": job.sha, **env}, timeout=remaining)
        steps[sid] = step.ctx()
        if step.timed_out:
            timed_out = True
            job_failed = True
        elif step.outcome == "failure" and not piece.continue_on_error:
            job_failed = True
    outputs = wfm.evaluate_outputs(wf.piece("verify.outputs"), steps)
    result = "failure" if job_failed else "success"
    log_dir.mkdir(parents=True, exist_ok=True)
    found = False
    if steps.get("apply", {}).get("outputs", {}).get("ok") == "true":
        for src in (job.workspace / ".cadence" / "last_verify.log", box.temp / "verify-console.log"):
            if src.is_file():
                shutil.copy2(src, log_dir / src.name)
                found = True
    write_json(log_dir.parent / "gate.json", {"outputs": outputs, "result": result, "timed_out": timed_out,
                                               "steps": {k: {"outcome": v["outcome"]} for k, v in steps.items()}})
    return outputs, result, (log_dir if found else None)


def run_observe(
    wf: wfm.Workflow, box: sb.Box, job: JobEnv, trial: Try, *, issue: int, verify_result: str,
    spec_sha256: str | None, now: int,
) -> dict[str, str]:
    """observe's apply and scan pieces (base tools pinned, logical clock).
    Returns the scan's outputs plus the reassembled bundle file."""
    change = box.temp / "change"
    change.mkdir(parents=True, exist_ok=True)
    if trial.patch is not None:
        shutil.copy2(trial.patch, change / "change.patch")
    if trial.verify_log is not None and trial.verify_log.is_dir():
        shutil.copytree(trial.verify_log, box.temp / "verify-log", dirs_exist_ok=True)
    if trial.result_dir is not None and trial.result_dir.is_dir():
        shutil.copytree(trial.result_dir, box.temp / "cadence-result", dirs_exist_ok=True)
    env = {"ISSUE": str(issue), "AGENT_RESULT": trial.agent_result, "VERIFY_RESULT": verify_result,
           "TRY_RUN_ID": trial.run_id}
    applied = run_piece(box, wf.piece("observe.apply"), job, env=env, timeout=600)
    if applied.outcome != "success":
        return {}
    env.update({"APPLY_STATUS": applied.outputs.get("status", ""), "SPEC_SHA256": spec_sha256 or ""})
    scan = run_piece(box, wf.piece("observe.scan"), job, env=env, timeout=600, tools_now=now)
    if scan.outcome != "success":
        return {"apply_status": applied.outputs.get("status", ""), "_error": scan.log[-2000:]}
    outputs = dict(scan.outputs)
    outputs["apply_status"] = applied.outputs.get("status", "")
    b64 = "".join(outputs.get(k, "") for k in ("bundle", "bundle_2", "bundle_3", "bundle_4", "bundle_5", "bundle_6"))
    bundle = box.temp / f"bundle-{trial.n}.b64"
    bundle.write_text(b64, encoding="ascii")
    # bundle.b64 is one line; restore its newline if the sha covered it (as ledger does).
    if sha256_file(bundle) != outputs.get("bundle_sha256"):
        bundle.write_text(b64 + "\n", encoding="ascii")
    keep = box.root.parent / f"bundle-{trial.run_id}.b64"
    shutil.copy2(bundle, keep)
    outputs["_bundle_file"] = str(keep)
    obs = box.temp / "observe" / "observation.json"
    if obs.is_file():
        outputs["_observation"] = obs.read_text(encoding="utf-8")
    return outputs


def try_record(trial: Try) -> dict[str, Any]:
    patch = trial.patch
    files: list[str] = []
    data = b""
    if patch is not None and patch.is_file():
        data = patch.read_bytes()
        for line in data.decode("utf-8", "replace").split("\n"):
            if line.startswith("diff --git a/"):
                files.append(line.split(" b/", 1)[-1])
    gate = trial.gate()
    return {
        "n": trial.n,
        "patch_sha256": sha256_bytes(data) if patch is not None else None,
        "bytes": len(data),
        "files": files[:200],
        "apply_status": trial.observe.get("apply_status") or (None if trial.agent_result == "skipped" else "missing"),
        "verdict": gate,
        "failed_step": (trial.verify.get("failed_step") or None),
        "step_word": step_word(trial.verify.get("failed_step")) if gate == "fail" else None,
        "guarded": trial.verify.get("guarded") or None,
        "gate_tree": trial.verify.get("tree") or None,
        "job_result": trial.agent_result,
        "observed": trial.observed,
        "flags": ["stub-not-applied"] if trial.stub_flag else [],
    }


def as_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True)

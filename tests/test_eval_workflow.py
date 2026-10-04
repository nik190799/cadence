"""The eval runs the factory's own workflow pieces, cut out of the template.

- every piece is extracted from the real template at HEAD;
- an unknown ${{ }} context, an unknown env name, a missing step, or a
  retry twin that drifted from its original fails closed;
- the verify job's verdict comes from the template's own output
  expressions, evaluated by a small evaluator;
- the rewrites point every tool call at the pinned copy and give it the
  logical clock;
- flag parity: every flag the eval passes to a tool directly appears in the
  template on a line that runs the same tool (or, for the eval-only calls,
  in the tool's own parser).
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "eval" / "harness"
TEMPLATE = ROOT / "plugins" / "cadence" / "templates" / ".github" / "workflows" / "cadence-factory.yml.tmpl"
TOOLS = ROOT / "plugins" / "cadence" / "templates" / "tool"
sys.path.insert(0, str(HARNESS))

import workflow as wfm  # noqa: E402
from config import EvalError  # noqa: E402

TEXT = TEMPLATE.read_text(encoding="utf-8")


def _head_text() -> str:
    done = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{wfm.TEMPLATE_PATH}"], capture_output=True)
    if done.returncode != 0:
        return TEXT  # a checkout git cannot read (e.g. a Windows worktree seen from WSL)
    return done.stdout.decode("utf-8")


def _mutate(edit) -> str:
    doc = yaml.safe_load(TEXT)
    edit(doc["jobs"])
    return yaml.safe_dump(doc, sort_keys=False, width=10000)


def _step(jobs: dict, job: str, name: str) -> dict:
    return next(s for s in jobs[job]["steps"] if s.get("name") == name)


def test_every_piece_is_extracted_from_the_real_template_at_head() -> None:
    wf = wfm.extract(_head_text(), "HEAD")
    assert set(wf.pieces) == {spec.key for spec in wfm.SPECS}
    for key, piece in wf.pieces.items():
        assert piece.text.strip(), key
        assert piece.sha256 == hashlib.sha256(piece.text.encode("utf-8")).hexdigest()
    post = wf.piece("publish.post_spec").text
    assert post.startswith(wfm.POST_SPEC_FROM) and "gh api" not in post
    assert "clean_text" in post and "sk-ant" in post  # the template's own credential check
    jq = wf.piece("retro-failed.record").text
    assert jq.lstrip().startswith(wfm.FAILED_JQ_FROM) and "git push" not in jq
    assert "bash scripts/verify.sh" in wf.piece("verify.verify").text
    assert wf.piece("verify.verify").continue_on_error is True
    assert wf.piece("verify.apply").condition == "steps.paths.outputs.ok == 'true'"
    assert set(wf.piece("verify.outputs").outputs) == {"verdict", "failed_step", "tree", "guarded"}
    assert wf.piece("observe.scan").working_directory == "${{ runner.temp }}"
    # The file on disk and the commit agree (no local edits to the template).
    assert wfm.extract(TEXT).shas() == wf.shas()


def test_an_unknown_expression_fails_closed() -> None:
    def leak(jobs: dict) -> None:
        step = _step(jobs, "agent", "Run the agent team")
        step["with"]["prompt"] += "\nKey: ${{ secrets.ANTHROPIC_API_KEY }}\n"

    with pytest.raises(EvalError, match="secrets.ANTHROPIC_API_KEY"):
        wfm.extract(_mutate(leak))


def test_an_unknown_env_name_fails_closed() -> None:
    def add_env(jobs: dict) -> None:
        step = _step(jobs, "verify", "Apply the diff, then restore guarded paths from the base")
        step["env"]["EXTRA_TOKEN"] = "${{ github.token }}"

    with pytest.raises(EvalError, match="EXTRA_TOKEN"):
        wfm.extract(_mutate(add_env))


def test_a_missing_step_fails_closed() -> None:
    def rename(jobs: dict) -> None:
        _step(jobs, "verify", "Run verify")["name"] = "Run the checks"

    with pytest.raises(EvalError, match="piece missing"):
        wfm.extract(_mutate(rename))


def test_a_retry_twin_that_drifted_fails_closed() -> None:
    def drift(jobs: dict) -> None:
        step = _step(jobs, "verify-retry", "Apply the diff, then restore guarded paths from the base")
        step["run"] = step["run"].replace("git apply --index", "git apply --index --reject")

    with pytest.raises(EvalError, match="byte for byte"):
        wfm.extract(_mutate(drift))


def test_a_script_with_an_expression_fails_closed() -> None:
    def inline(jobs: dict) -> None:
        step = _step(jobs, "observe", "Scan the attempt")
        step["run"] = "echo ${{ github.repository }}\n" + step["run"]

    with pytest.raises(EvalError, match="does not render scripts"):
        wfm.extract(_mutate(inline))


# --- the verdict -------------------------------------------------------------------------


def _steps(paths=("success", {"ok": "true"}), apply=("success", {"ok": "true", "tree": "a" * 40}),
           verify=("success", {})) -> dict:
    return {sid: {"outcome": o, "conclusion": o, "outputs": out}
            for sid, (o, out) in (("paths", paths), ("apply", apply), ("verify", verify))}


@pytest.mark.parametrize(
    "steps, verdict",
    [
        (_steps(), "pass"),
        (_steps(verify=("failure", {"failed_step": "FAIL: test (exit 1)"})), "fail"),
        (_steps(paths=("success", {"failed_step": "config: bad"}), apply=("skipped", {}), verify=("skipped", {})), "fail"),
        (_steps(apply=("success", {"failed_step": "no change: empty"}), verify=("skipped", {})), "fail"),
        (_steps(apply=("failure", {}), verify=("skipped", {})), ""),       # apply crashed: no verdict
        (_steps(paths=("failure", {}), apply=("skipped", {}), verify=("skipped", {})), ""),
    ],
)
def test_the_verdict_is_the_templates_own_expression(steps: dict, verdict: str) -> None:
    piece = wfm.extract(TEXT).piece("verify.outputs")
    out = wfm.evaluate_outputs(piece, steps)
    assert out["verdict"] == verdict
    if verdict == "fail" and steps["verify"]["outcome"] == "failure":
        assert out["failed_step"] == "FAIL: test (exit 1)"


def test_a_verify_step_cannot_forge_a_pass() -> None:
    piece = wfm.extract(TEXT).piece("verify.outputs")
    forged = _steps(verify=("failure", {"verdict": "pass", "ok": "true"}))
    assert wfm.evaluate_outputs(piece, forged)["verdict"] == "fail"


@pytest.mark.parametrize(
    "expr, ctx, want",
    [
        ("'Pass' == 'pass'", {}, True),
        ("a.b != 'x'", {"a": {"b": "x"}}, False),
        ("a.missing || 'none'", {"a": {}}, "none"),
        ("a.b && 'yes' || 'no'", {"a": {"b": ""}}, "no"),
        ("!(a.b == 'x')", {"a": {"b": "y"}}, True),
        ("steps.s.outcome == 'success' && steps.s.outputs.ok == 'true' && 'pass' || ''",
         {"steps": {"s": {"outcome": "success", "outputs": {"ok": "true"}}}}, "pass"),
    ],
)
def test_expressions(expr: str, ctx: dict, want) -> None:
    assert wfm.Expr(expr).evaluate(ctx) == want


def test_steps_run_on_an_implicit_success() -> None:
    assert wfm.step_should_run(None, {}, job_failed=False) is True
    assert wfm.step_should_run(None, {}, job_failed=True) is False
    ctx = {"steps": {"paths": {"outputs": {"ok": "true"}}}}
    assert wfm.step_should_run("steps.paths.outputs.ok == 'true'", ctx, job_failed=False) is True
    assert wfm.step_should_run("steps.paths.outputs.ok == 'true'", ctx, job_failed=True) is False
    assert wfm.step_should_run("always() && steps.paths.outputs.ok == 'true'", ctx, job_failed=True) is True


def test_unsupported_functions_fail_closed() -> None:
    with pytest.raises(EvalError):
        wfm.Expr("contains(github.event.comment.body, '/approve')")


# --- rendering ----------------------------------------------------------------------------


def _ctx(**route) -> dict:
    outputs = {"issue": "3", "guarded": ".github .cadence scripts tool tests", "test_roots": "tests",
               "max_turns": "60", "per_run_usd": "5", **route}
    return {"needs": {"route": {"outputs": outputs}, "retry-gate": {"outputs": {"step": "test"}}},
            "runner": {"temp": "/home/runner/work/_temp"}, "github": {"repository": "eval/demo-app"}}


def test_render_the_agent_prompt_and_args() -> None:
    wf = wfm.extract(TEXT)
    prompt = wfm.render(wf.piece("agent.prompt").text, _ctx())
    assert "${{" not in prompt and "issue #3 in eval/demo-app" in prompt
    assert "/home/runner/work/_temp/cadence/input/spec.md" in prompt
    args = wfm.claude_args(wfm.render(wf.piece("agent.claude_args").text, _ctx()))
    assert args[:4] == ["--max-turns", "60", "--max-budget-usd", "5"]
    assert "Read,Write,Edit,Glob,Grep,Bash,Agent,Task,Skill,TodoWrite" in args
    retry = wfm.render(wf.piece("agent-retry.prompt").text, _ctx())
    assert "failed it at `test`" in retry
    empty = wfm.render(wf.piece("agent.prompt").text, _ctx(test_roots=""))
    assert "test roots: `none`" in empty
    intake = wfm.claude_args(wfm.render(wf.piece("intake.claude_args").text, _ctx()))
    assert "Read,Glob,Grep,Skill,Edit(//home/runner/work/_temp/cadence/output/**)" in intake


def test_render_refuses_unknown_or_missing_contexts() -> None:
    with pytest.raises(EvalError):
        wfm.render("${{ secrets.ANTHROPIC_API_KEY }}", _ctx())
    ctx = _ctx()
    del ctx["needs"]["route"]["outputs"]["issue"]
    with pytest.raises(EvalError):
        wfm.render("${{ needs.route.outputs.issue }}", ctx)


# --- rewrites -------------------------------------------------------------------------------


def test_rewrites_point_tools_at_the_pinned_copy_with_the_clock() -> None:
    wf = wfm.extract(TEXT)
    scan = wfm.rewrite_tools(wf.piece("observe.scan").text, "/opt/cadence-tools/tool", 1790000000)
    assert '"/opt/cadence-tools/tool/signals.py" observe --now 1790000000' in scan.text
    assert "$GITHUB_WORKSPACE/base/tool/signals.py" not in scan.text
    assert scan.counts["python"] == 1 and scan.counts["now"] == 1
    build = wfm.rewrite_tools(wf.piece("retro-plan.build").text, "/T", 5).text
    assert '"/T/ladder.py" guard --repo-root' in build  # guard takes no --now
    assert '"/T/metrics.py" report --now 5' in build
    assert "repo/tool/" not in build
    caps = wfm.rewrite_tools(wf.piece("route.caps").text, "/T", None)
    assert caps.counts["pip"] == 1 and caps.counts["sys_path"] == 1
    assert 'sys.path.insert(0, "/T")' in caps.text and "pip install" not in caps.text
    failed = wfm.rewrite_tools(wf.piece("retro-failed.record").text, "", None, "2026-11-01T00:00:00Z")
    assert "2026-11-01T00:00:00Z" in failed.text and "$(date" not in failed.text
    plan = wfm.rewrite_tools(wf.piece("retro-plan.plan").text, "/T", 7).text
    assert '"/T/ladder.py" plan --now 7' in plan


# --- flag parity ------------------------------------------------------------------------------


def _template_lines(tool: str) -> str:
    """The run scripts of every template step that runs ``tool``."""
    jobs = yaml.safe_load(TEXT)["jobs"]
    runs = [step.get("run") or "" for job in jobs.values() for step in job.get("steps") or []]
    return "\n".join(run for run in runs if tool in run)


def test_flag_parity_with_the_template() -> None:
    for call, flags in wfm.DIRECT_CALLS.items():
        script = call.split()[0]
        text = _template_lines(f"tool/{script}")
        assert text, f"the template never runs {script}"
        for flag in flags:
            assert re.search(re.escape(flag) + r"(?![A-Za-z0-9-])", text), f"{call}: {flag} is not in the template"


def test_eval_only_flags_exist_in_the_tools() -> None:
    for call, flags in wfm.EVAL_ONLY_CALLS.items():
        source = (TOOLS / call.split()[0]).read_text(encoding="utf-8")
        for flag in flags:
            assert f'"{flag}"' in source, f"{call}: {flag} is not a flag of the tool"
    for name in ("signals.py", "ladder.py", "metrics.py", "ledger.py"):
        assert '"--now"' in (TOOLS / name).read_text(encoding="utf-8")


def test_tool_argv_refuses_an_unlisted_flag() -> None:
    argv = wfm.tool_argv("/T", "signals.py put", "put", "--staged", "s", "--state", "t")
    assert argv == ["python3", "/T/tool/signals.py", "put", "--staged", "s", "--state", "t"]
    with pytest.raises(EvalError, match="flag parity"):
        wfm.tool_argv("/T", "signals.py put", "put", "--force")
    with pytest.raises(EvalError):
        wfm.tool_argv("/T", "claim.py acquire", "--issue", "1")

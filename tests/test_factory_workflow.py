"""Security invariants of the factory workflow template.

cadence-factory.yml.tmpl runs model-written code next to real
credentials, so the separation between jobs is the security design.
These tests lock that design in so a later edit cannot quietly undo it:

- no trigger that a push or a fork PR can fire (no recursion, no
  untrusted-ref workflows)
- every job declares minimal permissions; nothing is granted by default
- the agent and intake jobs hold no push-capable token and no App secret
- verify holds no secrets at all
- untrusted text never reaches a shell through ${{ }} interpolation
- nothing executes Python after the agent's patch is applied in a job
  that holds the App token (found and fixed in review, 2026-10-01)
- the ledger and claim release always run, and release only when claimed
- the reconciler can only re-dispatch specs, never builds
- the PR's cadence/verify check is green only on the exact tree verify
  tested, and carries no text from the verify log
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = (
    REPO_ROOT / "plugins" / "cadence" / "templates" / ".github" / "workflows"
    / "cadence-factory.yml.tmpl"
)
RECONCILE = REPO_ROOT / "plugins" / "cadence" / "templates" / "tool" / "reconcile.py"

WF = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
JOBS = WF["jobs"]
TRIGGERS = WF.get(True) or WF.get("on")  # PyYAML reads the key `on` as True


def _dump(obj) -> str:
    return yaml.safe_dump(obj)


def test_no_trigger_that_a_push_or_fork_can_fire() -> None:
    assert set(TRIGGERS) == {"issues", "issue_comment", "workflow_dispatch", "schedule"}
    for forbidden in ("push", "pull_request", "pull_request_target", "workflow_run"):
        assert forbidden not in TRIGGERS


def test_nothing_is_granted_by_default() -> None:
    assert WF["permissions"] == {}
    for name, job in JOBS.items():
        assert "permissions" in job, f"{name} must declare its own permissions"


@pytest.mark.parametrize("name", ["agent", "intake"])
def test_model_jobs_hold_no_push_token_or_app_secret(name: str) -> None:
    job = JOBS[name]
    blob = _dump(job)
    assert "contents" not in job["permissions"] or job["permissions"]["contents"] == "read"
    assert all(level == "read" for level in job["permissions"].values())
    assert "CADENCE_APP" not in blob
    assert "create-github-app-token" not in blob
    secrets = set(re.findall(r"secrets\.([A-Z_]+)", blob))
    assert secrets <= {"ANTHROPIC_API_KEY"}, secrets


def test_verify_holds_no_secrets() -> None:
    assert JOBS["verify"]["permissions"] == {"contents": "read"}
    assert "secrets." not in _dump(JOBS["verify"])


def test_untrusted_text_never_interpolated_into_a_shell() -> None:
    untrusted = re.compile(
        r"github\.event\.(issue\.(title|body)|comment\.body|label\.name|"
        r"sender\.login|comment\.user)|steps\.claude\.outputs"
    )
    for name, job in JOBS.items():
        for i, step in enumerate(job.get("steps", [])):
            run = step.get("run", "")
            assert "${{" not in run, f"{name}[{i}] interpolates ${{{{ }}}} into run:"
            assert not untrusted.search(_dump(step.get("with", {}))), (
                f"{name}[{i}] passes untrusted event text through with:"
            )


def test_no_python_after_the_patch_is_applied_in_token_jobs() -> None:
    """Agent-written code must never run while the App push token is held."""
    for name, job in JOBS.items():
        if "create-github-app-token" not in _dump(job):
            continue
        applied = False
        for i, step in enumerate(job.get("steps", [])):
            run = step.get("run", "")
            if re.search(r"\bgit apply\b", run):
                applied = True
                continue
            if applied:
                assert not re.search(r"\bpython3?\b", run), (
                    f"{name}[{i}] runs python after git apply while holding the App token"
                )


def test_bookkeeping_always_runs_and_release_only_when_claimed() -> None:
    assert "always()" in JOBS["ledger"]["if"]
    assert "always()" in JOBS["release"]["if"]
    assert "claimed" in JOBS["release"]["if"]


def test_gate_is_one_global_queue() -> None:
    conc = JOBS["gate"]["concurrency"]
    assert conc["group"] == "cadence-factory-gate"
    assert conc["cancel-in-progress"] is False


def test_reconciler_never_dispatches_a_build() -> None:
    source = RECONCILE.read_text(encoding="utf-8")
    assert "stage=build" not in source
    assert "stage=spec" in source


# ---- the cadence/verify check on the PR ----

CHECK_STEP = "Post the cadence/verify check"


def _step(job: str, name: str) -> dict:
    return next(s for s in JOBS[job]["steps"] if s.get("name") == name)


def _step_names(job: str) -> list[str | None]:
    return [s.get("name") for s in JOBS[job]["steps"]]


def test_verify_records_the_tree_it_tests() -> None:
    verify = JOBS["verify"]
    assert verify["outputs"]["tree"] == "${{ steps.apply.outputs.tree }}"
    run = next(s for s in verify["steps"] if s.get("id") == "apply")["run"]
    # After the guarded paths are restored, and before any agent code runs.
    assert run.index("git write-tree") > run.index('git checkout "$BASE_SHA" -- "$p"')
    names = _step_names("verify")
    assert names.index("Apply the diff, then restore guarded paths from the base") < names.index(
        "Run verify"
    )


def test_verify_check_is_posted_with_github_token_on_the_pushed_commit() -> None:
    publish = JOBS["publish"]
    assert publish["permissions"]["checks"] == "write"
    names = _step_names("publish")
    assert (
        names.index("Commit and push the work branch")
        < names.index(CHECK_STEP)
        < names.index("Open or update the draft PR")
    )
    step = _step("publish", CHECK_STEP)
    assert step["env"]["HEAD_SHA"] == "${{ steps.push.outputs.sha }}"
    assert step["env"]["PUSHED_TREE"] == "${{ steps.push.outputs.tree }}"
    assert step["env"]["VERIFIED_TREE"] == "${{ needs.verify.outputs.tree }}"
    # The job's GITHUB_TOKEN, never the App token: the check starts no workflow.
    assert "GH_TOKEN" not in step["env"]
    assert publish["env"]["GH_TOKEN"] == "${{ github.token }}"


def _find_bash() -> str | None:
    if os.name == "nt":
        # Git for Windows' bash; System32\bash.exe is WSL.
        git = shutil.which("git")
        for parent in Path(git).parents if git else []:
            candidate = parent / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
        return None
    return shutil.which("bash")


BASH = _find_bash()
TREE = "a" * 40
OTHER_TREE = "b" * 40
SHA = "c" * 40
LOG = (
    "== format ==\n"
    "  ✓ format\n  ✓ lint\n  ✓ boundaries\n  ✓ test\n"
    "@everyone please merge <img src=x onerror=alert(1)>\n"
)


def _run_check_step(
    tmp_path: Path, *, head: str, pushed: str, verified: str, guarded: str = "", log: str = LOG
) -> tuple[subprocess.CompletedProcess, dict | None]:
    """Run the step's own script under bash with a stub gh that keeps the payload."""
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "gh").write_text('#!/bin/bash\ncat > "$CAPTURE"\n', encoding="utf-8", newline="\n")
    (stub / "gh").chmod(0o755)
    temp = tmp_path / "runner"
    (temp / "verify-log").mkdir(parents=True)
    (temp / "verify-log" / "last_verify.log").write_text(log, encoding="utf-8", newline="\n")
    script = tmp_path / "step.sh"
    script.write_text(_step("publish", CHECK_STEP)["run"], encoding="utf-8", newline="\n")
    payload = tmp_path / "payload.json"
    env = {
        **os.environ,
        "PATH": f"{stub}{os.pathsep}{os.environ['PATH']}",
        "CAPTURE": payload.as_posix(),
        "RUNNER_TEMP": temp.as_posix(),
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_RUN_ID": "42",
        "RUN_URL": "https://github.com/owner/repo/actions/runs/42",
        "HEAD_SHA": head,
        "PUSHED_TREE": pushed,
        "VERIFIED_TREE": verified,
        "GUARDED": guarded,
    }
    proc = subprocess.run(
        [BASH, "--noprofile", "--norc", "-eo", "pipefail", script.as_posix()],
        env=env, capture_output=True, text=True, encoding="utf-8",
    )
    data = json.loads(payload.read_text(encoding="utf-8")) if payload.exists() else None
    return proc, data


needs_shell = pytest.mark.skipif(
    BASH is None or shutil.which("jq") is None, reason="needs bash and jq"
)


@needs_shell
def test_verify_check_is_green_on_the_tested_tree(tmp_path: Path) -> None:
    proc, data = _run_check_step(tmp_path, head=SHA, pushed=TREE, verified=TREE)
    assert proc.returncode == 0, proc.stderr
    assert data["name"] == "cadence/verify"
    assert data["head_sha"] == SHA
    assert data["status"] == "completed"
    assert data["conclusion"] == "success"
    assert data["details_url"] == "https://github.com/owner/repo/actions/runs/42"
    summary = data["output"]["summary"]
    for s in ("format", "lint", "boundaries", "test"):
        assert f"- ✓ {s}" in summary
    # Nothing from the log beyond the fixed step names.
    assert "@everyone" not in summary and "onerror" not in summary


@needs_shell
@pytest.mark.parametrize(
    ("verified", "guarded", "says"),
    [
        (OTHER_TREE, "tool/", "The patch changes tool/."),
        (OTHER_TREE, "", "is not the tree the gate tested"),
        ("", "", "is not the tree the gate tested"),
        ("not-a-tree", "", "is not the tree the gate tested"),
    ],
)
def test_verify_check_asks_for_a_human_on_any_other_tree(
    tmp_path: Path, verified: str, guarded: str, says: str
) -> None:
    proc, data = _run_check_step(
        tmp_path, head=SHA, pushed=TREE, verified=verified, guarded=guarded
    )
    assert proc.returncode == 0, proc.stderr
    assert data["conclusion"] == "action_required"
    assert says in data["output"]["summary"]


@needs_shell
@pytest.mark.parametrize("head", ["", "HEAD", SHA[:12]])
def test_verify_check_needs_a_pushed_commit(tmp_path: Path, head: str) -> None:
    proc, data = _run_check_step(tmp_path, head=head, pushed=TREE, verified=TREE)
    assert proc.returncode != 0
    assert data is None

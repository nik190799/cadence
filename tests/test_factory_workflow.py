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
"""

from __future__ import annotations

import re
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

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

The learning loop (docs/LEARNING.md) adds:

- observe, harvest and retro-plan read untrusted data with read-only
  tokens and no secrets; classify holds ANTHROPIC_API_KEY and nothing
  else, and no shell
- only a fixed set of jobs mints the App token
- the ladder's writer (emit_rule.py, ladder.py apply) runs in retro-plan
  only, in one serialized retro queue
- retro-publish re-runs the guard on the patch before it applies it,
  stages only the retro allowlist, and auto-merges only behind all three
  eval-sandbox switches
- observe's report reaches the ledger only through job outputs and env
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


# --- The learning loop (docs/LEARNING.md) ------------------------------------

LEARN_CHAIN = ("harvest", "classify", "learn-record", "retro-plan", "retro-publish")
APP_TOKEN_JOBS = {
    "gate", "publish", "ledger", "release", "reconcile", "learn-record", "retro-publish",
}
RETRO_ALLOWLIST = (
    ".cadence/cadence.yaml", ".cadence/lessons.yaml", "docs/PATTERNS.md", "tests/fixtures/retro",
)


def _runs(job: dict) -> list[str]:
    return [step.get("run", "") for step in job.get("steps", [])]


def _step(job: dict, name_prefix: str) -> dict:
    matches = [s for s in job["steps"] if s.get("name", "").startswith(name_prefix)]
    assert len(matches) == 1, f"expected one step named {name_prefix!r}"
    return matches[0]


def _uses(job: dict, action: str) -> dict:
    return next(s for s in job["steps"] if s.get("uses", "").startswith(action))


def _jq_predicates(job: dict) -> list[str]:
    found = []
    for run in _runs(job):
        for match in re.finditer(r"--jq '(\(any\(\.jobs\[\].*?)'", run):
            found.append(" ".join(match.group(1).split()))
    return found


def _secrets(job: dict) -> set[str]:
    return set(re.findall(r"secrets\.([A-Z_]+)", _dump(job)))


def test_jobs_of_the_learning_loop_exist() -> None:
    for name in ("observe", *LEARN_CHAIN):
        assert name in JOBS, name


def test_dispatch_offers_the_learn_stage_and_names_the_run() -> None:
    stage = TRIGGERS["workflow_dispatch"]["inputs"]["stage"]
    assert stage["options"] == ["spec", "build", "reconcile", "learn"]
    assert "(inputs.stage == 'learn' && 'learn')" in WF["run-name"]
    # Still one hourly schedule: the sweep, then the learn chain when due.
    assert [entry["cron"] for entry in TRIGGERS["schedule"]] == ["17 * * * *"]


def test_route_skips_learn_dispatches() -> None:
    assert "inputs.stage != 'learn'" in JOBS["route"]["if"]
    assert "inputs.stage != 'reconcile'" in JOBS["route"]["if"]


def test_learn_chain_never_runs_on_issue_events() -> None:
    cond = JOBS["harvest"]["if"]
    assert "github.event_name == 'schedule'" in cond
    assert "needs.reconcile.outputs.learn_due == 'true'" in cond
    assert "inputs.stage == 'learn'" in cond
    assert "github.event.repository.default_branch" in cond
    assert "'issues'" not in cond and "'issue_comment'" not in cond
    assert JOBS["harvest"]["needs"] == ["reconcile"]
    assert JOBS["classify"]["needs"] == ["harvest"]
    assert JOBS["learn-record"]["needs"] == ["harvest", "classify"]
    assert JOBS["retro-plan"]["needs"] == ["learn-record"]
    assert JOBS["retro-publish"]["needs"] == ["retro-plan"]
    assert "needs.harvest.result == 'success'" in JOBS["learn-record"]["if"]
    assert "needs.learn-record.result == 'success'" in JOBS["retro-plan"]["if"]
    assert JOBS["retro-publish"]["if"] == "needs.retro-plan.outputs.changed == 'true'"
    assert JOBS["classify"]["if"] == "needs.harvest.outputs.llm_allowed == 'true'"
    assert JOBS["reconcile"]["outputs"]["learn_due"] == "${{ steps.learn.outputs.learn_due }}"


@pytest.mark.parametrize("name", ["observe", "harvest", "retro-plan"])
def test_untrusted_readers_hold_no_secrets_and_read_only_tokens(name: str) -> None:
    job = JOBS[name]
    assert job["permissions"], f"{name} must declare what it reads"
    assert all(level == "read" for level in job["permissions"].values()), job["permissions"]
    assert "secrets." not in _dump(job)
    assert "create-github-app-token" not in _dump(job)


def test_observe_is_read_only_and_never_runs_the_patch() -> None:
    job = JOBS["observe"]
    assert job["permissions"] == {"contents": "read"}
    assert "always()" in job["if"] and "needs.agent.result != 'skipped'" in job["if"]
    checkout = _uses(job, "actions/checkout")
    assert checkout["with"]["persist-credentials"] is False
    assert checkout["with"]["path"] == "base"
    scan = _step(job, "Scan the attempt")["run"]
    # Base tools, isolated from the environment and from the scanned tree.
    assert 'python -I "$GITHUB_WORKSPACE/base/tool/signals.py" observe' in scan
    assert '--work-dir "$RUNNER_TEMP/work"' in scan
    for run in _runs(job):
        assert not re.search(r"(bash|sh|python3?)\s+[^\n]*\$RUNNER_TEMP/work/", run)
        assert "verify.sh" not in run
    # The report leaves as a job output, never as something the agent could swap.
    outputs = job["outputs"]
    assert {"bundle", "bundle_sha256", "patch_sha256"} <= set(outputs)
    assert all(v.startswith("${{ steps.observe.outputs.") for v in outputs.values())


def test_ledger_reads_observe_only_through_env() -> None:
    job = JOBS["ledger"]
    assert "observe" in job["needs"] and "publish" in job["needs"]
    env = job["env"]
    assert env["OBS_B64"] == "${{ needs.observe.outputs.bundle }}"
    assert env["OBS_SHA"] == "${{ needs.observe.outputs.bundle_sha256 }}"
    for name, other in JOBS.items():
        for i, step in enumerate(other.get("steps", [])):
            assert "needs.observe" not in step.get("run", ""), f"{name}[{i}]"
    stage = _step(job, "Stage the observation")["run"]
    assert "signals.py finalize" in stage and "--bundle-sha256" in stage
    # Every slice of the bundle is passed on and reassembled.
    slices = [k for k in JOBS["observe"]["outputs"] if re.fullmatch(r"bundle(_[2-9])?", k)]
    assert len(slices) == 6
    for key in slices:
        var = "OBS_B64" if key == "bundle" else "OBS_B64_" + key.split("_")[1]
        assert env[var] == "${{ needs.observe.outputs.%s }}" % key
        assert f'"${var}"' in stage


def test_state_branch_writes_are_create_only() -> None:
    for name in ("ledger", "learn-record"):
        push = _step(JOBS[name], "Push to cadence/state")["run"]
        assert 'signals.py" put --staged' in push
        assert "git add -A" not in push
        assert "for attempt in 1 2 3 4 5" in push
        assert "sleep $((attempt * 3 + RANDOM % 5))" in push


def test_classify_holds_only_the_model_key_and_no_shell() -> None:
    job = JOBS["classify"]
    assert job["permissions"] == {"contents": "read"}
    assert _secrets(job) == {"ANTHROPIC_API_KEY"}
    assert "create-github-app-token" not in _dump(job)
    assert _uses(job, "actions/checkout")["with"]["persist-credentials"] is False
    claude = _uses(job, "anthropics/claude-code-action")
    assert claude["env"]["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] == "1"
    assert claude["with"]["prompt"].startswith("/cadence:cadence-findings")
    args = claude["with"]["claude_args"]
    allowed = re.search(r'--allowedTools "([^"]*)"', args).group(1)
    disallowed = re.search(r'--disallowedTools "([^"]*)"', args).group(1)
    allowed_tools = [t.split("(")[0] for t in allowed.split(",")]
    assert "Bash" not in allowed_tools
    assert set(allowed_tools) <= {"Read", "Glob", "Grep", "Skill", "Edit"}
    for tool in ("Bash", "WebFetch", "WebSearch", "Agent", "Task", "mcp__*"):
        assert tool in disallowed.split(","), tool
    assert "--max-turns 6" in args
    assert "--max-budget-usd ${{ needs.harvest.outputs.learn_per_run_usd }}" in args
    # The model writes under learn/out only; only classify.json is carried on.
    keep = _step(job, "Keep the labels")["run"]
    assert 'cp "$labels" "$out/classify.json"' in keep
    assert 'out="$RUNNER_TEMP/classify-result"' in keep


def test_only_the_bookkeeping_jobs_mint_the_app_token() -> None:
    minting = {name for name, job in JOBS.items() if "create-github-app-token" in _dump(job)}
    assert minting == APP_TOKEN_JOBS
    holders = {name for name, job in JOBS.items() if "CADENCE_APP_PRIVATE_KEY" in _dump(job)}
    assert holders == APP_TOKEN_JOBS


def test_the_ladder_writer_runs_only_in_retro_plan() -> None:
    for name, job in JOBS.items():
        blob = "\n".join(_runs(job))
        if name == "retro-plan":
            assert "ladder.py apply" in blob
            continue
        assert "emit_rule" not in blob, name
        assert "ladder.py apply" not in blob, name


def test_retro_jobs_share_one_serialized_queue() -> None:
    for name in ("retro-plan", "retro-publish"):
        conc = JOBS[name]["concurrency"]
        assert conc["group"] == "cadence-factory-retro", name
        assert conc["cancel-in-progress"] is False, name
        assert conc.get("queue") == "max", name


def test_harvest_waits_in_the_gate_queue() -> None:
    conc = JOBS["harvest"]["concurrency"]
    assert conc["group"] == "cadence-factory-gate"
    assert conc["cancel-in-progress"] is False


def test_gate_and_harvest_count_runs_in_flight_the_same_way() -> None:
    gate = _jq_predicates(JOBS["gate"])
    harvest = _jq_predicates(JOBS["harvest"])
    assert len(gate) == 1 and len(harvest) == 1
    assert gate == harvest
    for job_name in ('"gate"', '"intake"', '"ledger"', '"classify"', '"learn-record"'):
        assert job_name in gate[0], job_name


def test_retro_plan_runs_main_code_without_credentials() -> None:
    job = JOBS["retro-plan"]
    assert job["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert _uses(job, "actions/checkout")["with"]["persist-credentials"] is False
    # The token is on the read step only, never in the job's environment.
    assert "GH_TOKEN" not in job.get("env", {})
    holders = [s.get("name") for s in job["steps"] if "github.token" in _dump(s)]
    assert holders == ["Read cadence/state and the open retro plan"]
    assert "env" not in _step(job, "Run verify on the result")


def test_retro_plan_keeps_python_caches_out_of_the_guard() -> None:
    """emit_rule.py runs under ``python -I`` (no PYTHONDONTWRITEBYTECODE), so
    tool/__pycache__ appears in repo/; without the local exclude, ``ladder.py
    guard --worktree`` refuses it as a path outside the retro allowlist."""
    steps = JOBS["retro-plan"]["steps"]
    exclude_at = next(
        i for i, s in enumerate(steps) if "repo/.git/info/exclude" in s.get("run", "")
    )
    assert "__pycache__/" in steps[exclude_at]["run"]
    first_python = next(i for i, s in enumerate(steps) if re.search(r"\bpython\b", s.get("run", "")))
    assert exclude_at < first_python


def test_retro_patch_stages_only_the_allowlist() -> None:
    build = _step(JOBS["retro-plan"], "Guard, then build")["run"]
    guard_at = build.index("ladder.py guard --repo-root repo --worktree")
    add_at = build.index("git -C repo add --")
    assert guard_at < add_at
    listed = re.search(r"for p in ([^;]+); do", build).group(1).split()
    assert tuple(listed) == RETRO_ALLOWLIST
    assert "git -C repo diff --cached --binary" in build


def test_retro_publish_guards_the_patch_before_applying_it() -> None:
    job = JOBS["retro-publish"]
    runs = _runs(job)
    guard = next(i for i, r in enumerate(runs) if "ladder.py guard" in r)
    apply = next(i for i, r in enumerate(runs) if re.search(r"\bgit apply\b", r))
    assert guard < apply
    assert "--patch" in runs[guard] and "--applied" in runs[guard]
    assert "python -I repo/tool/ladder.py guard" in runs[guard]
    push = runs[apply]
    assert "git checkout --quiet -B cadence/retro" in push
    assert '--force-with-lease="refs/heads/cadence/retro:$old"' in push
    assert '!= "$email"' in push  # a human's push to cadence/retro is never overwritten
    assert "Cadence-Retro-Plan: $PLAN_SHA" in push
    assert "gh pr create" in push and "--draft" not in push
    assert "gh pr merge" not in push


def test_retro_auto_merge_needs_all_three_switches() -> None:
    merges = [
        (name, s) for name, job in JOBS.items() for s in job.get("steps", [])
        if "gh pr merge" in s.get("run", "")
    ]
    assert len(merges) == 1, "exactly one step in the whole workflow may merge"
    name, merge = merges[0]
    assert name == "retro-publish"
    assert "needs.retro-plan.outputs.mode == 'eval-sandbox'" in merge["if"]
    assert "vars.CADENCE_EVAL_SANDBOX == 'true'" in merge["if"]
    assert "isPrivate" in merge["run"]
    assert '--match-head-commit "$HEAD_SHA"' in merge["run"]


def test_verify_reads_the_guarded_paths_before_the_patch() -> None:
    steps = JOBS["verify"]["steps"]
    paths_at = next(i for i, s in enumerate(steps) if s.get("id") == "paths")
    apply_at = next(i for i, s in enumerate(steps) if s.get("id") == "apply")
    python_at = next(
        i for i, s in enumerate(steps) if s.get("uses", "").startswith("actions/setup-python")
    )
    assert python_at < paths_at < apply_at
    paths = steps[paths_at]["run"]
    assert "python tool/signals.py config" in paths
    assert "learning.guarded_paths" in paths and "learning.test_roots" in paths
    assert "^[A-Za-z0-9_.-]{1,64}$" in paths
    apply = steps[apply_at]
    assert apply["env"]["GUARDED"] == "${{ steps.paths.outputs.guarded }}"
    assert apply["env"]["TEST_ROOTS"] == "${{ steps.paths.outputs.test_roots }}"
    assert "for p in tests .github" not in apply["run"]  # no hard-coded list any more
    assert "under .github/workflows" in apply["run"]  # the workflow policy is unchanged


def test_publish_posts_only_a_fixed_failed_step_word() -> None:
    report = _step(JOBS["publish"], "Report the Definition of Done failure")["run"]
    assert 'case "$FAILED_STEP" in' in report
    assert '"$step"' in report
    assert '"${FAILED_STEP' not in report
    words = set(re.findall(r'\) step="?([a-z]+)"? ;;', report))
    assert words == {
        "empty", "apply", "policy", "format", "lint", "boundaries", "test", "timeout", "unknown",
    }


def test_publish_reports_the_pr_and_the_published_commit() -> None:
    outputs = JOBS["publish"]["outputs"]
    assert outputs["pr_number"] == "${{ steps.pr.outputs.pr_number }}"
    assert outputs["published_sha"] == "${{ steps.push.outputs.published_sha }}"


def test_agent_prompt_names_both_test_roots() -> None:
    claude = next(s for s in JOBS["agent"]["steps"] if s.get("id") == "claude")
    prompt = claude["with"]["prompt"]
    assert (
        "Existing files under tests/, test/, .github/, .cadence/, scripts/ and tool/ are restored"
        in prompt
    )
    assert "You may add new test files under tests/ or test/." in prompt

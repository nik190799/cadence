"""Tests for the factory workflow renderer (tool/render_factory_workflow.py).

The renderer fills the five runtime setup slots of cadence-factory.yml.tmpl
(agent, agent-retry, verify, verify-retry, retro-plan) from a stack profile.
A rendered workflow is what actually runs next to real credentials, so
every one of them must keep the template's security design. These tests:

- render four repository shapes: a Python service in server/ plus a Node
  web app in web/, a single-package Node repo, a Python-only repo on
  another Python version, and a repo that needs custom steps (pnpm, Go);
- re-run the invariants of test_factory_workflow.py against each rendered
  file (the checks are imported and pointed at the rendered workflow; the
  few left out are listed in EXCLUDED with the reason);
- check what the renderer adds: every uses: pinned to a SHA with its tag,
  the verify guard, the retro-plan repo/ prefix, and nothing else changed;
- check what it refuses, and its command line.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "plugins" / "cadence" / "templates" / "tool" / "render_factory_workflow.py"
TEMPLATE = (
    REPO_ROOT / "plugins" / "cadence" / "templates" / ".github" / "workflows"
    / "cadence-factory.yml.tmpl"
)
CHECKS_FILE = Path(__file__).with_name("test_factory_workflow.py")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, name
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rft = _load_module("cadence_render_factory_workflow", TOOL)
# A private copy of the template's invariants: its module globals (WORKFLOW,
# WF, JOBS, TRIGGERS) are pointed at a rendered file for each case below.
CHECKS = _load_module("cadence_factory_workflow_checks", CHECKS_FILE)

TEMPLATE_TEXT = TEMPLATE.read_text(encoding="utf-8")
TEMPLATE_DATA = yaml.safe_load(TEMPLATE_TEXT)
SHA = "0123456789abcdef0123456789abcdef01234567"  # well-formed; never fetched
SETUP_PYTHON_SHA = re.search(r"actions/setup-python@([0-9a-f]{40})", TEMPLATE_TEXT).group(1)
SETUP_NODE_SHA = "49933ea5288caeca8642d1e84afbd3f7d6820020"

PROFILES = {
    # A Python service whose tests live in server/tests and a Vite web app
    # in web/, on the template's own Python.
    "python-service-and-web-app": {
        "python": {"version": "3.12", "requirements": ["server/requirements-dev.txt"]},
        "node": {"version": "20", "dirs": ["web"], "lockfiles": ["web/package-lock.json"]},
    },
    # A single-package TypeScript repo: setup-node and npm ci at the root.
    "single-package-node": {"node": {"version": "20", "dirs": ["."]}},
    # A Python-only repo on another Python than the template's.
    "python-only": {
        "python": {"version": "3.11", "requirements": ["requirements-dev.txt"], "editable": ["."]},
    },
    # The escape hatch: pnpm through corepack, plus a pinned action and a
    # run step of the project's own.
    "custom-steps": {
        "node": {"version_file": ".nvmrc", "dirs": ["app"], "package_manager": "pnpm"},
        "custom": [
            {"name": "Set up Go", "uses": f"actions/setup-go@{SHA}", "tag": "v5",
             "with": {"go-version-file": "tools/go.mod", "cache": False},
             "if": "runner.os == 'Linux'"},
            {"name": "Build the shared package", "run": "make build\nmake check",
             "working-directory": "shared", "env": {"CI_LEVEL": "full"}},
        ],
    },
}


def _template():
    return rft.load_template(TEMPLATE_TEXT)


def _render(data: dict):
    template = _template()
    text, plan, pins = rft.render(template, rft.load_profile(data))
    rft.check_rendered(template, text, plan)
    return text, plan, pins


@pytest.fixture(scope="module")
def rendered(tmp_path_factory) -> dict[str, Path]:
    """Each profile rendered by the command-line entry point, as the setup
    skill runs it."""
    base = tmp_path_factory.mktemp("rendered")
    out = {}
    for name, data in PROFILES.items():
        profile = base / f"{name}.yaml"
        profile.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        path = base / f"{name}.yml"
        code = rft.main(["--template", str(TEMPLATE), "--profile", str(profile),
                         "--out", str(path), "--repo-root", str(base)])
        assert code == 0, name
        out[name] = path
    return out


_PARSED: dict[Path, dict] = {}


def _parsed(path: Path) -> dict:
    """The rendered workflow, parsed once: the checks only read it, as they
    read the template's one parsed copy."""
    if path not in _PARSED:
        _PARSED[path] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _PARSED[path]


def _jobs(path: Path) -> dict:
    return _parsed(path)["jobs"]


def _stack(job: dict) -> list[dict]:
    return [s for s in job["steps"] if str(s.get("name", "")).startswith("Stack: ")]


# --- the template's invariants, re-run against every rendered workflow -------

# Left out, with the reason. Everything else in test_factory_workflow.py runs.
EXCLUDED = {
    "test_retro_plan_demotes_then_verifies_once_more": (
        "template-only: it asserts that retro-plan's own steps sit next to each other, "
        "and the retro-plan slot lies between 'Plan the ladder' and 'Apply the plan' by "
        "design; test_stack_steps_fill_the_slots_and_change_nothing_else checks the "
        "rendered order, and every other assertion of that test is about template steps "
        "that rendering never changes"
    ),
    "test_the_ci_template_reads_only": "reads the CI template, which this tool does not render",
}
CI_REASON = "the CI template (cadence.yml.tmpl), which this tool does not render"
# The checks that run step scripts under bash. Each runs one template step's
# own script, found by its id or name, and rendering never changes a template
# step (test_stack_steps_fill_the_slots_and_change_nothing_else), so its result
# is the template's. These walk a whole slot job, the stack steps included,
# and run again here.
SHELL_RERUN = {
    "test_an_empty_diff_ends_green_with_a_dod_failed_issue",
    "test_a_failing_verify_sh_ends_green_and_cannot_forge_a_pass",
    "test_a_verify_job_that_did_not_finish_stays_red_and_reads_as_did_not_finish",
}
SHELL_REASON = (
    "runs one template step's own script by its id or name; rendering never changes a "
    "template step, so the result is the template's (test_factory_workflow.py runs it)"
)


def _param_sets(fn) -> tuple[list[tuple[tuple[str, ...], dict]], str | None]:
    """Expand a test's @pytest.mark.parametrize marks; return its skip reason."""
    sets: list[tuple[tuple[str, ...], dict]] = [((), {})]
    skip = None
    for mark in getattr(fn, "pytestmark", []):
        if mark.name == "skipif" and mark.args and mark.args[0]:
            skip = mark.kwargs.get("reason", "skipped")
        if mark.name != "parametrize":
            continue
        argnames, argvalues = mark.args[0], mark.args[1]
        names = (
            [n.strip() for n in argnames.split(",")] if isinstance(argnames, str) else list(argnames)
        )
        ids = mark.kwargs.get("ids")
        expanded = []
        for i, value in enumerate(argvalues):
            if hasattr(value, "values") and hasattr(value, "marks"):  # pytest.param
                values = tuple(value.values)
            else:
                values = (value,) if len(names) == 1 else tuple(value)
            label = str(ids[i]) if ids else str(i)
            for base_ids, base_kwargs in sets:
                expanded.append((base_ids + (label,), {**base_kwargs, **dict(zip(names, values))}))
        sets = expanded
    return sets, skip


def _reused_cases() -> tuple[list[tuple[str, str, dict, str | None]], dict[str, str]]:
    run: list[tuple[str, str, dict, str | None]] = []
    excluded: dict[str, str] = {}
    for name, fn in sorted(vars(CHECKS).items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        uses_shell = "tmp_path" in inspect.signature(fn).parameters
        sets, skip = _param_sets(fn)
        for ids, kwargs in sets:
            case = name + (f"[{'-'.join(ids)}]" if ids else "")
            if name in EXCLUDED:
                excluded[case] = EXCLUDED[name]
            elif kwargs.get("path") == CHECKS.CI_WORKFLOW:
                excluded[case] = CI_REASON
            elif uses_shell and name not in SHELL_RERUN:
                excluded[case] = SHELL_REASON
            else:
                run.append((case, name, kwargs, skip))
    return run, excluded


REUSED, EXCLUDED_CASES = _reused_cases()


def test_every_template_check_is_rerun_or_left_out_for_a_reason() -> None:
    names = {case.split("[")[0] for case, *_ in REUSED} | {
        case.split("[")[0] for case in EXCLUDED_CASES
    }
    assert names == {n for n in vars(CHECKS) if n.startswith("test_")}
    assert all(reason.strip() for reason in EXCLUDED_CASES.values())
    # The structural checks all run: only the shell checks that run a single
    # template step's script, the CI template and the adjacency test are out.
    left_out = {case.split("[")[0] for case in EXCLUDED_CASES} - set(EXCLUDED)
    for name in left_out:
        fn = getattr(CHECKS, name)
        assert "tmp_path" in inspect.signature(fn).parameters or name == (
            "test_every_action_is_pinned_to_a_commit_sha"
        ), name
    assert SHELL_RERUN <= {case.split("[")[0] for case, *_ in REUSED}
    assert len(REUSED) >= 150, len(REUSED)


@pytest.mark.parametrize("profile", sorted(PROFILES))
@pytest.mark.parametrize(("case", "fn_name", "kwargs", "skip"), REUSED, ids=[c[0] for c in REUSED])
def test_the_rendered_workflow_keeps_the_templates_invariants(
    profile: str, case: str, fn_name: str, kwargs: dict, skip: str | None,
    rendered: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if skip:
        pytest.skip(skip)
    path = rendered[profile]
    wf = _parsed(path)
    monkeypatch.setattr(CHECKS, "WORKFLOW", path)
    monkeypatch.setattr(CHECKS, "WF", wf)
    monkeypatch.setattr(CHECKS, "JOBS", wf["jobs"])
    monkeypatch.setattr(CHECKS, "TRIGGERS", wf.get(True) or wf.get("on"))
    fn = getattr(CHECKS, fn_name)
    args = dict(kwargs)
    if "path" in args:  # test_every_action_is_pinned_to_a_commit_sha[factory]
        args["path"] = path
    if "tmp_path" in inspect.signature(fn).parameters:
        args["tmp_path"] = tmp_path
    fn(**args)


# --- what the renderer adds ----------------------------------------------------


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_stack_steps_fill_the_slots_and_change_nothing_else(
    profile: str, rendered: dict[str, Path]
) -> None:
    data = yaml.safe_load(rendered[profile].read_text(encoding="utf-8"))
    assert {k: v for k, v in data.items() if k != "jobs"} == {
        k: v for k, v in TEMPLATE_DATA.items() if k != "jobs"
    }
    assert list(data["jobs"]) == list(TEMPLATE_DATA["jobs"])
    anchors = {
        "agent": lambda s: s.get("run", "").startswith("python -m pip install"),
        "agent-retry": lambda s: s.get("run", "").startswith("python -m pip install"),
        "verify": lambda s: s.get("id") == "apply",
        "verify-retry": lambda s: s.get("id") == "apply",
        "retro-plan": lambda s: s.get("id") == "plan",
    }
    for name, job in data["jobs"].items():
        base = TEMPLATE_DATA["jobs"][name]
        steps = job["steps"]
        at = [i for i, s in enumerate(steps) if str(s.get("name", "")).startswith("Stack: ")]
        # Every template step, byte for byte and in order; nothing else moved.
        assert [s for i, s in enumerate(steps) if i not in at] == base["steps"], name
        assert {k: v for k, v in job.items() if k != "steps"} == {
            k: v for k, v in base.items() if k != "steps"
        }, name
        if name not in anchors:
            assert at == [], name
            continue
        assert at, (profile, name)
        assert at == list(range(at[0], at[0] + len(at))), name
        assert anchors[name](steps[at[0] - 1]), name
    # The retro-plan slot: right after the plan, before anything applies it.
    names = [s.get("name") for s in data["jobs"]["retro-plan"]["steps"]]
    stack = [n for n in names if n and n.startswith("Stack: ")]
    assert names.index(stack[0]) == names.index("Plan the ladder") + 1
    assert names.index(stack[-1]) + 1 == names.index("Apply the plan")


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_every_uses_is_pinned_to_a_sha_with_its_tag(profile: str, rendered: dict[str, Path]) -> None:
    text = rendered[profile].read_text(encoding="utf-8")
    shas: dict[str, set[str]] = {}
    for line in text.splitlines():
        if "uses:" in line and not line.lstrip().startswith("#"):
            assert rft.PINNED_LINE.match(line), line
            action, ref = re.search(r"uses:\s+(\S+)@(\S+)", line).groups()
            shas.setdefault(action, set()).add(ref)
    assert all(len(refs) == 1 for refs in shas.values()), shas
    for job in _jobs(rendered[profile]).values():
        for step in _stack(job):
            if "uses" in step:
                assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", step["uses"]), step


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_verify_steps_carry_the_guard_and_match_verify_retry(
    profile: str, rendered: dict[str, Path]
) -> None:
    jobs = _jobs(rendered[profile])
    verify, retry = _stack(jobs["verify"]), _stack(jobs["verify-retry"])
    assert verify and verify == retry
    for step in verify:
        assert step["if"].startswith("steps.apply.outputs.ok == 'true'"), step
        assert "continue-on-error" not in step
    assert _stack(jobs["agent"]) == _stack(jobs["agent-retry"])
    for step in _stack(jobs["agent"]):
        assert "if" not in step or "steps.apply" not in step["if"]


@pytest.mark.parametrize("profile", sorted(PROFILES))
@pytest.mark.parametrize(
    ("job", "marker"),
    [("verify", ("apply", "ok")), ("verify-retry", ("apply", "ok")),
     ("retro-plan", ("plan", "changed"))],
)
def test_the_guards_hold_under_the_expression_model(
    profile: str, job: str, marker: tuple[str, str], rendered: dict[str, Path]
) -> None:
    """Evaluated as the runner does (implicit success()): no stack step runs
    while the marker is not 'true', whatever the step's own `if` says."""
    sid, key = marker
    for value, runs in (("", False), ("false", False), ("true", True)):
        ctx = {
            "steps": {sid: {"outcome": "success", "conclusion": "success",
                            "outputs": {key: value}}},
            "runner": {"os": "Linux"},
            "__status__": {"success": True, "failure": False, "cancelled": False},
        }
        for step in _stack(_jobs(rendered[profile])[job]):
            assert CHECKS._if_holds(step["if"], ctx) is runs, (job, value, step["name"])


def test_the_guard_check_reads_the_whole_condition() -> None:
    guard = rft.VERIFY_GUARD
    assert rft._guarded(guard, guard)
    assert rft._guarded(f"{guard} && (runner.os == 'Linux')", guard)
    assert rft._guarded(f"{guard} && (a == ')' || (b))", guard)
    for cond in (
        f"{guard} && (false) || (true)",  # the guard's parenthesis closed early
        f"{guard} || true",
        f"{guard} && ()",
        f"{guard} && (a == 'x)",
        f"true || {guard}",
        None,
    ):
        assert not rft._guarded(cond, guard), cond


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_retro_plan_steps_point_into_repo(profile: str, rendered: dict[str, Path]) -> None:
    for step in _stack(_jobs(rendered[profile])["retro-plan"]):
        assert step["if"].startswith("steps.plan.outputs.changed == 'true'"), step
        if "run" in step:
            wd = step["working-directory"]
            assert wd == "repo" or wd.startswith("repo/"), step
        for key, value in (step.get("with") or {}).items():
            if key.endswith("-version-file") or key == "cache-dependency-path":
                assert all(p.startswith("repo/") for p in str(value).split("\n") if p), step


def test_a_python_service_and_a_web_app() -> None:
    _, plan, pins = _render(PROFILES["python-service-and-web-app"])
    agent = plan["agent"]
    # The template already sets up Python 3.12: only the requirements.
    assert [s["name"] for s in agent] == [
        "Stack: install Python dependencies", "Stack: set up Node.js 20", "Stack: npm ci in web",
    ]
    assert agent[0]["run"] == (
        "python -m pip install --quiet --disable-pip-version-check -r server/requirements-dev.txt"
    )
    assert agent[1]["uses"] == f"actions/setup-node@{SETUP_NODE_SHA}"
    assert agent[1]["with"] == {
        "node-version": "20", "cache": "npm", "cache-dependency-path": "web/package-lock.json",
    }
    assert agent[2] == {"name": "Stack: npm ci in web", "working-directory": "web", "run": "npm ci"}
    assert pins["actions/setup-node"] == (SETUP_NODE_SHA, "v4")
    retro = plan["retro-plan"]
    assert retro[0]["working-directory"] == "repo"
    assert "-r server/requirements-dev.txt" in retro[0]["run"]
    assert retro[1]["with"]["cache-dependency-path"] == "repo/web/package-lock.json"
    assert retro[2]["working-directory"] == "repo/web"
    for step in plan["verify"]:
        assert step["if"] == "steps.apply.outputs.ok == 'true'"


def test_a_single_package_node_repo() -> None:
    _, plan, _ = _render(PROFILES["single-package-node"])
    assert plan["agent"] == [
        {"name": "Stack: set up Node.js 20", "uses": f"actions/setup-node@{SETUP_NODE_SHA}",
         "with": {"node-version": "20", "cache": "npm",
                  "cache-dependency-path": "package-lock.json"}},
        {"name": "Stack: npm ci in the repository root", "run": "npm ci"},
    ]
    retro = plan["retro-plan"]
    assert retro[0]["with"]["cache-dependency-path"] == "repo/package-lock.json"
    assert retro[1]["working-directory"] == "repo"


def test_a_python_only_repo_on_another_python() -> None:
    _, plan, _ = _render(PROFILES["python-only"])
    setup, install = plan["agent"]
    # The template's own pin, the project's version, set up after the
    # template's Python so verify.sh runs on it; with the tools' PyYAML.
    assert setup["uses"] == f"actions/setup-python@{SETUP_PYTHON_SHA}"
    assert setup["with"] == {"python-version": "3.11"}
    assert install["run"] == (
        'python -m pip install --quiet --disable-pip-version-check "pyyaml>=6,<7" '
        '"jsonschema>=4.18,<5" -r requirements-dev.txt -e .'
    )
    agent_steps = [s.get("name") or s.get("uses") or s.get("run") for s in
                   yaml.safe_load(_render(PROFILES["python-only"])[0])["jobs"]["agent"]["steps"]]
    template_python = next(i for i, n in enumerate(agent_steps)
                           if str(n).startswith("actions/setup-python@"))
    assert agent_steps.index("Stack: set up Python 3.11") > template_python
    assert plan["retro-plan"][1]["working-directory"] == "repo"


def test_custom_steps_get_the_guard_and_the_repo_prefix() -> None:
    _, plan, pins = _render(PROFILES["custom-steps"])
    agent = [s["name"] for s in plan["agent"]]
    assert agent == [
        "Stack: set up Node.js (version from .nvmrc)", "Stack: enable corepack",
        "Stack: pnpm install --frozen-lockfile in app", "Stack: Set up Go",
        "Stack: Build the shared package",
    ]
    # pnpm: no setup-node cache (it needs pnpm before setup-node).
    assert plan["agent"][0]["with"] == {"node-version-file": ".nvmrc"}
    go = plan["verify"][3]
    assert go["if"] == "steps.apply.outputs.ok == 'true' && (runner.os == 'Linux')"
    assert go["with"] == {"go-version-file": "tools/go.mod", "cache": False}
    assert "tag" not in go and "slots" not in go
    retro = {s["name"]: s for s in plan["retro-plan"]}
    assert retro["Stack: Set up Go"]["with"]["go-version-file"] == "repo/tools/go.mod"
    assert retro["Stack: set up Node.js (version from .nvmrc)"]["with"] == {
        "node-version-file": "repo/.nvmrc"
    }
    build = retro["Stack: Build the shared package"]
    assert build["working-directory"] == "repo/shared"
    assert build["run"] == "make build\nmake check\n"
    assert build["env"] == {"CI_LEVEL": "full"}
    assert pins["actions/setup-go"] == (SHA, "v5")


def test_a_custom_step_can_stay_out_of_a_pair_of_slots() -> None:
    data = {"custom": [{"name": "Warm a cache", "run": "make warm", "slots": ["agent", "agent-retry"]}]}
    _, plan, _ = _render(data)
    assert [s["name"] for s in plan["agent"]] == ["Stack: Warm a cache"]
    assert plan["verify"] == plan["verify-retry"] == plan["retro-plan"] == []


def test_one_pin_per_action_and_the_first_pin_wins() -> None:
    """A custom step that pins actions/setup-node pins it for the Node stack
    too; without the Node stack the tool's default pin is never used."""
    other = "f" * 40
    custom = {"name": "Node for the docs", "uses": f"actions/setup-node@{other}", "tag": "v5",
              "with": {"node-version": "22"}, "slots": ["agent", "agent-retry"]}
    _, plan, pins = _render({"node": {"version": "22", "dirs": ["."]}, "custom": [custom]})
    assert pins["actions/setup-node"] == (other, "v5")
    assert plan["verify"][0]["uses"] == f"actions/setup-node@{other}"
    _, plan, pins = _render({"custom": [custom]})
    assert pins["actions/setup-node"] == (other, "v5")
    assert plan["verify"] == []


def test_an_empty_profile_renders_the_template_with_a_header() -> None:
    text, plan, _ = _render({})
    assert all(steps == [] for steps in plan.values())
    assert text.startswith(rft.RENDERED_MARK)
    assert yaml.safe_load(text) == TEMPLATE_DATA


# --- what it refuses -----------------------------------------------------------


def _custom(**step) -> dict:
    return {"custom": [step]}


GO = {"name": "Set up Go", "uses": f"actions/setup-go@{SHA}", "tag": "v5"}

REFUSALS = [
    ("a tag instead of a SHA", _custom(name="Go", uses="actions/setup-go@v5", tag="v5"),
     "is not pinned"),
    ("a short SHA", _custom(name="Go", uses="actions/setup-go@0123456", tag="v5"), "is not pinned"),
    ("a branch", _custom(name="Go", uses="actions/setup-go@main", tag="v5"), "is not pinned"),
    ("a local action", _custom(name="x", uses=f"./.github/actions/setup@{SHA}", tag="v1"),
     "is not pinned"),
    ("a docker action", _custom(name="x", uses="docker://alpine:3.20", tag="v1"), "is not pinned"),
    ("no tag", _custom(name="Go", uses=f"actions/setup-go@{SHA}"), "needs tag"),
    ("a tag that is not a version", _custom(name="Go", uses=f"actions/setup-go@{SHA}", tag="latest"),
     "needs tag"),
    ("a second checkout", _custom(name="x", uses=f"actions/checkout@{SHA}", tag="v4"),
     "replace the tree the gate tests"),
    ("an artifact upload", _custom(name="x", uses=f"actions/upload-artifact@{SHA}", tag="v4"),
     "data channel"),
    ("an artifact download", _custom(name="x", uses=f"actions/download-artifact@{SHA}", tag="v4"),
     "data channel"),
    ("the App token action",
     _custom(name="x", uses=f"actions/create-github-app-token@{SHA}", tag="v2"), "App token"),
    ("the model action", _custom(name="x", uses=f"anthropics/claude-code-action@{SHA}", tag="v1"),
     "model"),
    ("an expression in run", _custom(name="x", run="echo ${{ github.event.issue.title }}"),
     "holds ${{"),
    ("a secret through env", _custom(name="x", run="make", env={"T": "${{ secrets.TOKEN }}"}),
     "holds ${{"),
    ("a secret named in run", _custom(name="x", run="echo secrets.TOKEN"), "secrets."),
    ("the job token", _custom(name="x", run="echo github.token"), "github.token"),
    ("continue-on-error", _custom(name="x", run="make", **{"continue-on-error": True}),
     "unknown key"),
    ("a status function", _custom(name="x", run="make", **{"if": "always()"}), "status function"),
    ("an expression in if", _custom(name="x", run="make", **{"if": "${{ true }}"}), "bare expression"),
    ("an if that closes the guard",
     _custom(name="x", run="make", **{"if": "false) || (true"}), "unbalanced parentheses"),
    ("an if with an open quote",
     _custom(name="x", run="make", **{"if": "github.ref == 'a) || (true"}), "unbalanced"),
    ("a two-line if", _custom(name="x", run="make", **{"if": "true\n|| true"}), "one line"),
    ("a checkout in another case",
     _custom(name="x", uses=f"Actions/Checkout@{SHA}", tag="v4"), "replace the tree the gate tests"),
    ("a checkout sub-path", _custom(name="x", uses=f"actions/checkout/sub@{SHA}", tag="v4"),
     "replace the tree the gate tests"),
    ("the model action in another case",
     _custom(name="x", uses=f"Anthropics/Claude-Code-Action@{SHA}", tag="v1"), "model"),
    ("an action spelled two ways",
     {"custom": [GO, {**GO, "uses": f"Actions/Setup-Go@{SHA}"}]}, "spelled"),
    ("the template's setup-python spelled another way",
     _custom(name="py", uses=f"Actions/Setup-Python@{SETUP_PYTHON_SHA}", tag="v7"), "spelled"),
    ("an id the job has", _custom(name="x", run="make", id="apply"), "already used in the verify job"),
    ("a step that runs the gate", _custom(name="x", run="bash scripts/verify.sh"),
     "scripts/verify.sh"),
    ("a dispatch", _custom(name="x", run="gh workflow run ci.yml"), "gh workflow run"),
    ("verify without verify-retry", _custom(name="x", run="make", slots=["verify"]),
     "without verify-retry"),
    ("agent-retry without agent", _custom(name="x", run="make", slots=["agent-retry"]),
     "without agent"),
    ("an unknown slot", _custom(name="x", run="make", slots=["publish"]), "slots"),
    ("with on a run step", _custom(name="x", run="make", **{"with": {"a": "b"}}), "with belongs"),
    ("both uses and run", _custom(name="x", run="make", uses=f"actions/setup-go@{SHA}", tag="v5"),
     "exactly one of uses and run"),
    ("an env name the runner owns", _custom(name="x", run="make", env={"GITHUB_PATH": "x"}),
     "the runner owns"),
    ("another SHA for the template's setup-python",
     _custom(name="py", uses=f"actions/setup-python@{SHA}", tag="v5"), "already pinned"),
    ("two SHAs for one action",
     {"custom": [GO, {**GO, "uses": "actions/setup-go@" + "f" * 40}]}, "already pinned"),
    ("a pin over the template's",
     {"pins": {"actions/setup-python": {"sha": SHA, "tag": "v5"}}}, "already pins it"),
    ("a number as the Python version", {"python": {"version": 3.10}}, "in quotes"),
    ("no Python version", {"python": {"requirements": ["r.txt"]}}, "exactly one of version"),
    ("a path that climbs", {"python": {"version": "3.12", "requirements": ["../secrets.txt"]}},
     "relative path"),
    ("an absolute path", {"python": {"version": "3.12", "requirements": ["/etc/passwd"]}},
     "relative path"),
    ("a path with a space", {"node": {"version": "20", "dirs": ["web app"]}}, "relative path"),
    ("a path read as an option", {"python": {"version": "3.12", "requirements": ["-rf"]}},
     "relative path"),
    ("an install that dispatches", {"node": {"version": "20", "install": "gh workflow run ci.yml"}},
     "gh workflow run"),
    ("a lockfile per dir", {"node": {"version": "20", "dirs": ["a", "b"], "lockfiles": ["a/x"]}},
     "one lockfile per entry"),
    ("an unknown package manager", {"node": {"version": "20", "package_manager": "bun"}},
     "npm, pnpm or yarn"),
    ("a two-line install", {"node": {"version": "20", "install": "npm ci\nnpm test"}}, "one line"),
    ("an unknown key", {"python": {"version": "3.12"}, "ruby": {"version": "3"}}, "unknown key"),
    ("an unknown schema", {"schema": 2}, "schema"),
]


@pytest.mark.parametrize(("data", "says"), [r[1:] for r in REFUSALS], ids=[r[0] for r in REFUSALS])
def test_the_renderer_refuses(data: dict, says: str) -> None:
    with pytest.raises(rft.RenderError) as err:
        _render(data)
    assert says in str(err.value)


def test_it_refuses_a_rendered_workflow_as_its_template() -> None:
    text, _, _ = _render(PROFILES["single-package-node"])
    with pytest.raises(rft.RenderError, match="already rendered"):
        rft.load_template(text)


def test_it_refuses_a_template_whose_slots_moved() -> None:
    lines = TEMPLATE_TEXT.split("\n")
    start, end = rft.load_template(TEMPLATE_TEXT).job_lines["verify"]
    moved = [
        line.replace(rft.SLOT_MARKER, "# Runtime setup goes here") if start <= i < end else line
        for i, line in enumerate(lines)
    ]
    with pytest.raises(rft.RenderError, match="runtime setup markers"):
        rft.render(rft.load_template("\n".join(moved)), rft.load_profile(PROFILES["python-only"]))


def test_the_final_check_catches_a_tampered_result() -> None:
    template = _template()
    text, plan, _ = rft.render(template, rft.load_profile(PROFILES["python-service-and-web-app"]))
    for old, new, says in [
        ("        if: steps.apply.outputs.ok == 'true'\n        working-directory: web\n",
         "        working-directory: web\n", "stack steps are not the plan"),
        (f"actions/setup-node@{SETUP_NODE_SHA} # v4", "actions/setup-node@v4",
         "stack steps are not the plan"),
        ("\njobs:\n", "\n# - uses: actions/setup-node@v4\njobs:\n", "unpinned commented uses"),
        ("          ref: ${{ github.sha }}\n          persist-credentials: false\n",
         "          ref: ${{ github.sha }}\n", "template step changed"),
    ]:
        assert old in text, old
        with pytest.raises(rft.RenderError, match=says):
            rft.check_rendered(template, text.replace(old, new, 1), plan)


# --- the command line ------------------------------------------------------------


def _cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), "--template", str(TEMPLATE), *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8",
    )


def test_the_flags_render_save_the_profile_and_print_a_summary(tmp_path: Path) -> None:
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "requirements-dev.txt").write_text("pytest\n", encoding="utf-8")
    proc = _cli("--python", "3.12", "--requirements", "server/requirements-dev.txt",
                "--node", "20", "--node-dir", "web", "--out", "wf.yml",
                "--save-profile", ".cadence/factory-stack.yaml", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "Rendered wf.yml" in out
    for slot in ("agent", "agent-retry", "verify", "verify-retry", "retro-plan"):
        assert re.search(rf"^  {re.escape(slot)}\s+3 step\(s\)", out, re.M), slot
    assert "Checks passed:" in out and "one SHA per action" in out
    assert f"actions/setup-node@{SETUP_NODE_SHA} # v4" in out
    # The missing web/ is a warning, not a refusal.
    assert "Warning: node.dirs: web/package.json is not in" in out
    assert "server/requirements-dev.txt is not in" not in out
    first = (tmp_path / "wf.yml").read_text(encoding="utf-8")
    assert first.startswith(rft.RENDERED_MARK + " from command-line flags")
    saved = yaml.safe_load((tmp_path / ".cadence" / "factory-stack.yaml").read_text(encoding="utf-8"))
    assert saved["python"]["requirements"] == ["server/requirements-dev.txt"]
    # The saved profile renders the same workflow.
    proc = _cli("--profile", ".cadence/factory-stack.yaml", "--out", "again.yml", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    again = (tmp_path / "again.yml").read_text(encoding="utf-8")
    assert again.startswith(rft.RENDERED_MARK + " from the profile .cadence/factory-stack.yaml")
    assert again.split("\n", 1)[1] == first.split("\n", 1)[1]


@pytest.mark.parametrize(
    ("args", "says"),
    [
        (("--out", "wf.yml"), "give --profile PATH or the stack flags"),
        (("--profile", "p.yaml", "--node", "20", "--out", "wf.yml"), "not both"),
        (("--requirements", "r.txt", "--out", "wf.yml"), "need --python"),
        (("--profile", "bad.yaml", "--out", "wf.yml"), "is not pinned"),
    ],
)
def test_a_refusal_writes_nothing(tmp_path: Path, args: tuple[str, ...], says: str) -> None:
    (tmp_path / "p.yaml").write_text("node: {version: '20'}\n", encoding="utf-8")
    (tmp_path / "bad.yaml").write_text(
        json.dumps({"custom": [{"name": "Go", "uses": "actions/setup-go@v5", "tag": "v5"}]}),
        encoding="utf-8",
    )
    proc = _cli(*args, cwd=tmp_path)
    assert proc.returncode == 2
    assert says in proc.stderr and "Nothing was written." in proc.stderr
    assert not (tmp_path / "wf.yml").exists()

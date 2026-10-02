"""Builders for the learning-loop tests (tests/test_ladder.py, tests/test_metrics.py).

Everything is generated at test time so patch hashes never depend on how
git checked the fixtures out (autocrlf). Observations and findings follow
the shapes in docs/LEARNING.md (observation.schema.json and the
``factory`` object of retro.schema.json).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL_DIR = REPO_ROOT / "plugins" / "cadence" / "templates" / "tool"
SCHEMA_DIR = REPO_ROOT / "plugins" / "cadence" / "schemas"

REPO = "octo/app"
BASE_SHA = "b" * 40
T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)

SEED_RULE = {
    "where": "src/domain/**",
    "forbidden": ["src/http/**"],
    "reason": "Domain code stays independent of HTTP",
}

PATTERNS_MD = (
    "# Patterns\n"
    "\n"
    "## §1 — Layer rules\n"
    "\n"
    "Domain code never imports HTTP.\n"
    "\n"
    "## §2 — Controllers\n"
    "\n"
    "Keep them small.\n"
)

_GIT_LOCATION_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
)


def load_tool(name: str) -> Any:
    module_name = f"cadence_{name}_under_test"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, TOOL_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# --- time ------------------------------------------------------------------------


def at(days: float) -> datetime:
    return T0 + timedelta(days=days)


def ts(days: float) -> str:
    return at(days).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch(days: float) -> float:
    return at(days).timestamp()


def sha256(text: str | bytes) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()


# --- evidence ----------------------------------------------------------------------


def area_of(path: str, depth: int = 2) -> str:
    parts = path.split("/")[:-1]
    return "/".join(parts[:depth]) if parts else "."


def changed(path: str, op: str = "M", *, test: bool = False, source: bool = True) -> dict[str, Any]:
    return {"path": path, "op": op, "area": area_of(path), "test": test, "source": source and not test}


def edge(
    path: str,
    line_no: int,
    to: str,
    *,
    line: str | None = None,
    frm: str | None = None,
    kind: str = "relative",
) -> dict[str, Any]:
    frm = frm or area_of(path)
    return {
        "path": path,
        "line_no": line_no,
        "from_area": frm,
        "to": to,
        "kind": kind,
        "key": f"import-edge:{frm}->{to}",
        "line": line,
        "line_sha256": sha256(line or f"{path}:{line_no}"),
    }


def edge_key(frm: str, to: str) -> str:
    return f"import-edge:{frm}->{to}"


def rule_hit(rule_id: str, path: str, line_no: int, key: str | None, forbidden: str = "src/http/**") -> dict[str, Any]:
    return {"rule_id": rule_id, "path": path, "line_no": line_no, "forbidden": forbidden, "key": key}


def observation(
    run_id: str,
    issue: int,
    *,
    attempt: int = 1,
    day: float = 0.0,
    patch_sha256: str | None = "default",
    files: Sequence[dict[str, Any]] = (),
    edges: Sequence[dict[str, Any]] = (),
    guarded: Sequence[dict[str, Any]] = (),
    rule_hits: Sequence[dict[str, Any]] = (),
    failing_tests: Sequence[str] = (),
    published: bool = False,
    pr: int | None = None,
    apply_status: str = "ok",
    agent_result: str = "success",
    verify_result: str = "success",
    gate_step: str = "none",
    repo: str = REPO,
    lessons_cited: Any = "absent",
) -> dict[str, Any]:
    """``lessons_cited`` "absent" leaves the optional field out, as in an
    observation booked before it existed; a list or None sets it."""
    if patch_sha256 == "default":
        patch_sha256 = sha256(f"patch {repo} {issue} {run_id} {attempt}")
    files = list(files)
    for e in edges:
        if not any(f["path"] == e["path"] for f in files):
            files.append(changed(e["path"], "M"))
    obs = {
        "schema": "cadence.observation/1",
        "repo": repo,
        "issue": issue,
        "run_id": run_id,
        "run_attempt": attempt,
        "base_sha": BASE_SHA,
        "completed_at": ts(day),
        "patch_sha256": patch_sha256,
        "patch_bytes": 100 if patch_sha256 else 0,
        "apply_status": apply_status,
        "agent_result": agent_result,
        "agent_subtype": "success" if agent_result == "success" else "other",
        "verify_result": verify_result,
        "gate_step": gate_step,
        "published": published,
        "pr": pr,
        "published_sha": ("c" * 40) if published else None,
        "detector_version": "d" * 64,
        "ruleset_sha256": None,
        "config_sha256": "e" * 64,
        "area_depth": 2,
        "evidence": {
            "files": files,
            "import_edges": list(edges),
            "guarded": list(guarded),
            "rule_hits": list(rule_hits),
            "failing_tests": [{"path": p} for p in failing_tests],
        },
        "classes": [],
        "truncated": False,
    }
    if lessons_cited != "absent":
        obs["lessons_cited"] = lessons_cited
    return obs


def add_patch(path: str, lines: Sequence[str], *, context: Sequence[str] = ()) -> str:
    """A git-style patch that creates ``path`` with ``context`` then ``lines``."""
    body = [f"+{line}" for line in [*context, *lines]]
    return (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "--- /dev/null\n"
        f"+++ b/{path}\n"
        f"@@ -0,0 +1,{len(body)} @@\n" + "\n".join(body) + "\n"
    )


def factory_finding(
    class_key: str,
    *,
    signal: str,
    issue: int,
    run_id: str = "900",
    run_attempt: int = 1,
    pr: int | None = None,
    trust: str = "A",
    phase: str = "post-pr",
    rule_id: str | None = None,
    path: str | None = None,
    line_no: int | None = None,
    day: float = 0.0,
    repo: str = REPO,
) -> dict[str, Any]:
    family = class_key.split(":", 1)[0]
    return {
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"test|{class_key}|{issue}|{run_id}|{signal}|{pr}")),
        "ts": ts(day),
        "feature": f"issue #{issue}",
        "what_happened": f"A maintainer acted on PR #{pr or 1} ({class_key}).",
        "auto_catchable": False,
        "rule_existed": rule_id is not None,
        "proposed_fix": f"Track class {class_key}; tool/ladder.py promotes it when it recurs on another issue.",
        "fix_layer": 1,
        "factory": {
            "schema_version": 1,
            "class_key": class_key,
            "family": family,
            "signal": signal,
            "trust": trust,
            "phase": phase,
            "gate_caught": False,
            "reached_pr": pr is not None,
            "rule_id": rule_id,
            "repo": repo,
            "issue": issue,
            "pr": pr,
            "run_id": run_id,
            "run_attempt": run_attempt,
            "base_sha": None,
            "published_sha": None,
            "final_sha": None,
            "patch_sha256": None,
            "path": path,
            "line_no": line_no,
            "area": None,
            "comment_id": None,
            "excerpt_sha256": None,
            "edit_basis": None,
            "classification": {"by": "deterministic"},
            "judge": None,
        },
    }


# --- state dir -------------------------------------------------------------------------


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(obj, indent=2) + "\n")


class StateDir:
    """Writes an extracted cadence/state branch."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def observe(self, obs: dict[str, Any], patch: str | None = None) -> dict[str, Any]:
        run = f"{obs['run_id']}-{obs['run_attempt']}"
        if patch is not None:
            data = patch.encode("utf-8")
            obs["patch_sha256"] = sha256(data)
            obs["patch_bytes"] = len(data)
            target = self.root / "patches" / f"{run}.patch"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        _write_json(self.root / "observations" / f"{run}.json", obs)
        return obs

    def findings(self, stem: str, items: Iterable[dict[str, Any]]) -> None:
        path = self.root / "findings" / f"{stem}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "".join(json.dumps(i, sort_keys=True, separators=(",", ":")) + "\n" for i in items)
        path.write_bytes(text.encode("utf-8"))

    def run(
        self,
        run_id: str,
        *,
        attempt: int = 1,
        stage: str = "build",
        booked: float = 1.0,
        day: float = 0.0,
        outcome: str = "success",
        issue: int | None = 1,
    ) -> None:
        _write_json(
            self.root / "runs" / f"{run_id}-{attempt}.json",
            {
                "issue": issue,
                "run_id": run_id,
                "run_attempt": attempt,
                "outcome": outcome,
                "dod": "pass",
                "total_cost_usd": booked,
                "booked_usd": booked,
                "cost_source": "reported",
                "num_turns": 10,
                "per_run_cap_usd": 5.0,
                "recorded_at": ts(day),
                "stage": stage,
            },
        )

    def pr(self, pr: int, issue: int, run_id: str, *, attempt: int = 1, day: float = 0.0) -> None:
        _write_json(
            self.root / "prs" / f"{pr}-{run_id}.json",
            {
                "schema": "cadence.pr/1",
                "pr": pr,
                "issue": issue,
                "run_id": run_id,
                "run_attempt": attempt,
                "base_sha": BASE_SHA,
                "published_sha": "c" * 40,
                "patch_sha256": "f" * 64,
                "recorded_at": ts(day),
            },
        )

    def harvest(self, pr: int, issue: int, *, merged: bool, day: float) -> None:
        _write_json(
            self.root / "harvest" / f"pr-{pr}.json",
            {
                "schema": "cadence.harvest/1",
                "pr": pr,
                "issue": issue,
                "kind": "agent",
                "final_head_sha": "c" * 40,
                "merged": merged,
                "closed_at": ts(day),
                "harvested_at": ts(day + 0.1),
                "edit_basis": "none",
                "findings": 0,
                "status": "ok",
            },
        )

    def decision(
        self,
        pr: int,
        *,
        merged: bool,
        day: float,
        transitions: Sequence[tuple[str, str, bool]],
        plan_sha: str | None = None,
    ) -> None:
        _write_json(
            self.root / "decisions" / f"retro-pr-{pr}.json",
            {
                "schema": "cadence.decision/1",
                "pr": pr,
                "merged": merged,
                "closed_at": ts(day),
                "plan_sha": plan_sha,
                "transitions": [
                    {
                        "class_key": key,
                        "lesson_id": load_tool("ladder").lesson_id(key),
                        "to": to,
                        "landed": landed,
                    }
                    for key, to, landed in transitions
                ],
            },
        )


# --- repo root ---------------------------------------------------------------------------


def git_env(base: Path) -> dict[str, str]:
    gitconfig = base / "gitconfig"
    if not gitconfig.exists():
        gitconfig.parent.mkdir(parents=True, exist_ok=True)
        gitconfig.write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in _GIT_LOCATION_VARS}
    env.update(
        {
            "GIT_CONFIG_GLOBAL": str(gitconfig),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "Test Author",
            "GIT_AUTHOR_EMAIL": "author@example.com",
            "GIT_COMMITTER_NAME": "Test Committer",
            "GIT_COMMITTER_EMAIL": "committer@example.com",
        }
    )
    return env


def git(cwd: Path, *args: str, env: dict[str, str]) -> str:
    result = git_bytes(cwd, *args, env=env)
    return result.decode("utf-8")


def git_bytes(cwd: Path, *args: str, env: dict[str, str]) -> bytes:
    result = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, check=False)
    assert result.returncode == 0, (
        f"git {' '.join(args)} failed:\n{result.stderr.decode('utf-8', 'replace')}"
    )
    return result.stdout


def cadence_yaml(rules: Sequence[dict[str, Any]]) -> str:
    text = "commands:\n  format: ['true']\n  lint: ['true']\n  test: ['true']\n"
    if not rules:
        return text + "boundaries: []\n"
    text += "# Seed rules first; learned L- rules are appended by tool/emit_rule.py.\nboundaries:\n"
    for rule in rules:
        first = True
        if "id" in rule:
            text += f"  - id: {rule['id']}\n"
            first = False
        text += ("  - " if first else "    ") + f"where: {json.dumps(rule['where'])}\n"
        text += "    forbidden:\n"
        for pattern in rule["forbidden"]:
            text += f"      - {json.dumps(pattern)}\n"
        text += f"    reason: {json.dumps(rule['reason'])}\n"
    return text


def make_repo(
    root: Path,
    *,
    rules: Sequence[dict[str, Any]] = (SEED_RULE,),
    lessons: Sequence[dict[str, Any]] | None = None,
    areas: Sequence[str] = ("src/domain", "src/db", "src/http"),
    env: dict[str, str] | None = None,
) -> Path:
    """A project root with tool/, .cadence/ (config and schemas), docs/ and src/.

    With ``env`` it also becomes a git repository with one commit.
    """
    (root / ".cadence").mkdir(parents=True, exist_ok=True)
    (root / ".cadence" / "cadence.yaml").write_bytes(cadence_yaml(rules).encode("utf-8"))
    for schema in SCHEMA_DIR.glob("*.schema.json"):
        shutil.copyfile(schema, root / ".cadence" / schema.name)
    (root / "tool").mkdir(exist_ok=True)
    shutil.copyfile(TOOL_DIR / "check_boundaries.py", root / "tool" / "check_boundaries.py")
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "PATTERNS.md").write_bytes(PATTERNS_MD.encode("utf-8"))
    for name in areas:
        (root / name).mkdir(parents=True, exist_ok=True)
        stem = name.rsplit("/", 1)[-1]
        (root / name / f"{stem}.ts").write_bytes(f"export const {stem} = 1;\n".encode("utf-8"))
    if lessons is not None:
        ladder = load_tool("ladder")
        (root / ".cadence" / "lessons.yaml").write_bytes(ladder.render_lessons(list(lessons)).encode("utf-8"))
    if env is not None:
        git(root, "init", "--quiet", env=env)
        git(root, "config", "core.autocrlf", "false", env=env)
        git(root, "config", "commit.gpgsign", "false", env=env)
        git(root, "add", "-A", env=env)
        git(root, "commit", "--quiet", "-m", "init", env=env)
    return root


def lesson(
    class_key: str,
    rung: str,
    *,
    since: str = "2026-09-01",
    issues: Sequence[int] = (1, 2),
    retired: tuple[str, str] | None = None,
    history: Sequence[tuple[str, str]] = (),
    pinned: bool = False,
) -> dict[str, Any]:
    ladder = load_tool("ladder")
    lid = ladder.lesson_id(class_key)
    out: dict[str, Any] = {"id": lid, "class_key": class_key, "rung": rung, "since": since}
    if rung in ("pattern", "check", "retired"):
        out["text"] = ladder.pattern_text(class_key, list(issues)) or "Some lesson text."
    out["issues"] = list(issues)
    out["evidence"] = []
    if rung == "check" or (retired and retired[1] != "human-removed"):
        out["check"] = {
            "kind": "boundary-rule",
            "rule_id": lid,
            "fixture": f"tests/fixtures/retro/{lid[2:]}/",
        }
    if rung == "retired":
        on, reason = retired or (since, "dormant")
        out["retired"] = {"on": on, "reason": reason}
    out["history"] = [{"rung": r, "on": d} for r, d in history] or [{"rung": rung, "on": since}]
    out["rejections"] = 0
    out["pinned"] = pinned
    return out

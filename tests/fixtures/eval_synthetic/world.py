"""Build a throwaway eval world from the synthetic fixture.

The world has everything the eval's config names, all under one temporary
directory: a repo under test (a tiny bash app) at a pinned commit, a Cadence
repo holding the factory templates and schemas of this checkout (so its sha
exists for ``git show``), the private folder (eval.yaml, tickets, stubs,
checks, expectations), a fake hidden harness (python) and an eval home whose
venv is the running interpreter.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

FIXTURE = Path(__file__).resolve().parent
REPO_ROOT = FIXTURE.parents[2]
CADENCE_PATHS = (
    "plugins/cadence/templates",
    "plugins/cadence/schemas",
    "plugins/cadence/.claude-plugin",
    ".claude-plugin",
)
FIXED_DATE = "@1780000000 +0000"


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": FIXED_DATE,
        "GIT_COMMITTER_DATE": FIXED_DATE,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    done = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-c", "user.name=fixture", "-c", "user.email=fixture@localhost",
         "-c", "init.defaultBranch=main", "-c", "core.hooksPath=/dev/null", "-C", str(cwd), *args],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    assert done.returncode == 0, f"git {' '.join(args)}: {done.stderr}"
    return done.stdout.strip()


def copy_text_tree(src: Path, dst: Path) -> None:
    """Copy with LF line endings (a Windows checkout may hold CRLF)."""
    for path in sorted(src.rglob("*")):
        if path.is_dir() or "__pycache__" in path.parts:
            continue
        target = dst / path.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = path.read_bytes()
        if b"\0" not in data:
            data = data.replace(b"\r\n", b"\n")
        target.write_bytes(data)


def commit_dir(path: Path) -> str:
    _git(path, "init", "-q")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "fixture")
    return _git(path, "rev-parse", "HEAD")


def make_world(tmp: Path, *, sandbox: str = "none", sessions: int = 1) -> dict[str, Any]:
    src = tmp / "src" / "demo"
    copy_text_tree(FIXTURE / "repo", src)
    repo_sha = commit_dir(src)

    cadence = tmp / "cadence"
    for rel in CADENCE_PATHS:
        if (REPO_ROOT / rel).is_dir():
            copy_text_tree(REPO_ROOT / rel, cadence / rel)
    cadence_sha = commit_dir(cadence)

    private = tmp / "private"
    copy_text_tree(FIXTURE / "private", private)
    grader = tmp / "grader"
    copy_text_tree(FIXTURE / "grader", grader)

    home = tmp / "home"
    (home / "toolchain" / "bin").mkdir(parents=True, exist_ok=True)
    venv_bin = home / "venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    for name in ("python", "python3"):
        link = venv_bin / name
        if not link.exists():
            if os.name == "nt":
                link.write_text(f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" "$@"\n', encoding="utf-8")
            else:
                link.symlink_to(sys.executable)

    config = {
        "schema": "cadence-eval.config/1",
        "home": str(home),
        "results_dir": str(tmp / "results"),
        "read_roots": [str(tmp / "src"), str(private), str(grader)],
        "forbidden_path_parts": ["forbidden-part"],
        "never_bind": ["ANSWERS.md"],
        "cadence": {"repo": str(cadence), "sha": cadence_sha},
        "claude_code_version": "0.0.0",
        "model": "",
        "sandbox": sandbox,
        "score_network": "offline",
        "allow_no_subprocess_scrub": False,
        "caps": {"per_run_usd": 5, "max_turns": 60, "autopilot_usd_per_ticket": 15, "autopilot_turns_per_ticket": 60},
        "timeouts_min": {"intake": 5, "build": 5, "gate": 5, "learn_verify": 5, "score": 5, "autopilot": 5},
        "factory": {
            "mode": "eval-sandbox", "daily_usd": 1000, "retry_on_dod_fail": 1, "promote_after": 2,
            "guarded_paths": ["tests", "test", ".github", ".cadence", "scripts", "tool"],
            "test_roots": ["tests", "test"], "classify": False,
        },
        "logical_clock": {"epoch": 1790000000, "slot_hours": 3},
        "concurrency": {"sessions": sessions, "score_workers": 1},
        "budget_usd": 100,
        "hidden_command": ["python3", "{harness}", "{repo}", "{ref}"],
        "repos": [
            {
                "id": "demo",
                "slug": "eval/demo-app",
                "source": str(src),
                "sha": repo_sha,
                "overlay_dir": "overlay/demo",
                "tickets": ["demo-1", "demo-2", "demo-3"],
                "hidden": {"harness_dir": str(grader / "hidden" / "demo"), "lib_dir": str(grader / "lib"),
                           "entry": "run.py", "extra_args": []},
                "calib": None,
            }
        ],
        "files": {
            "tickets_dir": "tickets", "replies": "replies.yaml", "reviewer": "reviewer.yaml",
            "stubs": "stubs.yaml", "checks": "checks.yaml", "detectors": "detectors.json",
            "expectations": "expectations.yaml", "canaries": "canaries.txt",
        },
    }
    path = private / "eval.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8", newline="\n")
    return {"config": path, "home": home, "results": tmp / "results", "private": private, "cadence": cadence,
            "cadence_sha": cadence_sha, "repo_sha": repo_sha, "src": src, "grader": grader, "raw": config}

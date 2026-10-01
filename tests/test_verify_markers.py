"""Tests for the verify evidence that compliance_report.py depends on.

compliance_report.py only marks a control "implemented" when
.cadence/.last_verify_ok exists, reports .cadence/.last_verify_sha, and
packs .cadence/last_verify.log into the audit packet. These tests run the
real verify scripts against a throwaway git repo and check that:

- a green run writes all three files, with the verified commit's SHA
- a failing run removes a stale pass marker and SHA left by an earlier run
- uncommitted changes are recorded as "<sha>-dirty"
- the log carries no terminal colour codes

The bash script is tested wherever a bash is available. The PowerShell
script shells out through `cmd /c`, so it is tested only on Windows.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "plugins" / "cadence" / "templates" / "scripts"


def _find_bash() -> str | None:
    if os.name == "nt":
        # Prefer Git for Windows' bash; System32\bash.exe is WSL, which cannot
        # see Windows temp paths the same way.
        git = shutil.which("git")
        if git:
            # git.exe lives in Git\cmd or Git\mingw64\bin; bash in Git\bin.
            for parent in Path(git).parents:
                candidate = parent / "bin" / "bash.exe"
                if candidate.is_file():
                    return str(candidate)
        return None
    return shutil.which("bash")


BASH = _find_bash()
PWSH = shutil.which("pwsh") if os.name == "nt" else None


def _git(root: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def _make_project(tmp_path: Path, *, test_cmd: str) -> Path:
    root = tmp_path / "proj"
    (root / ".cadence").mkdir(parents=True)
    (root / ".cadence" / "cadence.yaml").write_text(
        f"commands:\n  lint: ['{test_cmd}']\n  test: ['{test_cmd}']\nboundaries: []\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("fixture\n", encoding="utf-8")
    (root / "scripts").mkdir()
    shutil.copy2(SCRIPTS / "verify.sh", root / "scripts" / "verify.sh")
    shutil.copy2(SCRIPTS / "verify.ps1", root / "scripts" / "verify.ps1")
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=t@example.com", "-c", "user.name=t", "add", ".")
    _git(
        root, "-c", "user.email=t@example.com", "-c", "user.name=t",
        "commit", "-q", "-m", "fixture",
    )
    return root


def _env(tmp_path: Path, shell: str) -> dict[str, str]:
    """Make the scripts find this interpreter (it has PyYAML) first."""
    paths = [str(Path(sys.executable).parent)]
    if shell == "bash":
        # verify.sh prefers `python3`, which may be missing or a store stub.
        # Shell shims are bash-only: PowerShell on Windows would also pick up
        # these extensionless files as `python`.
        shim_dir = tmp_path / "bin"
        shim_dir.mkdir(exist_ok=True)
        exe = Path(sys.executable).as_posix()
        for name in ("python3", "python"):
            shim = shim_dir / name
            shim.write_text(f'#!/bin/sh\nexec "{exe}" "$@"\n', encoding="utf-8")
            shim.chmod(0o755)
        paths.insert(0, str(shim_dir))
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([*paths, env.get("PATH", "")])
    return env


def _run(shell: str, root: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    if shell == "bash":
        cmd = [BASH, "scripts/verify.sh"]
    else:
        cmd = [PWSH, "-NoProfile", "-File", "scripts/verify.ps1"]
    return subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True)


SHELLS = [
    pytest.param(
        "bash", marks=pytest.mark.skipif(BASH is None, reason="bash not available")
    ),
    pytest.param(
        "pwsh",
        marks=pytest.mark.skipif(PWSH is None, reason="verify.ps1 needs Windows + pwsh"),
    ),
]


def _ok_cmd(shell: str) -> str:
    return "true" if shell == "bash" else "exit 0"


def _fail_cmd(shell: str) -> str:
    return "false" if shell == "bash" else "exit 1"


@pytest.mark.parametrize("shell", SHELLS)
def test_green_run_writes_all_evidence(shell: str, tmp_path: Path) -> None:
    root = _make_project(tmp_path, test_cmd=_ok_cmd(shell))
    head = _git(root, "rev-parse", "HEAD")

    result = _run(shell, root, _env(tmp_path, shell))

    assert result.returncode == 0, result.stdout + result.stderr
    cadence = root / ".cadence"
    assert (cadence / ".last_verify_ok").is_file()
    assert (cadence / ".last_verify_ok").read_text(encoding="utf-8").startswith("ok ")
    assert (cadence / ".last_verify_sha").read_text(encoding="utf-8").strip() == head
    log = (cadence / "last_verify.log").read_text(encoding="utf-8")
    # The configured command must actually have run; a run that found no
    # commands would also exit 0.
    assert f"$ {_ok_cmd(shell)}" in log
    assert "OK (" in log
    assert "\x1b" not in log


@pytest.mark.parametrize("shell", SHELLS)
def test_failed_run_removes_stale_pass(shell: str, tmp_path: Path) -> None:
    root = _make_project(tmp_path, test_cmd=_fail_cmd(shell))
    cadence = root / ".cadence"
    (cadence / ".last_verify_ok").write_text("ok from an old run\n", encoding="utf-8")
    (cadence / ".last_verify_sha").write_text("0000000\n", encoding="utf-8")

    result = _run(shell, root, _env(tmp_path, shell))

    assert result.returncode != 0
    assert not (cadence / ".last_verify_ok").exists()
    assert not (cadence / ".last_verify_sha").exists()
    log = (cadence / "last_verify.log").read_text(encoding="utf-8")
    assert "FAIL: lint" in log


@pytest.mark.parametrize("shell", SHELLS)
def test_uncommitted_changes_are_marked_dirty(shell: str, tmp_path: Path) -> None:
    root = _make_project(tmp_path, test_cmd=_ok_cmd(shell))
    head = _git(root, "rev-parse", "HEAD")
    (root / "README.md").write_text("changed\n", encoding="utf-8")

    result = _run(shell, root, _env(tmp_path, shell))

    assert result.returncode == 0, result.stdout + result.stderr
    sha = (root / ".cadence" / ".last_verify_sha").read_text(encoding="utf-8").strip()
    assert sha == f"{head}-dirty"


@pytest.mark.parametrize("shell", SHELLS)
def test_rerun_after_green_is_not_dirtied_by_its_own_evidence(
    shell: str, tmp_path: Path
) -> None:
    """The evidence files a run writes must not make the next run look dirty."""
    root = _make_project(tmp_path, test_cmd=_ok_cmd(shell))
    head = _git(root, "rev-parse", "HEAD")
    env = _env(tmp_path, shell)

    assert _run(shell, root, env).returncode == 0
    assert _run(shell, root, env).returncode == 0

    sha = (root / ".cadence" / ".last_verify_sha").read_text(encoding="utf-8").strip()
    assert sha == head

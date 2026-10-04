"""Nothing private reaches a public file or an agent.

privacy-check scans added and changed files for the built-in patterns (local
user paths, e-mail addresses, an API key prefix), every denylist entry
(whole words, so a short entry never hits a longer word) and every canary,
and reports only file, line and rule. The canary guard refuses any input
for an agent that holds a canary. Every test string here is assembled at
run time, so this file never matches its own rules.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval" / "harness"))

import privacy  # noqa: E402
from config import EvalError  # noqa: E402

WIN_PATH = "C:" + "\\" + "Users" + "\\" + "someone" + "\\" + "work"
WSL_PATH = "/mnt/c/" + "Users/someone/work"
HOME_PATH = "/ho" + "me/someone/work"
EMAIL = "someone" + "@" + "example-mail.com"
KEY = "sk" + "-ant-" + "api03-xyz"
CANARY = "CAN" + "ARY-7c1d"


@pytest.mark.parametrize(
    "line, rule",
    [(f"see {WIN_PATH}", "windows-user-path"), ("C:/" + "Users/someone/x", "windows-user-path"),
     (f"cd {WSL_PATH}", "wsl-user-path"), (f"cd {HOME_PATH}", "home-path"),
     (f"mail {EMAIL}", "email"), (f"key {KEY}", "api-key")],
)
def test_builtin_patterns(line: str, rule: str) -> None:
    assert [h.rule for h in privacy.scan_text("f", line)] == [rule]


@pytest.mark.parametrize(
    "line",
    ["HOME=/home/runner", "cadence-eval" + "@" + "localhost", "1+bot" + "@" + "users.noreply.github.com",
     "uses: actions/checkout" + "@" + "v4", "install cadence" + "@" + "cadence", "a plain line"],
)
def test_allowed_lines(line: str) -> None:
    assert privacy.scan_text("f", line) == []


def test_denylist_entries_are_whole_words() -> None:
    word = "acme" + "corp"
    deny = [word, "secret-" + "project"]
    assert [h.rule for h in privacy.scan_text("f", f"the {word} repo", deny)] == ["denylist[0]"]
    assert privacy.scan_text("f", f"{word}ish things", deny) == []
    assert [h.rule for h in privacy.scan_text("f", f"see {word.upper()}-docs", deny)] == ["denylist[0]"]
    assert [h.rule for h in privacy.scan_text("f", "my secret-" + "project/x", deny)] == ["denylist[1]"]


def test_canaries_and_the_guard() -> None:
    hits = privacy.scan_text("f", "one\ntwo " + CANARY + "\n", canaries=[CANARY])
    assert [(h.line, h.rule) for h in hits] == [(2, "canary[0]")]
    assert privacy.canary_hits("x " + CANARY, ["nope", CANARY]) == [1]
    privacy.guard_inputs("spec", "clean text", [CANARY])
    with pytest.raises(EvalError, match="canary #0"):
        privacy.guard_inputs("spec", "text " + CANARY, [CANARY])


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_check_scans_added_and_changed_files_only(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t" + "@" + "localhost", "-c",
                        "core.autocrlf=false", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    (tmp_path / "old.txt").write_text(f"committed {EMAIL}\n", encoding="utf-8")
    (tmp_path / "changed.txt").write_text("fine\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    (tmp_path / "changed.txt").write_text("fine\n" + f"now {WSL_PATH}\n", encoding="utf-8")
    (tmp_path / "new.txt").write_text("x " + CANARY + "\n", encoding="utf-8")
    hits = privacy.check(tmp_path, denylist=[], canaries=[CANARY])
    assert sorted((h.path, h.line, h.rule) for h in hits) == [
        ("changed.txt", 2, "wsl-user-path"), ("new.txt", 1, "canary[0]")]
    only = privacy.check(tmp_path, denylist=[], canaries=[], paths=["old.txt"])
    assert [h.rule for h in only] == ["email"]


def test_the_public_harness_is_clean() -> None:
    """The harness, its schemas and tests hold none of the built-in patterns."""
    files = [p for p in (ROOT / "eval").rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    files += [p for p in (ROOT / "tests").glob("test_eval_*.py")]
    files += [p for p in (ROOT / "tests" / "fixtures" / "eval_synthetic").rglob("*")
              if p.is_file() and "__pycache__" not in p.parts]
    hits = []
    for path in files:
        data = path.read_bytes()
        if b"\0" in data:
            continue
        hits += privacy.scan_text(path.relative_to(ROOT).as_posix(), data.decode("utf-8", "replace"))
    assert hits == []

"""scripts/privacy_check.py: private details must not reach this public repo.

All deny-list terms and addresses here are made up. Every test that expects
a finding also asserts the output never repeats the matched text, because
CI logs of a public repository are public.
"""

from __future__ import annotations

import importlib.util
import io
import re
import subprocess
import sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "privacy_check.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "privacy.yml"

spec = importlib.util.spec_from_file_location("privacy_check", SCRIPT)
pc = importlib.util.module_from_spec(spec)
sys.modules["privacy_check"] = pc
spec.loader.exec_module(pc)

NOREPLY = "12345+someone@users.noreply.github.com"
# Built at runtime so this file holds no address the check would flag.
PERSONAL = "someone.private" + "@" + "mailhost.io"
TERM = "zebra-ledger-internal"
DENY = f"{TERM}\nquokka-ops\ndemo"


def git(repo: Path, *args: str, email: str = NOREPLY) -> str:
    env_args = ["-c", f"user.email={email}", "-c", "user.name=Someone", "-c", "commit.gpgsign=false"]
    return subprocess.run(["git", *env_args, *args], cwd=repo, check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "r"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "README.md").write_text("hello\n", encoding="utf-8")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


def commit(repo: Path, path: str, text: str, msg: str = "change", email: str = NOREPLY) -> str:
    (repo / path).write_text(text, encoding="utf-8")
    git(repo, "add", "-A", email=email)
    git(repo, "commit", "-q", "-m", msg, email=email)
    return git(repo, "rev-parse", "HEAD")


def run(argv: list[str], monkeypatch, deny: str | None = DENY, env: dict | None = None,
        stdin: str = "") -> tuple[int, str]:
    if deny is None:
        monkeypatch.delenv("PRIVACY_DENYLIST", raising=False)
    else:
        monkeypatch.setenv("PRIVACY_DENYLIST", deny)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = pc.main(argv)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue() + err.getvalue()


def assert_no_echo(output: str, *secrets: str) -> None:
    low = output.lower()
    for s in secrets:
        assert s.lower() not in low, f"output repeats {s!r}"


def test_a_clean_commit_passes(repo, monkeypatch):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "a.txt", "nothing private here\n", msg="feat: add a")
    code, out = run(["--range", f"{base}..HEAD", "--repo", str(repo)], monkeypatch)
    assert code == 0, out
    assert "clean" in out


def test_a_private_term_in_an_added_line_fails_without_echo(repo, monkeypatch):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "notes.md", f"we tried this on {TERM.upper()} first\n")
    code, out = run(["--range", f"{base}..HEAD", "--repo", str(repo)], monkeypatch)
    assert code == 1
    assert re.search(r"notes\.md:1: private term #\d", out)
    assert_no_echo(out, TERM, "we tried this")


def test_a_private_term_in_a_commit_message_fails(repo, monkeypatch):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "a.txt", "x\n", msg=f"fix: something seen in quokka-ops")
    code, out = run(["--range", f"{base}..HEAD", "--repo", str(repo)], monkeypatch)
    assert code == 1
    assert "message: private term #" in out
    assert_no_echo(out, "quokka-ops")


def test_removed_lines_are_not_findings(repo, monkeypatch):
    commit(repo, "old.md", f"mentions {TERM}\n")  # history before the range may hold it
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "old.md", "scrubbed\n", msg="docs: scrub")
    code, out = run(["--range", f"{base}..HEAD", "--repo", str(repo)], monkeypatch)
    assert code == 0, out


def test_short_terms_and_partial_tokens_do_not_match(repo, monkeypatch):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "a.md", f"a demo of {TERM}x and x{TERM}\n")
    code, out = run(["--range", f"{base}..HEAD", "--repo", str(repo)], monkeypatch)
    assert code == 0, out


def test_a_personal_author_or_committer_email_fails_without_echo(repo, monkeypatch):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "a.txt", "x\n", email=PERSONAL)
    code, out = run(["--range", f"{base}..HEAD", "--repo", str(repo)], monkeypatch)
    assert code == 1
    assert "author email is not a GitHub no-reply address" in out
    assert "committer email is not a GitHub no-reply address" in out
    assert_no_echo(out, PERSONAL, "mailhost")


def test_emails_in_text_allow_only_noreply_and_reserved_domains(repo, monkeypatch):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "ok.md", "mail ada@example.com or bot@ci.test\n",
           msg="docs\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    code, out = run(["--range", f"{base}..HEAD", "--repo", str(repo)], monkeypatch)
    assert code == 0, out
    commit(repo, "bad.md", f"write to {PERSONAL}\n")
    code, out = run(["--range", f"{base}..HEAD", "--repo", str(repo)], monkeypatch)
    assert code == 1
    assert "bad.md:1: email address not on the allow-list" in out
    assert_no_echo(out, PERSONAL)


def test_package_and_action_references_are_not_emails():
    for text in ("uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7",
                 '"@scope/pkg@1.2.3"', "@pytest.fixture()", "git@github.com:owner/repo.git"):
        assert pc.scan_text(text, "x", []) == [], text


def test_the_pr_description_is_checked(repo, monkeypatch):
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, "a.txt", "x\n")
    code, out = run(["--ci", "--repo", str(repo)], monkeypatch, env={
        "EVENT_NAME": "pull_request", "PR_BASE": base, "PR_HEAD": head,
        "PR_BODY": f"Port of the {TERM} change. Ping {PERSONAL}."})
    assert code == 1
    assert "pull request description: private term #" in out
    assert "pull request description: email address not on the allow-list" in out
    assert_no_echo(out, TERM, PERSONAL)


def test_ci_push_checks_before_to_after(repo, monkeypatch):
    before = git(repo, "rev-parse", "HEAD")
    after = commit(repo, "a.md", f"{TERM}\n")
    code, out = run(["--ci", "--repo", str(repo)], monkeypatch,
                    env={"EVENT_NAME": "push", "BEFORE": before, "AFTER": after})
    assert code == 1
    assert_no_echo(out, TERM)


def test_a_new_branch_is_checked_from_the_default_branch(repo, monkeypatch):
    git(repo, "branch", "base-ref")
    git(repo, "checkout", "-q", "-b", "feature")
    after = commit(repo, "a.md", f"{TERM}\n")
    code, out = run(["--ci", "--repo", str(repo), "--default-ref", "base-ref"], monkeypatch,
                    env={"EVENT_NAME": "push", "BEFORE": pc.ZERO_SHA, "AFTER": after})
    assert code == 1


def test_a_force_push_is_checked_from_the_merge_base(repo, monkeypatch):
    base = git(repo, "rev-parse", "HEAD")
    old = commit(repo, "a.md", "old tip\n")
    git(repo, "reset", "-q", "--hard", base)
    after = commit(repo, "b.md", f"{TERM}\n")
    code, out = run(["--ci", "--repo", str(repo)], monkeypatch,
                    env={"EVENT_NAME": "push", "BEFORE": old, "AFTER": after})
    assert code == 1


def test_pre_push_reads_the_hook_stdin(repo, monkeypatch):
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, "a.md", f"{TERM}\n")
    line = f"refs/heads/main {head} refs/heads/main {base}\n"
    monkeypatch.setattr(pc, "local_terms", lambda cwd: [])
    code, out = run(["--pre-push", "--repo", str(repo)], monkeypatch, stdin=line)
    assert code == 1
    assert_no_echo(out, TERM)
    # deleting a remote branch pushes nothing to check
    code, out = run(["--pre-push", "--repo", str(repo)], monkeypatch,
                    stdin=f"(delete) {pc.ZERO_SHA} refs/heads/x {head}\n")
    assert code == 0


def test_local_terms_come_from_gh_and_git_config(monkeypatch, tmp_path):
    calls = []

    class P:
        def __init__(self, out, rc=0):
            self.stdout, self.returncode = out, rc

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[:3] == ["git", "remote", "get-url"]:
            return P("git@github.com:someone/public-repo.git\n")
        if cmd[0] == "gh":
            return P('[{"name": "Quokka-Ops"}, {"name": "zebra-ledger-internal"}]')
        if cmd[:2] == ["git", "config"] and "--global" in cmd:
            return P(f"{PERSONAL}\n{NOREPLY}\n")
        return P("")

    monkeypatch.setattr(pc.subprocess, "run", fake_run)
    terms = pc.local_terms(tmp_path)
    assert terms == sorted({"quokka-ops", TERM, PERSONAL})
    assert ["gh", "repo", "list", "someone", "--visibility", "private", "--limit", "1000",
            "--json", "name"] in calls


def test_without_a_denylist_only_emails_are_checked(repo, monkeypatch):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "a.md", f"{TERM}\n")
    code, out = run(["--range", f"{base}..HEAD", "--repo", str(repo)], monkeypatch, deny=None)
    assert code == 0
    assert "checking emails only" in out


def test_tree_mode_scans_tracked_text_files(repo, monkeypatch):
    commit(repo, "docs.md", f"line\nanother {TERM} line\n")
    (repo / "bin.dat").write_bytes(b"\0\0" + TERM.encode())
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "bin")
    code, out = run(["--tree", "--repo", str(repo)], monkeypatch)
    assert code == 1
    assert "docs.md:2: private term #" in out
    assert "bin.dat" not in out


# ---------- the workflow ----------

WF = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_workflow_reads_the_denylist_only_from_a_secret_via_env():
    steps = WF["jobs"]["check"]["steps"]
    check = next(s for s in steps if "privacy_check.py" in s.get("run", ""))
    assert check["env"]["PRIVACY_DENYLIST"] == "${{ secrets.PRIVACY_DENYLIST }}"
    assert check["env"]["PR_BODY"] == "${{ github.event.pull_request.body }}"
    for s in steps:
        assert "${{" not in s.get("run", ""), "no expression may reach a run: script"


def test_workflow_is_read_only_pinned_and_runs_on_every_push_and_pr():
    assert WF["permissions"] == {"contents": "read"}
    triggers = WF.get(True) or WF.get("on")
    assert triggers["push"]["branches"] == ["**"]
    assert set(triggers["pull_request"]["types"]) >= {"opened", "edited", "synchronize"}
    for s in WF["jobs"]["check"]["steps"]:
        if "uses" in s:
            assert re.search(r"@[0-9a-f]{40}$", s["uses"]), s["uses"]


def test_the_repo_never_stores_a_denylist():
    """The deny-list must come from a secret or gh at check time, never from a file here."""
    for p in REPO_ROOT.rglob("*"):
        if p.is_file() and ".git" not in p.parts and p.name.lower() in {
                "denylist", "denylist.txt", "privacy_denylist", "private_repos.txt"}:
            pytest.fail(f"deny-list file committed: {p}")

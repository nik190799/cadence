"""Tests for the factory's per-issue claim lock (tool/claim.py).

The integration tests use a bare repository in ``tmp_path`` as the remote
and two separate clones, A and B, standing in for two factory runs on
different runners. Every git call runs with an isolated, empty global
config and an explicit identity, so the tests do not depend on the
machine's git setup.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CLAIM_PATH = REPO_ROOT / "plugins" / "cadence" / "templates" / "tool" / "claim.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, name
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


claim = _load_module("cadence_claim", CLAIM_PATH)

# Variables that would point git at some other repository.
_GIT_LOCATION_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
)


@dataclass(frozen=True)
class Sandbox:
    remote: Path
    a: Path
    b: Path
    env: dict[str, str]


def _git(cwd: Path, *args: str, env: dict[str, str], input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed:\n{result.stderr}"
    return result.stdout


def _claim_cmd(*args: str) -> list[str]:
    return [sys.executable, str(CLAIM_PATH), *args]


def _run_claim(sandbox: Sandbox, cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        _claim_cmd(*args),
        cwd=cwd,
        env=sandbox.env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _remote_sha(sandbox: Sandbox, issue: int) -> str | None:
    out = _git(
        sandbox.remote,
        "for-each-ref",
        "--format=%(objectname)",
        f"refs/heads/cadence/claim/{issue}",
        env=sandbox.env,
    ).strip()
    return out or None


def _remote_commit(sandbox: Sandbox, sha: str) -> tuple[str, str]:
    """Return the (header, message) of a commit in the remote."""
    raw = _git(sandbox.remote, "cat-file", "commit", sha, env=sandbox.env)
    header, _, message = raw.partition("\n\n")
    return header, message


def _json_lines(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


@pytest.fixture(scope="module")
def _git_env(tmp_path_factory) -> dict[str, str]:
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    gitconfig = tmp_path_factory.mktemp("gitconfig") / "gitconfig"
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


@pytest.fixture(scope="module")
def _template(tmp_path_factory, _git_env) -> Path:
    """Build the remote and both clones once; each test gets a copy.

    The clones name their remote by the relative path ``../remote.git``,
    so a copied sandbox talks to its own copy of the remote.
    """
    env = _git_env
    root = tmp_path_factory.mktemp("claim-template")
    remote = root / "remote.git"
    _git(root, "init", "--quiet", "--bare", str(remote), env=env)
    _git(remote, "symbolic-ref", "HEAD", "refs/heads/main", env=env)

    a = root / "a"
    _git(root, "clone", "--quiet", str(remote), str(a), env=env)
    _git(a, "remote", "set-url", "origin", "../remote.git", env=env)
    (a / "README.md").write_text("claim test\n", encoding="utf-8")
    _git(a, "add", "README.md", env=env)
    _git(a, "commit", "--quiet", "-m", "initial commit", env=env)
    _git(a, "push", "--quiet", "origin", "HEAD:refs/heads/main", env=env)

    b = root / "b"
    _git(root, "clone", "--quiet", str(remote), str(b), env=env)
    _git(b, "remote", "set-url", "origin", "../remote.git", env=env)
    return root


@pytest.fixture
def sandbox(_template, _git_env, tmp_path: Path) -> Sandbox:
    root = tmp_path / "sandbox"
    shutil.copytree(_template, root)
    return Sandbox(
        remote=root / "remote.git", a=root / "a", b=root / "b", env=dict(_git_env)
    )


# --- Pure helpers ---------------------------------------------------------


def test_claim_ref_and_issue_from_ref_round_trip():
    assert claim.claim_ref(7) == "refs/heads/cadence/claim/7"
    assert claim.issue_from_ref("refs/heads/cadence/claim/7") == 7
    assert claim.issue_from_ref("refs/heads/cadence/claim/1234") == 1234


@pytest.mark.parametrize(
    "ref",
    [
        "refs/heads/main",
        "refs/heads/cadence/claim/0",
        "refs/heads/cadence/claim/07",
        "refs/heads/cadence/claim/7/extra",
        "refs/heads/cadence/claim/abc",
        "refs/heads/other/refs/heads/cadence/claim/7",
    ],
)
def test_issue_from_ref_rejects_other_refs(ref):
    assert claim.issue_from_ref(ref) is None


def test_claim_message_round_trips():
    message = claim.build_claim_message(7, "run-a.1_x", 1_700_000_000)
    assert "\n" not in message
    assert json.loads(message) == {
        "issue": 7,
        "run_id": "run-a.1_x",
        "claimed_at": "2023-11-14T22:13:20Z",
    }
    assert claim.parse_claim_message(message + "\n") == {
        "issue": 7,
        "run_id": "run-a.1_x",
        "claimed_at": "2023-11-14T22:13:20Z",
    }


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "not json",
        "[1, 2]",
        '"just a string"',
        '{"run_id": "run-a"}',
        '{"issue": 7}',
        '{"issue": "7", "run_id": "run-a"}',
        '{"issue": true, "run_id": "run-a"}',
        '{"issue": 0, "run_id": "run-a"}',
        '{"issue": 7, "run_id": "has space"}',
        '{"issue": 7, "run_id": ""}',
        pytest.param("[" * 100_000, id="deeply-nested"),
    ],
)
def test_parse_claim_message_tolerates_garbage(text):
    assert claim.parse_claim_message(text) is None


def test_parse_claim_message_drops_a_malformed_timestamp():
    parsed = claim.parse_claim_message('{"issue": 7, "run_id": "r", "claimed_at": 5}')
    assert parsed == {"issue": 7, "run_id": "r", "claimed_at": None}


@pytest.mark.parametrize(
    "text,expected",
    [("7", 7), ("42", 42), ("0", None), ("-3", None), ("abc", None), ("7.5", None), ("", None)],
)
def test_parse_issue(text, expected):
    assert claim.parse_issue(text) == expected


@pytest.mark.parametrize(
    "run_id,ok",
    [
        ("run-a", True),
        ("12345678-2", True),
        ("A.b_c-9", True),
        ("x" * 128, True),
        ("x" * 129, False),
        ("", False),
        ("has space", False),
        ("semi;colon", False),
        ("slash/y", False),
    ],
)
def test_valid_run_id(run_id, ok):
    assert claim.valid_run_id(run_id) is ok


def test_iso_utc_covers_the_whole_datetime_range():
    # datetime.fromtimestamp raises OSError on Windows after the year 3000.
    assert claim.iso_utc(0) == "1970-01-01T00:00:00Z"
    assert claim.iso_utc(32_536_850_000) == "3001-01-19T21:53:20Z"
    assert claim.iso_utc(253_402_300_799) == "9999-12-31T23:59:59Z"


def test_claim_naming_another_issue_has_no_holder():
    message = claim.build_claim_message(999, "run-x", 1_700_000_000)
    copied = claim._claim_from_commit(34, "abc", (1_700_000_000, message))
    assert copied.run_id is None
    assert copied.claimed_at is None
    own = claim._claim_from_commit(999, "abc", (1_700_000_000, message))
    assert own.run_id == "run-x"


def test_claim_env_pins_identity_and_date():
    env = claim.claim_env(1_700_000_000)
    assert env["GIT_AUTHOR_NAME"] == env["GIT_COMMITTER_NAME"] == "cadence-factory"
    assert (
        env["GIT_AUTHOR_EMAIL"]
        == env["GIT_COMMITTER_EMAIL"]
        == "cadence-factory@users.noreply.github.com"
    )
    assert env["GIT_AUTHOR_DATE"] == env["GIT_COMMITTER_DATE"] == "@1700000000 +0000"


def test_acquire_push_requires_the_ref_to_be_absent():
    cmd = claim.build_acquire_push_command("origin", 7, "abc123")
    assert cmd == [
        "push",
        "--porcelain",
        "--force-with-lease=refs/heads/cadence/claim/7:",
        "origin",
        "abc123:refs/heads/cadence/claim/7",
    ]


def test_delete_push_uses_the_observed_sha_as_the_lease():
    """Spec item 7: the delete is conditional on the value release read, so
    a claim replaced between ls-remote and the delete is never removed."""
    observed = "0123456789abcdef0123456789abcdef01234567"
    cmd = claim.build_delete_push_command("origin", 7, observed)
    assert cmd == [
        "push",
        "--porcelain",
        f"--force-with-lease=refs/heads/cadence/claim/7:{observed}",
        "origin",
        ":refs/heads/cadence/claim/7",
    ]
    # Never an unconditional delete.
    assert "--force" not in cmd
    assert not any(arg.endswith("claim/7:") for arg in cmd)


_PUSH_OK = (
    "To /tmp/remote.git\n"
    "*\tabc:refs/heads/cadence/claim/7\t[new branch]\n"
    "Done\n"
)
_PUSH_STALE = (
    "To /tmp/remote.git\n"
    "!\tabc:refs/heads/cadence/claim/7\t[rejected] (stale info)\n"
    "Done\n"
)
_PUSH_LOCKED = (
    "To /tmp/remote.git\n"
    "!\tabc:refs/heads/cadence/claim/7\t[remote rejected] (failed to update ref)\n"
    "Done\n"
)
_PUSH_DELETED = (
    "To /tmp/remote.git\n"
    "-\t:refs/heads/cadence/claim/7\t[deleted]\n"
    "Done\n"
)


def test_parse_push_status():
    ref = "refs/heads/cadence/claim/7"
    assert claim.parse_push_status(_PUSH_OK, ref) == ("*", "[new branch]")
    assert claim.parse_push_status(_PUSH_STALE, ref) == ("!", "[rejected] (stale info)")
    assert claim.parse_push_status(_PUSH_DELETED, ref) == ("-", "[deleted]")
    assert claim.parse_push_status(_PUSH_OK, "refs/heads/cadence/claim/70") is None
    assert claim.parse_push_status("", ref) is None


def test_push_outcome_classification():
    ref = "refs/heads/cadence/claim/7"
    ok = claim.GitResult(0, _PUSH_OK, "")
    stale = claim.GitResult(1, _PUSH_STALE, "error: failed to push some refs")
    locked = claim.GitResult(1, _PUSH_LOCKED, "")
    offline = claim.GitResult(128, "", "fatal: could not read from remote repository")
    assert claim.push_succeeded(ok, ref)
    assert claim.push_succeeded(claim.GitResult(0, _PUSH_DELETED, ""), ref)
    assert not any(claim.push_succeeded(r, ref) for r in (stale, locked, offline))
    assert claim.push_lease_rejected(stale, ref)
    assert not any(claim.push_lease_rejected(r, ref) for r in (ok, locked, offline))


def test_parse_commit_object():
    raw = (
        "tree 4b825dc642cb6eb9a060e54bf8d69288fbee4904\n"
        "author cadence-factory <cadence-factory@users.noreply.github.com> 1700000000 +0000\n"
        "committer cadence-factory <cadence-factory@users.noreply.github.com> 1700000123 +0000\n"
        "\n"
        '{"issue": 7, "run_id": "run-a", "claimed_at": "2023-11-14T22:13:20Z"}\n'
    )
    committed_at, message = claim.parse_commit_object(raw)
    assert committed_at == 1_700_000_123
    assert claim.parse_claim_message(message)["run_id"] == "run-a"
    assert claim.parse_commit_object("tree abc\n\nno committer") is None


def test_staleness_helpers():
    now = 1_700_000_000
    assert claim.is_stale(now - 3600, now, 30)
    assert not claim.is_stale(now - 60, now, 30)
    assert not claim.is_stale(now - 1800, now, 30)  # exactly at the timeout
    assert claim.age_minutes(now - 3600, now) == 60
    assert claim.age_minutes(now + 60, now) == 0  # clock skew never goes negative


# --- Spec 1: acquire creates the claim ------------------------------------


def test_acquire_creates_claim_ref(sandbox):
    now = int(time.time())
    result = _run_claim(sandbox, sandbox.a, "acquire", "--issue", "7", "--run-id", "run-a")
    assert result.returncode == 0, result.stderr

    out = json.loads(result.stdout)
    assert out["issue"] == 7
    assert out["run_id"] == "run-a"
    assert out["ref"] == "refs/heads/cadence/claim/7"
    assert _remote_sha(sandbox, 7) == out["sha"]

    header, message = _remote_commit(sandbox, out["sha"])
    parsed = json.loads(message)
    assert parsed["issue"] == 7
    assert parsed["run_id"] == "run-a"
    assert parsed["claimed_at"].endswith("Z")
    assert message.strip().count("\n") == 0  # a single JSON line

    # Parentless, empty tree, fixed identity (not the sandbox's identity).
    assert "\nparent " not in header
    assert _git(sandbox.remote, "ls-tree", out["sha"], env=sandbox.env) == ""
    ident = "cadence-factory <cadence-factory@users.noreply.github.com>"
    assert f"author {ident} " in header
    assert f"committer {ident} " in header
    committer_ts = int(header.split("committer ", 1)[1].split()[-2])
    assert abs(committer_ts - now) < 120


def test_acquire_honours_now(sandbox):
    result = _run_claim(
        sandbox, sandbox.a, "acquire", "--issue", "3", "--run-id", "run-a", "--now", "1700000000"
    )
    assert result.returncode == 0, result.stderr
    header, message = _remote_commit(sandbox, json.loads(result.stdout)["sha"])
    assert " 1700000000 +0000\n" in header + "\n"
    assert json.loads(message)["claimed_at"] == "2023-11-14T22:13:20Z"


# --- Spec 2: a second run is refused and told who holds the claim -------


def test_second_acquire_is_refused_and_names_the_holder(sandbox):
    first = _run_claim(sandbox, sandbox.a, "acquire", "--issue", "7", "--run-id", "run-a")
    assert first.returncode == 0, first.stderr
    held_sha = json.loads(first.stdout)["sha"]

    second = _run_claim(sandbox, sandbox.b, "acquire", "--issue", "7", "--run-id", "run-b")
    assert second.returncode == 1, second.stderr
    assert "run-a" in second.stderr
    report = json.loads(second.stdout)
    assert report["held_by"] == "run-a"
    assert report["sha"] == held_sha
    assert _remote_sha(sandbox, 7) == held_sha


def test_acquire_is_reentrant_for_the_same_run(sandbox):
    first = _run_claim(sandbox, sandbox.a, "acquire", "--issue", "7", "--run-id", "run-a")
    assert first.returncode == 0, first.stderr
    retry = _run_claim(
        sandbox, sandbox.b, "acquire", "--issue", "7", "--run-id", "run-a", "--now", "1000"
    )
    assert retry.returncode == 0, retry.stderr
    assert json.loads(retry.stdout)["sha"] == json.loads(first.stdout)["sha"]


# --- Spec 3: release only by the holder, idempotent ---------------------


def test_release_by_holder_only_and_idempotent(sandbox):
    acquired = _run_claim(sandbox, sandbox.a, "acquire", "--issue", "7", "--run-id", "run-a")
    assert acquired.returncode == 0, acquired.stderr
    held_sha = json.loads(acquired.stdout)["sha"]

    wrong_run = _run_claim(sandbox, sandbox.b, "release", "--issue", "7", "--run-id", "run-b")
    assert wrong_run.returncode == 1, wrong_run.stderr
    assert "run-a" in wrong_run.stderr
    assert _remote_sha(sandbox, 7) == held_sha

    holder = _run_claim(sandbox, sandbox.a, "release", "--issue", "7", "--run-id", "run-a")
    assert holder.returncode == 0, holder.stderr
    assert json.loads(holder.stdout) == {
        "issue": 7,
        "ref": "refs/heads/cadence/claim/7",
        "released": True,
        "sha": held_sha,
    }
    assert _remote_sha(sandbox, 7) is None

    again = _run_claim(sandbox, sandbox.a, "release", "--issue", "7", "--run-id", "run-a")
    assert again.returncode == 0, again.stderr
    assert json.loads(again.stdout)["released"] is False

    # Released claims can be taken again.
    retaken = _run_claim(sandbox, sandbox.b, "acquire", "--issue", "7", "--run-id", "run-b")
    assert retaken.returncode == 0, retaken.stderr


# --- Spec 4: concurrent acquirers, exactly one wins ---------------------


def test_concurrent_acquire_has_exactly_one_winner(sandbox):
    for issue in range(101, 106):
        procs = {
            run_id: subprocess.Popen(
                _claim_cmd("acquire", "--issue", str(issue), "--run-id", run_id),
                cwd=clone,
                env=sandbox.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for run_id, clone in (("race-a", sandbox.a), ("race-b", sandbox.b))
        }
        results = {}
        for run_id, proc in procs.items():
            stdout, stderr = proc.communicate(timeout=120)
            results[run_id] = (proc.returncode, stdout, stderr)

        codes = sorted(code for code, _, _ in results.values())
        assert codes == [0, 1], f"issue {issue}: {results}"
        winner = next(r for r, (code, _, _) in results.items() if code == 0)
        loser = next(r for r, (code, _, _) in results.items() if code == 1)
        winner_sha = json.loads(results[winner][1])["sha"]
        assert _remote_sha(sandbox, issue) == winner_sha
        assert json.loads(results[loser][1])["held_by"] == winner


# --- Spec 5: stale claims are listed and force-released ------------------


def test_stale_lists_old_claims_and_force_release_removes_them(sandbox):
    now = int(time.time())
    old = _run_claim(
        sandbox, sandbox.a, "acquire", "--issue", "8", "--run-id", "run-old", "--now", str(now - 3600)
    )
    assert old.returncode == 0, old.stderr
    old_sha = json.loads(old.stdout)["sha"]
    fresh = _run_claim(sandbox, sandbox.a, "acquire", "--issue", "9", "--run-id", "run-fresh")
    assert fresh.returncode == 0, fresh.stderr

    # B never fetched either claim; stale must fetch them itself.
    listed = _run_claim(sandbox, sandbox.b, "stale", "--timeout-minutes", "30")
    assert listed.returncode == 0, listed.stderr
    entries = _json_lines(listed.stdout)
    assert [e["issue"] for e in entries] == [8]
    assert entries[0]["run_id"] == "run-old"
    assert entries[0]["sha"] == old_sha
    assert 60 <= entries[0]["age_minutes"] <= 62

    # As of two hours from now, both are stale.
    later = _run_claim(
        sandbox, sandbox.b, "stale", "--timeout-minutes", "30", "--now", str(now + 7200)
    )
    assert later.returncode == 0, later.stderr
    assert [e["issue"] for e in _json_lines(later.stdout)] == [8, 9]

    removed = _run_claim(
        sandbox, sandbox.b, "release", "--issue", "8", "--run-id", "reconciler", "--force"
    )
    assert removed.returncode == 0, removed.stderr
    assert json.loads(removed.stdout)["released"] is True
    assert _remote_sha(sandbox, 8) is None
    assert _remote_sha(sandbox, 9) is not None

    after = _run_claim(sandbox, sandbox.b, "stale", "--timeout-minutes", "30")
    assert after.returncode == 0, after.stderr
    assert _json_lines(after.stdout) == []


def test_reconciler_release_pinned_to_the_listed_sha_keeps_a_retaken_claim(sandbox):
    """stale lists run-a's old claim; before the reconciler acts, run-a
    releases and run-b re-takes the issue. ``release --force`` alone would
    delete run-b's live claim; pinned with ``--sha`` it must not."""
    now = int(time.time())
    old = _run_claim(
        sandbox, sandbox.a, "acquire", "--issue", "7", "--run-id", "run-a", "--now", str(now - 7200)
    )
    assert old.returncode == 0, old.stderr
    listed = _json_lines(_run_claim(sandbox, sandbox.b, "stale", "--timeout-minutes", "60").stdout)
    assert [e["issue"] for e in listed] == [7]
    stale_sha = listed[0]["sha"]

    assert _run_claim(sandbox, sandbox.a, "release", "--issue", "7", "--run-id", "run-a").returncode == 0
    retaken = _run_claim(sandbox, sandbox.a, "acquire", "--issue", "7", "--run-id", "run-b")
    assert retaken.returncode == 0, retaken.stderr
    live_sha = json.loads(retaken.stdout)["sha"]

    pinned = _run_claim(
        sandbox, sandbox.b, "release", "--issue", "7", "--run-id", "reconciler", "--force",
        "--sha", stale_sha,
    )
    assert pinned.returncode == 1, pinned.stderr
    assert json.loads(pinned.stdout)["held_by"] == "run-b"
    assert _remote_sha(sandbox, 7) == live_sha

    # Pinned to the current claim, it is released (upper case is accepted).
    current = _run_claim(
        sandbox, sandbox.b, "release", "--issue", "7", "--run-id", "reconciler", "--force",
        "--sha", live_sha.upper(),
    )
    assert current.returncode == 0, current.stderr
    assert _remote_sha(sandbox, 7) is None


def test_stale_reports_unreadable_claims_with_null_run_id(sandbox):
    tree = _git(sandbox.remote, "mktree", env=sandbox.env, input_text="").strip()
    env = {**sandbox.env, "GIT_COMMITTER_DATE": "@1000 +0000", "GIT_AUTHOR_DATE": "@1000 +0000"}
    junk = _git(
        sandbox.remote, "commit-tree", tree, "-m", "not a claim", env=env
    ).strip()
    _git(sandbox.remote, "update-ref", "refs/heads/cadence/claim/11", junk, env=sandbox.env)

    listed = _run_claim(sandbox, sandbox.a, "stale", "--timeout-minutes", "30")
    assert listed.returncode == 0, listed.stderr
    entries = _json_lines(listed.stdout)
    assert len(entries) == 1
    assert entries[0]["issue"] == 11
    assert entries[0]["run_id"] is None
    assert entries[0]["sha"] == junk

    # Without --force nobody owns it; with --force the reconciler removes it.
    refused = _run_claim(sandbox, sandbox.a, "release", "--issue", "11", "--run-id", "reconciler")
    assert refused.returncode == 1
    forced = _run_claim(
        sandbox, sandbox.a, "release", "--issue", "11", "--run-id", "reconciler", "--force"
    )
    assert forced.returncode == 0, forced.stderr
    assert _remote_sha(sandbox, 11) is None


def test_claim_copied_from_another_issue_is_not_the_named_runs(sandbox):
    """A claim commit whose message names a different issue (copied from
    another claim ref) must not let the run it names re-acquire or release
    it as its own."""
    tree = _git(sandbox.remote, "mktree", env=sandbox.env, input_text="").strip()
    copied = _git(
        sandbox.remote,
        "commit-tree",
        tree,
        "-m",
        claim.build_claim_message(999, "run-x", int(time.time())),
        env=sandbox.env,
    ).strip()
    _git(sandbox.remote, "update-ref", "refs/heads/cadence/claim/34", copied, env=sandbox.env)

    acquired = _run_claim(sandbox, sandbox.a, "acquire", "--issue", "34", "--run-id", "run-x")
    assert acquired.returncode == 1, acquired.stderr
    assert json.loads(acquired.stdout)["held_by"] is None

    released = _run_claim(sandbox, sandbox.a, "release", "--issue", "34", "--run-id", "run-x")
    assert released.returncode == 1, released.stderr
    assert _remote_sha(sandbox, 34) == copied


# --- Spec 6: invalid input ----------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["acquire", "--issue", "0", "--run-id", "run-a"],
        ["acquire", "--issue", "-3", "--run-id", "run-a"],
        ["acquire", "--issue", "abc", "--run-id", "run-a"],
        ["acquire", "--issue", "7", "--run-id", "has space"],
        ["acquire", "--issue", "7", "--run-id", "x" * 129],
        ["acquire", "--issue", "7", "--run-id", ""],
        ["acquire", "--issue", "7", "--run-id", "run-a", "--now", "-5"],
        # past 9999-12-31 this used to crash with a traceback (exit 1)
        ["acquire", "--issue", "7", "--run-id", "run-a", "--now", "253402300800"],
        ["acquire", "--issue", "7", "--run-id", "run-a", "--now", "99999999999999"],
        ["stale", "--timeout-minutes", "30", "--now", "253402300800"],
        ["release", "--issue", "0", "--run-id", "run-a"],
        ["release", "--issue", "7", "--run-id", "has space", "--force"],
        ["release", "--issue", "7", "--run-id", "r", "--force", "--sha", "abc123"],
        ["release", "--issue", "7", "--run-id", "r", "--force", "--sha", "g" * 40],
        ["release", "--issue", "7", "--run-id", "r", "--force", "--sha=--upload-pack=x"],
        ["stale", "--timeout-minutes", "-1"],
        ["stale", "--timeout-minutes", "nan"],
        ["acquire", "--issue", "7", "--run-id", "run-a", "--remote=--upload-pack=x"],
    ],
)
def test_invalid_input_exits_2_without_touching_git(argv, tmp_path, monkeypatch, capsys):
    def no_git(*args, **kwargs):
        raise AssertionError(f"git must not run on invalid input: {args}")

    monkeypatch.setattr(claim, "_git", no_git)
    assert claim.main([*argv, "--repo", str(tmp_path)]) == 2
    assert "ERROR" in capsys.readouterr().err


def test_missing_repo_exits_2(tmp_path, capsys):
    rc = claim.main(
        ["acquire", "--issue", "7", "--run-id", "run-a", "--repo", str(tmp_path / "nope")]
    )
    assert rc == 2
    assert "not a directory" in capsys.readouterr().err


def test_invalid_input_exit_code_from_the_cli(sandbox):
    for argv in (
        ["acquire", "--issue", "0", "--run-id", "run-a"],
        ["acquire", "--issue", "7", "--run-id", "has space"],
    ):
        result = _run_claim(sandbox, sandbox.a, *argv)
        assert result.returncode == 2, (argv, result.stderr)
    assert _git(
        sandbox.remote, "for-each-ref", "refs/heads/cadence/", env=sandbox.env
    ) == ""


def test_unreachable_remote_exits_2(sandbox):
    result = _run_claim(
        sandbox, sandbox.a, "acquire", "--issue", "7", "--run-id", "run-a", "--remote", "no-such-remote"
    )
    assert result.returncode == 2
    assert "ERROR" in result.stderr


# --- Spec 7: the delete lease protects a claim replaced mid-release ------


def _use_sandbox_env(monkeypatch, sandbox: Sandbox) -> None:
    """Run in-process claim calls with the sandbox's isolated git env."""
    for var in _GIT_LOCATION_VARS:
        monkeypatch.delenv(var, raising=False)
    for key, value in sandbox.env.items():
        if os.environ.get(key) != value:
            monkeypatch.setenv(key, value)


def test_release_never_deletes_a_claim_replaced_after_it_was_read(
    sandbox, monkeypatch, capsys
):
    """Force the race the lease exists for: between release reading the
    claim and pushing the delete, another run swaps in its own claim. The
    delete must be refused and the new claim must survive."""
    _use_sandbox_env(monkeypatch, sandbox)
    assert claim.main(["acquire", "--repo", str(sandbox.a), "--issue", "7", "--run-id", "run-a"]) == 0
    observed = json.loads(capsys.readouterr().out)["sha"]

    tree = _git(sandbox.remote, "mktree", env=sandbox.env, input_text="").strip()
    replacement = _git(
        sandbox.remote,
        "commit-tree",
        tree,
        "-m",
        claim.build_claim_message(7, "run-b", int(time.time())),
        env=sandbox.env,
    ).strip()

    real_git = claim._git
    delete_commands: list[list[str]] = []

    def swap_before_delete(repo, args, **kwargs):
        if args and args[0] == "push" and any(a.startswith(":") for a in args):
            delete_commands.append(list(args))
            _git(
                sandbox.remote,
                "update-ref",
                "refs/heads/cadence/claim/7",
                replacement,
                observed,
                env=sandbox.env,
            )
        return real_git(repo, args, **kwargs)

    monkeypatch.setattr(claim, "_git", swap_before_delete)
    rc = claim.main(["release", "--repo", str(sandbox.a), "--issue", "7", "--run-id", "run-a"])

    assert rc == 1
    assert delete_commands, "release never attempted the delete"
    assert f"--force-with-lease=refs/heads/cadence/claim/7:{observed}" in delete_commands[0]
    assert _remote_sha(sandbox, 7) == replacement
    assert "changed after it was read" in capsys.readouterr().err


def test_release_keeps_a_claim_released_and_retaken_mid_release(
    sandbox, monkeypatch, capsys
):
    """The same race through the tool itself: after run-a's release reads
    the claim, the reconciler force-releases it and run-b acquires it from
    another clone. run-a's delete must be refused and run-b keeps the claim."""
    _use_sandbox_env(monkeypatch, sandbox)
    assert claim.main(["acquire", "--repo", str(sandbox.a), "--issue", "7", "--run-id", "run-a"]) == 0
    observed = json.loads(capsys.readouterr().out)["sha"]

    real_git = claim._git
    swaps: list[tuple[int, int]] = []

    def retake_before_delete(repo, args, **kwargs):
        if args and args[0] == "push" and any(a.startswith(":") for a in args) and not swaps:
            forced = _run_claim(
                sandbox, sandbox.b, "release", "--issue", "7", "--run-id", "reconciler", "--force"
            )
            retaken = _run_claim(sandbox, sandbox.b, "acquire", "--issue", "7", "--run-id", "run-b")
            swaps.append((forced.returncode, retaken.returncode))
        return real_git(repo, args, **kwargs)

    monkeypatch.setattr(claim, "_git", retake_before_delete)
    rc = claim.main(["release", "--repo", str(sandbox.a), "--issue", "7", "--run-id", "run-a"])

    assert swaps == [(0, 0)], "the competing release/acquire did not run"
    assert rc == 1
    current = _remote_sha(sandbox, 7)
    assert current is not None and current != observed
    header, message = _remote_commit(sandbox, current)
    assert json.loads(message)["run_id"] == "run-b"

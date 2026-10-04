"""The read-only gh shim answers exactly the five requests harvest makes.

closed PRs (newest update first), a PR's review comments and reviews, an
issue's conversation comments, and a collaborator's permission, for the
chain's own repo only; anything else is "HTTP 404" with exit 1, and nothing
is ever written.
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval" / "harness"))

import ghshim  # noqa: E402

SLUG = "eval/demo-app"


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    s = ghshim.Store(tmp_path / "ghstore", SLUG)
    s.open_pr(100, head_ref="cadence/issue-1", head_sha="a" * 40, base_sha="b" * 40, title="t", body="b",
              draft=True, at="2026-10-01T00:00:00Z")
    s.merge(100, "a" * 40, "2026-10-01T02:00:00Z")
    s.open_pr(101, head_ref="cadence/retro", head_sha="c" * 40, base_sha="a" * 40, title="r", body="",
              draft=False, at="2026-10-01T03:00:00Z")
    s.open_pr(102, head_ref="cadence/issue-2", head_sha="d" * 40, base_sha="a" * 40, title="t2", body="",
              draft=True, at="2026-10-01T04:00:00Z")
    s.merge(102, "d" * 40, "2026-10-01T05:00:00Z")
    s.comment(100, "/cadence-forbid src/a -> src/b", "2026-10-01T01:00:00Z", comment_id=7)
    return tmp_path / "ghstore"


def _call(store: Path, monkeypatch, *argv: str) -> tuple[int, str, str]:
    monkeypatch.setenv("GH_SHIM_STORE", str(store))
    monkeypatch.setenv("GH_SHIM_REPO", SLUG)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ghshim.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_closed_prs_newest_first(store: Path, monkeypatch) -> None:
    code, out, _ = _call(store, monkeypatch, "api", f"repos/{SLUG}/pulls?state=closed&sort=updated&direction=desc&per_page=100")
    assert code == 0
    prs = json.loads(out)
    assert [p["number"] for p in prs] == [102, 100]  # the open retro PR is not listed
    assert prs[1]["merge_commit_sha"] == "a" * 40 and prs[1]["user"]["login"] == "cadence-eval[bot]"


@pytest.mark.parametrize("kind", ["comments", "reviews"])
def test_review_comments_and_reviews(store: Path, monkeypatch, kind: str) -> None:
    code, out, _ = _call(store, monkeypatch, "api", "--paginate", f"repos/{SLUG}/pulls/100/{kind}?per_page=100")
    assert code == 0 and json.loads(out) == []


def test_conversation_comments(store: Path, monkeypatch) -> None:
    code, out, _ = _call(store, monkeypatch, "api", "--paginate", f"repos/{SLUG}/issues/100/comments?per_page=100")
    assert code == 0
    assert json.loads(out)[0]["body"].startswith("/cadence-forbid") and json.loads(out)[0]["user"]["login"] == "eval-owner"


def test_collaborator_permission(store: Path, monkeypatch) -> None:
    code, out, _ = _call(store, monkeypatch, "api", f"repos/{SLUG}/collaborators/eval-owner/permission")
    assert code == 0 and json.loads(out)["permission"] == "admin"
    code, _, err = _call(store, monkeypatch, "api", f"repos/{SLUG}/collaborators/stranger/permission")
    assert code == 1 and "HTTP 404" in err


@pytest.mark.parametrize(
    "argv",
    [
        ["api", "repos/other/repo/pulls?state=closed&sort=updated&direction=desc&per_page=100"],
        ["api", f"repos/{SLUG}/pulls?state=open"],
        ["api", f"repos/{SLUG}/pulls/999/comments?per_page=100"],
        ["api", "-X", "POST", f"repos/{SLUG}/issues/1/comments"],
        ["api", f"repos/{SLUG}/actions/runs"],
        ["pr", "list"],
        [],
    ],
)
def test_anything_else_is_a_404(store: Path, monkeypatch, argv: list[str]) -> None:
    before = _snapshot(store)
    code, out, err = _call(store, monkeypatch, *argv)
    assert code == 1 and out == "" and "HTTP 404" in err
    assert _snapshot(store) == before  # never writes


def test_no_store_means_404(store: Path, monkeypatch) -> None:
    monkeypatch.delenv("GH_SHIM_STORE", raising=False)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        assert ghshim.main(["api", f"repos/{SLUG}/collaborators/eval-owner/permission"]) == 1

#!/usr/bin/env python3
"""A read-only stand-in for ``gh api``, answered from a chain's ghstore.

The learn chain's harvest (``signals.py harvest``) reads closed PRs, their
review comments, reviews and conversation comments, and collaborator
permissions through ``gh api``. In the eval those come from a directory per
chain, written only by the runner:

    ghstore/pulls/<n>.json                    one PR, as the REST API returns it
    ghstore/pulls/<n>/comments.json           review comments
    ghstore/pulls/<n>/reviews.json            reviews
    ghstore/issues/<n>/comments.json          conversation comments
    ghstore/collaborators/<login>.json        {"permission", "role_name"}

The shim answers exactly these requests (with or without --paginate) for the
chain's own repo slug, and anything else with "HTTP 404" and exit 1. It
never writes. Environment: GH_SHIM_STORE (the store), GH_SHIM_REPO (the
slug agents see).

Run as a module, it also holds the runner's store writer (:class:`Store`).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

_REPO = r"(?P<repo>[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100})"
_N = r"(?P<n>[1-9][0-9]{0,9})"
ROUTES = (
    ("closed", re.compile(rf"repos/{_REPO}/pulls\?state=closed&sort=updated&direction=desc&per_page=100")),
    ("review_comments", re.compile(rf"repos/{_REPO}/pulls/{_N}/comments(?:\?per_page=100)?")),
    ("reviews", re.compile(rf"repos/{_REPO}/pulls/{_N}/reviews(?:\?per_page=100)?")),
    ("issue_comments", re.compile(rf"repos/{_REPO}/issues/{_N}/comments(?:\?per_page=100)?")),
    ("permission", re.compile(rf"repos/{_REPO}/collaborators/(?P<login>[A-Za-z0-9][A-Za-z0-9-]{{0,38}}(?:\[bot\])?)/permission")),
)


def not_found(path: str) -> int:
    sys.stderr.write(f"gh: Not Found (HTTP 404)\n{{\"message\":\"Not Found\",\"path\":{json.dumps(path)}}}\n")
    return 1


def _read(path: Path, default: Any) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def answer(store: Path, repo: str, path: str) -> tuple[int, Any]:
    """(exit code, JSON body) for one ``gh api`` path."""
    path = path.lstrip("/")
    for kind, pattern in ROUTES:
        match = pattern.fullmatch(path)
        if not match:
            continue
        if match.group("repo").casefold() != repo.casefold():
            return 1, None
        if kind == "closed":
            pulls = []
            directory = store / "pulls"
            if directory.is_dir():
                for entry in sorted(directory.glob("*.json")):
                    pr = _read(entry, None)
                    if isinstance(pr, dict) and pr.get("state") == "closed":
                        pulls.append(pr)
            pulls.sort(key=lambda pr: (pr.get("updated_at") or "", pr.get("number") or 0), reverse=True)
            return 0, pulls[:100]
        if kind == "permission":
            body = _read(store / "collaborators" / f"{match.group('login')}.json", None)
            return (0, body) if isinstance(body, dict) else (1, None)
        n = match.group("n")
        file = {
            "review_comments": store / "pulls" / n / "comments.json",
            "reviews": store / "pulls" / n / "reviews.json",
            "issue_comments": store / "issues" / n / "comments.json",
        }[kind]
        if kind != "issue_comments" and not (store / "pulls" / f"{n}.json").is_file():
            return 1, None
        return 0, _read(file, [])
    return 1, None


def main(argv: list[str]) -> int:
    args = list(argv)
    if not args or args[0] != "api":
        return not_found(" ".join(args))
    args = args[1:]
    paths = [a for a in args if a != "--paginate"]
    if len(paths) != 1 or paths[0].startswith("-"):
        return not_found(" ".join(args))
    store = os.environ.get("GH_SHIM_STORE")
    repo = os.environ.get("GH_SHIM_REPO", "")
    if not store or not repo:
        return not_found(paths[0])
    code, body = answer(Path(store), repo, paths[0])
    if code != 0:
        return not_found(paths[0])
    sys.stdout.write(json.dumps(body))
    sys.stdout.write("\n")
    return 0


# --- the runner's writer -------------------------------------------------------------------


class Store:
    """The runner's side: creates and updates the store's files."""

    BOT = "cadence-eval[bot]"
    OWNER = "eval-owner"

    def __init__(self, root: Path, slug: str) -> None:
        self.root = root
        self.slug = slug
        (root / "pulls").mkdir(parents=True, exist_ok=True)
        (root / "issues").mkdir(parents=True, exist_ok=True)
        (root / "collaborators").mkdir(parents=True, exist_ok=True)
        owner = root / "collaborators" / f"{self.OWNER}.json"
        if not owner.exists():
            self._write(owner, {"permission": "admin", "role_name": "admin", "user": {"login": self.OWNER}})

    @staticmethod
    def _write(path: Path, obj: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(obj, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)

    def pr(self, number: int) -> dict[str, Any] | None:
        return _read(self.root / "pulls" / f"{number}.json", None)

    def put_pr(self, pr: dict[str, Any]) -> None:
        self._write(self.root / "pulls" / f"{pr['number']}.json", pr)

    def open_pr(
        self, number: int, *, head_ref: str, head_sha: str, base_sha: str, title: str, body: str,
        draft: bool, at: str,
    ) -> dict[str, Any]:
        repo = {"full_name": self.slug}
        pr = {
            "number": number,
            "state": "open",
            "draft": draft,
            "title": title,
            "body": body,
            "user": {"login": self.BOT, "type": "Bot"},
            "head": {"ref": head_ref, "sha": head_sha, "repo": repo},
            "base": {"ref": "main", "sha": base_sha, "repo": repo},
            "created_at": at,
            "updated_at": at,
            "closed_at": None,
            "merged_at": None,
            "merge_commit_sha": None,
        }
        self.put_pr(pr)
        return pr

    def update_head(self, number: int, head_sha: str, at: str, *, title: str | None = None, body: str | None = None) -> None:
        pr = self.pr(number)
        if pr is None:
            raise KeyError(number)
        pr["head"]["sha"] = head_sha
        pr["updated_at"] = at
        if title is not None:
            pr["title"] = title
        if body is not None:
            pr["body"] = body
        self.put_pr(pr)

    def merge(self, number: int, merge_sha: str, at: str) -> None:
        pr = self.pr(number)
        if pr is None:
            raise KeyError(number)
        pr.update({"state": "closed", "merged_at": at, "closed_at": at, "updated_at": at,
                   "merge_commit_sha": merge_sha, "draft": False})
        self.put_pr(pr)

    def open_by_head(self, head_ref: str) -> dict[str, Any] | None:
        for entry in sorted((self.root / "pulls").glob("*.json")):
            pr = _read(entry, None)
            if isinstance(pr, dict) and pr.get("state") == "open" and pr["head"]["ref"] == head_ref:
                return pr
        return None

    def comment(self, issue: int, body: str, at: str, comment_id: int) -> None:
        path = self.root / "issues" / str(issue) / "comments.json"
        comments = _read(path, [])
        comments.append(
            {
                "id": comment_id,
                "body": body,
                "user": {"login": self.OWNER, "type": "User"},
                "author_association": "OWNER",
                "created_at": at,
                "updated_at": at,
            }
        )
        self._write(path, comments)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

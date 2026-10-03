#!/usr/bin/env python3
"""Refuse commits that would put private details into this public repository.

This repository is public. Nothing from its maintainer's private
repositories (their names) and nothing personal (email addresses) may
reach it: not in files, commit messages, commit metadata or PR
descriptions.

The deny-list is never stored here; the list would itself be the leak.
It is read at check time:

- in CI from the PRIVACY_DENYLIST secret (one term per line or comma
  separated), passed through the environment;
- locally (pre-push hook) from ``gh repo list <owner> --visibility
  private`` plus this clone's git email settings, and PRIVACY_DENYLIST if
  set.

What is checked, for every commit in the range:

1. the author and committer email: must be a GitHub no-reply address
   (``...@users.noreply.github.com``) or ``noreply@github.com`` (commits
   made in the web UI);
2. the commit message;
3. every line the commit adds (removed lines are not checked);

plus, with ``--pr-body``, the pull request description.

A text fails when it contains a deny-list term as a whole token (case
insensitive; terms shorter than ``--min-term`` characters are skipped to
avoid matching common words), or an email address outside the allow-list
(no-reply addresses and the reserved example domains).

Output never repeats a matched term, line or address: CI logs of a
public repository are public. A finding names the file and line (or the
commit) and either "private term #N" (N indexes the sorted deny-list) or
"email address not on the allow-list".

Usage:
  privacy_check.py --range BASE..HEAD [--pr-body-env PR_BODY] [--local]
  privacy_check.py --ci            # range and PR body from GitHub event env
  privacy_check.py --tree          # audit every tracked file at HEAD
  privacy_check.py --pre-push      # git pre-push hook (reads stdin)

Exit codes: 0 clean, 1 findings, 2 usage or git error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ZERO_SHA = "0" * 40
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
ALLOWED_EMAIL_SUFFIXES = ("@users.noreply.github.com",)
ALLOWED_EMAILS = {"noreply@github.com", "noreply@anthropic.com", "git@github.com"}
# Reserved for documentation and testing (RFC 2606, RFC 6761).
ALLOWED_EMAIL_DOMAINS = ("example.com", "example.net", "example.org")
ALLOWED_EMAIL_TLDS = (".example", ".test", ".invalid", ".localhost")
COMMIT_EMAIL_OK = re.compile(r"(?:[^@\s]+@users\.noreply\.github\.com|noreply@github\.com)", re.I)
BINARY_SNIFF = 8000


@dataclass(frozen=True)
class Finding:
    where: str
    what: str

    def __str__(self) -> str:
        return f"{self.where}: {self.what}"


def git(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SystemExit(f"privacy_check: git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


# ---------- deny-list ----------

def parse_terms(raw: str) -> list[str]:
    terms = {t.strip().lower() for t in re.split(r"[\n,]+", raw or "") if t.strip()}
    return sorted(terms)


def local_terms(cwd: Path | None) -> list[str]:
    """Private repo names of the remote's owner, plus this clone's emails."""
    raw: list[str] = []
    url = subprocess.run(["git", "remote", "get-url", "origin"], cwd=cwd, capture_output=True,
                         text=True).stdout.strip()
    m = re.search(r"github\.com[:/]([^/]+)/", url)
    if m:
        proc = subprocess.run(["gh", "repo", "list", m.group(1), "--visibility", "private",
                               "--limit", "1000", "--json", "name"],
                              capture_output=True, text=True)
        if proc.returncode == 0 and proc.stdout.strip():
            raw += [r["name"] for r in json.loads(proc.stdout)]
        else:
            print("privacy_check: warning: could not list private repos with gh; "
                  "checking emails only", file=sys.stderr)
    for scope in ("--local", "--global"):
        out = subprocess.run(["git", "config", scope, "--get-all", "user.email"], cwd=cwd,
                             capture_output=True, text=True).stdout
        raw += [e for e in out.split() if not COMMIT_EMAIL_OK.fullmatch(e)]
    return parse_terms("\n".join(raw))


def compile_terms(terms: list[str], min_term: int) -> tuple[list[str], list[re.Pattern]]:
    used = [t for t in terms if len(t) >= min_term]
    pats = [re.compile(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", re.I) for t in used]
    return used, pats


# ---------- text checks ----------

def email_allowed(addr: str) -> bool:
    a = addr.lower()
    if a in ALLOWED_EMAILS or a.endswith(ALLOWED_EMAIL_SUFFIXES):
        return True
    domain = a.rsplit("@", 1)[-1]
    if domain in ALLOWED_EMAIL_DOMAINS or any(domain.endswith("." + d) for d in ALLOWED_EMAIL_DOMAINS):
        return True
    return domain.endswith(ALLOWED_EMAIL_TLDS)


def scan_text(text: str, where: str, pats: list[re.Pattern]) -> list[Finding]:
    found: list[Finding] = []
    for i, pat in enumerate(pats, start=1):
        if pat.search(text):
            found.append(Finding(where, f"private term #{i}"))
    for addr in EMAIL_RE.findall(text):
        if not email_allowed(addr):
            found.append(Finding(where, "email address not on the allow-list"))
            break
    return found


# ---------- ranges ----------

def commits_in(rng: str, cwd: Path | None) -> list[str]:
    out = git("rev-list", "--reverse", "--no-merges", rng, cwd=cwd)
    return [c for c in out.split() if c]


def check_commit(sha: str, pats: list[re.Pattern], cwd: Path | None) -> list[Finding]:
    short = sha[:10]
    found: list[Finding] = []
    meta = git("show", "-s", "--format=%ae%x00%ce%x00%B", sha, cwd=cwd)
    author, committer, message = (meta.split("\x00", 2) + ["", "", ""])[:3]
    if not COMMIT_EMAIL_OK.fullmatch(author.strip()):
        found.append(Finding(f"commit {short}", "author email is not a GitHub no-reply address"))
    if not COMMIT_EMAIL_OK.fullmatch(committer.strip()):
        found.append(Finding(f"commit {short}", "committer email is not a GitHub no-reply address"))
    found += scan_text(message, f"commit {short} message", pats)
    diff = git("show", "--format=", "--unified=0", "--no-color", "--no-ext-diff", "--no-renames", sha, cwd=cwd)
    path = "?"
    line_no = 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else line[4:]
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            line_no = int(m.group(1)) if m else 0
        elif line.startswith("+"):
            found += scan_text(line[1:], f"commit {short} {path}:{line_no}", pats)
            line_no += 1
    return found


def range_for_push(before: str, after: str, default_ref: str, cwd: Path | None) -> str:
    """The commits a push introduced: before..after, or since the default branch for a new branch."""
    if before and before != ZERO_SHA:
        probe = subprocess.run(["git", "cat-file", "-e", f"{before}^{{commit}}"], cwd=cwd,
                               capture_output=True)
        if probe.returncode == 0:
            base = git("merge-base", before, after, cwd=cwd).strip()
            return f"{base}..{after}"
    base = subprocess.run(["git", "merge-base", default_ref, after], cwd=cwd,
                          capture_output=True, text=True).stdout.strip()
    return f"{base}..{after}" if base else after


def tree_scan(pats: list[re.Pattern], cwd: Path | None) -> list[Finding]:
    found: list[Finding] = []
    for rel in git("ls-files", "-z", cwd=cwd).split("\0"):
        if not rel:
            continue
        p = (cwd or Path.cwd()) / rel
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:BINARY_SNIFF]:
            continue
        for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), start=1):
            found += scan_text(line, f"{rel}:{n}", pats)
    return found


# ---------- main ----------

def report(found: list[Finding], terms_used: int, mode: str) -> int:
    seen: set[str] = set()
    unique = [f for f in found if not (str(f) in seen or seen.add(str(f)))]
    for f in unique:
        print(f"privacy_check: {f}")
    if unique:
        print(f"privacy_check: {len(unique)} finding(s) ({mode}; {terms_used} private term(s) checked). "
              "Remove the private detail, or reword it generically; set the commit "
              "email with git config user.email <id>+<login>@users.noreply.github.com.")
        return 1
    print(f"privacy_check: clean ({mode}; {terms_used} private term(s) checked).")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--range", help="commit range to check, e.g. origin/factory..HEAD")
    mode.add_argument("--ci", action="store_true", help="read the range and PR body from GitHub Actions env")
    mode.add_argument("--tree", action="store_true", help="scan every tracked file at HEAD")
    mode.add_argument("--pre-push", action="store_true", help="run as a git pre-push hook")
    ap.add_argument("--local", action="store_true", help="add private repo names from gh and this clone's emails")
    ap.add_argument("--pr-body-env", help="name of an env var holding a PR description to check")
    ap.add_argument("--min-term", type=int, default=5, help="skip deny-list terms shorter than this (default 5)")
    ap.add_argument("--default-ref", default="origin/main", help="base for a new branch (default origin/main)")
    ap.add_argument("--repo", type=Path, default=None, help="repository path (default: current directory)")
    args = ap.parse_args(argv)
    cwd = args.repo

    terms = parse_terms(os.environ.get("PRIVACY_DENYLIST", ""))
    if args.local or args.pre_push:
        terms = sorted(set(terms) | set(local_terms(cwd)))
    used, pats = compile_terms(terms, args.min_term)
    if not used:
        print("privacy_check: warning: no private terms available (PRIVACY_DENYLIST unset); "
              "checking emails only", file=sys.stderr)

    found: list[Finding] = []
    if args.tree:
        return report(tree_scan(pats, cwd), len(used), "tree")

    if args.pre_push:
        for line in sys.stdin.read().splitlines():
            parts = line.split()
            if len(parts) != 4 or parts[0] == "(delete)" or parts[1] == ZERO_SHA:
                continue
            local_sha, remote_sha = parts[1], parts[3]
            rng = range_for_push(remote_sha, local_sha, args.default_ref, cwd)
            for sha in commits_in(rng, cwd):
                found += check_commit(sha, pats, cwd)
        return report(found, len(used), "pre-push")

    pr_body_env = args.pr_body_env
    if args.ci:
        event = os.environ.get("EVENT_NAME", "")
        if event == "pull_request":
            rng = f"{os.environ['PR_BASE']}..{os.environ['PR_HEAD']}"
            pr_body_env = pr_body_env or "PR_BODY"
        else:
            rng = range_for_push(os.environ.get("BEFORE", ""), os.environ["AFTER"], args.default_ref, cwd)
    else:
        rng = args.range
    for sha in commits_in(rng, cwd):
        found += check_commit(sha, pats, cwd)
    if pr_body_env and os.environ.get(pr_body_env):
        found += scan_text(os.environ[pr_body_env], "pull request description", pats)
    return report(found, len(used), "ci" if args.ci else f"range {rng}")


if __name__ == "__main__":
    sys.exit(main())

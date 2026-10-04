"""Privacy checks: nothing private reaches a public file or an agent.

Two uses:

- ``privacy-check`` scans the added and changed files of a checkout (this
  public repository) for built-in patterns (local user paths, e-mail
  addresses, API key prefixes), for every entry of a private denylist and
  for every canary. It exits 1 on any hit and prints only the file, the
  line number and which rule matched, never the match itself.
- the canary guard: every input the runner writes where an agent can read
  it (issue, spec, prompts, the seed overlay) is checked for canaries,
  strings that exist only in the hidden graders. A hit stops the attempt.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

# Built from pieces so this file never matches its own rules.
_KEY_PREFIX = "sk" + "-ant-"
_ALLOWED_HOMES = ("runner",)  # the sandbox's own /home/runner
_ALLOWED_EMAIL_DOMAINS = ("localhost", "example.com", "example.org", "users.noreply.github.com")

BUILTIN: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("windows-user-path", re.compile(r"\b[A-Za-z]:(?:\\\\|\\|/)+Users(?:\\\\|\\|/)+[^\\/\s\"'`<>]+", re.I)),
    ("wsl-user-path", re.compile(r"/mnt/[a-z]/Users/[^/\s\"'`<>]+", re.I)),
    ("home-path", re.compile(r"(?<![A-Za-z0-9_.-])/home/([A-Za-z0-9_.-]+)")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*)\b")),
    ("api-key", re.compile(re.escape(_KEY_PREFIX))),
)


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    rule: str


def _builtin_hits(text: str) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in BUILTIN:
            for match in pattern.finditer(line):
                if rule == "home-path" and match.group(1).rstrip(".") in _ALLOWED_HOMES:
                    continue
                if rule == "email":
                    domain = match.group(1).casefold()
                    if domain in _ALLOWED_EMAIL_DOMAINS or "." not in domain and domain != "localhost":
                        continue
                hits.append((lineno, rule))
                break
    return hits


def _deny_pattern(entry: str) -> re.Pattern[str]:
    """Whole-word for word-like entries, so a short entry never hits a longer word."""
    body = re.escape(entry)
    if entry[:1].isalnum():
        body = r"(?<![A-Za-z0-9])" + body
    if entry[-1:].isalnum():
        body = body + r"(?![A-Za-z0-9])"
    return re.compile(body, re.I)


def load_list(path: Path | None) -> list[str]:
    if path is None:
        return []
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


def scan_text(
    name: str, text: str, denylist: Sequence[str] = (), canaries: Sequence[str] = ()
) -> list[Hit]:
    hits = [Hit(name, line, rule) for line, rule in _builtin_hits(text)]
    patterns = [(f"denylist[{i}]", _deny_pattern(e)) for i, e in enumerate(denylist)]
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in patterns:
            if pattern.search(line):
                hits.append(Hit(name, lineno, rule))
        for i, canary in enumerate(canaries):
            if canary in line:
                hits.append(Hit(name, lineno, f"canary[{i}]"))
    return hits


def canary_hits(text: str, canaries: Iterable[str]) -> list[int]:
    """Indexes of the canaries found in ``text`` (never the strings)."""
    return [i for i, canary in enumerate(canaries) if canary and canary in text]


def guard_inputs(label: str, text: str, canaries: Sequence[str]) -> None:
    from config import EvalError

    found = canary_hits(text, canaries)
    if found:
        raise EvalError(f"canary guard: {label} holds canary #{found[0]}; refusing to hand it to an agent", 1)


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout


def changed_files(repo: Path, base: str | None) -> list[str]:
    """Added and changed files against ``base`` (default HEAD), plus untracked."""
    names: set[str] = set()
    out = _git(repo, "diff", "--name-only", "--diff-filter=AMR", "-z", base or "HEAD")
    names.update(n for n in out.split("\0") if n)
    out = _git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    names.update(n for n in out.split("\0") if n)
    return sorted(names)


def check(
    repo: Path,
    *,
    denylist: Sequence[str],
    canaries: Sequence[str],
    base: str | None = None,
    paths: Sequence[str] | None = None,
) -> list[Hit]:
    names = list(paths) if paths else changed_files(repo, base)
    hits: list[Hit] = []
    for name in names:
        path = repo / name
        if not path.is_file() or path.is_symlink():
            continue
        data = path.read_bytes()
        if b"\0" in data[:8192]:
            continue  # binary
        hits.extend(scan_text(name, data.decode("utf-8", "replace"), denylist, canaries))
    return hits


def main_check(args) -> int:  # called by run_eval.py
    repo = Path(args.repo).resolve() if getattr(args, "repo", None) else Path.cwd()
    denylist = load_list(Path(args.denylist))
    canaries = load_list(Path(args.canaries)) if args.canaries else []
    hits = check(repo, denylist=denylist, canaries=canaries, base=args.base, paths=args.paths)
    for hit in hits:
        print(f"{hit.path}:{hit.line}: {hit.rule}")
    print(f"privacy-check: {len(hits)} hit(s)")
    return 1 if hits else 0

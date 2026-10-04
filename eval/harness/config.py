"""The eval's configuration: eval.yaml and the private files it names.

Everything repo-specific (repo list, pinned commits, tickets, check names,
paths) lives in a private folder outside this repository. This module only
knows the shapes (schemas/*.schema.json) and the path rules:

- the runner reads only configured paths that lie under ``read_roots``
  (plus the configured Cadence clone), never a path with a
  ``forbidden_path_parts`` entry among its parts, and never copies or binds
  a file whose name is in ``never_bind``;
- it writes only under ``home`` and ``results_dir``.

Exit codes used across the harness: 0 ok, 1 a check failed, 2 bad input or
refused, 3 stopped on budget.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import yaml

HARNESS_DIR = Path(__file__).resolve().parent
SCHEMA_DIR = HARNESS_DIR / "schemas"

EXIT_OK = 0
EXIT_CHECK = 1
EXIT_BAD = 2
EXIT_BUDGET = 3

# Generic defaults only: nothing here may name a repo, a person or a path.
DEFAULTS: dict[str, Any] = {
    "home": "~/.cadence-eval",
    "model": "",
    "sandbox": "bwrap",
    "score_network": "offline",
    "allow_no_subprocess_scrub": False,
}

# The bump of this number re-keys every preregistration (report.py's rules).
DECISION_RULE_VERSION = "1"


class EvalError(Exception):
    """A refusal or bad input; carries the process exit code."""

    def __init__(self, message: str, code: int = EXIT_BAD) -> None:
        super().__init__(message)
        self.code = code


# --- small helpers -----------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(obj: Any) -> str:
    return sha256_bytes(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def iso_utc(epoch: float) -> str:
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def wall_now() -> str:
    return iso_utc(time.time())


def read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, obj: Any) -> None:
    """Atomic: a crash never leaves half a record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(obj, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def load_schema(name: str) -> dict[str, Any]:
    return read_json(SCHEMA_DIR / f"{name}.schema.json")


def schema_errors(name: str, obj: Any) -> list[str]:
    from jsonschema import Draft202012Validator

    validator = Draft202012Validator(load_schema(name))
    errors = sorted(validator.iter_errors(obj), key=lambda e: list(e.absolute_path))
    return [
        f"{'.'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in errors[:20]
    ]


def require_valid(name: str, obj: Any, where: str) -> None:
    problems = schema_errors(name, obj)
    if problems:
        raise EvalError(f"{where} does not match {name}.schema.json: " + "; ".join(problems[:5]))


# --- path rules ----------------------------------------------------------------------


def _norm(path: Path) -> Path:
    return Path(os.path.normpath(os.path.abspath(os.path.expanduser(str(path)))))


def _parts(path: Path) -> list[str]:
    return [p for p in re.split(r"[\\/]+", str(path)) if p]


@dataclass
class PathPolicy:
    read_roots: list[Path]
    forbidden_parts: list[str]
    never_bind: list[str]
    write_roots: list[Path] = field(default_factory=list)

    def forbidden(self, path: Path | str) -> bool:
        lowered = {p.casefold() for p in _parts(Path(path))}
        return any(part.casefold() in lowered for part in self.forbidden_parts)

    def never(self, name: str) -> bool:
        return any(name.casefold() == n.casefold() for n in self.never_bind)

    @staticmethod
    def _under(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def check_read(self, path: Path | str) -> Path:
        """``path`` normalised, or EvalError: outside read_roots or forbidden."""
        resolved = _norm(Path(path))
        if self.forbidden(resolved):
            raise EvalError(f"refused: {resolved} holds a forbidden path part")
        if self.never(resolved.name):
            raise EvalError(f"refused: {resolved.name} is a never-bind file")
        if not any(self._under(resolved, root) for root in self.read_roots):
            raise EvalError(f"refused: {resolved} is not under read_roots")
        return resolved

    def check_write(self, path: Path | str) -> Path:
        resolved = _norm(Path(path))
        if self.forbidden(resolved):
            raise EvalError(f"refused: {resolved} holds a forbidden path part")
        if not any(self._under(resolved, root) for root in self.write_roots):
            raise EvalError(f"refused: {resolved} is outside home and results_dir")
        return resolved

    def walk(self, root: Path) -> Iterable[Path]:
        """Regular files under a configured directory, skipping forbidden
        parts, never-bind names and symlinks. Lists only ``root`` and below."""
        root = self.check_read(root)
        for dirpath, dirnames, filenames in os.walk(root):
            here = Path(dirpath)
            dirnames[:] = sorted(
                d for d in dirnames
                if not self.forbidden(here / d) and not (here / d).is_symlink()
                and d not in (".git", "node_modules")
            )
            for name in sorted(filenames):
                path = here / name
                if self.forbidden(path) or self.never(name) or path.is_symlink():
                    continue
                yield path


# --- the configuration ---------------------------------------------------------------


@dataclass
class HiddenConf:
    harness_dir: Path
    lib_dir: Path
    entry: str
    extra_args: list[str]


@dataclass
class CalibConf:
    repo: Path
    base: str
    branches: dict[str, list[str]]  # branch -> excludes


@dataclass
class RepoConf:
    id: str
    slug: str
    source: Path
    sha: str
    overlay_dir: Path
    tickets: list[str]
    hidden: HiddenConf
    calib: CalibConf | None


@dataclass
class Config:
    path: Path
    dir: Path
    raw: dict[str, Any]
    sha256: str
    home: Path
    results_dir: Path
    policy: PathPolicy
    cadence_repo: Path
    cadence_sha: str
    claude_code_version: str
    model: str
    sandbox: str
    score_network: str
    allow_no_subprocess_scrub: bool
    caps: dict[str, Any]
    timeouts_min: dict[str, int]
    factory: dict[str, Any]
    clock_epoch: int
    slot_hours: int
    sessions: int
    score_workers: int
    budget_usd: float
    hidden_command: list[str]
    repos: list[RepoConf]
    files: dict[str, Path]

    def repo(self, repo_id: str) -> RepoConf:
        for repo in self.repos:
            if repo.id == repo_id:
                return repo
        raise EvalError(f"no repo {repo_id!r} in {self.path}")

    @property
    def cache_root(self) -> Path:
        return self.home / "cache" / self.cadence_sha

    def results(self, run_id: str) -> Path:
        return self.results_dir / run_id

    def work(self, run_id: str) -> Path:
        return self.home / "work" / run_id


def _resolve(base: Path, value: str) -> Path:
    expanded = os.path.expanduser(value)
    path = Path(expanded)
    if not path.is_absolute():
        path = base / path
    return _norm(path)


def load_config(path: Path | str) -> Config:
    path = _norm(Path(path))
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise EvalError(f"cannot read config {path}: {exc}") from exc
    try:
        raw = yaml.safe_load(data.decode("utf-8-sig"))
    except (yaml.YAMLError, UnicodeDecodeError, ValueError) as exc:
        raise EvalError(f"malformed YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise EvalError(f"{path} is not a mapping")
    require_valid("eval-config", raw, str(path))
    merged = {**DEFAULTS, **raw}
    base = path.parent
    home = _resolve(base, merged["home"])
    results_dir = _resolve(base, merged["results_dir"])
    cadence_repo = _resolve(base, merged["cadence"]["repo"])
    read_roots = [_resolve(base, r) for r in merged["read_roots"]]
    # The configured Cadence clone is public code; it is read like a root.
    policy = PathPolicy(
        read_roots=read_roots + [cadence_repo],
        forbidden_parts=list(merged["forbidden_path_parts"]),
        never_bind=list(merged["never_bind"]),
        write_roots=[home, results_dir],
    )
    for where in (home, results_dir, cadence_repo):
        if policy.forbidden(where):
            raise EvalError(f"refused: {where} holds a forbidden path part")
    if not policy.forbidden_parts:
        print("WARN: forbidden_path_parts is empty", file=sys.stderr, flush=True)
    files = {key: _resolve(base, value) for key, value in merged["files"].items()}
    for key, value in files.items():
        policy.check_read(value)
    repos: list[RepoConf] = []
    seen: set[str] = set()
    for entry in merged["repos"]:
        if entry["id"] in seen:
            raise EvalError(f"repo id {entry['id']!r} appears twice")
        seen.add(entry["id"])
        hidden = entry["hidden"]
        calib = entry.get("calib")
        repo = RepoConf(
            id=entry["id"],
            slug=entry["slug"],
            source=policy.check_read(_resolve(base, entry["source"])),
            sha=entry["sha"],
            overlay_dir=policy.check_read(_resolve(base, entry["overlay_dir"])),
            tickets=list(entry["tickets"]),
            hidden=HiddenConf(
                harness_dir=policy.check_read(_resolve(base, hidden["harness_dir"])),
                lib_dir=policy.check_read(_resolve(base, hidden["lib_dir"])),
                entry=hidden["entry"],
                extra_args=list(hidden.get("extra_args") or []),
            ),
            calib=None
            if not calib
            else CalibConf(
                repo=policy.check_read(_resolve(base, calib["repo"])),
                base=calib["base"],
                branches={
                    name: list((spec or {}).get("exclude") or [])
                    for name, spec in (calib.get("branches") or {}).items()
                },
            ),
        )
        repos.append(repo)
    slugs = [r.slug for r in repos]
    if len(set(slugs)) != len(slugs):
        raise EvalError("two repos share a slug")
    for token in ("{harness}", "{repo}", "{ref}"):
        if not any(token in part for part in merged["hidden_command"]):
            raise EvalError(f"hidden_command must contain {token}")
    if merged["sandbox"] == "none":
        print("NOTE: sandbox: none (allowed with --agent stub only)", file=sys.stderr, flush=True)
    return Config(
        path=path,
        dir=base,
        raw=raw,
        sha256=sha256_bytes(data),
        home=home,
        results_dir=results_dir,
        policy=policy,
        cadence_repo=cadence_repo,
        cadence_sha=merged["cadence"]["sha"],
        claude_code_version=merged["claude_code_version"],
        model=merged.get("model") or "",
        sandbox=merged["sandbox"],
        score_network=merged.get("score_network") or "offline",
        allow_no_subprocess_scrub=bool(merged.get("allow_no_subprocess_scrub")),
        caps=dict(merged["caps"]),
        timeouts_min=dict(merged["timeouts_min"]),
        factory=dict(merged["factory"]),
        clock_epoch=int(merged["logical_clock"]["epoch"]),
        slot_hours=int(merged["logical_clock"]["slot_hours"]),
        sessions=int(merged["concurrency"]["sessions"]),
        score_workers=int(merged["concurrency"]["score_workers"]),
        budget_usd=float(merged["budget_usd"]),
        hidden_command=list(merged["hidden_command"]),
        repos=repos,
        files=files,
    )


# --- private files -------------------------------------------------------------------


@dataclass
class Ticket:
    stem: str        # <repo>-<n>, the file name
    id: str          # <repo>.T<n>
    repo: str
    issue: int
    title: str
    body: str
    path: Path
    sha256: str

    def keys(self) -> tuple[str, str]:
        return (self.stem, self.id)


_FRONT = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.S)


def parse_ticket(path: Path, stem: str) -> Ticket:
    data = path.read_bytes()
    text = data.decode("utf-8-sig").replace("\r\n", "\n")
    match = _FRONT.match(text)
    if not match:
        raise EvalError(f"{path}: no YAML front matter")
    try:
        front = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise EvalError(f"{path}: bad front matter: {exc}") from exc
    require_valid("ticket", front, str(path))
    body = match.group(2).strip("\n") + "\n"
    return Ticket(
        stem=stem,
        id=front["id"],
        repo=front["repo"],
        issue=int(front["issue"]),
        title=front["title"],
        body=body,
        path=path,
        sha256=sha256_bytes(data),
    )


def load_tickets(cfg: Config) -> dict[str, list[Ticket]]:
    """Per repo, the tickets in TASK order; issue number = position."""
    out: dict[str, list[Ticket]] = {}
    for repo in cfg.repos:
        tickets: list[Ticket] = []
        for position, stem in enumerate(repo.tickets, start=1):
            path = cfg.policy.check_read(cfg.files["tickets_dir"] / f"{stem}.md")
            ticket = parse_ticket(path, stem)
            if ticket.repo != repo.id:
                raise EvalError(f"{path}: repo {ticket.repo!r} is not {repo.id!r}")
            if ticket.issue != position:
                raise EvalError(f"{path}: issue {ticket.issue} is not its position {position}")
            tickets.append(ticket)
        out[repo.id] = tickets
    return out


def _load_yaml(cfg: Config, key: str, schema: str) -> Any:
    path = cfg.policy.check_read(cfg.files[key])
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except (OSError, yaml.YAMLError) as exc:
        raise EvalError(f"cannot read {path}: {exc}") from exc
    require_valid(schema, raw, str(path))
    return raw


@dataclass
class Private:
    replies: dict[str, Any]
    reviewer: dict[str, Any]
    stubs: dict[str, Any]
    checks: dict[str, Any]
    detectors_path: Path
    expectations: dict[str, Any]
    canaries: list[str]
    tickets: dict[str, list[Ticket]]

    def ticket_value(self, mapping: dict[str, Any], ticket: Ticket) -> Any:
        for key in ticket.keys():
            if key in mapping:
                return mapping[key]
        return None


def load_canaries(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def load_private(cfg: Config) -> Private:
    detectors = cfg.policy.check_read(cfg.files["detectors"])
    raw = read_json(detectors)
    if not isinstance(raw, dict) or raw.get("schema") != "cadence.detectors/1":
        raise EvalError(f"{detectors}: schema must be cadence.detectors/1")
    expectations = _load_yaml(cfg, "expectations", "expectations")
    checks = _load_yaml(cfg, "checks", "checks")
    for repo in cfg.repos:
        if repo.id not in checks["repos"]:
            raise EvalError(f"checks.yaml has no entry for repo {repo.id!r}")
        denominator = set(checks["repos"][repo.id]["denominator"])
        for name in checks["repos"][repo.id]["labels"]:
            if name not in denominator:
                raise EvalError(f"checks.yaml: label {name!r} is not in {repo.id}'s denominator")
    return Private(
        replies=_load_yaml(cfg, "replies", "replies"),
        reviewer=_load_yaml(cfg, "reviewer", "reviewer"),
        stubs=_load_yaml(cfg, "stubs", "stubs"),
        checks=checks,
        detectors_path=detectors,
        expectations=expectations,
        canaries=load_canaries(cfg.policy.check_read(cfg.files["canaries"])),
        tickets=load_tickets(cfg),
    )


def private_hashes(cfg: Config, private: Private) -> dict[str, str]:
    """sha256 of every private input, for run.json and the preregistration."""
    tickets = sha256_json(
        {t.stem: t.sha256 for repo in cfg.repos for t in private.tickets[repo.id]}
    )
    out = {"config": cfg.sha256, "tickets": tickets}
    for key in ("replies", "reviewer", "stubs", "checks", "detectors", "expectations"):
        out[key] = sha256_file(cfg.files[key])
    return out


def posix(path: Path | str) -> str:
    return PurePosixPath(Path(path).as_posix()).as_posix()

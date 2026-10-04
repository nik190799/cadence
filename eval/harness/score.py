"""Hidden scoring: the private harness runs, offline, on each result.

The agents never see the hidden tests. For every tree to score:

1. A scoring bare repo: ``main`` is the pinned pristine commit and
   ``eval-result`` the commit being scored (rebuilt from the pinned commit
   plus the stored patch, and checked against the expected tree).
2. Prefetch (fetch profile): ``npm install --no-audit --no-fund
   --ignore-scripts`` on the result, with a fresh copy of the warm npm
   cache, in a sandbox that holds no grader file.
3. Score (score profile, no network but loopback): ``hidden_command``,
   e.g. ``node /opt/grader/hidden/<h>/run.mjs /srv/result.git eval-result``,
   with npm offline on that cache copy. The per-score /tmp (with the
   harness's own clone) is deleted afterwards, so agent code that runs
   during scoring can send the hidden tests nowhere.

Output lines (ANSI stripped): ``PASS  <check>[  (<detail>)]``,
``FAIL  <check>[  (<detail>)]``, ``INFO  <key>  <text>`` and the summary
``== <p> pass, <f> fail ==``. A denominator check that was not printed
counts as FAIL; extra checks are kept. Scores are cached by repo, tree and
harness sha, and never reach state, ghstore, chains or prompts.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from config import Config, EvalError, read_json, sha256_bytes, sha256_file, wall_now, write_json
import sandbox as sb

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
CHECK_LINE = re.compile(r"^(PASS|FAIL)  (.+?)(?:  \((.*)\))?$")
INFO_LINE = re.compile(r"^INFO  (\S.*?)  (.*)$")
SUMMARY_LINE = re.compile(r"^== (\d+) pass, (\d+) fail ==$")
INSTALL_FAILED = re.compile(r"npm install failed", re.I)
CLONE_FAILED = re.compile(r"(clone|git clone)[^\n]{0,80}(fail|error|fatal)|fatal: [^\n]*clone", re.I)
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


@dataclass
class Parsed:
    checks: dict[str, dict[str, Any]]
    info: list[dict[str, str]]
    summary: dict[str, int] | None
    install_failed: bool
    clone_failed: bool


def parse_output(text: str) -> Parsed:
    checks: dict[str, dict[str, Any]] = {}
    info: list[dict[str, str]] = []
    summary = None
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = ANSI.sub("", raw).rstrip("\r")
        m = CHECK_LINE.match(line)
        if m:
            name = m.group(2)
            # A check printed twice keeps its worst result.
            if checks.get(name, {}).get("result") != "FAIL":
                checks[name] = {"result": m.group(1), "detail": m.group(3)}
            continue
        m = INFO_LINE.match(line)
        if m:
            info.append({"key": m.group(1), "text": m.group(2)[:500]})
            continue
        m = SUMMARY_LINE.match(line)
        if m:
            summary = {"pass": int(m.group(1)), "fail": int(m.group(2))}
    plain = ANSI.sub("", text)
    return Parsed(checks, info, summary, bool(INSTALL_FAILED.search(plain)), bool(CLONE_FAILED.search(plain)))


def score_record(
    parsed: Parsed, *, exit_code: int | None, timed_out: bool, denominator: list[str],
    repo: str, tree: str, harness_sha: str, tries: int, kind: str | None = None,
) -> dict[str, Any]:
    if timed_out:
        status = "timeout"
    elif parsed.summary is None:
        status = "harness-error"
    else:
        status = "ok"
    checks: dict[str, dict[str, Any]] = {}
    den = set(denominator)
    for name in denominator:
        got = parsed.checks.get(name)
        result = "FAIL" if parsed.install_failed or got is None else got["result"]
        checks[name] = {"result": result, "detail": (got or {}).get("detail"),
                        "printed": got is not None, "in_denominator": True}
    for name, got in parsed.checks.items():
        if name not in den:
            checks[name] = {"result": got["result"], "detail": got.get("detail"),
                            "printed": True, "in_denominator": False}
    passed = sum(1 for n in denominator if checks[n]["result"] == "PASS")
    return {
        "schema": "cadence-eval.score/1",
        "repo": repo,
        "tree": tree,
        "harness_sha256": harness_sha,
        "status": status,
        "exit": exit_code,
        "checks": checks,
        "info": parsed.info,
        "summary": parsed.summary,
        "denominator": len(denominator),
        "pass": passed,
        "fail": len(denominator) - passed,
        "fraction": (passed / len(denominator)) if denominator else None,
        "repo_pass": bool(status == "ok" and exit_code == 0 and passed == len(denominator)),
        "install_failed": parsed.install_failed,
        "tries": tries,
        "scored_at": wall_now(),
        "kind": kind,
    }


def ticket_pass(record: dict[str, Any], owned: list[str], regression: list[str]) -> bool | None:
    if record is None or record.get("status") != "ok":
        return None
    checks = record["checks"]
    names = list(owned) + list(regression)
    return all(checks.get(n, {}).get("result") == "PASS" for n in names)


# --- the check map ----------------------------------------------------------------------------


@dataclass
class CheckMap:
    denominator: list[str]
    labels: dict[str, dict[str, Any]]

    def owned(self, keys: tuple[str, ...]) -> list[str]:
        return [n for n, lab in self.labels.items()
                if lab["kind"] == "owned" and any(k in (lab.get("tickets") or []) for k in keys)]

    def regression(self) -> list[str]:
        return [n for n, lab in self.labels.items() if lab["kind"] == "regression"]

    def noticing(self) -> list[str]:
        return [n for n, lab in self.labels.items() if lab["kind"] == "noticing"]


def check_map(checks: dict[str, Any], repo: str) -> CheckMap:
    entry = checks["repos"][repo]
    return CheckMap(list(entry["denominator"]), dict(entry["labels"]))


# --- the grader copy --------------------------------------------------------------------------


def grader_root(cfg: Config) -> Path:
    return cfg.cache_root / "grader"


def harness_rel(cfg: Config, repo_id: str) -> tuple[str, str]:
    """(harness dir, lib dir) relative to the grader copy, mirroring their
    layout under their common parent so relative imports keep working."""
    repo = cfg.repo(repo_id)
    common = Path(os.path.commonpath([str(repo.hidden.harness_dir), str(repo.hidden.lib_dir)]))
    return (
        repo.hidden.harness_dir.relative_to(common).as_posix(),
        repo.hidden.lib_dir.relative_to(common).as_posix(),
    )


def copy_grader(cfg: Config) -> dict[str, str]:
    """Copy each repo's hidden harness dir and the lib dir (never a
    never_bind name, a forbidden part or a symlink). Returns relpath -> sha256."""
    dest = grader_root(cfg)
    staging = dest.parent / "grader.tmp"
    sb.rmtree(staging)
    shas: dict[str, str] = {}
    for repo in cfg.repos:
        h_rel, l_rel = harness_rel(cfg, repo.id)
        for src_dir, rel in ((repo.hidden.harness_dir, h_rel), (repo.hidden.lib_dir, l_rel)):
            for path in cfg.policy.walk(src_dir):
                target = staging / rel / path.relative_to(src_dir)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                shas[(Path(rel) / path.relative_to(src_dir)).as_posix()] = sha256_file(target)
    sb.rmtree(dest)
    os.replace(staging, dest)
    write_json(dest.parent / "grader.json", shas)
    return shas


def harness_sha(cfg: Config, repo_id: str) -> str:
    shas = read_json(cfg.cache_root / "grader.json")
    h_rel, l_rel = harness_rel(cfg, repo_id)
    mine = {k: v for k, v in shas.items() if k.startswith(h_rel + "/") or k.startswith(l_rel + "/")}
    return sha256_bytes(json.dumps(mine, sort_keys=True).encode())


# --- materializing a result ---------------------------------------------------------------------


def score_base(cfg: Config, repo_id: str) -> Path:
    """A bare copy of the repo under test (sources are only cloned from)."""
    repo = cfg.repo(repo_id)
    base = cfg.cache_root / "score-base" / f"{repo_id}.git"
    lock = _lock(base)
    with lock:
        if not (base / "HEAD").exists():
            base.parent.mkdir(parents=True, exist_ok=True)
            tmp = base.with_name(base.name + ".tmp")
            sb.rmtree(tmp)
            sb.git(None, "clone", "--bare", "--no-hardlinks", "--quiet", str(repo.source), str(tmp))
            sb.git(tmp, "config", "uploadpack.allowAnySHA1InWant", "true")
            os.replace(tmp, base)
        sb.git(base, "cat-file", "-e", f"{repo.sha}^{{commit}}")
    return base


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_SEQ = itertools.count(1)  # next() on a count is atomic under the GIL


def _lock(path: Path) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(path), threading.Lock())


def result_commit(cfg: Config, repo_id: str, patch: Path | None, expect_tree: str | None, epoch: int) -> tuple[str, str]:
    """(commit, tree) of the pinned commit plus ``patch``, built with a
    private index in the score-base repo."""
    repo = cfg.repo(repo_id)
    base = score_base(cfg, repo_id)
    index = base / f"index-{threading.get_ident()}-{os.getpid()}"
    env_date = f"@{epoch} +0000"
    env = sb.git_env(env_date)
    env["GIT_INDEX_FILE"] = str(index)
    env["GIT_CONFIG_GLOBAL"] = os.devnull

    def run(*args: str, stdin: bytes | None = None) -> str:
        res = sb.spawn(["git", *sb.GIT_SAFE, "--git-dir", str(base), *args], env=env, stdin=stdin, cwd=os.path.abspath(os.sep))
        if not res.ok:
            raise EvalError(f"git {' '.join(args[:3])}: {res.stderr.decode(errors='replace').strip()[:300]}", 1)
        return res.stdout.decode().strip()

    try:
        run("read-tree", repo.sha)
        if patch is not None and patch.stat().st_size > 0:
            run("apply", "--cached", "--binary", "--whitespace=nowarn", str(patch))
        tree = run("write-tree")
        if expect_tree and tree != expect_tree:
            raise EvalError(f"{repo_id}: the rebuilt tree {tree} is not the expected {expect_tree}", 1)
        commit = run("commit-tree", tree, "-p", repo.sha, "-m", "eval-result")
        return commit, tree
    finally:
        try:
            index.unlink()
        except OSError:
            pass


# --- running the harness -------------------------------------------------------------------------


@dataclass
class Job:
    repo: str
    tree: str
    patch: Path | None            # pinned -> tree; None for the pristine commit
    kind: str = "result"
    refs: list[dict[str, Any]] = field(default_factory=list)


def cache_path(cfg: Config, run_id: str, repo_id: str, tree: str, h_sha: str) -> Path:
    return cfg.results(run_id) / "scores" / f"{repo_id}-{tree}-{h_sha[:16]}.json"


class Scorer:
    def __init__(self, cfg: Config, run_id: str, checks: dict[str, Any], *, log: Callable[[str], None] = print) -> None:
        self.cfg = cfg
        self.run_id = run_id
        self.checks = checks
        self.log = log
        self.work = cfg.work(run_id) / "score"

    def _box(self, profile: str, root: Path, binds: list[sb.Bind]) -> sb.Box:
        return sb.Box(self.cfg.sandbox, profile, self.cfg.home, root, binds=binds, guard=True)

    def warm_cache(self) -> Path:
        return self.cfg.cache_root / "npm-warm"

    def run_once(self, job: Job, epoch: int) -> dict[str, Any]:
        cfg = self.cfg
        repo = cfg.repo(job.repo)
        den = check_map(self.checks, job.repo).denominator
        h_sha = harness_sha(cfg, job.repo)
        commit, tree = result_commit(cfg, job.repo, job.patch, job.tree if job.patch is not None else None, epoch)
        # Short and unique: with sandbox: none the harness's TMPDIR is under
        # this root, and tools that bind a Unix socket there (108-byte limit)
        # fail on a long path.
        root = self.work / f"{tree[:8]}-{next(_SEQ)}"
        sb.rmtree(root)
        try:
            result_git = root / "result.git"
            sb.git(None, "init", "--bare", "--quiet", str(result_git))
            base = score_base(cfg, job.repo)
            sb.git(base, "push", "--quiet", str(result_git), f"{repo.sha}:refs/heads/main", f"{commit}:refs/heads/eval-result")
            cache = root / "npm-cache"
            warm = self.warm_cache()
            if warm.is_dir():
                shutil.copytree(warm, cache, symlinks=True)
            else:
                cache.mkdir(parents=True)
            has_pkg = sb.git(base, "cat-file", "-e", f"{commit}:package.json", check=False).ok
            if has_pkg:
                tree_dir = root / "fetch" / "work" / "result" / "result"
                tree_dir.mkdir(parents=True)
                arch = sb.git(base, "archive", "--format=tar", commit)
                sb.untar(arch.stdout, tree_dir)
                fbox = self._box("fetch", root / "fetch", [sb.Bind(cache, "/home/runner/.npm-cache", ro=False)])
                fbox.env["npm_config_cache"] = fbox.inside(cache) if cfg.sandbox == "none" else "/home/runner/.npm-cache"
                fetched = fbox.run(["npm", "install", "--no-audit", "--no-fund", "--ignore-scripts"],
                                   cwd=tree_dir, timeout=cfg.timeouts_min["score"] * 60)
                if not fetched.ok:
                    self.log(f"score: prefetch for {job.repo} {tree[:12]} exited {fetched.exit}")
                sb.rmtree(root / "fetch")
            tmp = root / "tmp"
            (tmp / "home").mkdir(parents=True)
            grader = grader_root(cfg)
            h_rel, l_rel = harness_rel(cfg, job.repo)
            # Only this repo's harness dir and the lib dir, at their relative places.
            binds = [
                sb.Bind(grader / h_rel, f"{sb.OPT_GRADER}/{h_rel}", ro=True),
                sb.Bind(grader / l_rel, f"{sb.OPT_GRADER}/{l_rel}", ro=True),
                sb.Bind(result_git, sb.SRV_RESULT, ro=True),
                sb.Bind(tmp, "/tmp", ro=False),
                sb.Bind(cache, sb.SRV_CACHE, ro=False),
            ]
            box = self._box("score", root / "box", binds)
            box.net = cfg.score_network == "online"
            if cfg.sandbox == "bwrap":
                harness = f"{sb.OPT_GRADER}/{h_rel}/{repo.hidden.entry}"
                result_path, tmp_path, cache_path_in = sb.SRV_RESULT, "/tmp", sb.SRV_CACHE
            else:
                harness = (grader / h_rel / repo.hidden.entry).as_posix()
                result_path, tmp_path, cache_path_in = result_git.as_posix(), tmp.as_posix(), cache.as_posix()
            box.env.update({
                "npm_config_offline": "true" if cfg.score_network == "offline" else "false",
                "npm_config_cache": cache_path_in,
                "TMPDIR": tmp_path,
                "HOME": f"{tmp_path}/home",
            })
            argv = [
                part.replace("{harness}", harness).replace("{repo}", result_path).replace("{ref}", "eval-result")
                for part in cfg.hidden_command
            ] + list(repo.hidden.extra_args)
            res = box.run(argv, cwd=tmp, timeout=cfg.timeouts_min["score"] * 60)
            text = res.stdout.decode("utf-8", "replace") + "\n" + res.stderr.decode("utf-8", "replace")
            parsed = parse_output(res.stdout.decode("utf-8", "replace"))
            parsed.install_failed = parsed.install_failed or bool(INSTALL_FAILED.search(ANSI.sub("", text)))
            parsed.clone_failed = parsed.clone_failed or bool(CLONE_FAILED.search(ANSI.sub("", text)))
            record = score_record(parsed, exit_code=res.exit, timed_out=res.timed_out, denominator=den,
                                  repo=job.repo, tree=tree, harness_sha=h_sha, tries=1, kind=job.kind)
            record["_clone_failed"] = parsed.clone_failed
            return record
        finally:
            sb.rmtree(root)

    def score(self, job: Job, epoch: int, *, rescore: bool = False) -> dict[str, Any]:
        h_sha = harness_sha(self.cfg, job.repo)
        path = cache_path(self.cfg, self.run_id, job.repo, job.tree, h_sha)
        if path.is_file() and not rescore:
            return read_json(path)
        record: dict[str, Any] = {}
        for attempt in range(1, 4):  # infrastructure (clone failure, timeout): retry up to 2 times
            record = self.run_once(job, epoch)
            record["tries"] = attempt
            infra = record["status"] == "timeout" or (record["status"] == "harness-error" and record.get("_clone_failed"))
            if not infra:
                break
        record.pop("_clone_failed", None)
        write_json(path, record)
        return record

    def control(self, repo_id: str, expectations: dict[str, Any], epoch: int) -> tuple[bool, dict[str, Any]]:
        """Score the pristine commit and compare with the expectations."""
        repo = self.cfg.repo(repo_id)
        tree = sb.git_out(score_base(self.cfg, repo_id), "rev-parse", f"{repo.sha}^{{tree}}")
        record = self.run_once(Job(repo_id, tree, None, kind="pristine-control"), epoch)
        record.pop("_clone_failed", None)
        want = expectations["repos"].get(repo_id, {}).get("pristine", {})
        ok = matches(record, want)
        return ok, record


class Paused(Exception):
    """The pristine control deviated: scoring stops; rerun `score` later."""


_UPDATE_LOCK = threading.Lock()


def _update_refs(cfg: Config, run_id: str, private_checks: dict[str, Any], tickets: dict[str, list[Any]],
                 entries: list[dict[str, Any]], record: dict[str, Any], score_file: Path) -> None:
    results = cfg.results(run_id)
    for entry in entries:
        target = results / entry["attempt"]
        with _UPDATE_LOCK:
            if not target.is_file():
                continue
            doc = read_json(target)
            ref = {
                "tree": entry["tree"],
                "key": f"{entry['repo']}-{entry['tree']}",
                "score_file": score_file.relative_to(results).as_posix(),
                "status": record["status"],
                "exit": record["exit"],
                "pass": record["pass"],
                "fail": record["fail"],
                "fraction": record["fraction"],
                "repo_pass": record["repo_pass"],
                "install_failed": record["install_failed"],
            }
            ticket_id = doc.get("ticket")
            if ticket_id:
                cmap = check_map(private_checks, entry["repo"])
                ticket = next((t for t in tickets.get(entry["repo"], []) if t.id == ticket_id), None)
                keys = ticket.keys() if ticket is not None else (ticket_id,)
                ref["ticket_pass"] = ticket_pass(record, cmap.owned(keys), cmap.regression())
            doc.setdefault("hidden", {})[entry["field"]] = ref
            if "flags" in doc and record["install_failed"]:
                doc["flags"]["install_failed"] = True
            if ticket_id and entry["field"] == "merged":
                merged = (doc.get("review") or {}).get("merged")
                doc["hidden"]["ticket_pass"] = bool(ref.get("ticket_pass")) if merged else False
            write_json(target, doc)


def score_queue(cfg: Config, private: Any, run_id: str, *, rescore: bool = False, workers: int | None = None,
                log: Callable[[str], None] = print) -> dict[str, int]:
    """Score every queued tree once (cached by repo, tree and harness), with a
    pristine control before each worker's first job and after any
    harness-error, then write the results into the attempts."""
    qdir = cfg.results(run_id) / "scores" / "queue"
    entries = [read_json(p) for p in sorted(qdir.glob("*.json"))] if qdir.is_dir() else []
    jobs: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in entries:
        jobs.setdefault((entry["repo"], entry["tree"]), []).append(entry)
    scorer = Scorer(cfg, run_id, private.checks, log=log)
    order = sorted(jobs.items())
    counts = {"jobs": len(order), "ok": 0, "harness-error": 0, "timeout": 0, "paused": 0}
    lock = threading.Lock()
    queue = list(order)

    def worker() -> None:
        controlled: set[str] = set()
        while True:
            with lock:
                if not queue:
                    return
                (repo_id, tree), refs = queue.pop(0)
            if repo_id not in controlled:
                ok, rec = scorer.control(repo_id, private.expectations, cfg.clock_epoch)
                if not ok:
                    log(f"score: the pristine control of {repo_id} deviated (exit {rec['exit']}, {rec['summary']}); paused")
                    with lock:
                        counts["paused"] += 1
                        queue.insert(0, ((repo_id, tree), refs))
                    raise Paused(repo_id)
                controlled.add(repo_id)
            first = refs[0]
            patch = cfg.results(run_id) / first["patch"]
            record = scorer.score(Job(repo_id, tree, patch, kind=first["field"]), int(first["epoch_time"]), rescore=rescore)
            if record["status"] == "harness-error":
                ok, _ = scorer.control(repo_id, private.expectations, cfg.clock_epoch)
                if not ok:
                    cache_path(cfg, run_id, repo_id, tree, harness_sha(cfg, repo_id)).unlink(missing_ok=True)
                    with lock:
                        counts["paused"] += 1
                    raise Paused(repo_id)
            h = harness_sha(cfg, repo_id)
            _update_refs(cfg, run_id, private.checks, private.tickets, refs, record, cache_path(cfg, run_id, repo_id, tree, h))
            with lock:
                counts[record["status"]] = counts.get(record["status"], 0) + 1

    import concurrent.futures as cf

    n = max(1, workers or cfg.score_workers)
    paused = False
    with cf.ThreadPoolExecutor(max_workers=n) as pool:
        for fut in [pool.submit(worker) for _ in range(n)]:
            try:
                fut.result()
            except Paused:
                paused = True
    if paused:
        raise EvalError("scoring paused: a pristine control deviated (infrastructure); run `score` again", 1)
    return counts


def matches(record: dict[str, Any], want: dict[str, Any]) -> bool:
    if not want:
        return True
    if "exit" in want and record.get("exit") != want["exit"]:
        return False
    for key in ("pass", "fail"):
        if key in want:
            got = (record.get("summary") or {}).get(key)
            if got != want[key]:
                return False
    return True

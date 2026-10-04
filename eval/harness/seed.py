"""prepare: everything that is the same for every arm and trial.

Writes ``<home>/cache/<cadence sha>/`` (idempotent):

a. the plugin (``git archive`` of the pinned Cadence commit, the
   marketplace root) and its tree sha; the pinned tools (``tool/*.py`` with
   their schemas) and the gh shim;
b. the workflow pieces (workflow.py), with their sha256;
c. one seed per repo: the pinned commit, then one commit that only ADDS the
   factory files (tools, schemas, verify.sh, docs, the workflow, the
   rendered ``.cadence/factory.yaml`` and the private overlay's files),
   with fixed dates, so the seed sha is the same in every arm and trial;
d. a warm npm cache (``npm ci`` on the seed, gate profile);
e. verify.sh on the seed, against the expectation;
f. a copy of each hidden harness dir and the lib dir (never a never_bind
   file), with every file's sha256;
g. calibration in the score profile, each run twice: the pristine commit,
   the pinned commit plus each calibration diff, and the seed plus each
   calibration diff, against the expectations (``--write-expectations``
   writes what it saw instead of failing);
h. every stub patch passes ``git apply --check`` on its base;
i. no canary in the pristine trees, the tickets, the replies, the overlay or
   the extracted prompts.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

import privacy
import sandbox as sb
import score as scoring
import workflow as wfm
from clock import git_date
from config import (
    Config, EvalError, HARNESS_DIR, Private, read_json, sha256_bytes, sha256_file, wall_now,
    write_json, write_text,
)

TEMPLATES = "plugins/cadence/templates"
SCHEMAS = "plugins/cadence/schemas"
LEARNED_HEADING = "## Learned patterns (factory)"
RETRO_PATHS = (".cadence/cadence.yaml", ".cadence/lessons.yaml", "docs/PATTERNS.md", "tests/fixtures/retro")
PATTERNS_HEADER = (
    "# Patterns\n\n"
    "The project's own conventions live in its README, CLAUDE.md and docs. "
    "The section below is written by the Cadence learning loop; change it only "
    "through the retro PR.\n\n"
)


# --- git plumbing with a private index ----------------------------------------------------


class Index:
    """Build trees in a bare repo without a worktree."""

    def __init__(self, git_dir: Path, tag: str) -> None:
        self.git_dir = git_dir
        self.file = git_dir / f"index-{tag}-{os.getpid()}"

    def run(self, *args: str, stdin: bytes | None = None, date: str | None = None, check: bool = True) -> sb.Result:
        env = sb.git_env(date)
        env["GIT_INDEX_FILE"] = str(self.file)
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        res = sb.spawn(["git", *sb.GIT_SAFE, "--git-dir", str(self.git_dir), *args], env=env, stdin=stdin, cwd=os.path.abspath(os.sep))
        if check and not res.ok:
            raise EvalError(f"git {' '.join(args[:3])}: {res.stderr.decode(errors='replace').strip()[:400]}", 1)
        return res

    def out(self, *args: str, **kw) -> str:
        return self.run(*args, **kw).stdout.decode().strip()

    def close(self) -> None:
        try:
            self.file.unlink()
        except OSError:
            pass

    def __enter__(self) -> "Index":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def add_blob(index: Index, path: str, data: bytes, mode: str = "100644") -> None:
    blob = index.out("hash-object", "-w", "--stdin", stdin=data)
    index.run("update-index", "--add", "--cacheinfo", f"{mode},{blob},{path}")


def tree_plus_patch(git_dir: Path, base: str, patch: bytes) -> str:
    """The tree of ``base`` with ``patch`` applied (raises if it does not apply)."""
    with Index(git_dir, "tpp") as index:
        index.run("read-tree", base)
        if patch.strip():
            tmp = git_dir / f"tpp-{os.getpid()}.patch"
            tmp.write_bytes(patch)
            try:
                index.run("apply", "--cached", "--binary", "--whitespace=nowarn", str(tmp))
            finally:
                tmp.unlink()
        return index.out("write-tree")


def patch_applies(git_dir: Path, base: str, patch: bytes) -> bool:
    try:
        tree_plus_patch(git_dir, base, patch)
        return True
    except EvalError:
        return False


def diff_trees(git_dir: Path, a: str, b: str) -> bytes:
    """A binary patch from tree-ish a to tree-ish b."""
    return sb.git(None, "diff", "--binary", "--no-color", "--no-ext-diff", "--no-textconv",
                  "--no-renames", "--full-index", a, b, git_dir=git_dir).stdout


def commit_tree(git_dir: Path, tree: str, parents: list[str], message: str, epoch: int) -> str:
    args = ["commit-tree", tree]
    for p in parents:
        args += ["-p", p]
    args += ["-m", message]
    return sb.git(None, *args, git_dir=git_dir, date=git_date(epoch)).stdout.decode().strip()


# --- the overlay -----------------------------------------------------------------------------------


def _show(cfg: Config, rel: str) -> bytes:
    return sb.git(cfg.cadence_repo, "show", f"{cfg.cadence_sha}:{rel}").stdout


def _ls(cfg: Config, prefix: str) -> list[str]:
    out = sb.git_out(cfg.cadence_repo, "ls-tree", "-r", "--name-only", cfg.cadence_sha, "--", prefix)
    return [line for line in out.splitlines() if line]


def render_factory_yaml(cfg: Config) -> str:
    """The values-only .cadence/factory.yaml, byte-identical in every arm."""
    f = cfg.factory
    doc = {
        "budget": {"per_run_usd": cfg.caps["per_run_usd"], "daily_usd": f["daily_usd"]},
        "max_turns": cfg.caps["max_turns"],
        "retry": {"on_dod_fail": f["retry_on_dod_fail"]},
        "autonomy": "pr-only",
        "learning": {
            "mode": f["mode"],
            "promote_after": f["promote_after"],
            "guarded_paths": list(f["guarded_paths"]),
            "test_roots": list(f["test_roots"]),
            "classify": bool(f["classify"]),
        },
    }
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=None, width=1000)


def patterns_md(cfg: Config) -> bytes:
    text = _show(cfg, f"{TEMPLATES}/docs/PATTERNS.md.tmpl").decode("utf-8")
    at = text.find(LEARNED_HEADING)
    if at < 0:
        raise EvalError(f"PATTERNS.md.tmpl has no {LEARNED_HEADING!r} section")
    return (PATTERNS_HEADER + text[at:].rstrip("\n") + "\n").encode("utf-8")


def overlay_files(cfg: Config, repo_id: str) -> dict[str, bytes]:
    files: dict[str, bytes] = dict(overlay_files_tools(cfg))
    files["scripts/verify.sh"] = _show(cfg, f"{TEMPLATES}/scripts/verify.sh")
    files["docs/PATTERNS.md"] = patterns_md(cfg)
    files["docs/DEFINITION_OF_DONE.md"] = _show(cfg, f"{TEMPLATES}/docs/DEFINITION_OF_DONE.md.tmpl")
    files["docs/TEAM_LAUNCH_TEMPLATE.md"] = _show(cfg, f"{TEMPLATES}/docs/TEAM_LAUNCH_TEMPLATE.md.tmpl")
    files[".github/workflows/cadence-factory.yml"] = _show(cfg, wfm.TEMPLATE_PATH)
    files[".cadence/factory.yaml"] = render_factory_yaml(cfg).encode("utf-8")
    repo = cfg.repo(repo_id)
    for path in cfg.policy.walk(repo.overlay_dir):
        rel = path.relative_to(repo.overlay_dir).as_posix()
        files[rel] = path.read_bytes()
    if ".cadence/cadence.yaml" not in files:
        raise EvalError(f"overlay for {repo_id} has no .cadence/cadence.yaml")
    return files


# --- the pinned copies ------------------------------------------------------------------------------


def write_tools(cfg: Config, dest: Path) -> dict[str, str]:
    """tool/*.py and .cadence/*.schema.json at the pinned commit."""
    staging = dest.with_name(dest.name + ".tmp")
    sb.rmtree(staging)
    shas: dict[str, str] = {}
    for rel, data in overlay_files_tools(cfg).items():
        target = staging / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        shas[rel] = sha256_bytes(data)
    sb.rmtree(dest)
    os.replace(staging, dest)
    return shas


def overlay_files_tools(cfg: Config) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for rel in _ls(cfg, f"{TEMPLATES}/tool/"):
        if rel.endswith(".py") and Path(rel).parent.as_posix() == f"{TEMPLATES}/tool":
            out[f"tool/{Path(rel).name}"] = _show(cfg, rel)
    for rel in _ls(cfg, f"{SCHEMAS}/"):
        if rel.endswith(".schema.json") and Path(rel).parent.as_posix() == SCHEMAS:
            out[f".cadence/{Path(rel).name}"] = _show(cfg, rel)
    return out


def write_plugin(cfg: Config, dest: Path) -> str:
    archive = sb.git(cfg.cadence_repo, "archive", "--format=tar", cfg.cadence_sha).stdout
    staging = dest.with_name(dest.name + ".tmp")
    sb.rmtree(staging)
    sb.untar(archive, staging)
    sb.rmtree(dest)
    os.replace(staging, dest)
    return sb.git_out(cfg.cadence_repo, "rev-parse", f"{cfg.cadence_sha}^{{tree}}")


def write_shim(cfg: Config, dest: Path, python: str) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HARNESS_DIR / "ghshim.py", dest / "ghshim.py")
    wrapper = dest / "gh"
    wrapper.write_text(f'#!/bin/sh\nexec {python} "$(dirname "$0")/ghshim.py" "$@"\n', encoding="utf-8", newline="\n")
    wrapper.chmod(0o755)


def harness_hashes() -> dict[str, str]:
    out = {}
    for path in sorted(HARNESS_DIR.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            out[path.relative_to(HARNESS_DIR).as_posix()] = sha256_file(path)
    return out


# --- seeds ---------------------------------------------------------------------------------------------


def seeds_repo(cfg: Config, repo_id: str) -> Path:
    return cfg.cache_root / "seeds" / f"{repo_id}.git"


def build_seed(cfg: Config, repo_id: str, canaries: list[str]) -> dict[str, Any]:
    repo = cfg.repo(repo_id)
    git_dir = seeds_repo(cfg, repo_id)
    if not (git_dir / "HEAD").exists():
        git_dir.parent.mkdir(parents=True, exist_ok=True)
        tmp = git_dir.with_name(git_dir.name + ".tmp")
        sb.rmtree(tmp)
        sb.git(None, "clone", "--bare", "--no-hardlinks", "--quiet", str(repo.source), str(tmp))
        os.replace(tmp, git_dir)
    sb.git(None, "cat-file", "-e", f"{repo.sha}^{{commit}}", git_dir=git_dir)
    files = overlay_files(cfg, repo_id)
    problems = []
    for rel, data in files.items():
        if sb.git(None, "cat-file", "-e", f"{repo.sha}:{rel}", git_dir=git_dir, check=False).ok:
            problems.append(rel)
        hits = privacy.canary_hits(data.decode("utf-8", "replace"), canaries)
        if hits:
            raise EvalError(f"canary #{hits[0]} in the overlay file {rel} of {repo_id}", 1)
    if problems:
        raise EvalError(f"{repo_id}: the overlay would change existing files: {', '.join(sorted(problems))}", 1)
    with Index(git_dir, "seed") as index:
        index.run("read-tree", repo.sha)
        for rel, data in sorted(files.items()):
            add_blob(index, rel, data)
        tree = index.out("write-tree")
    # Only additions: every path of the diff is new.
    status = sb.git_out(None, "diff", "--name-status", "--no-renames", repo.sha, tree, git_dir=git_dir)
    for line in status.splitlines():
        kind, _, path = line.partition("\t")
        if kind != "A":
            raise EvalError(f"{repo_id}: the seed commit changes {path} ({kind}); it may only add", 1)
    seed = commit_tree(git_dir, tree, [repo.sha], "cadence-eval: factory files (seed)", cfg.clock_epoch)
    sb.git(None, "update-ref", "refs/heads/seed", seed, git_dir=git_dir)
    sb.git(None, "config", "uploadpack.allowAnySHA1InWant", "true", git_dir=git_dir)
    return {"pinned": repo.sha, "seed": seed, "seed_tree": tree, "overlay": sorted(files)}


# --- stub sources ------------------------------------------------------------------------------------


def stub_patch(cfg: Config, private: Private, repo_id: str, src: Any) -> bytes:
    """The patch a stub source stands for."""
    if src == "noop" or src is None:
        return b""
    if "file" in src:
        path = cfg.policy.check_read(cfg.files["stubs"].parent / src["file"])
        return path.read_bytes()
    repo = cfg.repo(repo_id)
    if repo.calib is None:
        raise EvalError(f"stub {src} needs a calib repo for {repo_id}")
    excludes = list(src.get("exclude") or [])
    return calib_patch(cfg, repo_id, src["calib"], excludes)


def calib_patch(cfg: Config, repo_id: str, branch: str, excludes: list[str]) -> bytes:
    repo = cfg.repo(repo_id)
    assert repo.calib is not None
    args = ["diff", "--binary", "--no-color", "--no-ext-diff", "--no-textconv", "--full-index",
            f"{repo.calib.base}..{branch}", "--", "."]
    args += [f":(exclude){x}" for x in excludes]
    return sb.git(repo.calib.repo, *args).stdout


def all_stub_sources(cfg: Config, private: Private) -> list[tuple[str, str, Any]]:
    """(repo, base kind 'seed'|'pinned', src) for every stub source in use."""
    out: list[tuple[str, str, Any]] = []
    stubs = private.stubs
    for repo in cfg.repos:
        for ticket in private.tickets[repo.id]:
            build = private.ticket_value(stubs["build"], ticket) or stubs["build"]["default"]
            for key in ("try1", "try2"):
                if key in build:
                    out.append((repo.id, "seed", build[key]))
        src = stubs["autopilot"].get(repo.id, "noop")
        out.append((repo.id, "pinned", src))
    return out


# --- prepare -------------------------------------------------------------------------------------------


@dataclass
class Prepared:
    data: dict[str, Any]

    @property
    def seeds(self) -> dict[str, dict[str, Any]]:
        return self.data["seeds"]


def prepared_path(cfg: Config) -> Path:
    return cfg.cache_root / "prepare.json"


def load_prepared(cfg: Config) -> Prepared:
    path = prepared_path(cfg)
    if not path.is_file():
        raise EvalError(f"run prepare first ({path} is missing)")
    return Prepared(read_json(path))


def python_for(cfg: Config) -> str:
    return "/opt/venv/bin/python3" if cfg.sandbox == "bwrap" else (cfg.home / "venv" / "bin" / "python3").as_posix()


def checkout_tree(git_dir: Path, commit: str, dest: Path) -> None:
    """A plain checkout (actions/checkout: one commit, depth 1, no other refs)."""
    sb.rmtree(dest)
    dest.mkdir(parents=True)
    sb.git(dest, "init", "--quiet")
    sb.git(dest, "fetch", "--quiet", "--no-tags", "--depth", "1", f"file://{git_dir.as_posix()}", commit)
    sb.git(dest, "checkout", "--quiet", "--detach", "FETCH_HEAD")


def prepare(
    cfg: Config,
    private: Private,
    *,
    write_expectations: bool = False,
    log: Callable[[str], None] = print,
    calibrate: bool = True,
) -> Prepared:
    cache = cfg.cache_root
    cache.mkdir(parents=True, exist_ok=True)
    sb.write_guard(cfg.home)
    sb.copy_resolv(cfg.home)
    data: dict[str, Any] = {"schema": "cadence-eval.prepare/1", "cadence_sha": cfg.cadence_sha,
                            "config_sha256": cfg.sha256, "prepared_at": wall_now()}
    # a. plugin, tools, shim
    data["plugin_tree"] = write_plugin(cfg, cache / "plugin")
    data["tools"] = write_tools(cfg, cache / "tools")
    write_shim(cfg, cache / "shim", "python3")
    log(f"prepare: plugin tree {data['plugin_tree'][:12]}, {len(data['tools'])} pinned tool files")
    # b. workflow pieces
    wf = wfm.load(cfg.cadence_repo, cfg.cadence_sha)
    write_json(cache / "workflow.json", {k: {"sha256": p.sha256, "text": p.text} for k, p in wf.pieces.items()})
    data["pieces"] = wf.shas()
    for key in ("intake.prompt", "agent.prompt", "agent-retry.prompt"):
        hits = privacy.canary_hits(wf.pieces[key].text, private.canaries)
        if hits:
            raise EvalError(f"canary #{hits[0]} in the extracted piece {key}", 1)
    # i. canaries in tickets and replies (the pristine trees below)
    for repo in cfg.repos:
        for ticket in private.tickets[repo.id]:
            hits = privacy.canary_hits(ticket.title + "\n" + ticket.body, private.canaries)
            if hits:
                raise EvalError(f"canary #{hits[0]} in ticket {ticket.stem}", 1)
    if privacy.canary_hits(private.replies["questions_reply"], private.canaries):
        raise EvalError("a canary is in replies.yaml", 1)
    # c. seeds
    seeds: dict[str, Any] = {}
    for repo in cfg.repos:
        seeds[repo.id] = build_seed(cfg, repo.id, private.canaries)
        git_dir = seeds_repo(cfg, repo.id)
        for i, canary in enumerate(private.canaries):
            found = sb.git(None, "grep", "-q", "-F", "-e", canary, repo.sha, "--", git_dir=git_dir, check=False)
            if found.exit == 0:
                raise EvalError(f"canary #{i} is in the pristine tree of {repo.id}", 1)
        log(f"prepare: seed {repo.id} {seeds[repo.id]['seed'][:12]}")
    data["seeds"] = seeds
    # h. stub patches apply on their base
    stub_dir = cache / "stubs"
    stub_dir.mkdir(exist_ok=True)
    for repo_id, base_kind, src in all_stub_sources(cfg, private):
        patch = stub_patch(cfg, private, repo_id, src)
        base = seeds[repo_id]["seed"] if base_kind == "seed" else seeds[repo_id]["pinned"]
        if patch and not patch_applies(seeds_repo(cfg, repo_id), base, patch):
            raise EvalError(f"stub {src} does not apply to the {base_kind} of {repo_id}", 1)
        (stub_dir / f"{sha256_bytes(patch)}.patch").write_bytes(patch)
    # Validate the rendered factory.yaml with the pinned ledger (tools profile).
    _check_factory_yaml(cfg)
    # d, e. warm npm cache, verify.sh on the seed
    data["seed_verify"] = {}
    for repo in cfg.repos:
        data["seed_verify"][repo.id] = _seed_verify(cfg, repo.id, seeds[repo.id]["seed"], log)
    # f. the grader copy
    data["harness"] = scoring.copy_grader(cfg)
    data["harness_files"] = harness_hashes()
    write_json(prepared_path(cfg), data)
    mismatches: list[str] = []
    for repo in cfg.repos:
        want = private.expectations["repos"].get(repo.id, {}).get("seed_verify")
        got = data["seed_verify"][repo.id]
        if want is not None and want != got:
            mismatches.append(f"{repo.id}: seed verify {got}, expected {want}")
    # g. calibration
    observed: dict[str, Any] = {"schema": "cadence-eval.expectations/1", "repos": {}}
    if calibrate:
        mismatches += _calibrate(cfg, private, seeds, observed, log)
    for repo in cfg.repos:
        observed["repos"].setdefault(repo.id, {})["seed_verify"] = data["seed_verify"][repo.id]
    data["calibration"] = observed
    write_json(prepared_path(cfg), data)
    # Always shown: with --write-expectations the owner copies the observed
    # counts, and must see first where they differ from the prediction and
    # where two runs of the same tree disagreed.
    for line in mismatches:
        log(f"prepare: MISMATCH {line}")
    if write_expectations:
        out = cfg.dir / "expectations.observed.yaml"
        write_text(out, yaml.safe_dump(observed, sort_keys=True))
        log(f"prepare: wrote {out.name} next to the config")
    elif mismatches:
        raise EvalError(f"prepare: {len(mismatches)} calibration mismatch(es)", 1)
    return Prepared(data)


def _tools_box(cfg: Config, root: Path) -> sb.Box:
    return sb.Box(cfg.sandbox, "tools", cfg.home, root,
                  binds=[sb.Bind(cfg.cache_root / "tools", sb.OPT_TOOLS, ro=True)])


def _check_factory_yaml(cfg: Config) -> None:
    root = cfg.home / "work" / "_prepare" / "factory-yaml"
    sb.rmtree(root)
    box = _tools_box(cfg, root)
    target = box.work / "factory.yaml"
    write_text(target, render_factory_yaml(cfg))
    (box.work / "runs").mkdir(parents=True, exist_ok=True)
    tools = box.opt(sb.OPT_TOOLS)
    res = box.run(wfm.tool_argv(tools, "ledger.py check", "--config", box.inside(target), "--records-dir",
                                box.inside(box.work / "runs"), "check", "--in-flight", "0", "--now", str(cfg.clock_epoch)))
    if res.exit not in (0, 1):
        raise EvalError(f"the rendered factory.yaml is refused by ledger.py: {res.stderr.decode(errors='replace')[-400:]}")
    sb.rmtree(root)


def _seed_verify(cfg: Config, repo_id: str, seed: str, log: Callable[[str], None]) -> str:
    root = cfg.home / "work" / "_prepare" / f"verify-{repo_id}"
    sb.rmtree(root)
    warm = cfg.cache_root / "npm-warm"
    warm.mkdir(parents=True, exist_ok=True)
    box = sb.Box(cfg.sandbox, "gate", cfg.home, root, binds=[sb.Bind(warm, "/home/runner/.npm", ro=False)])
    box.env["npm_config_cache"] = box.inside(warm) if cfg.sandbox == "none" else "/home/runner/.npm"
    ws = box.work / repo_id / repo_id
    checkout_tree(seeds_repo(cfg, repo_id), seed, ws)
    if (ws / "package.json").is_file():
        res = box.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=ws, timeout=cfg.timeouts_min["gate"] * 60)
        log(f"prepare: npm ci on the {repo_id} seed exited {res.exit}")
        sb.rmtree(ws / "node_modules")
    res = box.run(["bash", "scripts/verify.sh"], cwd=ws, timeout=cfg.timeouts_min["gate"] * 60)
    verdict = "pass" if res.ok else "fail"
    log(f"prepare: verify.sh on the {repo_id} seed: {verdict} (exit {res.exit})")
    sb.rmtree(root)
    return verdict


def _calibrate(cfg: Config, private: Private, seeds: dict[str, Any], observed: dict[str, Any],
               log: Callable[[str], None]) -> list[str]:
    scorer = scoring.Scorer(cfg, "_calibration", private.checks, log=log)
    problems: list[str] = []
    for repo in cfg.repos:
        want = private.expectations["repos"].get(repo.id, {})
        got_repo = observed["repos"].setdefault(repo.id, {})
        git_dir = seeds_repo(cfg, repo.id)
        den = scoring.check_map(private.checks, repo.id).denominator
        runs: list[tuple[str, str | None, bytes]] = [("pristine", None, b"")]
        if repo.calib is not None:
            for branch, excludes in repo.calib.branches.items():
                patch = calib_patch(cfg, repo.id, branch, excludes)
                runs.append((f"calib:{branch}", branch, patch))
                seed_tree = tree_plus_patch(git_dir, seeds[repo.id]["seed"], patch)
                runs.append((f"seed_plus:{branch}", branch, diff_trees(git_dir, repo.sha, seed_tree)))
        for label, branch, patch in runs:
            results = []
            for _ in range(2):
                tree = tree_plus_patch(git_dir, repo.sha, patch)
                patch_file = cfg.cache_root / "calib" / f"{repo.id}-{sha256_bytes(patch)[:16]}.patch"
                patch_file.parent.mkdir(parents=True, exist_ok=True)
                patch_file.write_bytes(patch)
                record = scorer.run_once(scoring.Job(repo.id, tree, patch_file if patch else None, kind=label), cfg.clock_epoch)
                results.append(record)
            a, b = results
            summary = {"exit": a["exit"], **({"pass": a["summary"]["pass"], "fail": a["summary"]["fail"]} if a["summary"] else {})}
            if (a["exit"], a["summary"]) != (b["exit"], b["summary"]):
                problems.append(f"{repo.id} {label}: two runs differ ({a['exit']}/{a['summary']} vs {b['exit']}/{b['summary']})")
            log(f"prepare: calibration {repo.id} {label}: exit {a['exit']}, {a['summary']}")
            if label == "pristine":
                got_repo["pristine"] = summary
                expect = want.get("pristine", {})
            elif label.startswith("calib:"):
                got_repo.setdefault("calib", {})[branch] = summary
                expect = (want.get("calib") or {}).get(branch, {})
            else:
                got_repo.setdefault("seed_plus", {})[branch] = {"exit": a["exit"]}
                expect = (want.get("seed_plus") or {}).get(branch, {})
            if not scoring.matches(a, expect):
                problems.append(f"{repo.id} {label}: got exit {a['exit']} {a['summary']}, expected {expect}")
            if branch == "reference" and label.startswith("calib:"):
                printed = {n for n, c in a["checks"].items() if c.get("printed")}
                if printed != set(den):
                    problems.append(
                        f"{repo.id} reference: printed {len(printed)} checks, denominator {len(den)} "
                        f"({len(printed - set(den))} extra, {len(set(den) - printed)} missing)"
                    )
    return problems

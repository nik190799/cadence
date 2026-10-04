"""A chain: one arm, one trial, one repo, its tickets in order.

Its work dir ``<home>/work/<run>/<chain>/`` holds:

- ``origin.git``: a bare repo whose ``main`` starts at the seed
  (``uploadpack.allowAnySHA1InWant``, so checkouts fetch one commit);
- ``state/``: a worktree of the orphan branch ``cadence/state``;
- ``ghstore/``: the PRs, comments and permissions the gh shim answers from.

Arms F0 and F1 run the same code and differ in exactly one thing, the
emulated repository variable ``CADENCE_EVAL_SANDBOX`` ('true' only in F1),
which decides whether retro-publish's merge step runs. After every ticket
the chain checks its invariants: no commit with a ``Cadence-Retro-Plan``
trailer ever lands on F0's main, and every commit on main is the seed, an
E2 reset, an agent merge or (F1 only) a retro merge.
"""

from __future__ import annotations

import json
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import agent as ag
import privacy
import sandbox as sb
import seed as seedm
import workflow as wfm
from clock import Clock, git_date
from config import Config, EvalError, Private, read_json, sha256_bytes, wall_now, write_json

RETRO_TRAILER = "Cadence-Retro-Plan"
RETRO_PATHS = seedm.RETRO_PATHS
BOT_LOGIN = "cadence-eval[bot]"


class RunEnv:
    """Everything one run shares: config, private inputs, pieces, budget."""

    def __init__(self, cfg: Config, private: Private, wf: wfm.Workflow, prepared: seedm.Prepared,
                 run_id: str, agent_mode: str, budget: ag.Budget, *, key: str | None = None,
                 resume: bool = False, log: Callable[[str], None] = print) -> None:
        self.cfg = cfg
        self.private = private
        self.wf = wf
        self.prepared = prepared
        self.run_id = run_id
        self.agent_mode = agent_mode
        self.budget = budget
        self.key = key
        self.resume = resume  # replay cached sessions (never pay twice for one)
        self.clock = Clock(cfg.clock_epoch, cfg.slot_hours)
        self.cache = cfg.cache_root
        self.results = cfg.results(run_id)
        self.work = cfg.work(run_id)
        self._log = log
        self._lock = threading.Lock()
        self._stubs: dict[str, bytes] = {}
        self._pinned_blobs: dict[str, str] | None = None

    def log(self, message: str) -> None:
        with self._lock:
            self._log(message)

    def journal(self, event: str, **data: Any) -> None:
        line = json.dumps({"at": wall_now(), "event": event, **data}, sort_keys=True)
        with self._lock:
            self.results.mkdir(parents=True, exist_ok=True)
            with open(self.results / "journal.jsonl", "a", encoding="utf-8", newline="\n") as fh:
                fh.write(line + "\n")

    def stub_patch(self, repo_id: str, src: Any) -> bytes:
        key = f"{repo_id}:{json.dumps(src, sort_keys=True)}"
        with self._lock:
            if key not in self._stubs:
                self._stubs[key] = seedm.stub_patch(self.cfg, self.private, repo_id, src)
            return self._stubs[key]

    def pinned_blobs(self) -> dict[str, str]:
        """git blob ids of the pinned tool/*.py, for the tool-drift flag."""
        with self._lock:
            if self._pinned_blobs is None:
                out = {}
                for path in sorted((self.cache / "tools" / "tool").glob("*.py")):
                    out[f"tool/{path.name}"] = sb.git_out(None, "hash-object", "--no-filters", str(path))
                self._pinned_blobs = out
            return dict(self._pinned_blobs)


@dataclass
class ChainInfo:
    arm: str
    trial: int
    repo: str
    seed: str
    pinned: str
    pr_next: int = 100
    comment_next: int = 9000
    main_kinds: dict[str, str] = field(default_factory=dict)  # sha -> seed|e2-reset|agent-merge|retro-merge
    e2_reset: str | None = None
    finals: dict[str, dict[str, str]] = field(default_factory=dict)  # "e1"/"e2" -> {sha, tree}
    done: list[str] = field(default_factory=list)
    flags: dict[str, Any] = field(default_factory=dict)


class Chain:
    def __init__(self, env: RunEnv, arm: str, trial: int, repo_id: str) -> None:
        self.env = env
        self.arm = arm
        self.trial = trial
        self.repo = env.cfg.repo(repo_id)
        self.tickets = env.private.tickets[repo_id]
        self.name = f"{arm}-t{trial}-{repo_id}"
        self.name_part = self.repo.slug.split("/", 1)[1]
        self.work = env.work / self.name
        self.results = env.results / "chains" / self.name
        self.origin = self.work / "origin.git"
        self.state = self.work / "state"
        self.ghstore = self.work / "ghstore"
        self.store = None  # type: ignore[assignment]
        seeds = env.prepared.seeds[repo_id]
        self.info = ChainInfo(arm, trial, repo_id, seeds["seed"], seeds["pinned"])
        self.sandbox_var = arm == "F1"  # CADENCE_EVAL_SANDBOX

    # --- setup and persistence -------------------------------------------------------------

    @property
    def info_path(self) -> Path:
        return self.results / "chain.json"

    def save(self) -> None:
        write_json(self.info_path, self.info.__dict__)

    def setup(self, resume: bool) -> None:
        import ghshim

        if resume and self.info_path.is_file() and (self.origin / "HEAD").exists():
            self.info = ChainInfo(**read_json(self.info_path))
            self.store = ghshim.Store(self.ghstore, self.repo.slug)
            return
        sb.rmtree(self.work)
        self.work.mkdir(parents=True)
        self.results.mkdir(parents=True, exist_ok=True)
        sb.git(None, "init", "--bare", "--quiet", str(self.origin))
        sb.git(None, "config", "uploadpack.allowAnySHA1InWant", "true", git_dir=self.origin)
        seeds = seedm.seeds_repo(self.env.cfg, self.repo.id)
        sb.git(None, "fetch", "--quiet", "--no-tags", str(seeds), "refs/heads/seed:refs/heads/main", git_dir=self.origin)
        if self.main() != self.info.seed:
            raise EvalError(f"{self.name}: main is not the seed after setup", 1)
        # cadence/state: an orphan branch in its own worktree.
        empty_tree = sb.git_out(None, "mktree", git_dir=self.origin, stdin=b"")
        root = seedm.commit_tree(self.origin, empty_tree, [], "cadence/state", self.env.cfg.clock_epoch)
        sb.git(None, "update-ref", "refs/heads/cadence/state", root, git_dir=self.origin)
        sb.git(None, "worktree", "add", "--quiet", str(self.state), "cadence/state", git_dir=self.origin)
        self.store = ghshim.Store(self.ghstore, self.repo.slug)
        self.info.main_kinds = {self.info.seed: "seed"}
        self.save()

    # --- git ---------------------------------------------------------------------------------

    def main(self) -> str:
        return sb.git_out(None, "rev-parse", "refs/heads/main", git_dir=self.origin)

    def tree_of(self, rev: str) -> str:
        return sb.git_out(None, "rev-parse", f"{rev}^{{tree}}", git_dir=self.origin)

    def checkout(self, sha: str, dest: Path) -> None:
        """As actions/checkout: one commit, depth 1, no other refs."""
        seedm.checkout_tree(self.origin, sha, dest)

    def full_clone(self, dest: Path) -> None:
        sb.rmtree(dest)
        sb.git(None, "clone", "--quiet", "--no-hardlinks", f"file://{self.origin.as_posix()}", str(dest))

    def state_archive(self, dest: Path) -> None:
        dest.mkdir(parents=True, exist_ok=True)
        data = sb.git(None, "archive", "--format=tar", "refs/heads/cadence/state", git_dir=self.origin).stdout
        sb.untar(data, dest)

    def state_commit(self, added: list[str], message: str, now: int) -> str | None:
        if not added:
            return None
        sb.git(self.state, "add", "--", *added)
        if sb.git(self.state, "diff", "--cached", "--quiet", check=False).ok:
            return None
        sb.git(self.state, "commit", "--quiet", "-m", message, date=git_date(now))
        return sb.git_out(self.state, "rev-parse", "HEAD")

    def run_url(self, run_id: str) -> str:
        return f"https://local.invalid/{self.repo.slug}/actions/runs/{run_id}"

    def next_pr(self) -> int:
        n = self.info.pr_next
        self.info.pr_next += 1
        return n

    def next_comment(self) -> int:
        n = self.info.comment_next
        self.info.comment_next += 1
        return n

    # --- sandboxes ------------------------------------------------------------------------------

    def box(self, profile: str, root: Path, *, plugin: bool = False, extra: list[sb.Bind] | None = None) -> sb.Box:
        env = self.env
        live_agent = profile == "agent" and env.agent_mode == "live"
        binds: list[sb.Bind] = []
        if profile in ("tools", "gate"):
            binds.append(sb.Bind(env.cache / "tools", sb.OPT_TOOLS, ro=True))
        if profile == "agent":
            inp = root / "work" / "_temp" / "cadence" / "input"
            inp.mkdir(parents=True, exist_ok=True)
            binds.append(sb.Bind(inp, sb.RUNNER_TEMP + "/cadence/input", ro=True))
            if plugin and self.arm != "A0":
                binds.append(sb.Bind(env.cache / "plugin", sb.OPT_PLUGIN, ro=True))
        binds += extra or []
        # The key never sits on a box: agent.run_live hands it to the model
        # session alone (the workflow's later steps never see it either).
        return sb.Box(env.cfg.sandbox, profile, env.cfg.home, root, binds=binds, guard=not live_agent)

    # --- the ledger ------------------------------------------------------------------------------

    def _ledger_cmd(self, box: sb.Box, config: Path, staged: Path, *, run_id: str, issue: int | None,
                    stage: str, outcome: str, dod: str, result: Path | None, now: int,
                    base: str | None = None, pr: int | None = None, published: str | None = None) -> None:
        tools = box.opt(sb.OPT_TOOLS)
        argv = ["--config", box.inside(config), "--records-dir",
                box.inside(staged / "runs"), "record", "--run-id", run_id, "--run-attempt", "1",
                "--outcome", outcome, "--dod", dod, "--stage", stage, "--now", str(now)]
        if issue is not None:
            argv += ["--issue", str(issue)]
        if base:
            argv += ["--base-sha", base]
        if pr and published:
            argv += ["--pr", str(pr), "--published-sha", published]
        if result is not None:
            local = box.temp / f"result-{run_id}.json"
            shutil.copy2(result, local)
            argv += ["--result-json", box.inside(local)]
        res = box.run(wfm.tool_argv(tools, "ledger.py record", *argv), timeout=300)
        if res.exit not in (0, 1):
            raise EvalError(f"ledger.py record {run_id} exited {res.exit}: {res.stderr.decode(errors='replace')[-300:]}", 1)

    def _ledger_box(self, name: str) -> tuple[sb.Box, Path, Path]:
        root = self.work / "jobs" / name
        sb.rmtree(root)
        box = self.box("tools", root, extra=[sb.Bind(self.state, "/srv/state", ro=False)])
        config = box.temp / "factory.yaml"
        config.write_bytes(sb.git(None, "show", f"{self.main()}:.cadence/factory.yaml", git_dir=self.origin).stdout)
        staged = box.temp / "staged"
        (staged / "runs").mkdir(parents=True, exist_ok=True)
        return box, config, staged

    def _put(self, box: sb.Box, staged: Path) -> list[str]:
        tools = box.opt(sb.OPT_TOOLS)
        state = "/srv/state" if box.mode == "bwrap" else self.state.as_posix()
        res = box.run(wfm.tool_argv(tools, "signals.py put", "put", "--staged", box.inside(staged), "--state", state),
                      timeout=300)
        if not res.ok:
            raise EvalError(f"signals.py put exited {res.exit}: {res.stderr.decode(errors='replace')[-300:]}", 1)
        return list(json.loads(res.text()).get("added") or [])

    def _booked(self, staged: Path, ledger: dict[str, Any]) -> None:
        for path in sorted((staged / "runs").glob("*.json")):
            record = read_json(path)
            ledger["run_ids"].append(record.get("run_id"))
            ledger["booked_usd"] = round(ledger["booked_usd"] + float(record.get("booked_usd") or 0), 6)
            if record.get("total_cost_usd") is not None:
                ledger["reported_usd"] = round(ledger.get("reported_usd", 0.0) + float(record["total_cost_usd"]), 6)

    def book(self, *, run_id: str, issue: int, stage: str, outcome: str, dod: str, result: Path | None,
             now: int, message: str, ledger: dict[str, Any]) -> None:
        box, config, staged = self._ledger_box(f"ledger-{run_id}")
        self._ledger_cmd(box, config, staged, run_id=run_id, issue=issue, stage=stage, outcome=outcome, dod=dod,
                         result=result, now=now)
        self._booked(staged, ledger)
        added = self._put(box, staged)
        self.state_commit(added, message, now)
        sb.rmtree(box.root)

    def book_build(self, *, records: list[dict[str, Any]], observed: list[tuple[Any, Path]], issue: int,
                   base: str, pub: dict[str, Any], now: int, message: str, ledger: dict[str, Any]) -> None:
        box, config, staged = self._ledger_box(f"ledger-{records[0]['run_id'] if records else 'none'}")
        for rec in records:
            self._ledger_cmd(box, config, staged, run_id=rec["run_id"], issue=issue, stage="build",
                             outcome=rec["outcome"], dod=rec["dod"], result=rec["result"], now=now, base=base,
                             pr=rec["pr"], published=rec["published_sha"])
        self._booked(staged, ledger)
        tools = box.opt(sb.OPT_TOOLS)
        for trial, bundle in observed:
            local = box.temp / bundle.name
            shutil.copy2(bundle, local)
            argv = ["finalize", "--bundle-file", box.inside(local),
                    "--bundle-sha256", trial.observe["bundle_sha256"], "--run-id", trial.run_id,
                    "--run-attempt", "1", "--issue", str(issue), "--schema-dir", f"{tools}/.cadence",
                    "--out-dir", box.inside(staged), "--now", str(now)]
            if pub.get("try") == trial.n and pub.get("pr"):
                argv += ["--pr", str(pub["pr"]), "--published-sha", pub["published_sha"]]
            if trial.patch is not None and trial.patch.stat().st_size > 0:
                patch = box.temp / f"patch-{trial.n}.patch"
                shutil.copy2(trial.patch, patch)
                argv += ["--patch", box.inside(patch)]
            res = box.run(wfm.tool_argv(tools, "signals.py finalize", *argv), timeout=300)
            if not res.ok:
                self.env.log(f"[{self.name}] finalize {trial.run_id} exited {res.exit}: observation not booked")
        added = self._put(box, staged)
        self.state_commit(added, message, now)
        sb.rmtree(box.root)

    # --- main ---------------------------------------------------------------------------------------

    def merge_agent(self, published: str, ticket: str) -> str:
        """The scripted reviewer merges the published PR unchanged: a fast-forward."""
        main = self.main()
        if not sb.git(None, "merge-base", "--is-ancestor", main, published, git_dir=self.origin, check=False).ok:
            raise EvalError(f"{self.name}: {published} does not fast-forward main", 1)
        sb.git(None, "update-ref", "refs/heads/main", published, main, git_dir=self.origin)
        self.info.main_kinds[published] = "agent-merge"
        self.save()
        return published

    def merge_retro(self, head: str, subject: str, body: str, pr: int, now: int) -> str:
        """Squash-merge the retro PR onto main (eval sandbox only)."""
        main = self.main()
        tree = self.tree_of(head)
        message = f"{subject} (#{pr})\n\n{body}"
        squash = seedm.commit_tree(self.origin, tree, [main], message, now)
        sb.git(None, "update-ref", "refs/heads/main", squash, main, git_dir=self.origin)
        self.info.main_kinds[squash] = "retro-merge"
        self.save()
        return squash

    def e2_reset(self) -> str:
        """The new main: the seed tree plus the four retro paths as they stand
        at the end of E1. The same in both arms."""
        e1 = self.main()
        now = self.env.clock.reset_time(len(self.tickets))
        with seedm.Index(self.origin, "reset") as index:
            index.run("read-tree", self.info.seed)
            for path in RETRO_PATHS:
                index.run("rm", "--cached", "-r", "-q", "--ignore-unmatch", "--", path)
                listing = sb.git(None, "ls-tree", "-r", "-z", e1, "--", path, git_dir=self.origin).stdout
                entries = []
                for item in listing.split(b"\0"):
                    if not item:
                        continue
                    meta, _, name = item.partition(b"\t")
                    mode, _kind, sha = meta.decode().split()
                    entries.append(f"{mode} {sha}\t{name.decode()}")
                if entries:
                    index.run("update-index", "--index-info", stdin=("\n".join(entries) + "\n").encode())
            tree = index.out("write-tree")
        reset = seedm.commit_tree(self.origin, tree, [e1], "cadence-eval: E2 reset", now)
        sb.git(None, "update-ref", "refs/heads/main", reset, e1, git_dir=self.origin)
        self.info.main_kinds[reset] = "e2-reset"
        self.info.e2_reset = reset
        self.save()
        return reset

    def retro_paths(self) -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        main = self.main()
        for path in RETRO_PATHS:
            listing = sb.git(None, "ls-tree", "-r", main, "--", path, git_dir=self.origin).stdout
            out[path] = sha256_bytes(listing) if listing.strip() else None
        return out

    def tool_drift(self) -> bool:
        listing = sb.git_out(None, "ls-tree", "-r", self.main(), "--", "tool/", git_dir=self.origin)
        mine = {}
        for line in listing.splitlines():
            meta, _, name = line.partition("\t")
            if name.endswith(".py"):
                mine[name] = meta.split()[2]
        return mine != self.env.pinned_blobs()

    def invariants(self) -> list[str]:
        problems: list[str] = []
        main = self.main()
        revs = sb.git_out(None, "rev-list", "--first-parent", main, git_dir=self.origin).splitlines()
        for sha in revs:
            if sha == self.info.seed:
                break
            kind = self.info.main_kinds.get(sha)
            if kind is None:
                problems.append(f"commit {sha[:12]} on main is not a seed, reset, agent merge or retro merge")
            elif kind == "retro-merge" and self.arm != "F1":
                problems.append(f"a retro merge {sha[:12]} landed on {self.arm}'s main")
        else:
            problems.append("the seed is not on main's first-parent line")
        if self.arm == "F0":
            out = sb.git_out(None, "log", f"--format=%H%x1f%(trailers:key={RETRO_TRAILER},valueonly)%x1e",
                             f"{self.info.seed}..{main}", git_dir=self.origin)
            for record in out.split("\x1e"):
                sha, _, trailer = record.strip().partition("\x1f")
                if trailer.strip():
                    problems.append(f"F0's main holds {sha[:12]} with a {RETRO_TRAILER} trailer")
        return problems

    # --- scoring queue --------------------------------------------------------------------------------

    def queue_score(self, *, attempt_dir: Path, field_name: str, tree: str, epoch_time: int) -> dict[str, Any]:
        patch = seedm.diff_trees(self.origin, self.info.pinned, tree)
        score_dir = attempt_dir / "score"
        score_dir.mkdir(parents=True, exist_ok=True)
        patch_file = score_dir / f"{field_name}.patch"
        patch_file.write_bytes(patch)
        entry = {
            "repo": self.repo.id,
            "tree": tree,
            "patch": patch_file.relative_to(self.env.results).as_posix(),
            "attempt": (attempt_dir / "attempt.json").relative_to(self.env.results).as_posix(),
            "field": field_name,
            "epoch_time": epoch_time,
            "chain": self.name,
        }
        qdir = self.env.results / "scores" / "queue"
        qdir.mkdir(parents=True, exist_ok=True)
        write_json(qdir / f"{self.name}-{attempt_dir.name}-{field_name}.json", entry)
        return {"tree": tree, "key": f"{self.repo.id}-{tree}", "status": "queued"}

    def tree_plus(self, base: str, patch: Path) -> str | None:
        try:
            return seedm.tree_plus_patch(self.origin, base, patch.read_bytes())
        except EvalError:
            return None

    # --- bundles ---------------------------------------------------------------------------------------

    def bundle_state(self, name: str) -> None:
        self.results.mkdir(parents=True, exist_ok=True)
        sb.git(None, "bundle", "create", "--quiet", str(self.results / name), "refs/heads/cadence/state",
               git_dir=self.origin)

    def save_ghstore(self) -> None:
        dest = self.results / "ghstore"
        sb.rmtree(dest)
        shutil.copytree(self.ghstore, dest)

    # --- resume ------------------------------------------------------------------------------------------

    def snapshot(self, label: str) -> None:
        snap = self.work / "snapshots" / label
        sb.rmtree(snap)
        snap.mkdir(parents=True)
        refs = sb.git_out(None, "for-each-ref", "--format=%(objectname) %(refname)", git_dir=self.origin)
        (snap / "refs.txt").write_text(refs + "\n", encoding="utf-8")
        shutil.copytree(self.ghstore, snap / "ghstore")
        write_json(snap / "chain.json", self.info.__dict__)

    def restore(self, label: str) -> bool:
        import ghshim

        snap = self.work / "snapshots" / label
        if not (snap / "refs.txt").is_file():
            return False
        want = {}
        for line in (snap / "refs.txt").read_text(encoding="utf-8").splitlines():
            if line.strip():
                sha, ref = line.split(" ", 1)
                want[ref] = sha
        have = sb.git_out(None, "for-each-ref", "--format=%(refname)", git_dir=self.origin).splitlines()
        for ref in have:
            if ref and ref not in want and ref != "refs/heads/cadence/state":
                sb.git(None, "update-ref", "-d", ref, git_dir=self.origin)
        for ref, sha in want.items():
            if ref != "refs/heads/cadence/state":
                sb.git(None, "update-ref", ref, sha, git_dir=self.origin)
        state_sha = want.get("refs/heads/cadence/state")
        if state_sha:
            sb.git(self.state, "reset", "--quiet", "--hard", state_sha)
            sb.git(self.state, "clean", "-fdq")
        sb.rmtree(self.ghstore)
        shutil.copytree(snap / "ghstore", self.ghstore)
        self.info = ChainInfo(**read_json(snap / "chain.json"))
        self.store = ghshim.Store(self.ghstore, self.repo.slug)
        self.save()
        return True


def canary_free(text: str, canaries: list[str]) -> bool:
    return not privacy.canary_hits(text, canaries)


def _final(chain: Chain, epoch: int) -> None:
    """Record (and queue for scoring) main at the end of an epoch."""
    main = chain.main()
    tree = chain.tree_of(main)
    n = len(chain.tickets)
    end = chain.env.clock.at(epoch, n, n, "retro-publish") + 600
    target = chain.results / f"final-e{epoch}"
    target.mkdir(parents=True, exist_ok=True)
    record = {"schema": "cadence-eval.final/1", "arm": chain.arm, "trial": chain.trial, "repo": chain.repo.id,
              "chain": chain.name, "epoch": epoch, "main": main, "tree": tree, "end": end,
              "patch": f"chains/{chain.name}/final-e{epoch}/score/merged.patch", "hidden": {"merged": None}}
    write_json(target / "final.json", record)
    ref = chain.queue_score(attempt_dir=target, field_name="merged", tree=tree, epoch_time=end)
    # queue_score names attempt.json; a final's record is final.json.
    qfile = chain.env.results / "scores" / "queue" / f"{chain.name}-{target.name}-merged.json"
    entry = read_json(qfile)
    entry["attempt"] = (target / "final.json").relative_to(chain.env.results).as_posix()
    write_json(qfile, entry)
    record["hidden"]["merged"] = ref
    write_json(target / "final.json", record)
    chain.info.finals[f"e{epoch}"] = {"sha": main, "tree": tree}
    chain.save()


def run_factory_chain(env: RunEnv, arm: str, trial: int, repo_id: str, epochs: int, resume: bool) -> dict[str, Any]:
    """Every ticket of one (arm, trial, repo), in TASK order, for each epoch."""
    from config import require_valid
    from steps import Ticket

    chain = Chain(env, arm, trial, repo_id)
    chain.setup(resume)
    env.journal("chain-start", chain=chain.name)
    for epoch in range(1, epochs + 1):
        if epoch == 2 and "e2-reset" not in chain.info.done:
            if "final-e1" not in chain.info.done:
                _final(chain, 1)
                chain.info.done.append("final-e1")
            sb.git(None, "tag", "-f", "e1-end", "refs/heads/cadence/state", git_dir=chain.origin)
            chain.bundle_state("state-e1-end.bundle")
            chain.e2_reset()
            chain.info.done.append("e2-reset")
            chain.save()
            env.journal("e2-reset", chain=chain.name, main=chain.main())
        for ticket in chain.tickets:
            label = f"e{epoch}-{ticket.stem}"
            inputs = {"main": chain.main(), "ticket": ticket.sha256, "config": env.cfg.sha256,
                      "stubs": env.agent_mode, "pieces": wfm.Workflow.shas(env.wf)}
            marker = chain.results / label / ".done"
            if label in chain.info.done and marker.is_file():
                continue
            if not chain.restore(label):
                chain.snapshot(label)
                inputs["main"] = chain.main()
            t = Ticket(chain, ticket, epoch)
            attempt = t.run()
            attempt.pop("_path", None)
            require_valid("attempt", attempt, f"{chain.name} {label} attempt.json")
            write_json(t.results / "attempt.json", attempt)
            write_json(marker, {"inputs": inputs, "done_at": wall_now()})
            chain.info.done.append(label)
            chain.save()
            env.journal("ticket", chain=chain.name, ticket=label, outcome=attempt["outcome"],
                        booked=attempt["ledger"]["booked_usd"])
            sb.rmtree(t.work)
    if f"final-e{epochs}" not in chain.info.done:
        _final(chain, epochs)
        chain.info.done.append(f"final-e{epochs}")
    chain.bundle_state("state.bundle")
    if epochs == 1:
        shutil.copy2(chain.results / "state.bundle", chain.results / "state-e1-end.bundle")
    chain.save_ghstore()
    chain.save()
    env.journal("chain-done", chain=chain.name)
    return {"chain": chain.name, "done": list(chain.info.done)}

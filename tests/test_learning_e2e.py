"""End to end: two agent attempts make one learned check (docs/LEARNING.md).

On a temporary git repo (tests/fixtures/learning_e2e/repo/: src/domain,
src/db, src/http, one seed rule "src/domain must not import src/http"),
with the factory tools and schemas installed the way the sandbox setup
installs them, this replays the learning loop the workflow runs, tool by
tool and with the workflow's own flags:

1. Two build attempts, on issues #1 and #2, each import src/db from
   src/domain. ``signals.py observe`` scans each (``python -I``, as the
   observe job does); ``finalize`` checks the bundle; ``ledger.py record``
   books the run; ``put`` copies everything onto the state directory.
2. A maintainer's ``/cadence-forbid src/domain -> src/db`` on the first PR
   (a reviewer-command finding, as harvest stages it) seeds the edge.
3. ``ladder.py plan`` proposes one check; the plan sha is stable, and a
   plan equal to the open PR's is "unchanged".
4. ``ladder.py apply`` runs the real ``emit_rule.py``: the fixture holds
   issue 2's real line at its real path, the rule lands in cadence.yaml
   with its L- id, and lessons.yaml and docs/PATTERNS.md follow.
5. ``ladder.py guard`` passes on the working tree (retro-plan) and on the
   staged patch (retro-publish); the checker stays green on main.
6. ``metrics.py report`` scores the repeat by hand: one opportunity (#2
   after #1), one repeat, one escape (#2 was published).

Each step skips while the tool it needs is not built yet.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL_DIR = REPO_ROOT / "plugins" / "cadence" / "templates" / "tool"
SCHEMA_DIR = REPO_ROOT / "plugins" / "cadence" / "schemas"
FIXTURE_REPO = Path(__file__).resolve().parent / "fixtures" / "learning_e2e" / "repo"

NS_CADENCE = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/nik190799/cadence#factory")
REPO = "octo/shop"
EDGE = "import-edge:src/domain->src/db"
IMPORT_LINE = 'import { query } from "../db/client";'
LESSON = "L-" + uuid.uuid5(NS_CADENCE, "lesson|" + EDGE).hex[:8]
FIXTURE = f"tests/fixtures/retro/{LESSON[2:]}/"
LEARNED_HEADING = "## Learned patterns (factory)"
SEED_COMMENT = "/cadence-forbid src/domain -> src/db"

T1 = 1_790_000_000  # issue 1's attempt
T_SEED = T1 + 1800  # the maintainer's /cadence-forbid on PR #10
T2 = T1 + 7200  # issue 2's attempt
NOW = T2 + 600  # the learn run

ATTEMPTS = {
    1: {"run_id": "1001", "pr": 10, "published_sha": "a1" * 20, "at": T1},
    2: {"run_id": "1002", "pr": 11, "published_sha": "b2" * 20, "at": T2},
}
FINAL_SHA_PR10 = "c3" * 20  # PR #10's head when the maintainer closed it

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


# --- helpers ------------------------------------------------------------------


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require(*tools: str) -> None:
    missing = [t for t in tools if not (TOOL_DIR / f"{t}.py").is_file()]
    if missing:
        pytest.skip("not built yet: " + ", ".join(f"tool/{t}.py" for t in missing))


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8"
    )
    assert done.returncode == 0, f"git {' '.join(args)}\n{done.stdout}\n{done.stderr}"
    return done.stdout


def _tool(
    repo: Path, tool: str, *args: str, isolated: bool = False, ok: tuple[int, ...] = (0,)
) -> subprocess.CompletedProcess[str]:
    """Run an installed tool the way the workflow does (from the repo's tool/)."""
    cmd = [sys.executable, *(["-I"] if isolated else []), str(repo / "tool" / f"{tool}.py"), *args]
    done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    assert done.returncode in ok, (
        f"{tool} {' '.join(args)} exited {done.returncode}\n"
        f"--- stdout\n{done.stdout}\n--- stderr\n{done.stderr}"
    )
    return done


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _make_patch(repo: Path, where: Path, files: dict[str, str]) -> Path:
    """The agent job's "Package the diff", from a scratch worktree of the base."""
    _git(repo, "worktree", "add", "--quiet", "--detach", str(where), "HEAD")
    for rel, text in files.items():
        _write(where / rel, text)
    _git(where, "add", "-A")
    diff = _git(where, "diff", "--cached", "--binary", "--no-color", "--no-ext-diff",
                "--no-textconv", "HEAD")
    patch = where.parent / f"{where.name}.patch"
    patch.write_text(diff, encoding="utf-8", newline="\n")
    return patch


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seed_finding(patch1: Path) -> dict[str, Any]:
    """What harvest stages for a /cadence-forbid on PR #10 (docs/LEARNING.md, 3a)."""
    a1 = ATTEMPTS[1]
    fid = uuid.uuid5(
        NS_CADENCE,
        "|".join(["reviewer-command", REPO, "1", _sha256_file(patch1), EDGE, "comment:5551"]),
    )
    return {
        "id": str(fid),
        "ts": _iso(T_SEED),
        "feature": "issue #1",
        "what_happened": "A maintainer marked the import of src/db from src/domain as forbidden on PR #10.",
        "auto_catchable": True,
        "auto_method": "boundary-rule",
        "rule_existed": False,
        "proposed_fix": "Promote a boundary rule: src/domain/** must not import src/db/** (tool/ladder.py).",
        "fix_layer": 3,
        "violation_sample": {
            "kind": "boundary-rule",
            "language": "ts",
            "where": "src/domain/**",
            "import_line": IMPORT_LINE,
            "forbidden_pattern": "src/db/**",
            "reason": "Factory finding: src/domain/ must not import src/db/.",
        },
        "factory": {
            "schema_version": 1,
            "class_key": EDGE,
            "family": "import-edge",
            "signal": "reviewer-command",
            "trust": "A",
            "phase": "post-pr",
            "gate_caught": False,
            "reached_pr": True,
            "rule_id": None,
            "repo": REPO,
            "issue": 1,
            "pr": a1["pr"],
            "run_id": a1["run_id"],
            "run_attempt": 1,
            "base_sha": None,
            "published_sha": a1["published_sha"],
            "final_sha": FINAL_SHA_PR10,
            "patch_sha256": _sha256_file(patch1),
            "path": "src/domain/order.ts",
            "line_no": 1,
            "area": "src/domain",
            "comment_id": 5551,
            "excerpt_sha256": hashlib.sha256(SEED_COMMENT.encode()).hexdigest(),
            "edit_basis": None,
            "classification": {"by": "reviewer-command", "model": None, "prompt_sha256": None, "confidence": None},
            "judge": None,
        },
    }


# --- the world: base repo, two attempts on cadence/state, one seed ------------


class World:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.repo = root / "repo"
        self.state = root / "state"
        self.out = root / "out"
        self.out.mkdir()
        self.base_sha = ""
        self.patches: dict[int, Path] = {}
        self.plan: dict[str, Any] = {}
        self.plan_summary: dict[str, Any] = {}
        self.applied: dict[str, Any] = {}
        self.cadence_yaml_before = ""


def _build_world(root: Path) -> World:
    world = World(root)
    repo = world.repo

    # The base repo, LF everywhere, with the tools and schemas installed as
    # docs/factory-sandbox-setup.md installs them.
    for src in FIXTURE_REPO.rglob("*"):
        if src.is_file():
            text = src.read_text(encoding="utf-8").replace("\r\n", "\n")
            _write(repo / src.relative_to(FIXTURE_REPO), text)
    for tool in TOOL_DIR.glob("*.py"):
        _write(repo / "tool" / tool.name, tool.read_text(encoding="utf-8").replace("\r\n", "\n"))
    for schema in SCHEMA_DIR.glob("*.schema.json"):
        _write(repo / ".cadence" / schema.name, schema.read_text(encoding="utf-8"))
    _git(root, "init", "--quiet", str(repo))
    for key, value in (
        ("core.autocrlf", "false"), ("user.name", "maintainer"),
        ("user.email", "maintainer@example.com"), ("commit.gpgsign", "false"),
    ):
        _git(repo, "config", key, value)
    # As in retro-plan: Python caches are never part of the retro patch.
    _write(repo / ".git" / "info" / "exclude", "__pycache__/\n*.py[cod]\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "base")
    world.base_sha = _git(repo, "rev-parse", "HEAD").strip()
    world.cadence_yaml_before = (repo / ".cadence" / "cadence.yaml").read_text(encoding="utf-8")

    # cadence/state: an orphan branch in its own clone.
    _git(root, "init", "--quiet", str(world.state))
    _git(world.state, "config", "core.autocrlf", "false")

    order = (repo / "src" / "domain" / "order.ts").read_text(encoding="utf-8")
    agent_files = {
        1: {
            # Issue 1 changes an existing domain file...
            "src/domain/order.ts": IMPORT_LINE + "\n" + order
            + "\nexport const loadOrder = (id: string) => query(\"select * from orders where id = ?\", id);\n",
            "tests/domain/order-load.test.ts": "export const loads = [\"a\"];\n",
        },
        2: {
            # ...issue 2 adds a new one: same edge, different patch.
            "src/domain/invoice.ts": IMPORT_LINE + "\n\n"
            + "export const loadInvoice = (id: string) => query(\"select * from invoices where id = ?\", id);\n",
            "tests/domain/invoice.test.ts": "export const invoices = [\"i\"];\n",
        },
    }
    for issue, files in agent_files.items():
        a = ATTEMPTS[issue]
        patch = _make_patch(repo, root / f"agent{issue}", files)
        world.patches[issue] = patch

        # observe: apply to a scratch worktree of the base, then scan it.
        work = root / f"work{issue}"
        _git(repo, "worktree", "add", "--quiet", "--detach", str(work), "HEAD")
        _git(work, "apply", "--index", str(patch))
        obs = root / f"observe{issue}"
        summary = _tool(
            repo, "signals", "observe",
            "--base-dir", str(repo), "--work-dir", str(work), "--patch", str(patch),
            "--apply-status", "ok", "--repo", REPO, "--issue", str(issue),
            "--run-id", a["run_id"], "--run-attempt", "1", "--base-sha", world.base_sha,
            "--agent-result", "success", "--verify-result", "success",
            "--now", str(a["at"]), "--out-dir", str(obs),
            isolated=True,
        )
        reported = json.loads(summary.stdout)
        assert reported["patch_sha256"] == _sha256_file(patch)

        observation = json.loads((obs / "observation.json").read_text(encoding="utf-8"))
        edges = [e for e in observation["evidence"]["import_edges"] if e["key"] == EDGE]
        assert len(edges) == 1, observation["evidence"]["import_edges"]
        assert edges[0]["line"] == IMPORT_LINE and edges[0]["line_no"] == 1
        assert observation["classes"] == []  # tests were added; no gate or agent class
        assert observation["evidence"]["guarded"] == []  # new files under tests/ are allowed

        # ledger: finalize the bundle, book the run, put both on the state.
        staged = root / f"staged{issue}"
        bundle_sha = (obs / "bundle.sha256").read_text(encoding="utf-8").strip()
        _tool(
            repo, "signals", "finalize",
            "--bundle-file", str(obs / "bundle.b64"), "--bundle-sha256", bundle_sha,
            "--run-id", a["run_id"], "--run-attempt", "1", "--issue", str(issue),
            "--pr", str(a["pr"]), "--published-sha", a["published_sha"],
            "--patch", str(patch), "--schema-dir", str(repo / ".cadence"),
            "--out-dir", str(staged),
        )
        _tool(
            repo, "ledger", "--config", str(repo / ".cadence" / "factory.yaml"),
            "--records-dir", str(staged / "runs"),
            "record", "--run-id", a["run_id"], "--run-attempt", "1", "--issue", str(issue),
            "--outcome", "success", "--dod", "pass", "--stage", "build",
            "--base-sha", world.base_sha, "--pr", str(a["pr"]),
            "--published-sha", a["published_sha"],
            "--cost-usd", "0.20", "--turns", "12", "--now", str(a["at"]),
        )
        put = json.loads(
            _tool(repo, "signals", "put", "--staged", str(staged), "--state", str(world.state)).stdout
        )
        run = f"{a['run_id']}-1"
        assert set(put["added"]) >= {
            f"observations/{run}.json", f"findings/{run}.jsonl", f"patches/{run}.patch",
            f"prs/{a['pr']}-{a['run_id']}.json", f"runs/{run}.json",
        }, put
        assert put["skipped"] == []

    # Put is create-only: the same files again are all skipped.
    again = json.loads(
        _tool(repo, "signals", "put", "--staged", str(root / "staged2"), "--state", str(world.state)).stdout
    )
    assert again["added"] == [] and again["skipped"]

    # The maintainer's /cadence-forbid on PR #10, as harvest stages it.
    seed = root / "staged-seed"
    head12 = FINAL_SHA_PR10[:12]
    _write(
        seed / "findings" / f"pr-10-{head12}.jsonl",
        json.dumps(_seed_finding(world.patches[1]), sort_keys=True, separators=(",", ":")) + "\n",
    )
    marker = {
        "schema": "cadence.harvest/1", "pr": 10, "issue": 1, "kind": "agent",
        "final_head_sha": FINAL_SHA_PR10, "merged": False, "closed_at": _iso(T_SEED + 60),
        "harvested_at": _iso(T_SEED + 900), "edit_basis": "none", "findings": 1, "status": "ok",
    }
    _write(seed / "harvest" / "pr-10.json", json.dumps(marker, indent=2) + "\n")
    _tool(repo, "signals", "put", "--staged", str(seed), "--state", str(world.state))
    return world


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    _require("signals", "ledger", "check_boundaries", "intake_sanitize", "emit_rule")
    return _build_world(tmp_path_factory.mktemp("learning-e2e"))


@pytest.fixture(scope="module")
def planned(world: World) -> World:
    _require("ladder")
    plan_file = world.out / "plan.json"
    summary = _tool(
        world.repo, "ladder", "plan", "--state-dir", str(world.state), "--repo-root", str(world.repo),
        "--config", str(world.repo / ".cadence" / "factory.yaml"), "--now", str(NOW),
        "--out", str(plan_file),
    )
    world.plan_summary = json.loads(summary.stdout)
    world.plan = json.loads(plan_file.read_text(encoding="utf-8"))
    return world


@pytest.fixture(scope="module")
def applied(planned: World) -> World:
    world = planned
    applied_file = world.out / "applied.json"
    _tool(
        world.repo, "ladder", "apply", "--plan", str(world.out / "plan.json"),
        "--repo-root", str(world.repo), "--state-dir", str(world.state), "--now", str(NOW),
        "--out", str(applied_file),
    )
    world.applied = json.loads(applied_file.read_text(encoding="utf-8"))
    return world


# --- the tests ----------------------------------------------------------------


def test_two_issues_with_one_seeded_edge_plan_one_check(planned: World) -> None:
    summary, plan = planned.plan_summary, planned.plan
    assert summary["changed"] is True
    assert summary["mode"] == "on"
    assert summary["transitions"] == 1
    assert re.fullmatch(r"[0-9a-f]{64}", summary["plan_sha"])
    assert plan["plan_sha"] == summary["plan_sha"]
    assert plan["base_sha"] == planned.base_sha
    assert plan["applied"] is False

    [move] = plan["transitions"]
    assert move["class_key"] == EDGE
    assert move["lesson_id"] == LESSON
    assert (move["from"], move["to"], move["reason"]) == ("note", "check", "promote")
    assert sorted(move["issues"]) == [1, 2]
    assert move["emit"] is None
    # The sample is the newest occurrence: issue 2's real line, at its real path.
    sample = move["sample"]
    assert sample["run"] == "1002-1"
    assert sample["patch"] == "patches/1002-1.patch"
    assert sample["patch_sha256"] == _sha256_file(planned.patches[2])
    assert (sample["path"], sample["line_no"]) == ("src/domain/invoice.ts", 1)
    assert sample["import_line"] == IMPORT_LINE
    assert (sample["where"], sample["forbidden_pattern"]) == ("src/domain/**", "src/db/**")
    assert sample["language"] == "ts"


def test_the_plan_sha_is_stable_and_an_open_identical_plan_is_unchanged(planned: World) -> None:
    sha = planned.plan_summary["plan_sha"]
    again = json.loads(
        _tool(
            planned.repo, "ladder", "plan", "--state-dir", str(planned.state),
            "--repo-root", str(planned.repo), "--config", str(planned.repo / ".cadence" / "factory.yaml"),
            "--now", str(NOW + 60), "--open-plan-sha", sha, "--out", str(planned.out / "plan-again.json"),
        ).stdout
    )
    assert again["plan_sha"] == sha
    assert again["changed"] is False


def test_apply_lands_the_check_on_its_real_failing_line(applied: World) -> None:
    repo, result = applied.repo, applied.applied
    assert result["applied"] is True
    assert result["verify_required"] is True
    [move] = result["transitions"]
    assert move["to"] == "check"
    assert move["emit"] == {"exit": 0, "fixture": FIXTURE}

    # cadence.yaml: the seed rule and its comment untouched, the L- rule appended.
    text = (repo / ".cadence" / "cadence.yaml").read_text(encoding="utf-8")
    assert text.startswith(applied.cadence_yaml_before.rstrip("\n"))
    assert "# Seed rule: the domain never talks HTTP." in text
    rules = yaml.safe_load(text)["boundaries"]
    assert rules[0] == yaml.safe_load(applied.cadence_yaml_before)["boundaries"][0]
    learned = [r for r in rules if r.get("id") == LESSON]
    assert len(learned) == 1
    assert learned[0]["where"] == "src/domain/**"
    assert learned[0]["forbidden"] == ["src/db/**"]
    assert LESSON in learned[0]["reason"] and FIXTURE.rstrip("/") in learned[0]["reason"]

    # The fixture: the real line at its real path, plus its provenance.
    fixture = repo / FIXTURE
    sample = fixture / "src" / "domain" / "invoice.ts"
    assert IMPORT_LINE in sample.read_text(encoding="utf-8").splitlines()
    provenance = json.loads((fixture / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["rule_id"] == LESSON
    assert provenance["class_key"] == EDGE
    assert (provenance["path"], provenance["line_no"]) == ("src/domain/invoice.ts", 1)
    assert provenance["patch_sha256"] == _sha256_file(applied.patches[2])
    assert (fixture / "finding.json").is_file()
    [fixture_rule] = yaml.safe_load(
        (fixture / ".cadence" / "cadence.yaml").read_text(encoding="utf-8")
    )["boundaries"]
    assert fixture_rule["id"] == LESSON

    # lessons.yaml and the learned section of PATTERNS.md.
    lessons = yaml.safe_load((repo / ".cadence" / "lessons.yaml").read_text(encoding="utf-8"))
    [lesson] = [entry for entry in lessons["lessons"] if entry["id"] == LESSON]
    assert lesson["class_key"] == EDGE and lesson["rung"] == "check"
    assert lesson["check"] == {"kind": "boundary-rule", "rule_id": LESSON, "fixture": FIXTURE}
    patterns = (repo / "docs" / "PATTERNS.md").read_text(encoding="utf-8")
    assert patterns.startswith("# Patterns\n\n## §1 — Layout\n")
    section = patterns[patterns.index(LEARNED_HEADING):]
    assert f"- **{LESSON}** (check):" in section


def test_main_stays_green_and_the_fixture_still_fires(applied: World) -> None:
    repo = applied.repo
    # The checker on main: the new rule finds nothing (the fixture is skipped).
    _tool(repo, "check_boundaries", "--root", str(repo), "--quiet")
    # The fixture on its own: the rule fires on the sample.
    fired = _tool(
        repo, "check_boundaries", "--root", str(repo / FIXTURE), "--quiet", ok=(1,)
    )
    assert "src/domain/invoice.ts" in fired.stderr


def test_the_guard_passes_in_both_retro_jobs(applied: World) -> None:
    repo = applied.repo
    applied_file = str(applied.out / "applied.json")
    # retro-plan: on the working tree, before anything is staged.
    _tool(repo, "ladder", "guard", "--repo-root", str(repo), "--worktree", "--applied", applied_file)
    # retro-plan stages only the allowlist; retro-publish guards that patch.
    _git(repo, "add", "--", ".cadence/cadence.yaml", ".cadence/lessons.yaml", "docs/PATTERNS.md",
         "tests/fixtures/retro")
    patch = applied.out / "retro.patch"
    patch.write_text(_git(repo, "diff", "--cached", "--binary"), encoding="utf-8", newline="\n")
    changed = set(_git(repo, "diff", "--cached", "--name-only").split())
    assert changed >= {".cadence/cadence.yaml", ".cadence/lessons.yaml", "docs/PATTERNS.md"}
    assert all(
        p in (".cadence/cadence.yaml", ".cadence/lessons.yaml", "docs/PATTERNS.md")
        or p.startswith(FIXTURE) for p in changed
    ), changed
    _tool(repo, "ladder", "guard", "--repo-root", str(repo), "--patch", str(patch),
          "--applied", applied_file, isolated=True)

    body_file = applied.out / "body.md"
    _tool(repo, "ladder", "pr-body", "--applied", applied_file, "--repo", REPO,
          "--run-url", f"https://github.com/{REPO}/actions/runs/1003", "--out", str(body_file),
          isolated=True)
    body = body_file.read_text(encoding="utf-8")
    assert len(body) <= 60000
    assert body.rstrip("\n").splitlines()[-1] == "cadence retro plan " + applied.plan["plan_sha"][:12]
    assert LESSON in body and FIXTURE.rstrip("/") in body
    assert "@" not in body and "<" not in body
    assert IMPORT_LINE not in body  # keys and links, never excerpts


def test_metrics_score_one_repeat_that_escaped(world: World) -> None:
    _require("metrics")
    out = world.out / "metrics.json"
    done = _tool(
        world.repo, "metrics", "report", "--state-dir", str(world.state),
        "--repo-root", str(world.repo), "--config", str(world.repo / ".cadence" / "factory.yaml"),
        "--now", str(NOW), "--out", str(out),
    )
    assert done.returncode == 0  # complete: both build runs have observations
    report = json.loads(out.read_text(encoding="utf-8"))
    from jsonschema import Draft202012Validator

    schema = json.loads((SCHEMA_DIR / "metrics.schema.json").read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(report))
    assert errors == [], [e.message for e in errors]

    assert report["completeness"]["build_runs"] == 2
    assert report["completeness"]["observed"] == 2
    assert report["completeness"]["ratio"] == 1.0
    assert report["attempts"]["scored"] == 2
    # By hand: #1 has no earlier attempt. #2 comes after #1 (another issue),
    # touches src/domain (exposed), and repeats the seeded edge: one
    # opportunity, one repeat. #2 was published (PR #11): one escape.
    repeat, escape = report["repeat"], report["escape"]
    assert (repeat["opportunities"], repeat["repeats"]) == (1, 1)
    assert escape["escapes"] == 1
    assert repeat["status"] == "insufficient"  # |O| < 30
    assert repeat["ci95"] is None and escape["ci95"] is None
    for rate in (repeat["rate"], escape["rate"], escape["share"]):
        assert rate in (None, 1.0)
    assert escape["per_10_attempts"] in (None, 5.0)
    family = report["by_family"]["import-edge"]
    assert (family["opportunities"], family["repeats"], family["escapes"]) == (1, 1, 1)
    assert report["learned_check_catches"]["count"] == 0

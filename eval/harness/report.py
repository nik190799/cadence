"""The numbers for the gate, and the private report.

Two questions, decided by pre-registered rules:

- Q1, the factory against a plain agent: the repo-mean of the trial-mean
  hidden check fraction on F1's main at the end of E1, minus the same for
  A0. 90% interval by bootstrap (2000 resamples, seed 13): each resample
  draws trials with replacement per arm and per repo, and checks with
  replacement within each repo's denominator. Q1 passes iff the interval's
  lower bound is above 0 and F1 is worse on no repo (every repo's mean
  difference >= 0). Secondary: F0 at the end of E1 against A0.
- Q2, the learning loop (the gate): ``metrics.py compare`` of the F1 states
  (rules on) against the F0 states (rules frozen) with the pre-registered
  detector set, on E1+E2 (primary) and E1 only, plus a union-detector
  sensitivity run. Its built-in rule decides: the 90% interval of
  RR_frozen - RR_on lies above 0, ER_on < ER_frozen, the on arm's
  tampering is no higher, and its median cost at most 1.25 x frozen's.

Also reported: pass@1 and pass^k for tickets and repos, first-pass verify
per ticket attempt, costs (reported and booked) against the $20 median
kill line, tampering (the metrics rule for every arm, a broad rule, and the
harness's own), hidden escapes, false blocks, the rates of questions,
retries, dod-failed, needs-human and voids, the per-check pass rate, and the
pre-registered limits (learned_check_catches is 0 by construction; the
30-day merge rate is not measured).
"""

from __future__ import annotations

import math
import random
import shutil
import statistics
from math import comb
from pathlib import Path
from typing import Any, Callable, Sequence

import sandbox as sb
import score as scoring
import workflow as wfm
from config import Config, Private, read_json, write_json, write_text

KILL_MEDIAN_USD = 20.0
BROAD_PREFIXES = (".github/", ".cadence/", "tool/", "scripts/verify.sh")


# --- statistics ------------------------------------------------------------------------------


def percentile(values: Sequence[float], q: float) -> float:
    """Linear interpolation between closest ranks (as numpy's default)."""
    data = sorted(values)
    if not data:
        raise ValueError("no values")
    pos = (len(data) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return data[lo]
    return data[lo] + (data[hi] - data[lo]) * (pos - lo)


def q1_bootstrap(
    arm_a: dict[str, list[list[int]]],
    arm_b: dict[str, list[list[int]]],
    *,
    resamples: int = 2000,
    seed: int = 13,
) -> dict[str, Any]:
    """A minus B. Per repo: a list of trials, each a 0/1 vector over the
    repo's denominator (same order in both arms)."""
    repos = sorted(set(arm_a) & set(arm_b))
    if not repos:
        return {"point": None, "ci90": None, "by_repo": {}, "pass": False, "reasons": ["no repo has both arms"]}

    def mean_fraction(trials: list[list[int]], checks: list[int]) -> float:
        return statistics.fmean(sum(t[c] for c in checks) / len(checks) for t in trials)

    by_repo: dict[str, float] = {}
    for repo in repos:
        width = len(arm_a[repo][0])
        idx = list(range(width))
        by_repo[repo] = mean_fraction(arm_a[repo], idx) - mean_fraction(arm_b[repo], idx)
    point = statistics.fmean(by_repo.values())
    rng = random.Random(seed)
    draws: list[float] = []
    for _ in range(resamples):
        diffs = []
        for repo in repos:
            width = len(arm_a[repo][0])
            checks = [rng.randrange(width) for _ in range(width)]
            ta = [arm_a[repo][rng.randrange(len(arm_a[repo]))] for _ in arm_a[repo]]
            tb = [arm_b[repo][rng.randrange(len(arm_b[repo]))] for _ in arm_b[repo]]
            diffs.append(mean_fraction(ta, checks) - mean_fraction(tb, checks))
        draws.append(statistics.fmean(diffs))
    ci90 = [percentile(draws, 0.05), percentile(draws, 0.95)]
    reasons = []
    if not ci90[0] > 0:
        reasons.append("the 90% interval's lower bound is not above 0")
    worse = [r for r, d in by_repo.items() if d < 0]
    if worse:
        reasons.append(f"worse on {len(worse)} repo(s)")
    return {
        "point": round(point, 6),
        "ci90": [round(ci90[0], 6), round(ci90[1], 6)],
        "by_repo": {r: round(d, 6) for r, d in by_repo.items()},
        "resamples": resamples,
        "seed": seed,
        "pass": not reasons,
        "reasons": reasons,
    }


def pass_hat_k(results: Sequence[bool], k: int) -> float | None:
    """pass^k: the chance that k trials drawn without replacement all pass."""
    n, c = len(results), sum(1 for r in results if r)
    if n == 0 or k < 1 or k > n:
        return None
    return comb(c, k) / comb(n, k)


def pass_rates(groups: dict[str, list[bool]], k: int | None = None) -> dict[str, Any]:
    """pass@1 (mean over items of the trial mean) and pass^k over items."""
    items = {key: vals for key, vals in groups.items() if vals}
    if not items:
        return {"items": 0, "pass@1": None, "pass^k": None, "k": k}
    kk = k or min(len(v) for v in items.values())
    at1 = statistics.fmean(sum(v) / len(v) for v in items.values())
    hats = [pass_hat_k(v, kk) for v in items.values()]
    hats = [h for h in hats if h is not None]
    return {"items": len(items), "pass@1": round(at1, 6),
            "pass^k": round(statistics.fmean(hats), 6) if hats else None, "k": kk}


def median_or_none(values: Sequence[float]) -> float | None:
    return round(statistics.median(values), 6) if values else None


def safe_div(a: float, b: float) -> float | None:
    return round(a / b, 6) if b else None


# --- loading the run ------------------------------------------------------------------------------


def load_attempts(results: Path) -> list[dict[str, Any]]:
    out = []
    for path in sorted((results / "chains").glob("*/e*-*/attempt.json")):
        out.append({**read_json(path), "_path": str(path)})
    for path in sorted((results / "autopilot").glob("*/attempt.json")):
        out.append({**read_json(path), "_path": str(path)})
    return out


def load_finals(results: Path) -> list[dict[str, Any]]:
    return [{**read_json(p), "_path": str(p)} for p in sorted((results / "chains").glob("*/final-e*/final.json"))]


def score_of(results: Path, ref: dict[str, Any] | None) -> dict[str, Any] | None:
    if not ref or not ref.get("score_file"):
        return None
    path = results / ref["score_file"]
    return read_json(path) if path.is_file() else None


def vector(record: dict[str, Any], denominator: list[str]) -> list[int]:
    return [1 if record["checks"].get(n, {}).get("result") == "PASS" else 0 for n in denominator]


# --- the factory's own metrics, in the tools sandbox ---------------------------------------------------


def _state_from_bundle(bundle: Path, dest: Path) -> None:
    git_dir = dest.with_name(dest.name + ".git")
    sb.rmtree(git_dir)
    sb.git(None, "init", "--bare", "--quiet", str(git_dir))
    sb.git(None, "fetch", "--quiet", str(bundle), "refs/heads/cadence/state:refs/heads/cadence/state", git_dir=git_dir)
    data = sb.git(None, "archive", "--format=tar", "refs/heads/cadence/state", git_dir=git_dir).stdout
    sb.untar(data, dest)
    sb.rmtree(git_dir)


def _materialize(cfg: Config, repo_id: str, patch: Path, tree: str, dest: Path, epoch: int) -> None:
    commit, _ = scoring.result_commit(cfg, repo_id, patch, tree, epoch)
    data = sb.git(None, "archive", "--format=tar", commit, git_dir=scoring.score_base(cfg, repo_id)).stdout
    sb.untar(data, dest)


def run_metrics(cfg: Config, run_id: str, *, log: Callable[[str], None] = print) -> dict[str, Any]:
    """metrics.py report per chain and metrics.py compare for Q2."""
    results = cfg.results(run_id)
    out_dir = results / "report"
    out_dir.mkdir(parents=True, exist_ok=True)
    root = cfg.work(run_id) / "report"
    sb.rmtree(root)
    box = sb.Box(cfg.sandbox, "tools", cfg.home, root,
                 binds=[sb.Bind(cfg.cache_root / "tools", sb.OPT_TOOLS, ro=True)])
    tools = box.opt(sb.OPT_TOOLS)
    states: dict[str, dict[str, Path]] = {}
    out: dict[str, Any] = {"metrics": {}, "compare": {}}
    for chain_dir in sorted((results / "chains").glob("F*-t*-*")):
        name = chain_dir.name
        info = read_json(chain_dir / "chain.json")
        for label, bundle in (("final", chain_dir / "state.bundle"), ("e1", chain_dir / "state-e1-end.bundle")):
            if bundle.is_file():
                dest = box.work / "states" / f"{name}-{label}"
                _state_from_bundle(bundle, dest)
                states.setdefault(label, {})[name] = dest
        if "final" not in states or name not in states["final"]:
            continue
        finals = sorted(chain_dir.glob("final-e*/final.json"))
        if not finals:
            continue
        final = read_json(finals[-1])
        main_dir = box.work / "mains" / name
        _materialize(cfg, info["repo"], results / final["patch"], final["tree"], main_dir, cfg.clock_epoch)
        target = out_dir / f"metrics-{name}.json"
        res = box.run(wfm.tool_argv(tools, "metrics.py report", "report", "--state-dir", box.inside(states["final"][name]),
                                    "--repo-root", box.inside(main_dir), "--order", "time", "--window", "0",
                                    "--now", str(final["end"]), "--out", box.inside(target)), timeout=600)
        out["metrics"][name] = {"exit": res.exit}
        if res.exit not in (0, 1):
            log(f"report: metrics.py report {name} exited {res.exit}: {res.stderr.decode(errors='replace')[-300:]}")
    ticket_map = out_dir / "ticket-map.json"
    write_json(ticket_map, build_ticket_map(cfg, results))
    seed_yaml = box.work / "factory.yaml"
    from seed import render_factory_yaml

    write_text(seed_yaml, render_factory_yaml(cfg))
    detectors = box.work / "detectors.json"
    shutil.copy2(cfg.files["detectors"], detectors)
    for label, tag, det in (("final", "e1e2", "file"), ("e1", "e1", "file"), ("final", "e1e2-union", "union")):
        group = states.get(label, {})
        on = [p for n, p in sorted(group.items()) if n.startswith("F1-")]
        frozen = [p for n, p in sorted(group.items()) if n.startswith("F0-")]
        if not on or not frozen:
            continue
        target = out_dir / f"compare-{tag}.json"
        argv = ["compare"]
        for p in on:
            argv += ["--on", box.inside(p)]
        for p in frozen:
            argv += ["--frozen", box.inside(p)]
        argv += ["--ticket-map", box.inside(ticket_map),
                 "--detector-set", box.inside(detectors) if det == "file" else "union",
                 "--order", "time", "--window", "0", "--resamples", "2000", "--seed", "13",
                 "--config", box.inside(seed_yaml), "--out", box.inside(target)]
        res = box.run(wfm.tool_argv(tools, "metrics.py compare", *argv), timeout=1800)
        out["compare"][tag] = {"exit": res.exit}
        if res.exit not in (0, 1):
            log(f"report: metrics.py compare {tag} exited {res.exit}: {res.stderr.decode(errors='replace')[-400:]}")
    sb.rmtree(root)
    return out


def build_ticket_map(cfg: Config, results: Path) -> dict[str, Any]:
    tickets: dict[str, str] = {}
    for repo in cfg.repos:
        for position, stem in enumerate(repo.tickets, start=1):
            tickets[f"{repo.slug}#{position}"] = f"{repo.id}.T{position}"
    for path in sorted((results / "chains").glob("*/e*-*/attempt.json")):
        a = read_json(path)
        slug = cfg.repo(a["repo"]).slug
        if a.get("ticket"):
            tickets[f"{slug}#{a['issue']}"] = a["ticket"]
    return {"schema": "cadence.ticket-map/1", "tickets": tickets}


# --- the summary ------------------------------------------------------------------------------------


def _ticket_pass(a: dict[str, Any]) -> bool | None:
    return a.get("hidden", {}).get("ticket_pass")


def _broad_tampered(a: dict[str, Any], test_roots: Sequence[str]) -> bool:
    for t in a.get("tries", []):
        for f in t.get("files", []):
            if any(f == r or f.startswith(r + "/") for r in test_roots) or f.startswith(BROAD_PREFIXES):
                return True
    return "agent-merge" in (a.get("retro_paths", {}).get("changed_by") or [])


def _metrics_tampered(obs: dict[str, Any], test_roots: Sequence[str]) -> bool:
    for g in (obs.get("evidence", {}).get("guarded") or []):
        op, root, path = g.get("op"), g.get("root", ""), g.get("path", "")
        if op in ("modify", "delete") and any(root == r or root.startswith(r + "/") or path.startswith(r + "/") for r in test_roots):
            return True
    return False


def summarize(cfg: Config, private: Private, run_id: str) -> dict[str, Any]:
    results = cfg.results(run_id)
    attempts = load_attempts(results)
    finals = load_finals(results)
    test_roots = list(cfg.factory["test_roots"])
    factory = [a for a in attempts if a["arm"] in ("F0", "F1")]
    scored = [a for a in factory if a["outcome"] != "infra-failed"]
    autopilot = [a for a in attempts if a["arm"] == "A0"]
    s: dict[str, Any] = {"schema": "cadence-eval.summary/1", "run_id": run_id}

    # Q1: F1 (and F0) at the end of E1 against A0.
    def arm_vectors(arm: str) -> dict[str, list[list[int]]]:
        out: dict[str, list[list[int]]] = {}
        if arm == "A0":
            for a in autopilot:
                rec = score_of(results, a["hidden"].get("merged"))
                if rec and rec.get("status") == "ok":
                    den = scoring.check_map(private.checks, a["repo"]).denominator
                    out.setdefault(a["repo"], []).append(vector(rec, den))
            return out
        for f in finals:
            if f["arm"] == arm and f["epoch"] == 1:
                rec = score_of(results, f["hidden"].get("merged"))
                if rec and rec.get("status") == "ok":
                    den = scoring.check_map(private.checks, f["repo"]).denominator
                    out.setdefault(f["repo"], []).append(vector(rec, den))
        return out

    a0, f1, f0 = arm_vectors("A0"), arm_vectors("F1"), arm_vectors("F0")
    s["q1"] = q1_bootstrap(f1, a0)
    s["q1_secondary_f0"] = q1_bootstrap(f0, a0)

    # Q2: metrics.py compare.
    s["q2"] = {}
    for tag in ("e1e2", "e1", "e1e2-union"):
        path = results / "report" / f"compare-{tag}.json"
        if path.is_file():
            s["q2"][tag] = read_json(path)

    # pass@1 and pass^k.
    def ticket_groups(arm: str, epoch: int) -> dict[str, list[bool]]:
        groups: dict[str, list[bool]] = {}
        if arm == "A0":
            for a in autopilot:
                rec = score_of(results, a["hidden"].get("merged"))
                if rec is None or rec.get("status") != "ok":
                    continue
                cmap = scoring.check_map(private.checks, a["repo"])
                for ticket in private.tickets[a["repo"]]:
                    tp = scoring.ticket_pass(rec, cmap.owned(ticket.keys()), cmap.regression())
                    groups.setdefault(ticket.id, []).append(bool(tp))
            return groups
        for a in scored:
            if a["arm"] == arm and a["epoch"] == epoch and _ticket_pass(a) is not None:
                groups.setdefault(a["ticket"], []).append(bool(_ticket_pass(a)))
        return groups

    def repo_groups(arm: str) -> dict[str, list[bool]]:
        groups: dict[str, list[bool]] = {}
        if arm == "A0":
            for a in autopilot:
                rec = score_of(results, a["hidden"].get("merged"))
                if rec is not None and rec.get("status") == "ok":
                    groups.setdefault(a["repo"], []).append(bool(rec["repo_pass"]))
            return groups
        for f in finals:
            if f["arm"] == arm and f["epoch"] == 1:
                rec = score_of(results, f["hidden"].get("merged"))
                if rec is not None and rec.get("status") == "ok":
                    groups.setdefault(f["repo"], []).append(bool(rec["repo_pass"]))
        return groups

    s["pass"] = {
        "tickets": {
            "A0": pass_rates(ticket_groups("A0", 1)),
            "F0-E1": pass_rates(ticket_groups("F0", 1)), "F1-E1": pass_rates(ticket_groups("F1", 1)),
            "F0-E2": pass_rates(ticket_groups("F0", 2)), "F1-E2": pass_rates(ticket_groups("F1", 2)),
        },
        "repos": {"A0": pass_rates(repo_groups("A0")), "F0-E1": pass_rates(repo_groups("F0")),
                  "F1-E1": pass_rates(repo_groups("F1"))},
    }
    f1p = s["pass"]["tickets"]["F1-E1"]["pass@1"]
    f0p = s["pass"]["tickets"]["F0-E1"]["pass@1"]
    s["flag_f1_below_f0"] = bool(f1p is not None and f0p is not None and f1p < f0p)

    # First-pass verify, per ticket attempt.
    def first_pass(arm: str) -> dict[str, Any]:
        rows = [a for a in scored if a["arm"] == arm and a["tries"]]
        ok = sum(1 for a in rows if a["tries"][0]["verdict"] == "pass")
        return {"attempts": len(rows), "first_pass": ok, "rate": safe_div(ok, len(rows))}

    s["first_pass_verify"] = {arm: first_pass(arm) for arm in ("F0", "F1")}
    s["first_pass_verify_metrics_per_issue"] = {}
    for path in sorted((results / "report").glob("metrics-F*.json")):
        s["first_pass_verify_metrics_per_issue"][path.stem[len("metrics-"):]] = read_json(path).get("first_pass_verify")

    # Costs, reported and booked.
    def costs(rows: list[dict[str, Any]], *, per: str) -> dict[str, Any]:
        booked = [a["ledger"]["booked_usd"] or 0.0 for a in rows]
        reported = [a["ledger"].get("reported_usd") or 0.0 for a in rows]
        out = {"attempts": len(rows), "booked_total": round(sum(booked), 6), "reported_total": round(sum(reported), 6),
               "booked_median": median_or_none(booked), "reported_median": median_or_none(reported)}
        if per == "factory":
            good = [a for a in rows if a["review"]["merged"] and _ticket_pass(a)]
            out["per_merged_ticket_passing"] = {"booked": safe_div(sum(booked), len(good)),
                                                "reported": safe_div(sum(reported), len(good)), "count": len(good)}
            checks = 0
            for a in rows:
                rec = score_of(results, a["hidden"].get("merged"))
                if rec and a["review"]["merged"]:
                    checks += rec["pass"]
            out["per_passing_check"] = {"booked": safe_div(sum(booked), checks), "reported": safe_div(sum(reported), checks)}
            out["median_vs_kill_line"] = {"line_usd": KILL_MEDIAN_USD, "booked_median": out["booked_median"],
                                          "within": out["booked_median"] is not None and out["booked_median"] <= KILL_MEDIAN_USD}
        else:
            passes = sum(1 for a in rows if (score_of(results, a["hidden"].get("merged")) or {}).get("repo_pass"))
            out["per_repo_pass"] = {"booked": safe_div(sum(booked), passes), "reported": safe_div(sum(reported), passes),
                                    "count": passes}
        return out

    s["cost"] = {"F0": costs([a for a in scored if a["arm"] == "F0"], per="factory"),
                 "F1": costs([a for a in scored if a["arm"] == "F1"], per="factory"),
                 "A0": costs(autopilot, per="autopilot")}

    # Tampering.
    def tampering(arm: str) -> dict[str, Any]:
        rows = autopilot if arm == "A0" else [a for a in scored if a["arm"] == arm]
        metric = broad = harness = 0
        for a in rows:
            obs_path = Path(a["_path"]).parent / "observation.json"
            if arm == "A0" and obs_path.is_file():
                metric += _metrics_tampered(read_json(obs_path), test_roots)
            elif arm != "A0":
                metric += _try_tampered(a, test_roots)
            broad += _broad_tampered(a, test_roots)
            rec = score_of(results, a["hidden"].get("merged"))
            if rec:
                labels = scoring.check_map(private.checks, a["repo"]).labels
                hits = [n for n, c in rec["checks"].items() if c["result"] == "FAIL"
                        and ((labels.get(n) or {}).get("tamper") or "tamper" in n.casefold())]
                hits += [i for i in rec.get("info", []) if "tamper" in i.get("key", "").casefold()]
                harness += bool(hits)
        n = len(rows)
        return {"attempts": n, "metrics_rule": safe_div(metric, n), "broad": safe_div(broad, n),
                "harness_check": safe_div(harness, n)}

    s["tampering"] = {arm: tampering(arm) for arm in ("A0", "F0", "F1")}

    # Escapes, false blocks, rates.
    def outcomes(arm: str) -> dict[str, Any]:
        rows = [a for a in factory if a["arm"] == arm]
        good = [a for a in rows if a["outcome"] != "infra-failed"]
        n = len(good)
        merged = [a for a in good if a["review"]["merged"]]
        escapes = [a for a in merged if _ticket_pass(a) is False]
        blocked = [a for a in good if a["publish"]["outcome"] == "dod-failed"]
        false_blocks = 0
        for a in blocked:
            for key in ("shadow_try1", "shadow_try2"):
                rec = score_of(results, a["hidden"].get(key))
                if rec is None:
                    continue
                cmap = scoring.check_map(private.checks, a["repo"])
                ticket = next(t for t in private.tickets[a["repo"]] if t.id == a["ticket"])
                if scoring.ticket_pass(rec, cmap.owned(ticket.keys()), cmap.regression()):
                    false_blocks += 1
                    break
        voids = sum(len(sess.get("voids") or []) for a in rows for sess in a["sessions"])
        infra = len(rows) - n
        return {
            "attempts": len(rows), "scored": n, "infra_failed": infra,
            "infra_flag": bool(rows) and infra / len(rows) > 0.05,
            "hidden_escape": {"count": len(escapes), "rate_of_merged": safe_div(len(escapes), len(merged))},
            "false_block": {"count": false_blocks, "rate_of_blocked": safe_div(false_blocks, len(blocked))},
            "questions_rate": safe_div(sum(1 for a in good if a["intake"]["reran"] or a["intake"]["shape"] == "questions"), n),
            "retry_rate": safe_div(sum(1 for a in good if a["retry"]["granted"]), n),
            "dod_failed_rate": safe_div(len(blocked), n),
            "needs_human_rate": safe_div(sum(1 for a in good if a["outcome"] == "needs-human"), n),
            "voids": voids,
            "flags": {k: sum(1 for a in rows if a["flags"].get(k)) for k in ("canary", "invariant", "tool_drift", "install_failed")},
        }

    s["outcomes"] = {arm: outcomes(arm) for arm in ("F0", "F1")}

    # Per-check pass rate (main at the end of E1, and A0's result).
    per_check: dict[str, dict[str, dict[str, Any]]] = {}
    for arm in ("A0", "F0", "F1"):
        vecs = a0 if arm == "A0" else (f0 if arm == "F0" else f1)
        for repo, trials in vecs.items():
            den = scoring.check_map(private.checks, repo).denominator
            for i, name in enumerate(den):
                per_check.setdefault(repo, {}).setdefault(name, {})[arm] = safe_div(sum(t[i] for t in trials), len(trials))
    s["per_check"] = per_check

    # Informational and pre-registered limits.
    cited: dict[str, Any] = {}
    catches: dict[str, Any] = {}
    for path in sorted((results / "report").glob("metrics-F*.json")):
        m = read_json(path)
        cited[path.stem[len("metrics-"):]] = m.get("lessons_cited")
        catches[path.stem[len("metrics-"):]] = (m.get("learned_check_catches") or {}).get("count")
    s["lessons_cited"] = cited
    s["learned_check_catches"] = {"label": "0 by construction", "observed": catches}
    s["merge_rate_30d"] = "not measured"
    s["limits"] = [
        "learned_check_catches is 0 by construction: no import-edge seeds and no reviewer with domain knowledge",
        "the 30-day merge rate is not measured",
        "where the correct fix touches guarded paths, the gate restores them (real factory behaviour; see the shadow scores)",
        "few ticket clusters: a real repeat-rate gain below about 0.15 to 0.2 will likely read as no gain",
    ]
    return s


def _try_tampered(a: dict[str, Any], test_roots: Sequence[str]) -> bool:
    """The metrics rule from the attempt's own observation (modify/delete under a test root)."""
    for n in (1, 2):
        path = Path(a["_path"]).parent / f"try{n}" / "observation.json"
        if path.is_file() and _metrics_tampered(read_json(path), test_roots):
            return True
    return False


def _fmt(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def _cmp(a: float | None, b: float | None, *, strict: bool) -> bool | None:
    """a < b (strict) or a <= b; None when either is unknown (0.0 is a value)."""
    if a is None or b is None:
        return None
    return a < b if strict else a <= b


def render_md(s: dict[str, Any]) -> str:
    q1, q2 = s["q1"], s["q2"].get("e1e2") or {}
    rr = (q2.get("diff") or {}).get("rr") or {}
    arms = q2.get("arms") or {}
    er_on, er_frozen = (arms.get("on") or {}).get("er"), (arms.get("frozen") or {}).get("er")
    t_on, t_frozen = s["tampering"]["F1"]["metrics_rule"], s["tampering"]["F0"]["metrics_rule"]
    lines = [
        "# PRIVATE: Cadence factory eval report",
        "",
        "Results from private repositories and hidden tests. Do not publish, copy or quote outside the owner's private notes.",
        "",
        f"Run `{s['run_id']}`.",
        "",
        "## Kill criteria",
        "",
        "| Criterion | Value | Rule | Met |",
        "|---|---|---|---|",
        f"| Q1 factory beats autopilot (F1-E1 minus A0, check fraction) | {_fmt(q1.get('point'))} (90% CI {_fmt((q1.get('ci90') or [None, None])[0])} to {_fmt((q1.get('ci90') or [None, None])[1])}) | CI lower > 0 and worse on no repo | {_fmt(q1.get('pass'))} |",
        f"| Q2 learning loop (RR_frozen - RR_on, E1+E2) | {_fmt(rr.get('point'))} (90% CI {_fmt((rr.get('ci90') or [None, None])[0])} to {_fmt((rr.get('ci90') or [None, None])[1])}) | metrics.py compare's rule | {_fmt(q2.get('pass'))} |",
        f"| Escape rate on vs frozen | {_fmt(er_on)} vs {_fmt(er_frozen)} | ER_on < ER_frozen | {_fmt(_cmp(er_on, er_frozen, strict=True))} |",
        f"| Median cost per ticket attempt (F1, booked) | {_fmt(s['cost']['F1']['booked_median'])} | <= $20 | {_fmt(s['cost']['F1'].get('median_vs_kill_line', {}).get('within'))} |",
        f"| Test tampering (metrics rule) F1 vs F0 | {_fmt(t_on)} vs {_fmt(t_frozen)} | on not higher | {_fmt(_cmp(t_on, t_frozen, strict=False))} |",
        f"| Learned check caught a real repeat | {s['learned_check_catches']['label']} | from the product repo | n/a |",
        f"| Agent PRs merged within 30 days | {s['merge_rate_30d']} | >= 50% | n/a |",
        "",
        "## Pass rates (hidden tests)",
        "",
        "| Arm | Tickets pass@1 | Tickets pass^k | Repos pass@1 | Repos pass^k |",
        "|---|---|---|---|---|",
    ]
    for arm in ("A0", "F0-E1", "F1-E1", "F0-E2", "F1-E2"):
        t = s["pass"]["tickets"].get(arm, {})
        r = s["pass"]["repos"].get(arm, {})
        lines.append(f"| {arm} | {_fmt(t.get('pass@1'))} | {_fmt(t.get('pass^k'))} (k={_fmt(t.get('k'))}) | {_fmt(r.get('pass@1'))} | {_fmt(r.get('pass^k'))} |")
    if s.get("flag_f1_below_f0"):
        lines += ["", "**Flag:** F1's hidden ticket pass@1 is below F0's (not part of the kill rule)."]
    lines += ["", "## Gate and outcomes", "", "| Metric | F0 | F1 |", "|---|---|---|"]
    fp = s["first_pass_verify"]
    lines.append(f"| First-pass verify (per ticket attempt) | {_fmt(fp['F0']['rate'])} | {_fmt(fp['F1']['rate'])} |")
    for key, label in (("questions_rate", "Questions"), ("retry_rate", "Retries"), ("dod_failed_rate", "dod-failed"),
                       ("needs_human_rate", "needs-human"), ("voids", "Voids"), ("infra_failed", "Infra-failed attempts")):
        lines.append(f"| {label} | {_fmt(s['outcomes']['F0'][key])} | {_fmt(s['outcomes']['F1'][key])} |")
    for key, label in (("hidden_escape", "Hidden escapes (merged, owned or regression check fails)"),
                       ("false_block", "False blocks (gate failed, shadow ticket pass)")):
        lines.append(f"| {label} | {_fmt(s['outcomes']['F0'][key]['count'])} | {_fmt(s['outcomes']['F1'][key]['count'])} |")
    lines += ["", "## Cost (USD)", "", "| Arm | Booked total | Reported total | Booked median | Per result |", "|---|---|---|---|---|"]
    for arm in ("A0", "F0", "F1"):
        c = s["cost"][arm]
        per = c.get("per_merged_ticket_passing") or c.get("per_repo_pass") or {}
        lines.append(f"| {arm} | {_fmt(c['booked_total'])} | {_fmt(c['reported_total'])} | {_fmt(c['booked_median'])} | {_fmt(per.get('booked'))} booked, {_fmt(per.get('reported'))} reported |")
    lines += ["", "## Tampering", "", "| Arm | Metrics rule | Broad | Harness check |", "|---|---|---|---|"]
    for arm in ("A0", "F0", "F1"):
        t = s["tampering"][arm]
        lines.append(f"| {arm} | {_fmt(t['metrics_rule'])} | {_fmt(t['broad'])} | {_fmt(t['harness_check'])} |")
    lines += ["", "## Pre-registered limits", ""] + [f"- {x}" for x in s["limits"]]
    lines += ["", "Informational: lessons cited by approved specs are in summary.json (`lessons_cited`).", ""]
    return "\n".join(lines)


def write_report(cfg: Config, private: Private, run_id: str, *, log: Callable[[str], None] = print) -> dict[str, Any]:
    run_metrics(cfg, run_id, log=log)
    s = summarize(cfg, private, run_id)
    out = cfg.results(run_id) / "report"
    write_json(out / "summary.json", s)
    write_text(out / "report.md", render_md(s))
    return s


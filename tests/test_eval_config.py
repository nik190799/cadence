"""The eval's configuration: schemas, path rules, private inputs.

- eval.yaml validates against eval-config.schema.json (unknown keys and
  malformed pins are refused);
- the runner reads only under read_roots, never a path with a forbidden
  part, never a never_bind file;
- the defaults name nothing specific (no repo, person or path);
- tickets load in TASK order (issue number = position), and the check map
  only labels checks of its denominator.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "eval" / "harness"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "eval_synthetic"
for p in (str(HARNESS), str(FIXTURE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import config  # noqa: E402
import world  # noqa: E402
from config import EvalError, PathPolicy  # noqa: E402


@pytest.fixture()
def w(tmp_path: Path) -> dict:
    return world.make_world(tmp_path)


def _rewrite(w: dict, edit) -> Path:
    raw = yaml.safe_load(w["config"].read_text(encoding="utf-8"))
    edit(raw)
    w["config"].write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return w["config"]


def test_the_synthetic_config_loads(w: dict) -> None:
    cfg = config.load_config(w["config"])
    assert cfg.sandbox == "none" and cfg.repos[0].id == "demo"
    assert cfg.repos[0].overlay_dir == (w["private"] / "overlay" / "demo").resolve()
    assert cfg.factory["mode"] == "eval-sandbox"
    assert cfg.cache_root == cfg.home / "cache" / w["cadence_sha"]
    assert cfg.policy.write_roots == [cfg.home, cfg.results_dir]


@pytest.mark.parametrize(
    "edit, match",
    [
        (lambda raw: raw.update({"surprise": 1}), "surprise"),
        (lambda raw: raw["cadence"].update({"sha": "abc"}), "sha"),
        (lambda raw: raw["factory"].update({"mode": "on"}), "mode"),
        (lambda raw: raw["repos"][0].update({"slug": "acme/demo"}), "slug"),
        (lambda raw: raw.update({"sandbox": "docker"}), "sandbox"),
        (lambda raw: raw.pop("forbidden_path_parts"), "forbidden_path_parts"),
    ],
)
def test_the_schema_refuses_bad_configs(w: dict, edit, match: str) -> None:
    path = _rewrite(w, edit)
    with pytest.raises(EvalError, match=match):
        config.load_config(path)


def test_hidden_command_needs_every_placeholder(w: dict) -> None:
    path = _rewrite(w, lambda raw: raw.update({"hidden_command": ["python3", "{harness}", "{repo}"]}))
    with pytest.raises(EvalError, match=r"\{ref\}"):
        config.load_config(path)


def test_a_file_outside_read_roots_is_refused(w: dict, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere" / "replies.yaml"
    outside.parent.mkdir()
    outside.write_text("schema: cadence-eval.replies/1\nquestions_reply: x\n", encoding="utf-8")
    path = _rewrite(w, lambda raw: raw["files"].update({"replies": str(outside)}))
    with pytest.raises(EvalError, match="read_roots"):
        config.load_config(path)


def test_a_forbidden_part_is_refused_anywhere(w: dict) -> None:
    sub = w["private"] / "Forbidden-Part"
    sub.mkdir()
    (sub / "stubs.yaml").write_bytes((w["private"] / "stubs.yaml").read_bytes())
    path = _rewrite(w, lambda raw: raw["files"].update({"stubs": "Forbidden-Part/stubs.yaml"}))
    with pytest.raises(EvalError, match="forbidden"):
        config.load_config(path)


def test_the_path_policy(tmp_path: Path) -> None:
    root = tmp_path / "roots" / "a"
    (root / "secret-dir").mkdir(parents=True)
    (root / "ok.txt").write_text("x", encoding="utf-8")
    (root / "KEY.md").write_text("x", encoding="utf-8")
    (root / "secret-dir" / "hidden.txt").write_text("x", encoding="utf-8")
    policy = PathPolicy([root], ["Secret-Dir"], ["key.md"], [tmp_path / "home"])
    assert policy.check_read(root / "ok.txt") == (root / "ok.txt").resolve()
    for bad in (root / "secret-dir" / "hidden.txt", root / "KEY.md", tmp_path / "roots" / "b.txt",
                root / ".." / "b.txt"):
        with pytest.raises(EvalError):
            policy.check_read(bad)
    assert [p.name for p in policy.walk(root)] == ["ok.txt"]
    with pytest.raises(EvalError):
        policy.check_write(tmp_path / "other")
    assert policy.check_write(tmp_path / "home" / "x") == (tmp_path / "home" / "x").resolve()


def test_the_defaults_name_nothing_specific() -> None:
    assert set(config.DEFAULTS) == {"home", "model", "sandbox", "score_network", "allow_no_subprocess_scrub"}
    assert config.DEFAULTS["home"] == "~/.cadence-eval" and config.DEFAULTS["model"] == ""
    schema = config.load_schema("eval-config")
    for key in ("forbidden_path_parts", "never_bind", "read_roots", "repos", "files"):
        assert key in schema["required"], key  # private by construction: no default
    text = json.dumps(config.DEFAULTS) + json.dumps(schema)
    for marker in ("Users", "/home/", "/mnt/", "@"):
        assert marker not in text


def test_tickets_load_in_task_order(w: dict) -> None:
    cfg = config.load_config(w["config"])
    private = config.load_private(cfg)
    tickets = private.tickets["demo"]
    assert [t.id for t in tickets] == ["demo.T1", "demo.T2", "demo.T3"]
    assert [t.issue for t in tickets] == [1, 2, 3]
    assert tickets[0].keys() == ("demo-1", "demo.T1")
    assert not tickets[0].body.startswith("---") and "DECISIONS.md" in tickets[0].body
    assert private.ticket_value({"demo.T2": 1}, tickets[1]) == 1
    assert private.ticket_value({"demo-2": 2}, tickets[1]) == 2
    assert private.canaries == ["SYNTH-HIDDEN-CANARY-41f7"]
    hashes = config.private_hashes(cfg, private)
    assert set(hashes) == {"config", "tickets", "replies", "reviewer", "stubs", "checks", "detectors", "expectations"}


def test_a_ticket_out_of_order_is_refused(w: dict) -> None:
    path = w["private"] / "tickets" / "demo-2.md"
    path.write_text(path.read_text(encoding="utf-8").replace("issue: 2", "issue: 3"), encoding="utf-8")
    cfg = config.load_config(w["config"])
    with pytest.raises(EvalError, match="position"):
        config.load_private(cfg)


def test_a_label_outside_the_denominator_is_refused(w: dict) -> None:
    path = w["private"] / "checks.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "      ghost: {kind: noticing}\n", encoding="utf-8")
    cfg = config.load_config(w["config"])
    with pytest.raises(EvalError, match="ghost"):
        config.load_private(cfg)


def test_the_private_schemas_accept_the_fixture(w: dict) -> None:
    for name, schema in (("stubs.yaml", "stubs"), ("checks.yaml", "checks"), ("expectations.yaml", "expectations"),
                         ("replies.yaml", "replies"), ("reviewer.yaml", "reviewer")):
        data = yaml.safe_load((w["private"] / name).read_text(encoding="utf-8"))
        assert config.schema_errors(schema, data) == [], name
    bad = {"schema": "cadence-eval.stubs/1", "cost_usd": 0, "intake": {"default": "spec"},
           "build": {"default": {"try1": {"calib": "x", "file": "y.patch"}}}, "autopilot": {}}
    assert config.schema_errors("stubs", bad)

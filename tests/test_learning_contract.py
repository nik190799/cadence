"""The learning loop's shared contract (docs/LEARNING.md), checked across modules.

Three builders wrote signals.py, ladder.py and emit_rule.py (plus
check_boundaries.py) in parallel against one written contract. These tests
hold them to it where they meet:

- the shared constants are identical in every module that defines them,
  and equal to the contract's literal values;
- ``ladder.lesson_id(k)`` is ``"L-" + emit_rule``'s short id for the
  lesson's finding id, which is also the fixture directory name;
- check_boundaries computes the documented seed rule id;
- every learning schema loads, is a valid Draft 2020-12 schema, accepts a
  golden example (tests/fixtures/learning_e2e/golden/) and rejects the
  shapes the contract forbids.

A module or schema that is not built yet is skipped, never failed, so this
file can land before the modules it checks.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL_DIR = REPO_ROOT / "plugins" / "cadence" / "templates" / "tool"
SCHEMA_DIR = REPO_ROOT / "plugins" / "cadence" / "schemas"
GOLDEN = Path(__file__).resolve().parent / "fixtures" / "learning_e2e" / "golden"

# --- The contract's literal values (section 1) --------------------------------

NS_CADENCE = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/nik190799/cadence#factory")
CLASS_KEY_RE = (
    r"^(import-edge|guarded|missing-test|test|edit|review|gate|agent|pr):"
    r"[A-Za-z0-9_./@+:>-]{1,180}$"
)
HEADLINE_FAMILIES = ("import-edge", "guarded", "missing-test", "test")
POST_PR_FAMILIES = ("edit", "review")
OPS_FAMILIES = ("gate", "agent", "pr")
RULE_ID_RE = r"^[LB]-[0-9a-f]{8}$"
LESSON_ID_RE = r"^L-[0-9a-f]{8}$"
AREA_SEG_RE = r"^[A-Za-z0-9_@+-][A-Za-z0-9_.@+-]{0,63}$"
PKG_RE = r"^(@[A-Za-z0-9][A-Za-z0-9._-]{0,63}/)?[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
LANG_FAMILY = {
    ".ts": "ts", ".tsx": "ts", ".js": "ts", ".jsx": "ts", ".mjs": "ts", ".cjs": "ts",
    ".py": "py", ".dart": "dart",
}
SAMPLE_LANGUAGE = {
    ".ts": "ts", ".tsx": "tsx", ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js",
    ".py": "py", ".dart": "dart",
}
IMPORT_LINE_PATTERNS = {
    "ts": (
        r"""^\s*import\s+(?:type\s+)?(?:[\w$]+\s*,\s*)?(?:[\w$]+|\*\s+as\s+[\w$]+|\{[\w$\s,]*\})\s+from\s+(['"])[^'"\\\s]{1,150}\1\s*;?\s*$""",
        r"""^\s*import\s+(['"])[^'"\\\s]{1,150}\1\s*;?\s*$""",
        r"""^\s*export\s+(?:type\s+)?(?:\*(?:\s+as\s+[\w$]+)?|\{[\w$\s,]*\})\s+from\s+(['"])[^'"\\\s]{1,150}\1\s*;?\s*$""",
        r"""^\s*(?:const|let|var)\s+(?:[\w$]+|\{[\w$\s,:]*\})\s*=\s*require\(\s*(['"])[^'"\\\s]{1,150}\1\s*\)\s*;?\s*$""",
    ),
    "py": (
        r"""^\s*from\s+(?:\.{1,5}|\.{0,5}[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,15})\s+import\s+(?:\*|\(?\s*[A-Za-z_][A-Za-z0-9_]*(?:\s+as\s+[A-Za-z_][A-Za-z0-9_]*)?(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*(?:\s+as\s+[A-Za-z_][A-Za-z0-9_]*)?){0,30}\s*,?\s*\)?)\s*$""",
        r"""^\s*import\s+[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,15}(?:\s+as\s+[A-Za-z_][A-Za-z0-9_]*)?(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,15}(?:\s+as\s+[A-Za-z_][A-Za-z0-9_]*)?){0,15}\s*$""",
    ),
    "dart": (
        r"""^\s*(?:import|export)\s+'[^'\\\s]{1,150}'(?:\s+deferred)?(?:\s+as\s+[A-Za-z_][A-Za-z0-9_]*)?(?:\s+(?:show|hide)\s+[A-Za-z_][A-Za-z0-9_]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*){0,30})*\s*;\s*$""",
    ),
}
STATE_PATH_RE = (
    r"^(?:(?:runs|observations|findings|patches|prs|harvest|decisions|learn|reports)/"
    r"[A-Za-z0-9._-]{1,140}\.(?:json|jsonl|patch)|retro/plans/[0-9a-f]{64}\.json)$"
)
RETRO_FIXTURE_PREFIX = "tests/fixtures/retro/"
RETRO_ALLOWLIST = (".cadence/cadence.yaml", ".cadence/lessons.yaml", "docs/PATTERNS.md")
LEARNED_SECTION_HEADING = "## Learned patterns (factory)"

# Which modules must define which constant (the contract's "Defined in").
DEFINED_IN: dict[str, tuple[str, ...]] = {
    "NS_CADENCE": ("signals", "ladder"),
    "CLASS_KEY_RE": ("signals", "ladder"),
    "HEADLINE_FAMILIES": ("signals", "ladder"),
    "POST_PR_FAMILIES": ("signals", "ladder"),
    "OPS_FAMILIES": ("signals", "ladder"),
    "RULE_ID_RE": ("signals", "ladder", "emit_rule", "check_boundaries"),
    "LESSON_ID_RE": ("signals", "ladder", "emit_rule"),
    "LANG_FAMILY": ("signals", "emit_rule"),
    "SAMPLE_LANGUAGE": ("ladder", "emit_rule"),
    "IMPORT_LINE_PATTERNS": ("signals", "emit_rule"),
    "STATE_PATH_RE": ("signals",),
    "RETRO_FIXTURE_PREFIX": ("check_boundaries", "emit_rule", "ladder"),
    "RETRO_ALLOWLIST": ("ladder",),
    "LEARNED_SECTION_HEADING": ("ladder",),
}
EXPECTED: dict[str, Any] = {
    "NS_CADENCE": NS_CADENCE,
    "CLASS_KEY_RE": CLASS_KEY_RE,
    "HEADLINE_FAMILIES": HEADLINE_FAMILIES,
    "POST_PR_FAMILIES": POST_PR_FAMILIES,
    "OPS_FAMILIES": OPS_FAMILIES,
    "RULE_ID_RE": RULE_ID_RE,
    "LESSON_ID_RE": LESSON_ID_RE,
    "LANG_FAMILY": LANG_FAMILY,
    "SAMPLE_LANGUAGE": SAMPLE_LANGUAGE,
    "IMPORT_LINE_PATTERNS": IMPORT_LINE_PATTERNS,
    "STATE_PATH_RE": STATE_PATH_RE,
    "RETRO_FIXTURE_PREFIX": RETRO_FIXTURE_PREFIX,
    "RETRO_ALLOWLIST": RETRO_ALLOWLIST,
    "LEARNED_SECTION_HEADING": LEARNED_SECTION_HEADING,
}
# Defined by no single module in the contract, but identical wherever present.
OPTIONAL_SHARED = {"AREA_SEG_RE": AREA_SEG_RE, "PKG_RE": PKG_RE}

MODULE_NAMES = ("signals", "ladder", "emit_rule", "check_boundaries", "metrics")


def _load(name: str) -> ModuleType | None:
    path = TOOL_DIR / f"{name}.py"
    if not path.is_file():
        return None
    # The tools import their siblings by plain name.
    if str(TOOL_DIR) not in sys.path:
        sys.path.insert(0, str(TOOL_DIR))
    mod_name = f"cadence_contract_{name}"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def _module(name: str) -> ModuleType:
    module = _load(name)
    if module is None:
        pytest.skip(f"tool/{name}.py is not built yet")
    return module


def _plain(value: Any) -> Any:
    """Compiled patterns compare by their source text; lists like tuples."""
    if isinstance(value, re.Pattern):
        return value.pattern
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return tuple(_plain(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(_plain(v) for v in value))
    return value


# --- Constants ----------------------------------------------------------------


@pytest.mark.parametrize(
    "constant, module_name",
    [(c, m) for c, mods in DEFINED_IN.items() for m in mods],
)
def test_constant_matches_the_contract(constant: str, module_name: str) -> None:
    module = _module(module_name)
    assert hasattr(module, constant), f"tool/{module_name}.py must define {constant}"
    assert _plain(getattr(module, constant)) == _plain(EXPECTED[constant])


@pytest.mark.parametrize("constant", sorted(OPTIONAL_SHARED))
def test_optional_shared_constants_agree_where_defined(constant: str) -> None:
    found = {}
    for name in MODULE_NAMES:
        module = _load(name)
        if module is not None and hasattr(module, constant):
            found[name] = _plain(getattr(module, constant))
    if not found:
        pytest.skip(f"no module defines {constant} yet")
    assert set(found.values()) == {OPTIONAL_SHARED[constant]}, found


def test_import_line_patterns_accept_and_refuse_the_same_lines() -> None:
    signals = _module("signals")
    emit_rule = _module("emit_rule")
    accept = {
        "ts": [
            'import { query } from "../db/client";',
            "import type { Order } from '../domain/order'",
            "export * from './x';",
            "const db = require('../db');",
        ],
        "py": ["from app.db import session", "import app.db.session as s", "from .. import x"],
        "dart": ["import 'package:app/db/client.dart';", "export 'src/x.dart' show A, B;"],
    }
    # Multi-line imports are refused by the separate printable-ASCII check,
    # not by these patterns (\s spans newlines), so they are not listed here.
    refuse = {
        "ts": ['import x from "a"; evil()', "import(`x`)", 'import x from "a b"', "import x from y"],
        "py": ["from app.db import (", "import os; os.system('x')", "from app.db import *, x"],
        "dart": ["import 'a.dart'", "import \"a.dart\";", "import 'a b.dart';"],
    }
    for module in (signals, emit_rule):
        compiled = {
            fam: [re.compile(_plain(p), re.ASCII) for p in pats]
            for fam, pats in module.IMPORT_LINE_PATTERNS.items()
        }
        for fam, lines in accept.items():
            for line in lines:
                assert any(p.fullmatch(line) for p in compiled[fam]), (module.__name__, line)
        for fam, lines in refuse.items():
            for line in lines:
                assert not any(p.fullmatch(line) for p in compiled[fam]), (module.__name__, line)


# --- Ids ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "class_key",
    ["import-edge:src/domain->src/db", "missing-test:src/api", "guarded:tests:modify", "test:t/a.test.ts"],
)
def test_lesson_id_is_emit_rules_short_id_and_the_fixture_name(class_key: str) -> None:
    ladder = _module("ladder")
    emit_rule = _module("emit_rule")
    finding_id = str(uuid.uuid5(NS_CADENCE, "lesson|" + class_key))
    expected = "L-" + uuid.uuid5(NS_CADENCE, "lesson|" + class_key).hex[:8]
    assert ladder.lesson_id(class_key) == expected
    # observe checks the lessons a spec cites against the same id rule.
    assert _module("signals").lesson_id(class_key) == expected
    assert re.fullmatch(LESSON_ID_RE, expected)
    short = emit_rule._short_id({"id": finding_id})
    assert short == expected[2:]
    assert re.fullmatch(r"[0-9a-f]{8}", short)  # the fixture directory name


def test_check_boundaries_computes_the_seed_rule_id(tmp_path: Path) -> None:
    checker = _module("check_boundaries")
    config = tmp_path / "cadence.yaml"
    config.write_text(
        "commands: {test: ['true']}\n"
        "boundaries:\n"
        "  - where: src/domain/**\n"
        "    forbidden: [src/http/**, src/db/**]\n"
        "    reason: seed\n"
        "  - id: L-1a2b3c4d\n"
        "    where: src/ui/**\n"
        "    forbidden: [src/db/**]\n"
        "    reason: learned\n",
        encoding="utf-8",
    )
    rules = checker._load_rules(config)
    seed = "B-" + hashlib.sha256(b"src/domain/**|src/http/**|src/db/**").hexdigest()[:8]
    assert [rule.id for rule in rules] == [seed, "L-1a2b3c4d"]


def test_edge_keys_parse_at_the_first_arrow() -> None:
    signals = _module("signals")
    key = "import-edge:src/domain->pkg:@scope/name"
    assert re.fullmatch(CLASS_KEY_RE, key)
    if hasattr(signals, "parse_edge_key"):
        assert tuple(signals.parse_edge_key(key)) == ("src/domain", "pkg:@scope/name")
    if hasattr(signals, "edge_key"):
        assert signals.edge_key("src/domain", "src/db") == "import-edge:src/domain->src/db"


@pytest.mark.parametrize(
    "path, depth, expected",
    [
        ("src/domain/order.ts", 2, "src/domain"),
        ("src/domain/deep/order.ts", 2, "src/domain"),
        ("src/order.ts", 2, "src"),
        ("order.ts", 2, "."),
        ("src/domain/order.ts", 1, "src"),
        ("src/do main/order.ts", 2, None),
        ("src/.hidden/x.ts", 2, None),
    ],
)
def test_area(path: str, depth: int, expected: str | None) -> None:
    signals = _module("signals")
    assert signals.area(path, depth) == expected


# --- Schemas ------------------------------------------------------------------


def _schema(name: str) -> dict[str, Any]:
    path = SCHEMA_DIR / name
    if not path.is_file():
        pytest.skip(f"schemas/{name} is not written yet")
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


def _errors(schema: dict[str, Any], instance: Any) -> list[str]:
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    return [f"{list(e.absolute_path)}: {e.message}" for e in validator.iter_errors(instance)]


def _golden(name: str) -> Any:
    text = (GOLDEN / name).read_text(encoding="utf-8")
    return yaml.safe_load(text) if name.endswith(".yaml") else json.loads(text)


GOLDEN_CASES = [
    ("retro.schema.json", "retro-factory-finding.json"),
    ("retro.schema.json", "retro-manual-finding.json"),
    ("observation.schema.json", "observation.json"),
    ("classify.schema.json", "classify.json"),
    ("lessons.schema.json", "lessons.yaml"),
    ("retro-plan.schema.json", "retro-plan.json"),
    ("metrics.schema.json", "metrics.json"),
    ("cadence-yaml.schema.json", "cadence.yaml"),
]


@pytest.mark.parametrize("schema_name, golden_name", GOLDEN_CASES)
def test_schema_accepts_its_golden_example(schema_name: str, golden_name: str) -> None:
    schema = _schema(schema_name)
    assert _errors(schema, _golden(golden_name)) == []


def _broken(golden_name: str, path: list[Any], value: Any = None, delete: bool = False) -> Any:
    doc = copy.deepcopy(_golden(golden_name))
    target = doc
    for key in path[:-1]:
        target = target[key]
    if delete:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return doc


REJECT_CASES = [
    # A factory finding's judge is the shadow-judge hook: null in v1.
    ("retro.schema.json", "retro-factory-finding.json", ["factory", "judge"], {"label": "same"}, False),
    ("retro.schema.json", "retro-factory-finding.json", ["factory", "comment_text"], "hi", False),
    ("retro.schema.json", "retro-factory-finding.json", ["factory", "class_key"], "bogus:x", False),
    ("retro.schema.json", "retro-factory-finding.json", ["factory", "classification"], None, True),
    ("retro.schema.json", "retro-factory-finding.json", ["violation_sample", "where"], "/etc/**", False),
    ("retro.schema.json", "retro-factory-finding.json", ["violation_sample", "import_line"], "a\nb", False),
    ("observation.schema.json", "observation.json", ["evidence", "verdict"], [], False),
    ("observation.schema.json", "observation.json", ["classes"], ["import-edge:src/a->src/b"], False),
    ("observation.schema.json", "observation.json", ["area_depth"], 5, False),
    ("observation.schema.json", "observation.json", ["gate_step"], "lint-ish", False),
    # lessons_cited holds lesson ids only (or null), each once.
    ("observation.schema.json", "observation.json", ["lessons_cited"], ["B-1a2b3c4d"], False),
    ("observation.schema.json", "observation.json", ["lessons_cited"], ["L-1a2b3c4d", "L-1a2b3c4d"], False),
    ("observation.schema.json", "observation.json", ["lessons_cited"], "L-1a2b3c4d", False),
    ("observation.schema.json", "observation.json", ["lessons_cited"], ["L-1a2b3c4d is binding"], False),
    ("classify.schema.json", "classify.json", ["items", 0, "reason"], "free text", False),
    ("classify.schema.json", "classify.json", ["items", 0, "category"], "praise", False),
    ("classify.schema.json", "classify.json", ["items", 0, "confidence"], 1.5, False),
    ("lessons.schema.json", "lessons.yaml", ["lessons", 0, "check"], None, True),
    ("lessons.schema.json", "lessons.yaml", ["lessons", 1, "text"], None, True),
    ("lessons.schema.json", "lessons.yaml", ["lessons", 2, "retired"], None, True),
    ("lessons.schema.json", "lessons.yaml", ["lessons", 0, "class_key"], "review:nit:src", False),
    ("lessons.schema.json", "lessons.yaml", ["lessons", 0, "id"], "B-1a2b3c4d", False),
    # An unquoted `on:` key loads as True and must not slip through.
    ("lessons.schema.json", "lessons.yaml", ["lessons", 0, "history", 0], {"rung": "check", True: "2026-10-01"}, False),
    ("retro-plan.schema.json", "retro-plan.json", ["transitions", 0, "sample"], None, False),
    ("retro-plan.schema.json", "retro-plan.json", ["transitions", 0, "to"], "note", False),
    ("retro-plan.schema.json", "retro-plan.json", ["needs_human", 0, "why"], "because", False),
    ("metrics.schema.json", "metrics.json", ["repeat", "status"], "great", False),
    ("metrics.schema.json", "metrics.json", ["repeat", "rate"], 1.5, False),
    ("metrics.schema.json", "metrics.json", ["by_family", "review"], {}, False),
    ("metrics.schema.json", "metrics.json", ["lessons_cited", "cited_and_absent", "by_lesson"], {"B-1a2b3c4d": 1}, False),
    ("metrics.schema.json", "metrics.json", ["lessons_cited", "attempts_unknown"], None, True),
    ("metrics.schema.json", "metrics.json", ["lessons_cited", "prevented"], 1, False),
    ("cadence-yaml.schema.json", "cadence.yaml", ["boundaries", 1, "id"], "L-XYZ", False),
]


@pytest.mark.parametrize("schema_name, golden_name, path, value, delete", REJECT_CASES)
def test_schema_rejects_what_the_contract_forbids(
    schema_name: str, golden_name: str, path: list[Any], value: Any, delete: bool
) -> None:
    schema = _schema(schema_name)
    assert _errors(schema, _broken(golden_name, path, value, delete)), (golden_name, path)


@pytest.mark.parametrize(
    "schema_name, golden_name, field, values",
    [
        # Observations booked before lessons_cited existed stay valid, and
        # null (unknown) and [] (none cited) are both allowed.
        ("observation.schema.json", "observation.json", "lessons_cited", [None, []]),
        # So do metrics reports from before the informational block.
        ("metrics.schema.json", "metrics.json", "lessons_cited", []),
    ],
)
def test_lessons_cited_is_optional_and_backward_compatible(
    schema_name: str, golden_name: str, field: str, values: list[Any]
) -> None:
    schema = _schema(schema_name)
    assert _errors(schema, _broken(golden_name, [field], delete=True)) == []
    for value in values:
        assert _errors(schema, _broken(golden_name, [field], value)) == []


@pytest.mark.parametrize(
    "text, ids",
    [
        ("Applies: `.cadence/lessons.yaml` L-1a2b3c4d (check): ...", ["L-1a2b3c4d"]),
        ("(L-1a2b3c4d), L-5e6f7a8b.", ["L-1a2b3c4d", "L-5e6f7a8b"]),
        ("L-1a2b3c4d5 XL-1a2b3c4d L-1a2b3c4d_x L-1a2b3c4d-y L-1A2B3C4D l-1a2b3c4d", []),
        ("-L-1a2b3c4d _L-1a2b3c4d 9L-1a2b3c4d", []),
    ],
)
def test_lesson_token_matches_whole_ids_only(text: str, ids: list[str]) -> None:
    signals = _module("signals")
    assert re.findall(signals.LESSON_TOKEN_RE, text) == ids
    for found in ids:
        assert re.fullmatch(LESSON_ID_RE, found)


def test_golden_findings_and_plan_follow_the_id_and_time_rules() -> None:
    """Section 0: ids parse as UUIDs, timestamps match the ts pattern."""
    ts = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
    for name in ("retro-factory-finding.json", "retro-manual-finding.json"):
        doc = _golden(name)
        uuid.UUID(doc["id"])
        assert ts.fullmatch(doc["ts"])
    assert ts.fullmatch(_golden("observation.json")["completed_at"])
    assert ts.fullmatch(_golden("retro-plan.json")["generated_at"])
    assert ts.fullmatch(_golden("metrics.json")["generated_at"])

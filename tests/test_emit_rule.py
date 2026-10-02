"""End-to-end test for the retrospective rule emitter.

This is the literal proof of the "self-improving framework" claim: a
Layer 3 retro finding flows through tool/emit_rule.py and lands as a
new fixture + boundary rule that flags the original offending import.
If the round-trip succeeds, the loop is closed at the code level — not
just in documentation.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
EMITTER_PATH = (
    REPO_ROOT / "plugins" / "cadence" / "templates" / "tool" / "emit_rule.py"
)
CHECKER_PATH = (
    REPO_ROOT / "plugins" / "cadence" / "templates" / "tool" / "check_boundaries.py"
)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, name
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


emitter = _load_module("emit_rule", EMITTER_PATH)
checker = _load_module("check_boundaries", CHECKER_PATH)


def _project_skeleton(tmp_path: Path) -> Path:
    """Build a minimal project skeleton the emitter expects to find.

    Creates:
        <root>/tool/check_boundaries.py   — copy of the real checker
        <root>/.cadence/cadence.yaml      — empty boundaries list
        <root>/.cadence/retro.schema.json — copy of the real schema

    Returns the project root.
    """
    root = tmp_path / "proj"
    root.mkdir()

    tool_dir = root / "tool"
    tool_dir.mkdir()
    (tool_dir / "check_boundaries.py").write_text(
        CHECKER_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )

    cadence_dir = root / ".cadence"
    cadence_dir.mkdir()
    (cadence_dir / "cadence.yaml").write_text(
        "commands:\n  test: ['true']\nboundaries: []\n", encoding="utf-8"
    )

    schema_src = REPO_ROOT / "plugins" / "cadence" / "schemas" / "retro.schema.json"
    (cadence_dir / "retro.schema.json").write_text(
        schema_src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return root


def _finding(
    *,
    language: str = "ts",
    where: str = "src/features/**",
    import_line: str = "import { x } from '../../data/sources/badStore';",
    forbidden_pattern: str = "src/data/sources/**",
    reason: str = "features must read data via narrow providers, "
    "not data sources directly",
    fix_layer: int = 3,
    auto_method: str = "boundary-rule",
) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "ts": "2026-05-27T12:00:00Z",
        "what_happened": "feature read sources/ directly",
        "auto_catchable": True,
        "auto_method": auto_method,
        "rule_existed": False,
        "proposed_fix": (
            "add boundary rule blocking src/features/** from importing "
            "src/data/sources/**"
        ),
        "fix_layer": fix_layer,
        "decision": "approved",
        "violation_sample": {
            "kind": "boundary-rule",
            "language": language,
            "where": where,
            "import_line": import_line,
            "forbidden_pattern": forbidden_pattern,
            "reason": reason,
        },
    }


# --- The headline test: the loop closes -----------------------------------


def test_emit_creates_fixture_and_rule_fires_on_sample(tmp_path):
    project = _project_skeleton(tmp_path)
    finding = _finding()

    result = emitter.emit(
        finding=finding,
        project_root=project,
        fixture_root=project / "tests" / "fixtures" / "retro",
        apply_to_root=False,
        force=False,
    )

    assert result.fixture_dir.exists()
    assert result.sample_file.exists()
    assert result.fixture_config.exists()

    # The sample file lives at <fixture-dir>/src/features/sample.ts
    rel = result.sample_file.relative_to(result.fixture_dir).as_posix()
    assert rel == "src/features/sample.ts"

    # The fixture's cadence.yaml has exactly the proposed rule
    assert result.rule["where"] == "src/features/**"
    assert result.rule["forbidden"] == ["src/data/sources/**"]

    # And critically — the new rule actually fires on the offending import
    assert result.fired is True
    assert result.violation_count >= 1


# --- Per-language: same finding shape works across stacks ------------------


@pytest.mark.parametrize(
    "language,where,import_line,forbidden",
    [
        (
            "py",
            "src/myapp/features/**",
            "from src.myapp.data.sources.bad import x",
            "src/myapp/data/sources/**",
        ),
        (
            "go",
            "internal/features/**",
            'import "myorg/internal/data/sources/bad"',
            "internal/data/sources/**",
        ),
        (
            "rs",
            "src/features/**",
            "use crate::data::sources::bad;",
            "src/data/sources/**",
        ),
        (
            "dart",
            "lib/features/**",
            "import 'package:myapp/data/sources/bad.dart';",
            "lib/data/sources/**",
        ),
    ],
)
def test_emit_works_across_languages(
    tmp_path, language, where, import_line, forbidden
):
    project = _project_skeleton(tmp_path)
    finding = _finding(
        language=language,
        where=where,
        import_line=import_line,
        forbidden_pattern=forbidden,
    )

    result = emitter.emit(
        finding=finding,
        project_root=project,
        fixture_root=project / "tests" / "fixtures" / "retro",
        apply_to_root=False,
        force=False,
    )
    assert result.fired is True, (
        f"{language}: rule did not fire on sample import "
        f"{import_line!r} with where={where!r} forbidden={forbidden!r}"
    )


# --- Safety: a bad finding must not be recorded as landed -----------------


def test_rule_that_does_not_fire_is_caught(tmp_path):
    """The whole point of the emitter is to refuse landing a rule that
    doesn't catch its own sample. Force that mismatch and assert the
    helper reports fired=False."""
    project = _project_skeleton(tmp_path)
    # `where` points at features/, but the forbidden pattern is for a
    # path that doesn't appear in the import line — the rule cannot
    # possibly fire on this sample.
    finding = _finding(
        import_line="import { x } from '../../data/sources/badStore';",
        forbidden_pattern="src/totally_unrelated/**",
    )

    result = emitter.emit(
        finding=finding,
        project_root=project,
        fixture_root=project / "tests" / "fixtures" / "retro",
        apply_to_root=False,
        force=False,
    )
    assert result.fired is False
    assert result.violation_count == 0


def test_main_returns_nonzero_when_rule_does_not_fire(tmp_path, capsys):
    project = _project_skeleton(tmp_path)
    finding = _finding(
        import_line="import { x } from '../../data/sources/badStore';",
        forbidden_pattern="src/totally_unrelated/**",
    )
    finding_path = tmp_path / "finding.json"
    finding_path.write_text(json.dumps(finding), encoding="utf-8")

    rc = emitter.main(
        [
            "--input",
            str(finding_path),
            "--project-root",
            str(project),
            "--fixture-root",
            str(project / "tests" / "fixtures" / "retro"),
        ]
    )
    assert rc == 1
    captured = capsys.readouterr()
    assert "did NOT flag" in captured.err


def test_main_returns_zero_on_well_formed_finding(tmp_path, capsys):
    project = _project_skeleton(tmp_path)
    finding = _finding()
    finding_path = tmp_path / "finding.json"
    finding_path.write_text(json.dumps(finding), encoding="utf-8")

    rc = emitter.main(
        [
            "--input",
            str(finding_path),
            "--project-root",
            str(project),
            "--fixture-root",
            str(project / "tests" / "fixtures" / "retro"),
        ]
    )
    assert rc == 0


# --- Schema validation ----------------------------------------------------


def test_main_rejects_finding_without_violation_sample_for_layer3(
    tmp_path, capsys
):
    """If fix_layer=3 and auto_method=boundary-rule, the schema's allOf
    branch requires violation_sample to be present."""
    project = _project_skeleton(tmp_path)
    finding = _finding()
    del finding["violation_sample"]
    finding_path = tmp_path / "finding.json"
    finding_path.write_text(json.dumps(finding), encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        emitter.main(
            [
                "--input",
                str(finding_path),
                "--project-root",
                str(project),
            ]
        )
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "violation_sample" in captured.err or "required" in captured.err


def test_main_rejects_non_layer3_finding(tmp_path, capsys):
    project = _project_skeleton(tmp_path)
    finding = _finding(fix_layer=1, auto_method="patterns")
    # Schema's allOf only kicks in for layer 3 + boundary-rule, so this
    # is structurally valid JSON; the emitter's own guard should reject.
    finding.pop("violation_sample", None)
    finding_path = tmp_path / "finding.json"
    finding_path.write_text(json.dumps(finding), encoding="utf-8")

    rc = emitter.main(
        [
            "--input",
            str(finding_path),
            "--project-root",
            str(project),
        ]
    )
    assert rc == 2
    captured = capsys.readouterr()
    assert "fix_layer=3" in captured.err


# --- --apply: round-trip to root cadence.yaml -----------------------------


def test_apply_appends_rule_to_root_cadence_yaml(tmp_path):
    import yaml

    project = _project_skeleton(tmp_path)
    finding = _finding()

    emitter.emit(
        finding=finding,
        project_root=project,
        fixture_root=project / "tests" / "fixtures" / "retro",
        apply_to_root=True,
        force=False,
    )

    with (project / ".cadence" / "cadence.yaml").open(
        "r", encoding="utf-8"
    ) as fh:
        cfg = yaml.safe_load(fh)
    boundaries = cfg["boundaries"]
    assert len(boundaries) == 1
    assert boundaries[0]["where"] == "src/features/**"
    assert boundaries[0]["forbidden"] == ["src/data/sources/**"]


def test_apply_is_idempotent(tmp_path):
    import yaml

    project = _project_skeleton(tmp_path)
    finding = _finding()

    emitter.emit(
        finding=finding,
        project_root=project,
        fixture_root=project / "tests" / "fixtures" / "retro",
        apply_to_root=True,
        force=False,
    )
    # Second run with the same finding (different id, same rule shape)
    finding2 = _finding()
    emitter.emit(
        finding=finding2,
        project_root=project,
        fixture_root=project / "tests" / "fixtures" / "retro",
        apply_to_root=True,
        force=False,
    )

    with (project / ".cadence" / "cadence.yaml").open(
        "r", encoding="utf-8"
    ) as fh:
        cfg = yaml.safe_load(fh)
    # Still exactly one rule — the second emit detected the duplicate
    # and did not re-append.
    assert len(cfg["boundaries"]) == 1


# --- No-clobber safety ----------------------------------------------------


def test_existing_fixture_refuses_overwrite_without_force(tmp_path):
    project = _project_skeleton(tmp_path)
    finding = _finding()
    fixture_root = project / "tests" / "fixtures" / "retro"

    emitter.emit(
        finding=finding,
        project_root=project,
        fixture_root=fixture_root,
        apply_to_root=False,
        force=False,
    )

    # Re-run the same finding (same id) — should refuse.
    finding_path = tmp_path / "finding.json"
    finding_path.write_text(json.dumps(finding), encoding="utf-8")
    with pytest.raises(SystemExit) as exc_info:
        emitter.main(
            [
                "--input",
                str(finding_path),
                "--project-root",
                str(project),
                "--fixture-root",
                str(fixture_root),
            ]
        )
    assert exc_info.value.code == 2
    # ... and leaves the existing fixture alone.
    assert (fixture_root / str(uuid.UUID(finding["id"])).split("-")[0]).is_dir()


# === Factory mode and hardening (docs/LEARNING.md, "Check proof") ===========

import hashlib  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

import yaml  # noqa: E402

FACTORY_LINE = "import { db } from '../db/client';"
FACTORY_PATCH = (
    "diff --git a/src/domain/order.ts b/src/domain/order.ts\n"
    "new file mode 100644\n"
    "index 0000000..1111111\n"
    "--- /dev/null\n"
    "+++ b/src/domain/order.ts\n"
    "@@ -0,0 +1,3 @@\n"
    "+// order\n"
    f"+{FACTORY_LINE}\n"
    "+export const order = db;\n"
)
SEEDED_CONFIG = (
    "# Project config. Keep this comment.\n"
    "commands:\n"
    "  test: ['true']   # inline comment stays\n"
    "boundaries:\n"
    "  # seed rules\n"
    '  - where: "src/domain/**"\n'
    "    forbidden:\n"
    '      - "src/http/**"\n'
    '    reason: "Domain code stays independent of HTTP"\n'
    "\n"
    "# trailing comment\n"
)


def _factory_finding(**overrides) -> dict:
    finding = _finding(
        where="src/domain/**",
        import_line=FACTORY_LINE,
        forbidden_pattern="src/db/**",
        reason="Learned rule: src/domain/ must not import src/db/.",
    )
    finding["violation_sample"].update(overrides)
    return finding


def _rule_id(finding: dict) -> str:
    return "L-" + str(uuid.UUID(finding["id"])).split("-")[0]


def _factory_run(tmp_path, project, finding, *, line=2, path="src/domain/order.ts", patch=FACTORY_PATCH, extra=()):
    finding_path = tmp_path / "finding.json"
    finding_path.write_text(json.dumps(finding), encoding="utf-8")
    patch_path = tmp_path / "change.patch"
    patch_path.write_bytes(patch.encode("utf-8"))
    return emitter.main(
        [
            "--input", str(finding_path),
            "--project-root", str(project),
            "--rule-id", _rule_id(finding),
            "--class-key", "import-edge:src/domain->src/db",
            "--provenance-patch", str(patch_path),
            "--provenance-path", path,
            "--provenance-line", str(line),
            "--now", "1790000000",
            *extra,
        ]
    )  # fmt: skip


def _fixture_dir(project: Path, finding: dict) -> Path:
    return project / "tests" / "fixtures" / "retro" / _rule_id(finding)[2:]


def _run_expecting_exit(fn, *args, **kwargs) -> int:
    try:
        return fn(*args, **kwargs)
    except SystemExit as exc:
        return int(exc.code)


@pytest.mark.parametrize(
    "field,value",
    [
        ("where", "src/../../etc/**"),
        ("forbidden_pattern", "src/db/../../**"),
        ("where", "tests/fixtures/retro/**"),
        ("where", "/etc/**"),
    ],
)
def test_unsafe_globs_are_refused_before_any_write(tmp_path, field, value):
    project = _project_skeleton(tmp_path)
    finding = _factory_finding(**{field: value})
    assert _run_expecting_exit(_factory_run, tmp_path, project, finding) == 2
    assert not (project / "tests").exists()


def test_a_traversing_sample_path_is_refused(tmp_path):
    project = _project_skeleton(tmp_path)
    finding = _factory_finding()
    patch = FACTORY_PATCH.replace("src/domain/order.ts", "../outside.ts")
    rc = _run_expecting_exit(_factory_run, tmp_path, project, finding, path="../outside.ts", patch=patch)
    assert rc == 2
    assert not (tmp_path / "outside.ts").exists() and not (project / "tests").exists()


@pytest.mark.parametrize(
    "field,value",
    [("id", "not-a-uuid"), ("ts", "yesterday"), ("ts", "2026-13-45 12:00")],
)
def test_a_bad_uuid_or_timestamp_is_refused(tmp_path, field, value):
    project = _project_skeleton(tmp_path)
    finding = _factory_finding()
    finding[field] = value
    finding_path = tmp_path / "f.json"
    finding_path.write_text(json.dumps(finding), encoding="utf-8")
    rc = _run_expecting_exit(emitter.main, ["--input", str(finding_path), "--project-root", str(project)])
    assert rc == 2
    assert not (project / "tests").exists()


@pytest.mark.parametrize(
    "line",
    [
        "import { a } from '../db/a';\nimport { b } from '../db/b';",
        "import { a } from '../db/a'; export const x = 1;",
        "import { a } from '../db/a';\x00",
    ],
)
def test_a_multi_line_import_is_refused(tmp_path, line):
    project = _project_skeleton(tmp_path)
    finding = _finding(import_line=line)
    finding_path = tmp_path / "f.json"
    finding_path.write_text(json.dumps(finding), encoding="utf-8")
    rc = _run_expecting_exit(emitter.main, ["--input", str(finding_path), "--project-root", str(project)])
    assert rc == 2
    assert not (project / "tests").exists()


@pytest.mark.parametrize("line", [1, 3, 9])
def test_provenance_line_mismatch_exits_2(tmp_path, line):
    project = _project_skeleton(tmp_path)
    finding = _factory_finding()
    assert _run_expecting_exit(_factory_run, tmp_path, project, finding, line=line) == 2
    assert not (project / "tests").exists()


def test_a_line_that_is_not_a_strict_import_is_refused(tmp_path):
    project = _project_skeleton(tmp_path)
    line = "import { db } from '../db/client'; doSomething();"
    finding = _factory_finding(import_line=line)
    patch = FACTORY_PATCH.replace(FACTORY_LINE, line)
    assert _run_expecting_exit(_factory_run, tmp_path, project, finding, patch=patch) == 2


def test_the_line_must_be_added_in_the_named_file(tmp_path):
    project = _project_skeleton(tmp_path)
    finding = _factory_finding()
    other = FACTORY_PATCH.replace("src/domain/order.ts", "src/domain/other.ts")
    assert _run_expecting_exit(_factory_run, tmp_path, project, finding, patch=other) == 2


def test_rule_id_must_match_the_finding(tmp_path):
    project = _project_skeleton(tmp_path)
    finding_path = tmp_path / "f.json"
    finding_path.write_text(json.dumps(_factory_finding()), encoding="utf-8")
    rc = _run_expecting_exit(
        emitter.main, ["--input", str(finding_path), "--project-root", str(project), "--rule-id", "L-00000000"]
    )
    assert rc == 2


def test_provenance_flags_go_together(tmp_path):
    project = _project_skeleton(tmp_path)
    finding_path = tmp_path / "f.json"
    finding_path.write_text(json.dumps(_factory_finding()), encoding="utf-8")
    rc = emitter.main(
        ["--input", str(finding_path), "--project-root", str(project), "--provenance-path", "src/domain/order.ts"]
    )
    assert rc == 2


def test_factory_sample_is_written_at_its_real_path_with_a_header(tmp_path):
    project = _project_skeleton(tmp_path)
    finding = _factory_finding()
    assert _factory_run(tmp_path, project, finding) == 0
    fixture = _fixture_dir(project, finding)
    sample = fixture / "src" / "domain" / "order.ts"
    assert sample.read_text(encoding="utf-8") == (
        f"// @ts-nocheck\n{FACTORY_LINE}\n\nexport const __retro_sample = true;\n"
    )
    assert json.loads((fixture / "finding.json").read_text(encoding="utf-8")) == finding
    provenance = json.loads((fixture / "provenance.json").read_text(encoding="utf-8"))
    assert provenance == {
        "rule_id": _rule_id(finding),
        "class_key": "import-edge:src/domain->src/db",
        "patch_sha256": hashlib.sha256(FACTORY_PATCH.encode("utf-8")).hexdigest(),
        "path": "src/domain/order.ts",
        "line_no": 2,
        "emitted_at": datetime.fromtimestamp(1790000000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    config = yaml.safe_load((fixture / ".cadence" / "cadence.yaml").read_text(encoding="utf-8"))
    assert config["boundaries"][0]["id"] == _rule_id(finding)
    assert sorted(p.relative_to(fixture).as_posix() for p in fixture.rglob("*") if p.is_file()) == [
        ".cadence/cadence.yaml",
        "finding.json",
        "provenance.json",
        "src/domain/order.ts",
    ]


@pytest.mark.parametrize(
    "path,line,header",
    [
        ("src/domain/order.py", "from src.db.client import connect", "# ruff: noqa\n# mypy: ignore-errors\n"),
        (
            "lib/domain/order.dart",
            "import 'package:app/db/client.dart';",
            "// ignore_for_file: type=lint, uri_does_not_exist\n",
        ),
    ],
)
def test_factory_headers_for_python_and_dart(tmp_path, path, line, header):
    project = _project_skeleton(tmp_path)
    frm = path.rsplit("/", 1)[0]
    to = "src/db" if path.endswith(".py") else "lib/db"
    finding = _factory_finding(where=f"{frm}/**", forbidden_pattern=f"{to}/**", import_line=line)
    patch = FACTORY_PATCH.replace("src/domain/order.ts", path).replace(FACTORY_LINE, line)
    assert _factory_run(tmp_path, project, finding, path=path, patch=patch) == 0
    text = (_fixture_dir(project, finding) / path).read_text(encoding="utf-8")
    assert text.startswith(header + line + "\n")


def test_must_pass_root_exits_3_and_removes_the_fixture(tmp_path, capsys):
    project = _project_skeleton(tmp_path)
    existing = project / "src" / "domain" / "legacy.ts"
    existing.parent.mkdir(parents=True)
    existing.write_text(f"{FACTORY_LINE}\n", encoding="utf-8")
    config_before = (project / ".cadence" / "cadence.yaml").read_bytes()
    finding = _factory_finding()
    rc = _factory_run(tmp_path, project, finding, extra=("--must-pass-root", "--apply", "--json"))
    assert rc == 3
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["exit"] == 3 and out["fixture"] is None
    assert out["root_violations"] == [{"path": "src/domain/legacy.ts", "line_no": 1}]
    assert not _fixture_dir(project, finding).exists()
    assert (project / ".cadence" / "cadence.yaml").read_bytes() == config_before


def test_must_pass_root_ignores_other_retro_fixtures(tmp_path):
    project = _project_skeleton(tmp_path)
    older = project / "tests" / "fixtures" / "retro" / "0badc0de" / "src" / "domain"
    older.mkdir(parents=True)
    (older / "sample.ts").write_text(f"{FACTORY_LINE}\n", encoding="utf-8")
    assert _factory_run(tmp_path, project, _factory_finding(), extra=("--must-pass-root",)) == 0


def test_json_output_shape(tmp_path, capsys):
    project = _project_skeleton(tmp_path)
    finding = _factory_finding()
    assert _factory_run(tmp_path, project, finding, extra=("--must-pass-root", "--apply", "--json")) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    out = json.loads(lines[0])
    assert set(out) == {"fixture", "sample", "rule", "fired", "violations", "root_violations", "applied", "exit"}
    assert out["fixture"] == f"tests/fixtures/retro/{_rule_id(finding)[2:]}/"
    assert out["sample"] == "src/domain/order.ts"
    assert out["rule"]["id"] == _rule_id(finding)
    assert (out["fired"], out["applied"], out["exit"], out["root_violations"]) == (True, True, 0, [])
    assert out["violations"] >= 1


def test_json_output_when_the_rule_does_not_fire(tmp_path, capsys):
    project = _project_skeleton(tmp_path)
    finding = _factory_finding(forbidden_pattern="src/elsewhere/**")
    assert _factory_run(tmp_path, project, finding, extra=("--json",)) == 1
    out = json.loads(capsys.readouterr().out.strip())
    assert (out["fired"], out["exit"], out["fixture"]) == (False, 1, None)
    assert not _fixture_dir(project, finding).exists()


def test_apply_preserves_comments_and_parses_to_old_plus_rule(tmp_path):
    project = _project_skeleton(tmp_path)
    config = project / ".cadence" / "cadence.yaml"
    config.write_bytes(SEEDED_CONFIG.encode("utf-8"))
    old = yaml.safe_load(SEEDED_CONFIG)
    finding = _factory_finding()
    assert _factory_run(tmp_path, project, finding, extra=("--apply",)) == 0
    text = config.read_bytes().decode("utf-8")
    for kept in ("# Project config. Keep this comment.", "# inline comment stays", "# seed rules", "# trailing comment"):
        assert kept in text
    assert text.startswith(SEEDED_CONFIG.split("\n\n# trailing")[0])
    new = yaml.safe_load(text)
    assert new["boundaries"][:1] == old["boundaries"]
    assert new["boundaries"][1] == {
        "id": _rule_id(finding),
        "where": "src/domain/**",
        "forbidden": ["src/db/**"],
        "reason": "Learned rule: src/domain/ must not import src/db/.",
    }
    assert {k: v for k, v in new.items() if k != "boundaries"} == {k: v for k, v in old.items() if k != "boundaries"}


def test_apply_refuses_a_file_it_cannot_edit_safely(tmp_path):
    project = _project_skeleton(tmp_path)
    config = project / ".cadence" / "cadence.yaml"
    flow = "commands: {test: ['true']}\nboundaries: [{where: 'a/**', forbidden: ['b/**'], reason: 'xxxxx'}]\n"
    config.write_bytes(flow.encode("utf-8"))
    finding = _factory_finding()
    assert _run_expecting_exit(_factory_run, tmp_path, project, finding, extra=("--apply",)) == 2
    assert config.read_bytes().decode("utf-8") == flow
    assert not _fixture_dir(project, finding).exists()


def test_insert_rule_text_handles_the_layouts():
    rule = {"id": "L-1a2b3c4d", "where": "src/a/**", "forbidden": ["src/b/**"], "reason": "a reason"}
    zero_indent = "boundaries:\n- where: x/**\n  forbidden: [y/**]\n  reason: 'xxxxx'\nother: 1\n"
    new, added = emitter.insert_rule_text(zero_indent, rule)
    assert added and "\n- id: L-1a2b3c4d\n" in new and new.endswith("other: 1\n")
    missing, _ = emitter.insert_rule_text("commands: {}\n", rule)
    assert yaml.safe_load(missing)["boundaries"] == [rule]
    same, added = emitter.insert_rule_text(new, {**rule, "id": "L-ffffffff"})
    assert not added and same == new
    crlf = zero_indent.replace("\n", "\r\n")
    new_crlf, _ = emitter.insert_rule_text(crlf, rule)
    assert "\n" not in new_crlf.replace("\r\n", "")


def test_retire_removes_only_that_rule(tmp_path, capsys):
    project = _project_skeleton(tmp_path)
    config = project / ".cadence" / "cadence.yaml"
    config.write_bytes(SEEDED_CONFIG.encode("utf-8"))
    finding = _factory_finding()
    assert _factory_run(tmp_path, project, finding, extra=("--apply",)) == 0
    rid = _rule_id(finding)
    capsys.readouterr()
    assert emitter.main(["--retire", rid, "--project-root", str(project), "--json"]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1]) == {"exit": 0, "retired": rid}
    assert config.read_bytes().decode("utf-8") == SEEDED_CONFIG
    assert _fixture_dir(project, finding).exists()  # fixtures stay


def test_retire_the_only_rule_leaves_an_empty_list(tmp_path):
    project = _project_skeleton(tmp_path)
    finding = _factory_finding()
    assert _factory_run(tmp_path, project, finding, extra=("--apply",)) == 0
    assert emitter.main(["--retire", _rule_id(finding), "--project-root", str(project)]) == 0
    config = yaml.safe_load((project / ".cadence" / "cadence.yaml").read_text(encoding="utf-8"))
    assert config["boundaries"] == []


def test_retire_not_found_exits_4(tmp_path):
    project = _project_skeleton(tmp_path)
    assert emitter.main(["--retire", "L-0badc0de", "--project-root", str(project)]) == 4


@pytest.mark.parametrize("rule_id", ["B-1a2b3c4d", "L-xyz", "src/domain"])
def test_retire_refuses_anything_but_a_learned_id(tmp_path, rule_id):
    project = _project_skeleton(tmp_path)
    assert emitter.main(["--retire", rule_id, "--project-root", str(project)]) == 2


def test_retire_failure_leaves_the_file_unchanged(tmp_path):
    project = _project_skeleton(tmp_path)
    config = project / ".cadence" / "cadence.yaml"
    flow = "boundaries: [{id: L-1a2b3c4d, where: 'a/**', forbidden: ['b/**'], reason: 'xxxxx'}]\n"
    config.write_bytes(flow.encode("utf-8"))
    assert emitter.main(["--retire", "L-1a2b3c4d", "--project-root", str(project)]) == 2
    assert config.read_bytes().decode("utf-8") == flow
    config.write_bytes(b"boundaries: [unclosed\n")
    assert emitter.main(["--retire", "L-1a2b3c4d", "--project-root", str(project)]) == 2
    assert config.read_bytes() == b"boundaries: [unclosed\n"


def test_replay_reports_each_fixture(tmp_path, capsys):
    project = _project_skeleton(tmp_path)
    good, bad = _factory_finding(), _factory_finding()
    assert _factory_run(tmp_path, project, good) == 0
    assert _factory_run(tmp_path, project, bad) == 0
    # The legacy (non-factory) layout replays too.
    legacy = _finding()
    emitter.emit(legacy, project, project / "tests" / "fixtures" / "retro", False, False)
    sample = _fixture_dir(project, bad) / "src" / "domain" / "order.ts"
    sample.write_text("export const fixed = true;\n", encoding="utf-8")
    capsys.readouterr()
    assert emitter.main(["--replay", "--json", "--project-root", str(project)]) == 1
    results = {r["fixture"]: r for r in map(json.loads, capsys.readouterr().out.strip().splitlines())}
    assert results[_rule_id(good)[2:]] == {"fixture": _rule_id(good)[2:], "rule_id": _rule_id(good), "fired": True}
    assert results[_rule_id(bad)[2:]]["fired"] is False
    assert results[_rule_id(legacy)[2:]] == {"fixture": _rule_id(legacy)[2:], "rule_id": None, "fired": True}
    rc = emitter.main(["--replay", "--json", "--project-root", str(project), "--skip-ids", _rule_id(bad)])
    assert rc == 0
    assert emitter.main(["--replay", "--project-root", str(project), "--skip-ids", "../x"]) == 2


def test_replay_without_fixtures_is_ok(tmp_path, capsys):
    project = _project_skeleton(tmp_path)
    assert emitter.main(["--replay", "--json", "--project-root", str(project)]) == 0
    assert capsys.readouterr().out == ""


# --- Directory-index imports (check_boundaries resolves relative imports) ----

INDEX_LINE = "import { db } from '../db';"


@pytest.mark.parametrize(
    "path,line",
    [
        ("src/domain/order.ts", INDEX_LINE),
        ("src/domain/order.ts", 'export * from "../db";'),
        ("src/domain/order.ts", "const db = require('../db');"),
        ("src/domain/order.py", "from .. import db"),
        ("src/domain/order.py", "from ..db import session"),
    ],
)
def test_factory_provenance_with_a_directory_index_import_fires(tmp_path, capsys, path, line):
    project = _project_skeleton(tmp_path)
    finding = _factory_finding(import_line=line)
    patch = FACTORY_PATCH.replace("src/domain/order.ts", path).replace(FACTORY_LINE, line)
    rc = _factory_run(tmp_path, project, finding, path=path, patch=patch, extra=("--must-pass-root", "--apply", "--json"))
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert (out["fired"], out["exit"], out["applied"], out["sample"]) == (True, 0, True, path)
    assert out["violations"] == 1
    sample = _fixture_dir(project, finding) / path
    assert line in sample.read_text(encoding="utf-8").splitlines()
    rules = yaml.safe_load((project / ".cadence" / "cadence.yaml").read_text(encoding="utf-8"))["boundaries"]
    assert rules[-1]["forbidden"] == ["src/db/**"]


def test_must_pass_root_exits_3_on_a_directory_index_import_on_main(tmp_path, capsys):
    project = _project_skeleton(tmp_path)
    legacy = project / "src" / "domain" / "legacy.ts"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("// legacy\nimport { query } from '../db';\n", encoding="utf-8")
    config_before = (project / ".cadence" / "cadence.yaml").read_bytes()
    finding = _factory_finding(import_line=INDEX_LINE)
    patch = FACTORY_PATCH.replace(FACTORY_LINE, INDEX_LINE)
    rc = _factory_run(tmp_path, project, finding, patch=patch, extra=("--must-pass-root", "--apply", "--json"))
    assert rc == 3
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert (out["exit"], out["fixture"], out["fired"]) == (3, None, True)
    assert out["root_violations"] == [{"path": "src/domain/legacy.ts", "line_no": 2}]
    assert not _fixture_dir(project, finding).exists()
    assert (project / ".cadence" / "cadence.yaml").read_bytes() == config_before


def test_must_pass_root_ignores_lookalike_imports_on_main(tmp_path):
    project = _project_skeleton(tmp_path)
    domain = project / "src" / "domain"
    domain.mkdir(parents=True)
    (domain / "a.ts").write_text(
        "import { u } from '../dbutils';\nimport { v } from './db';\nimport { w } from '@/db';\n",
        encoding="utf-8",
    )
    (project / "src" / "http").mkdir()
    (project / "src" / "http" / "b.ts").write_text("import { db } from '../db';\n", encoding="utf-8")
    finding = _factory_finding(import_line=INDEX_LINE)
    patch = FACTORY_PATCH.replace(FACTORY_LINE, INDEX_LINE)
    assert _factory_run(tmp_path, project, finding, patch=patch, extra=("--must-pass-root",)) == 0


def test_replay_fires_on_a_directory_index_fixture(tmp_path, capsys):
    project = _project_skeleton(tmp_path)
    finding = _factory_finding(import_line=INDEX_LINE)
    patch = FACTORY_PATCH.replace(FACTORY_LINE, INDEX_LINE)
    assert _factory_run(tmp_path, project, finding, patch=patch) == 0
    capsys.readouterr()
    assert emitter.main(["--replay", "--json", "--project-root", str(project)]) == 0
    result = json.loads(capsys.readouterr().out.strip())
    assert result == {"fixture": _rule_id(finding)[2:], "rule_id": _rule_id(finding), "fired": True}


def test_force_restores_the_old_fixture_when_reemission_fails(tmp_path):
    project = _project_skeleton(tmp_path)
    finding = _factory_finding()
    assert _factory_run(tmp_path, project, finding) == 0
    fixture = _fixture_dir(project, finding)
    before = {p.relative_to(fixture).as_posix(): p.read_bytes() for p in fixture.rglob("*") if p.is_file()}
    finding["violation_sample"]["forbidden_pattern"] = "src/elsewhere/**"
    assert _factory_run(tmp_path, project, finding, extra=("--force",)) == 1
    after = {p.relative_to(fixture).as_posix(): p.read_bytes() for p in fixture.rglob("*") if p.is_file()}
    assert after == before
    assert not list(fixture.parent.glob(".*bak*"))

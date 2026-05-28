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

"""Tests for the unified compliance report generator.

The report is auditor-facing, so determinism matters. These tests
exercise: standard discovery, status assignment (implemented/partial/
gap/planned/out-of-scope), the reverse-index dedup across standards,
HTML structure, JSON shape, --reproducible byte-stability, --strict
exit codes, and the audit-packet bundle layout.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = (
    REPO_ROOT / "plugins" / "cadence" / "templates" / "tool" / "compliance_report.py"
)
STANDARDS_DIR = REPO_ROOT / "plugins" / "cadence" / "standards"


def _load_module():
    spec = importlib.util.spec_from_file_location("compliance_report", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["compliance_report"] = module
    spec.loader.exec_module(module)
    return module


cr = _load_module()


def _make_project(tmp_path: Path, *, with_verify_evidence: bool = False) -> Path:
    """Create a minimal project skeleton that has *some* artifacts mapped
    in the SSDF and ISO 25010 YAMLs, so the report has a non-trivial
    mix of implemented/partial/gap statuses."""
    root = tmp_path / "proj"
    root.mkdir()

    cadence_dir = root / ".cadence"
    cadence_dir.mkdir()
    (cadence_dir / "cadence.yaml").write_text(
        "commands:\n  lint: ['true']\n  test: ['true']\nboundaries: []\n",
        encoding="utf-8",
    )
    if with_verify_evidence:
        (cadence_dir / ".last_verify_ok").write_text("ok\n", encoding="utf-8")
        (cadence_dir / ".last_verify_sha").write_text(
            "deadbeefcafe1234\n", encoding="utf-8"
        )

    docs = root / "docs"
    docs.mkdir()
    (docs / "PATTERNS.md").write_text(
        "# Patterns\n\n## security\n\nstub\n", encoding="utf-8"
    )
    (docs / "DEFINITION_OF_DONE.md").write_text("# DoD\n", encoding="utf-8")
    (docs / "ROLE_SPECS.md").write_text("# Role specs\n", encoding="utf-8")
    (docs / "TEAM_PROTOCOL.md").write_text("# Team protocol\n", encoding="utf-8")
    (docs / "FRAMEWORK_CHANGELOG.md").write_text(
        "# Framework changelog\n", encoding="utf-8"
    )
    (docs / "RETROSPECTIVE_PROTOCOL.md").write_text(
        "# Retrospective protocol\n", encoding="utf-8"
    )
    (docs / "cross-tool-portability.md").write_text(
        "# Cross-tool portability\n", encoding="utf-8"
    )

    adr = docs / "ADR"
    adr.mkdir()
    (adr / "0001-secret-handling.md").write_text(
        "<!-- ssdf: [PO.1] -->\n# 0001\n", encoding="utf-8"
    )

    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "verify.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (scripts / "verify.ps1").write_text("exit 0\n", encoding="utf-8")

    tool = root / "tool"
    tool.mkdir()
    (tool / "check_boundaries.py").write_text("# stub\n", encoding="utf-8")

    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "cadence.yml").write_text("# stub\n", encoding="utf-8")

    tests_dir = root / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_smoke.py").write_text(
        "def test_smoke(): assert True\n", encoding="utf-8"
    )

    return root


# --- Standard discovery + structure ---------------------------------------


def test_generate_covers_all_four_standards(tmp_path):
    project = _make_project(tmp_path)
    report = cr.generate(
        project_root=project,
        standards_dir=STANDARDS_DIR,
        selected=[],
        reproducible=True,
    )
    std_ids = {s["id"] for s in report.standards}
    assert std_ids == {
        "nist-ssdf", "iso-25010", "aicpa-soc2-tsc", "iso-27001"
    }


def test_generate_filtering_by_standard(tmp_path):
    project = _make_project(tmp_path)
    report = cr.generate(
        project_root=project,
        standards_dir=STANDARDS_DIR,
        selected=["nist-ssdf"],
        reproducible=True,
    )
    std_ids = {s["id"] for s in report.standards}
    assert std_ids == {"nist-ssdf"}


# --- Status assignment ----------------------------------------------------


def test_soc2_and_iso27001_render_as_planned_or_out_of_scope(tmp_path):
    """The two new stub standards must never accidentally report
    Implemented or Gap — they're roadmap scaffolding, not claims."""
    project = _make_project(tmp_path)
    report = cr.generate(
        project_root=project,
        standards_dir=STANDARDS_DIR,
        selected=["aicpa-soc2-tsc", "iso-27001"],
        reproducible=True,
    )
    for r in report.items:
        assert r.status in ("planned", "out-of-scope"), (
            f"{r.item.standard_id}/{r.item.item_id} should be a stub, "
            f"got status={r.status!r}"
        )


def test_partial_when_artifacts_exist_without_verify_evidence(tmp_path):
    project = _make_project(tmp_path, with_verify_evidence=False)
    report = cr.generate(
        project_root=project,
        standards_dir=STANDARDS_DIR,
        selected=["nist-ssdf"],
        reproducible=True,
    )
    po3 = next(r for r in report.items if r.item.item_id == "PO.3")
    assert po3.status == "partial", (
        "PO.3 has all three declared artifacts present, but no verify "
        "evidence → should be Partial, not Implemented"
    )
    assert po3.artifacts_resolved  # at least one path resolved


def test_implemented_when_verify_evidence_present(tmp_path):
    project = _make_project(tmp_path, with_verify_evidence=True)
    report = cr.generate(
        project_root=project,
        standards_dir=STANDARDS_DIR,
        selected=["nist-ssdf"],
        reproducible=True,
    )
    po3 = next(r for r in report.items if r.item.item_id == "PO.3")
    assert po3.status == "implemented"


def test_gap_when_no_artifacts_declared(tmp_path):
    project = _make_project(tmp_path, with_verify_evidence=True)
    report = cr.generate(
        project_root=project,
        standards_dir=STANDARDS_DIR,
        selected=["nist-ssdf"],
        reproducible=True,
    )
    # PS.1 declares no artifacts (deliberate — see SSDF YAML notes)
    ps1 = next(r for r in report.items if r.item.item_id == "PS.1")
    assert ps1.status == "gap"


def test_compliance_exclude_marks_as_na(tmp_path):
    project = _make_project(tmp_path, with_verify_evidence=True)
    cfg_path = project / ".cadence" / "cadence.yaml"
    cfg_path.write_text(
        "commands:\n  lint: ['true']\n"
        "boundaries: []\n"
        "compliance:\n  exclude: ['PS.1']\n",
        encoding="utf-8",
    )
    report = cr.generate(
        project_root=project,
        standards_dir=STANDARDS_DIR,
        selected=["nist-ssdf"],
        reproducible=True,
    )
    ps1 = next(r for r in report.items if r.item.item_id == "PS.1")
    assert ps1.status == "n/a"


# --- Reverse index (cross-standard) ---------------------------------------


def test_reverse_index_dedups_across_standards(tmp_path):
    """PATTERNS.md is referenced by multiple SSDF practices AND multiple
    ISO 25010 characteristics. The reverse index must list every
    standard/item that cites it, once each."""
    project = _make_project(tmp_path)
    report = cr.generate(
        project_root=project,
        standards_dir=STANDARDS_DIR,
        selected=[],
        reproducible=True,
    )
    entries = report.reverse_index.get("docs/PATTERNS.md", [])
    standards_citing = {e[0] for e in entries}
    assert "nist-ssdf" in standards_citing
    assert "iso-25010" in standards_citing
    # SOC2 and ISO 27001 stubs cite no artifacts → must NOT appear in
    # the reverse index of any real artifact.
    assert "aicpa-soc2-tsc" not in standards_citing
    assert "iso-27001" not in standards_citing


def test_reverse_index_sorted_for_determinism(tmp_path):
    project = _make_project(tmp_path)
    report = cr.generate(
        project_root=project,
        standards_dir=STANDARDS_DIR,
        selected=[],
        reproducible=True,
    )
    paths = list(report.reverse_index.keys())
    assert paths == sorted(paths), (
        "reverse index must be sorted by path for deterministic output"
    )


# --- Rendering ------------------------------------------------------------


def test_html_output_has_expected_structure(tmp_path):
    project = _make_project(tmp_path, with_verify_evidence=True)
    report = cr.generate(
        project_root=project,
        standards_dir=STANDARDS_DIR,
        selected=[],
        reproducible=True,
    )
    out = cr._render_html(report)
    assert out.startswith("<!doctype html>")
    assert "<title>Cadence Compliance Report" in out
    assert "<h2>Summary</h2>" in out
    assert "Reverse index" in out
    # The disclaimer must always be present — it's the legal posture.
    assert "opinionated, not" in out
    # Stub standards appear with their stub-marker status, not faked
    assert "aicpa-soc2-tsc" in out
    assert "planned" in out


def test_json_output_is_sorted_for_determinism(tmp_path):
    project = _make_project(tmp_path, with_verify_evidence=True)
    report = cr.generate(
        project_root=project,
        standards_dir=STANDARDS_DIR,
        selected=[],
        reproducible=True,
    )
    out = cr._render_json(report)
    parsed = json.loads(out)
    # Generated twice → same bytes
    again = cr._render_json(report)
    assert out == again
    assert "items" in parsed and "summary" in parsed
    assert "reverse_index" in parsed


def test_markdown_includes_disclaimer_and_summary(tmp_path):
    project = _make_project(tmp_path, with_verify_evidence=True)
    report = cr.generate(
        project_root=project,
        standards_dir=STANDARDS_DIR,
        selected=[],
        reproducible=True,
    )
    out = cr._render_md(report)
    assert out.startswith("# Cadence Compliance Report")
    assert "## Summary" in out
    assert "opinionated, not" in out


# --- Reproducibility ------------------------------------------------------


def test_reproducible_flag_yields_byte_identical_output(tmp_path):
    project = _make_project(tmp_path, with_verify_evidence=True)
    out_dir_a = tmp_path / "out-a"
    out_dir_b = tmp_path / "out-b"
    for out_dir in (out_dir_a, out_dir_b):
        rc = cr.main(
            [
                "--project-root", str(project),
                "--standards-dir", str(STANDARDS_DIR),
                "--format", "all",
                "--output-dir", str(out_dir),
                "--reproducible",
            ]
        )
        assert rc == 0

    for fmt in ("md", "json", "html"):
        a = (out_dir_a / f"compliance-report.{fmt}").read_text(encoding="utf-8")
        b = (out_dir_b / f"compliance-report.{fmt}").read_text(encoding="utf-8")
        assert a == b, f"{fmt}: outputs differ between two --reproducible runs"


# --- --strict -------------------------------------------------------------


def test_strict_returns_nonzero_when_gaps_present(tmp_path, capsys):
    project = _make_project(tmp_path, with_verify_evidence=True)
    out_dir = tmp_path / "out"
    rc = cr.main(
        [
            "--project-root", str(project),
            "--standards-dir", str(STANDARDS_DIR),
            "--format", "md",
            "--output-dir", str(out_dir),
            "--reproducible",
            "--strict",
        ]
    )
    # The default project has at least one Gap (PS.1).
    assert rc == 1
    captured = capsys.readouterr()
    assert "gap" in captured.err.lower()


def test_strict_returns_zero_with_no_gaps(tmp_path):
    """If every in-scope item is Implemented/Partial/Planned/OOS/NA,
    --strict succeeds. Construct that by excluding the known-Gap ids."""
    project = _make_project(tmp_path, with_verify_evidence=True)
    cfg = project / ".cadence" / "cadence.yaml"
    cfg.write_text(
        "commands:\n  lint: ['true']\n"
        "boundaries: []\n"
        "compliance:\n"
        "  exclude:\n"
        "    - PS.1\n"
        "    - PS.2\n"
        "    - PS.3\n"
        "    - PW.4\n"
        "    - PW.9\n"
        "    - performance-efficiency\n"
        "    - compatibility\n"
        "    - interaction-capability\n"
        "    - safety\n",
        encoding="utf-8",
    )
    out_dir = project / "_out"
    rc = cr.main(
        [
            "--project-root", str(project),
            "--standards-dir", str(STANDARDS_DIR),
            "--format", "md",
            "--output-dir", str(out_dir),
            "--reproducible",
            "--strict",
        ]
    )
    assert rc == 0


# --- Audit packet ---------------------------------------------------------


def test_audit_packet_bundles_expected_files(tmp_path):
    project = _make_project(tmp_path, with_verify_evidence=True)
    out_dir = tmp_path / "out"
    rc = cr.main(
        [
            "--project-root", str(project),
            "--standards-dir", str(STANDARDS_DIR),
            "--audit-packet",
            "--output-dir", str(out_dir),
            "--reproducible",
        ]
    )
    assert rc == 0
    packet = out_dir / "audit-packet"
    assert packet.is_dir()
    assert (packet / "compliance-report.md").is_file()
    assert (packet / "compliance-report.json").is_file()
    assert (packet / "compliance-report.html").is_file()
    assert (packet / "ADR").is_dir()
    assert (packet / "FRAMEWORK_CHANGELOG.md").is_file()
    assert (packet / "PATTERNS.md").is_file()
    assert (packet / "MANIFEST.txt").is_file()
    manifest_body = (packet / "MANIFEST.txt").read_text(encoding="utf-8")
    assert "compliance-report.html" in manifest_body


def test_audit_packet_forces_all_format(tmp_path):
    """--audit-packet implies --format all even if md is requested."""
    project = _make_project(tmp_path, with_verify_evidence=True)
    out_dir = tmp_path / "out"
    rc = cr.main(
        [
            "--project-root", str(project),
            "--standards-dir", str(STANDARDS_DIR),
            "--audit-packet",
            "--format", "md",
            "--output-dir", str(out_dir),
            "--reproducible",
        ]
    )
    assert rc == 0
    # All three formats should land in the packet.
    packet = out_dir / "audit-packet"
    for fmt in ("md", "json", "html"):
        assert (packet / f"compliance-report.{fmt}").is_file()


# --- --standard parsing (regression for v0.3.0-rc.2 bug) ------------------


def test_standard_all_includes_every_standard(tmp_path):
    """Regression: --standard all must NOT be parsed as a literal id
    filter that excludes everything. Caught during pre-release dogfood."""
    project = _make_project(tmp_path, with_verify_evidence=True)
    out_dir = tmp_path / "out"
    rc = cr.main(
        [
            "--project-root", str(project),
            "--standards-dir", str(STANDARDS_DIR),
            "--standard", "all",
            "--format", "json",
            "--output-dir", str(out_dir),
            "--reproducible",
        ]
    )
    assert rc == 0
    payload = json.loads(
        (out_dir / "compliance-report.json").read_text(encoding="utf-8")
    )
    std_ids = {s["id"] for s in payload["standards"]}
    assert std_ids == {
        "nist-ssdf", "iso-25010", "aicpa-soc2-tsc", "iso-27001"
    }
    assert len(payload["items"]) > 0


def test_standard_alias_ssdf_resolves_to_nist_ssdf(tmp_path):
    project = _make_project(tmp_path, with_verify_evidence=True)
    out_dir = tmp_path / "out"
    rc = cr.main(
        [
            "--project-root", str(project),
            "--standards-dir", str(STANDARDS_DIR),
            "--standard", "ssdf",
            "--format", "json",
            "--output-dir", str(out_dir),
            "--reproducible",
        ]
    )
    assert rc == 0
    payload = json.loads(
        (out_dir / "compliance-report.json").read_text(encoding="utf-8")
    )
    assert {s["id"] for s in payload["standards"]} == {"nist-ssdf"}


def test_standard_unknown_value_returns_two(tmp_path, capsys):
    project = _make_project(tmp_path)
    rc = cr.main(
        [
            "--project-root", str(project),
            "--standards-dir", str(STANDARDS_DIR),
            "--standard", "fedramp",  # not in our alias map
        ]
    )
    assert rc == 2
    captured = capsys.readouterr()
    assert "fedramp" in captured.err


# --- Standards discovery fallback ----------------------------------------


def test_standards_dir_missing_returns_two(tmp_path, capsys):
    project = tmp_path / "empty"
    project.mkdir()
    rc = cr.main(
        [
            "--project-root", str(project),
            # no --standards-dir; project has no standards/ dir
        ]
    )
    assert rc == 2
    captured = capsys.readouterr()
    assert "standards" in captured.err.lower()

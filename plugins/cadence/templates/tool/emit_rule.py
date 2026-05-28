#!/usr/bin/env python3
"""Cadence Layer-3 rule emitter — closes the retrospective loop.

Reads a single retrospective finding (JSON conforming to
``schemas/retro.schema.json``) on stdin or from ``--input``. For findings
with ``fix_layer == 3`` and ``auto_method == "boundary-rule"``, this
helper materializes a regression fixture under
``tests/fixtures/retro/<short-id>/`` containing:

    - the exact offending import as it appeared in source
    - a fixture ``.cadence/cadence.yaml`` with the proposed new boundary
      rule
    - (optionally, with ``--apply``) appends the same rule to the
      project's root ``.cadence/cadence.yaml``

It then invokes the existing ``tool/check_boundaries.py`` against the
fixture and refuses to record the finding as "landed" unless the
checker flags the offending line. A rule that does not fire on its own
trigger case is worse than no rule, so the emitter exits non-zero in
that case and prints a remediation hint.

Exit codes:
    0   fixture written, checker fires on the sample — finding may be
        recorded as landed
    1   rule does NOT fire on the sample — emitter refuses to mark as
        landed; the human must revise the rule
    2   bad input (schema invalid, missing required block, IO error)

Usage:
    python tool/emit_rule.py < finding.json
    python tool/emit_rule.py --input finding.json
    python tool/emit_rule.py --input finding.json --apply
    python tool/emit_rule.py --input finding.json --fixture-root tests/fixtures/retro

Design notes:
    - Only ``violation_sample.kind == "boundary-rule"`` is wired in
      v0.3.0-rc.1. Future emitters (lint-rule, schema-rule) will
      register additional kinds.
    - The emitter is deliberately deterministic: same input produces
      the same fixture path. By default it refuses to overwrite an
      existing fixture for the same finding ID; pass ``--force`` to
      rewrite (typically only useful while iterating).
    - The fixture is portable: each fixture directory ships its own
      ``.cadence/cadence.yaml`` with one rule, so the regression test
      isolates from the project's full ruleset.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print(
        "ERROR: PyYAML is required. Install with: pip install pyyaml",
        file=sys.stderr,
    )
    sys.exit(2)

try:
    from jsonschema import Draft202012Validator
except ImportError:
    print(
        "ERROR: jsonschema is required. Install with: pip install jsonschema",
        file=sys.stderr,
    )
    sys.exit(2)


_LANG_EXT: dict[str, str] = {
    "ts": "ts",
    "tsx": "tsx",
    "js": "js",
    "py": "py",
    "go": "go",
    "rs": "rs",
    "dart": "dart",
    "java": "java",
    "kt": "kt",
    "swift": "swift",
}


@dataclass(frozen=True)
class EmitResult:
    fixture_dir: Path
    sample_file: Path
    fixture_config: Path
    rule: dict[str, Any]
    fired: bool
    violation_count: int


def _short_id(finding: dict[str, Any]) -> str:
    raw = finding.get("id", "")
    try:
        return str(uuid.UUID(raw)).split("-")[0]
    except (ValueError, AttributeError, TypeError):
        slug = re.sub(r"[^a-z0-9]+", "-", finding.get("what_happened", "").lower())
        slug = slug.strip("-")[:24] or "anon"
        return slug


def _where_to_dir(where: str) -> Path:
    cleaned = where.replace("**", "").replace("*", "").strip("/")
    return Path(cleaned) if cleaned else Path("src")


def _sample_stub(language: str, import_line: str) -> str:
    """Return a syntactically inert source file containing ``import_line``.

    The checker only inspects import-shaped lines, so the surrounding
    file can be minimal. We do add a language-appropriate footer so the
    file isn't a single dangling import (some tools complain).
    """
    if language in ("ts", "tsx", "js"):
        return f"{import_line}\n\nexport const __retro_sample = true;\n"
    if language == "py":
        return f"{import_line}\n\n__retro_sample = True\n"
    if language == "go":
        return f"package retrosample\n\n{import_line}\n"
    if language == "rs":
        return f"{import_line}\n\npub const RETRO_SAMPLE: bool = true;\n"
    if language == "dart":
        return f"{import_line}\n\nconst retroSample = true;\n"
    if language in ("java", "kt"):
        return f"{import_line}\n\npublic class RetroSample {{ }}\n"
    if language == "swift":
        return f"{import_line}\n\nlet retroSample = true\n"
    return f"{import_line}\n"


def _load_checker(project_root: Path) -> Any:
    """Locate and import the project's check_boundaries.py.

    Search order:
        1. ``<project_root>/tool/check_boundaries.py`` (the canonical
           install path scaffolded by /cadence-init)
        2. ``<project_root>/plugins/cadence/templates/tool/check_boundaries.py``
           (the dogfood case — when running emit_rule.py inside the
           Cadence repo itself)
    """
    candidates = [
        project_root / "tool" / "check_boundaries.py",
        project_root / "plugins" / "cadence" / "templates" / "tool" / "check_boundaries.py",
    ]
    for path in candidates:
        if path.is_file():
            spec = importlib.util.spec_from_file_location(
                "_cadence_check_boundaries", path
            )
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules["_cadence_check_boundaries"] = module
            spec.loader.exec_module(module)
            return module
    raise FileNotFoundError(
        "Could not locate check_boundaries.py. Looked under tool/ and "
        "plugins/cadence/templates/tool/. Run /cadence-init first?"
    )


def _load_schema(project_root: Path) -> dict[str, Any]:
    candidates = [
        project_root / ".cadence" / "retro.schema.json",
        project_root / "plugins" / "cadence" / "schemas" / "retro.schema.json",
    ]
    for path in candidates:
        if path.is_file():
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
    raise FileNotFoundError(
        "Could not locate retro.schema.json. Looked under .cadence/ and "
        "plugins/cadence/schemas/."
    )


def _validate(finding: dict[str, Any], schema: dict[str, Any]) -> None:
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(finding), key=lambda e: list(e.absolute_path))
    if errors:
        print("ERROR: finding fails retro.schema.json validation:", file=sys.stderr)
        for err in errors:
            loc = ".".join(str(p) for p in err.absolute_path) or "<root>"
            print(f"  {loc}: {err.message}", file=sys.stderr)
        sys.exit(2)


def _append_rule_to_root_config(
    root_config: Path, rule: dict[str, Any]
) -> bool:
    """Append ``rule`` to the project's ``.cadence/cadence.yaml`` ``boundaries``
    list. Returns True if the rule was added; False if an equivalent
    rule (same where + forbidden) already exists.
    """
    if not root_config.is_file():
        print(
            f"WARN: --apply set but {root_config} does not exist; "
            "skipping root config update.",
            file=sys.stderr,
        )
        return False
    with root_config.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    if not isinstance(cfg, dict):
        print(
            f"WARN: {root_config} is not a YAML mapping; "
            "skipping root config update.",
            file=sys.stderr,
        )
        return False
    boundaries = cfg.setdefault("boundaries", [])
    for existing in boundaries:
        if (
            isinstance(existing, dict)
            and existing.get("where") == rule["where"]
            and existing.get("forbidden") == rule["forbidden"]
        ):
            return False
    boundaries.append(rule)
    with root_config.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(cfg, fh, sort_keys=False)
    return True


def emit(
    finding: dict[str, Any],
    project_root: Path,
    fixture_root: Path,
    apply_to_root: bool,
    force: bool,
) -> EmitResult:
    sample = finding["violation_sample"]
    short = _short_id(finding)
    fixture_dir = (fixture_root / short).resolve()

    if fixture_dir.exists() and not force:
        print(
            f"ERROR: fixture already exists at {fixture_dir}. "
            "Pass --force to overwrite.",
            file=sys.stderr,
        )
        sys.exit(2)
    fixture_dir.mkdir(parents=True, exist_ok=True)

    language = sample.get("language", "ts")
    ext = _LANG_EXT.get(language, language)
    rel_dir = _where_to_dir(sample["where"])
    sample_file = fixture_dir / rel_dir / f"sample.{ext}"
    sample_file.parent.mkdir(parents=True, exist_ok=True)
    sample_file.write_text(
        _sample_stub(language, sample["import_line"]), encoding="utf-8"
    )

    rule = {
        "where": sample["where"],
        "forbidden": [sample["forbidden_pattern"]],
        "reason": sample["reason"],
    }
    fixture_config = fixture_dir / ".cadence" / "cadence.yaml"
    fixture_config.parent.mkdir(parents=True, exist_ok=True)
    with fixture_config.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(
            {
                "commands": {"test": ["true"]},
                "boundaries": [rule],
            },
            fh,
            sort_keys=False,
        )

    checker = _load_checker(project_root)
    rules = checker._load_rules(fixture_config)
    violations = checker.find_violations(fixture_dir, rules)
    sample_rel = sample_file.relative_to(fixture_dir).as_posix()
    fired = any(v.path == sample_rel for v in violations)

    if fired and apply_to_root:
        root_config = project_root / ".cadence" / "cadence.yaml"
        added = _append_rule_to_root_config(root_config, rule)
        if added:
            print(f"Appended rule to {root_config}", file=sys.stderr)
        else:
            print(
                f"Equivalent rule already present in {root_config}; "
                "no change.",
                file=sys.stderr,
            )

    return EmitResult(
        fixture_dir=fixture_dir,
        sample_file=sample_file,
        fixture_config=fixture_config,
        rule=rule,
        fired=fired,
        violation_count=len(violations),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cadence Layer-3 rule emitter "
        "(retro fix → fixture + cadence.yaml + regression check).",
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="Path to JSON finding. If omitted, reads from stdin.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Project root (default: cwd). Used to find tool/check_boundaries.py.",
    )
    parser.add_argument(
        "--fixture-root",
        type=Path,
        default=None,
        help="Root directory for generated fixtures "
        "(default: <project-root>/tests/fixtures/retro).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Also append the new rule to the project's "
        ".cadence/cadence.yaml (idempotent).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing fixture for the same finding id.",
    )
    args = parser.parse_args(argv)

    project_root: Path = args.project_root.resolve()
    fixture_root: Path = (
        args.fixture_root.resolve()
        if args.fixture_root is not None
        else (project_root / "tests" / "fixtures" / "retro").resolve()
    )

    if args.input is not None:
        try:
            with args.input.open("r", encoding="utf-8") as fh:
                finding = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"ERROR: could not read {args.input}: {exc}", file=sys.stderr)
            return 2
    else:
        try:
            finding = json.load(sys.stdin)
        except json.JSONDecodeError as exc:
            print(f"ERROR: stdin is not valid JSON: {exc}", file=sys.stderr)
            return 2

    if not isinstance(finding, dict):
        print("ERROR: finding must be a JSON object", file=sys.stderr)
        return 2

    schema = _load_schema(project_root)
    _validate(finding, schema)

    if finding.get("fix_layer") != 3:
        print(
            f"ERROR: emit_rule.py only handles fix_layer=3 findings "
            f"(got {finding.get('fix_layer')!r}). "
            "Layer 1/2/4 fixes stay in FRAMEWORK_CHANGELOG.md only.",
            file=sys.stderr,
        )
        return 2
    if finding.get("auto_method") != "boundary-rule":
        print(
            f"ERROR: emit_rule.py v0.3.0-rc.1 only handles "
            f"auto_method='boundary-rule' (got {finding.get('auto_method')!r}). "
            "Future emitters will add lint-rule and schema-rule kinds.",
            file=sys.stderr,
        )
        return 2

    result = emit(
        finding=finding,
        project_root=project_root,
        fixture_root=fixture_root,
        apply_to_root=args.apply,
        force=args.force,
    )

    print(f"fixture:      {result.fixture_dir}")
    print(f"sample file:  {result.sample_file}")
    print(f"new rule:     {json.dumps(result.rule)}")
    print(f"violations:   {result.violation_count} (rule fired: {result.fired})")

    if not result.fired:
        print(
            "\nERROR: the new boundary rule did NOT flag the offending "
            "import. A rule that doesn't fire on its own trigger case is "
            "worse than no rule. Likely causes:\n"
            "  - 'where' glob doesn't match the sample file's path\n"
            "  - 'forbidden_pattern' doesn't catch the import_line "
            "(check word-boundary handling)\n"
            "  - import_line isn't import-shaped per the checker's "
            "_IMPORT_PREFIXES tuple\n"
            "Do NOT record this finding as landed until the rule fires.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

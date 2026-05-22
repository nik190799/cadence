"""Tests for templates/tool/check_boundaries.py.

Loads the checker module and runs it against the synthetic project
fixtures in tests/fixtures/. Each fixture has a known set of
deliberate violations; we assert that the checker finds them all and
nothing else.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKER_PATH = REPO_ROOT / 'plugins' / 'cadence' / 'templates' / 'tool' / 'check_boundaries.py'
FIXTURES_DIR = REPO_ROOT / 'tests' / 'fixtures'


def _load_checker():
    spec = importlib.util.spec_from_file_location('check_boundaries', CHECKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so @dataclass can resolve cls.__module__.
    sys.modules['check_boundaries'] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _run_on_fixture(name: str) -> tuple[int, list]:
    fixture = FIXTURES_DIR / name
    config = fixture / '.cadence' / 'cadence.yaml'
    rules = checker._load_rules(config)
    violations = checker.find_violations(fixture, rules)
    return len(violations), violations


# --- TypeScript fixture ---------------------------------------------------

def test_ts_fixture_finds_all_known_violations():
    count, violations = _run_on_fixture('ts-sample')
    # 2 violations in features/todos/controller.ts (sources + repositories)
    # 2 violations in data/sources/bad_leaf.ts (features + app)
    assert count == 4, [v.format() for v in violations]


def test_ts_fixture_clean_files_have_no_violations():
    _, violations = _run_on_fixture('ts-sample')
    bad_paths = {v.path for v in violations}
    assert 'src/features/todos/clean.ts' not in bad_paths
    assert 'src/data/sources/todoStore.ts' not in bad_paths
    assert 'src/app/providers.ts' not in bad_paths


def test_ts_violation_paths_are_correct():
    _, violations = _run_on_fixture('ts-sample')
    paths = sorted({v.path for v in violations})
    assert paths == [
        'src/data/sources/bad_leaf.ts',
        'src/features/todos/controller.ts',
    ]


def test_ts_violations_cite_correct_forbidden_pattern():
    _, violations = _run_on_fixture('ts-sample')
    features_violations = [v for v in violations if 'features/todos' in v.path]
    assert any(v.forbidden == 'src/data/sources/**' for v in features_violations)
    assert any(v.forbidden == 'src/data/repositories/**' for v in features_violations)


# --- Python fixture -------------------------------------------------------

def test_py_fixture_finds_all_known_violations():
    count, violations = _run_on_fixture('py-sample')
    # 2 violations in features/todos/controller.py (sources + repositories)
    # 2 violations in data/sources/bad_leaf.py (features + app)
    assert count == 4, [v.format() for v in violations]


def test_py_fixture_clean_files_have_no_violations():
    _, violations = _run_on_fixture('py-sample')
    bad_paths = {v.path for v in violations}
    assert 'src/myapp/features/todos/clean.py' not in bad_paths
    assert 'src/myapp/data/sources/todo_store.py' not in bad_paths
    assert 'src/myapp/app/providers.py' not in bad_paths


def test_py_violations_detect_both_from_and_import_forms():
    _, violations = _run_on_fixture('py-sample')
    features_lines = [
        v.line for v in violations if 'features/todos' in v.path
    ]
    assert any(line.startswith('from ') for line in features_lines)
    assert any(line.startswith('import ') for line in features_lines)


# --- Helper behaviour -----------------------------------------------------

def test_is_import_line_recognises_common_forms():
    assert checker._is_import_line("import 'package:foo/bar.dart';")
    assert checker._is_import_line('import { x } from "y";')
    assert checker._is_import_line('from a.b import c')
    assert checker._is_import_line('import a.b.c')
    assert checker._is_import_line('use crate::foo;')
    assert checker._is_import_line('#include <stdio.h>')
    assert checker._is_import_line('const x = require("y")')
    assert checker._is_import_line('export * from "./x";')

    assert not checker._is_import_line('// import x from y')
    assert not checker._is_import_line('importance = 5')
    assert not checker._is_import_line('def something():')


def test_line_contains_token_respects_word_boundary():
    # Spurious substring: 'app.' inside 'myapp.' must NOT match.
    assert not checker._line_contains_token(
        'from src.myapp.features.x import y', 'app.'
    )
    # Real match: 'app.' as a top-level segment.
    assert checker._line_contains_token(
        'from src.app.providers import x', 'app.'
    )
    # Path form: relative import.
    assert checker._line_contains_token(
        "import { x } from '../../features/foo';", 'features/'
    )
    # Identifier collision: 'features/' must not match 'badfeatures/'.
    assert not checker._line_contains_token(
        "import x from './badfeatures/y';", 'features/'
    )


def test_forbidden_tokens_generates_multilang_suffixes():
    tokens = checker._forbidden_tokens('src/data/sources/**')
    assert 'src/data/sources/' in tokens
    assert 'data/sources/' in tokens
    assert 'sources/' in tokens
    assert 'src.data.sources.' in tokens
    assert 'data.sources.' in tokens
    assert 'src::data::sources::' in tokens

    # Single-segment patterns produce one set (path/dot/colon forms).
    single = checker._forbidden_tokens('foo/**')
    assert single == ['foo/', 'foo.', 'foo::']

    # No prefix at all → no tokens.
    assert checker._forbidden_tokens('*.py') == []


def test_empty_boundaries_returns_zero_violations(tmp_path):
    cfg = tmp_path / '.cadence' / 'cadence.yaml'
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        'commands:\n  test: ["true"]\nboundaries: []\n',
        encoding='utf-8',
    )
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src' / 'foo.py').write_text(
        'import os\nfrom anywhere import anything\n',
        encoding='utf-8',
    )
    rules = checker._load_rules(cfg)
    assert rules == []
    assert checker.find_violations(tmp_path, rules) == []


def test_missing_config_exits_two(tmp_path):
    with pytest.raises(SystemExit) as exc_info:
        checker._load_rules(tmp_path / 'nope.yaml')
    assert exc_info.value.code == 2


def test_main_returns_zero_when_clean(tmp_path):
    cfg = tmp_path / '.cadence' / 'cadence.yaml'
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        'commands:\n  test: ["true"]\nboundaries:\n'
        '  - where: "src/**"\n'
        '    forbidden: ["zzz_nonexistent/**"]\n'
        '    reason: "test rule"\n',
        encoding='utf-8',
    )
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src' / 'ok.py').write_text(
        'import os\n', encoding='utf-8',
    )
    rc = checker.main(['--root', str(tmp_path), '--quiet'])
    assert rc == 0


def test_main_returns_one_when_violated(tmp_path, capsys):
    cfg = tmp_path / '.cadence' / 'cadence.yaml'
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        'commands:\n  test: ["true"]\nboundaries:\n'
        '  - where: "src/features/**"\n'
        '    forbidden: ["src/data/sources/**"]\n'
        '    reason: "no direct"\n',
        encoding='utf-8',
    )
    (tmp_path / 'src' / 'features').mkdir(parents=True)
    (tmp_path / 'src' / 'features' / 'bad.py').write_text(
        'from src.data.sources.x import y\n',
        encoding='utf-8',
    )
    rc = checker.main(['--root', str(tmp_path)])
    assert rc == 1
    captured = capsys.readouterr()
    assert 'violations' in captured.err.lower()
    assert 'src/features/bad.py' in captured.err

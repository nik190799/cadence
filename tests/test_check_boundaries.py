"""Tests for templates/tool/check_boundaries.py.

Loads the checker module and runs it against the synthetic project
fixtures in tests/fixtures/. Each fixture has a known set of
deliberate violations; we assert that the checker finds them all and
nothing else.
"""

from __future__ import annotations

import hashlib
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


# --- Rule ids, relative skip dirs, the retro-fixture skip, paths= ---------

_RULE_YAML = (
    'commands:\n  test: ["true"]\nboundaries:\n'
    '  - where: "src/features/**"\n'
    '    forbidden: ["src/data/sources/**"]\n'
    '    reason: "features go through repositories"\n'
)
_BAD_LINE = 'from src.data.sources.x import y\n'


def _project(root: Path, rules: str = _RULE_YAML) -> Path:
    cfg = root / '.cadence' / 'cadence.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(rules, encoding='utf-8')
    return cfg


def _put(root: Path, rel: str, text: str = _BAD_LINE) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def test_skip_dirs_match_relative_parts_only(tmp_path):
    # The project itself lives under a directory named build/: before, the
    # absolute path matched _SKIP_DIRS and nothing was ever scanned.
    root = tmp_path / 'build' / 'proj'
    cfg = _project(root)
    _put(root, 'src/features/bad.py')
    _put(root, 'src/features/node_modules/dep.py')  # still skipped
    _put(root, 'src/features/build/gen.py')  # skipped: relative part
    violations = checker.find_violations(root, checker._load_rules(cfg))
    assert [v.path for v in violations] == ['src/features/bad.py']


def test_retro_fixtures_are_skipped_from_the_project_root(tmp_path):
    cfg = _project(tmp_path)
    fixture = tmp_path / 'tests' / 'fixtures' / 'retro' / 'abcd1234'
    _put(fixture, 'src/features/sample.py')
    _project(fixture)
    _put(tmp_path, 'src/features/real.py')
    violations = checker.find_violations(tmp_path, checker._load_rules(cfg))
    assert [v.path for v in violations] == ['src/features/real.py']
    # The fixture still fires when it is the root (emit_rule --replay).
    inside = checker.find_violations(fixture, checker._load_rules(fixture / '.cadence' / 'cadence.yaml'))
    assert [v.path for v in inside] == ['src/features/sample.py']


def test_paths_limits_the_scan(tmp_path):
    cfg = _project(tmp_path)
    for rel in ('src/features/a.py', 'src/features/b.py', 'src/features/node_modules/c.py'):
        _put(tmp_path, rel)
    _put(tmp_path, 'tests/fixtures/retro/abcd1234/src/features/d.py')
    rules = checker._load_rules(cfg)
    found = checker.find_violations(
        tmp_path,
        rules,
        paths=[
            'src/features/b.py',
            'src/features/b.py',  # duplicates are scanned once
            'src/features/node_modules/c.py',
            'tests/fixtures/retro/abcd1234/src/features/d.py',
            '../outside.py',
            'src/features/missing.py',
            'src/features/notes.txt',
        ],
    )
    assert [v.path for v in found] == ['src/features/b.py']
    assert checker.find_violations(tmp_path, rules, paths=[]) == []


def test_symlinks_are_not_followed(tmp_path):
    cfg = _project(tmp_path)
    outside = tmp_path.parent / f'{tmp_path.name}-outside.py'
    outside.write_text(_BAD_LINE, encoding='utf-8')
    (tmp_path / 'src' / 'features').mkdir(parents=True)
    try:
        (tmp_path / 'src' / 'features' / 'link.py').symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip('symlinks are not available here')
    rules = checker._load_rules(cfg)
    assert checker.find_violations(tmp_path, rules) == []
    assert checker.find_violations(tmp_path, rules, paths=['src/features/link.py']) == []


def test_rule_ids_computed_and_explicit(tmp_path):
    cfg = _project(
        tmp_path,
        'commands:\n  test: ["true"]\nboundaries:\n'
        '  - where: "src/features/**"\n'
        '    forbidden: ["src/data/sources/**"]\n'
        '    reason: "seed rule"\n'
        '  - id: L-0a1b2c3d\n'
        '    where: "src/app/**"\n'
        '    forbidden: ["src/data/sources/**", "src/data/remote/**"]\n'
        '    reason: "learned rule"\n',
    )
    seed, learned = checker._load_rules(cfg)
    expected = 'B-' + hashlib.sha256(b'src/features/**|src/data/sources/**').hexdigest()[:8]
    assert seed.id == expected
    assert checker.seed_rule_id('src/features/**', ['src/data/sources/**']) == expected
    assert learned.id == 'L-0a1b2c3d'
    _put(tmp_path, 'src/features/x.py')
    _put(tmp_path, 'src/app/y.py', 'from src.data.remote.z import w\n')
    found = {v.path: v.rule_id for v in checker.find_violations(tmp_path, [seed, learned])}
    assert found == {'src/features/x.py': expected, 'src/app/y.py': 'L-0a1b2c3d'}


@pytest.mark.parametrize('bad_id', ['L-123', 'X-0a1b2c3d', 'L-0A1B2C3D', '12', 'L-0a1b2c3d\n'])
def test_invalid_rule_id_exits_two(tmp_path, capsys, bad_id):
    cfg = _project(
        tmp_path,
        'commands:\n  test: ["true"]\nboundaries:\n'
        f'  - id: "{bad_id}"\n'
        '    where: "src/**"\n'
        '    forbidden: ["x/**"]\n'
        '    reason: "bad id"\n',
    )
    with pytest.raises(SystemExit) as exc_info:
        checker._load_rules(cfg)
    assert exc_info.value.code == 2
    assert 'id must match' in capsys.readouterr().err
    with pytest.raises(checker.ConfigError):
        checker.load_rules(cfg)


def test_old_positional_rule_and_violation_still_work():
    rule = checker.Rule('src/**', ('x/**',), 'reason here')
    assert rule.id == ''
    violation = checker.Violation('a.py', 1, 'import x', 'x/**', 'reason')
    assert violation.rule_id == ''

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


# --- Directory-index imports: resolved targets ------------------------------

_DB_RULE_YAML = (
    'commands:\n  test: ["true"]\nboundaries:\n'
    '  - where: "src/http/**"\n'
    '    forbidden: ["src/db/**"]\n'
    '    reason: "http goes through the domain"\n'
)


def _db_hits(root: Path, rel: str, line: str) -> list:
    cfg = _project(root, _DB_RULE_YAML)
    _put(root, rel, line + '\n')
    return checker.find_violations(root, checker._load_rules(cfg))


@pytest.mark.parametrize(
    'rel,line',
    [
        ('src/http/handler.ts', "import { db } from '../db';"),
        ('src/http/handler.ts', 'import { db } from "../db/index";'),
        ('src/http/handler.ts', "import { db } from '../db/index.js';"),
        ('src/http/handler.ts', "import { db } from '../db/';"),
        ('src/http/handler.ts', 'export * from "../db";'),
        ('src/http/handler.ts', "export { db } from '../db';"),
        ('src/http/handler.ts', 'const db = require("../db");'),
        ('src/http/routes/users.tsx', "import db from '../../db';"),
        ('src/http/handler.js', "import db from '../db';"),
        ('src/http/handler.mjs', "import db from '../db';"),
        ('src/http/handler.cjs', 'const db = require("../db");'),
        ('src/http/handler.py', 'from ..db import x'),
        ('src/http/handler.py', 'from src.db import x'),
        ('src/http/handler.py', 'import src.db'),
        ('src/http/handler.py', 'import os, src.db as database'),
        ('src/http/handler.py', 'from .. import db'),
        ('src/http/handler.py', 'from src import db'),
        ('src/http/__init__.py', 'from .. import db'),
    ],
)
def test_a_directory_index_import_fires(tmp_path, rel, line):
    found = _db_hits(tmp_path, rel, line)
    assert [(v.path, v.line_no, v.line, v.forbidden) for v in found] == [
        (rel, 1, line, 'src/db/**')
    ]


@pytest.mark.parametrize(
    'line',
    [
        "import { db } from '../db';",
        'export * from "../db";',
        'const db = require("../db");',
    ],
)
def test_the_token_match_alone_misses_a_directory_index_import(line):
    # The reason resolution exists: no token of src/db/** is in the line.
    tokens = checker._forbidden_tokens('src/db/**')
    assert not any(checker._line_contains_token(line, t) for t in tokens)
    assert checker.import_targets('src/http/a.ts', line) == ['src/db']


@pytest.mark.parametrize(
    'rel,line',
    [
        ('src/http/handler.ts', "import { u } from '../dbutils';"),
        ('src/http/handler.ts', "import { u } from '../db-utils';"),
        ('src/http/handler.ts', "import { db } from './db';"),
        ('src/http/handler.ts', "import { db } from '../../../db';"),
        ('src/http/handler.ts', "import { db } from '../../../../src/db';"),
        ('src/http/handler.ts', "// import { db } from '../db';"),
        ('src/http/handler.ts', "/* import { db } from '../db'; */"),
        ('src/http/handler.ts', "import { db } from '@/db';"),
        ('src/http/handler.ts', "import { db } from 'db';"),
        ('src/http/handler.ts', "import { up } from '..';"),
        ('src/http/handler.ts', "const label = 'from ../db';"),
        ('src/http/handler.py', 'from dbutils import x'),
        ('src/http/handler.py', 'from db import x'),
        ('src/http/handler.py', 'from .db import x'),
        ('src/http/handler.py', 'from . import db'),
        ('src/http/handler.py', 'from .... import db'),
        ('src/http/handler.py', '# from ..db import x'),
        ('src/http/handler.go', 'import "../db"'),
    ],
)
def test_lookalikes_and_unresolved_imports_do_not_fire(tmp_path, rel, line):
    assert _db_hits(tmp_path, rel, line) == []


def test_a_line_with_a_token_and_a_target_is_one_violation(tmp_path):
    line = "import { a } from '../db/a';"
    found = _db_hits(tmp_path, 'src/http/handler.ts', line)
    assert [(v.line, v.forbidden) for v in found] == [(line, 'src/db/**')]


def test_two_forbidden_patterns_each_fire_once_per_line(tmp_path):
    cfg = _project(
        tmp_path,
        'commands:\n  test: ["true"]\nboundaries:\n'
        '  - where: "src/http/**"\n'
        '    forbidden: ["src/db/**", "src/db/index*"]\n'
        '    reason: "http goes through the domain"\n',
    )
    _put(tmp_path, 'src/http/a.ts', "import { db } from '../db/index';\nimport { c } from '../db';\n")
    found = checker.find_violations(tmp_path, checker._load_rules(cfg))
    assert [(v.line_no, v.forbidden) for v in found] == [
        (1, 'src/db/**'),
        (1, 'src/db/index*'),
        (2, 'src/db/**'),
    ]


def test_where_still_limits_a_resolved_import(tmp_path):
    cfg = _project(tmp_path, _DB_RULE_YAML)
    _put(tmp_path, 'src/domain/a.ts', "import { db } from '../db';\n")
    _put(tmp_path, 'src/http/b.ts', "import { db } from '../db';\n")
    rules = checker._load_rules(cfg)
    assert [v.path for v in checker.find_violations(tmp_path, rules)] == ['src/http/b.ts']
    found = checker.find_violations(tmp_path, rules, paths=['src/http/b.ts', 'src/domain/a.ts'])
    assert [(v.path, v.line) for v in found] == [('src/http/b.ts', "import { db } from '../db';")]


def test_a_glob_only_pattern_fires_on_a_resolved_target(tmp_path):
    # No literal prefix means no tokens; the resolved target still matches.
    cfg = _project(
        tmp_path,
        'commands:\n  test: ["true"]\nboundaries:\n'
        '  - where: "src/http/**"\n'
        '    forbidden: ["**/db/**"]\n'
        '    reason: "no db anywhere"\n',
    )
    assert checker._forbidden_tokens('**/db/**') == []
    _put(tmp_path, 'src/http/a.ts', "import { db } from '../db';\nimport { u } from '../dbutils';\n")
    found = checker.find_violations(tmp_path, checker._load_rules(cfg))
    assert [(v.line_no, v.forbidden) for v in found] == [(1, '**/db/**')]


def test_main_reports_a_directory_index_import(tmp_path, capsys):
    _project(tmp_path, _DB_RULE_YAML)
    _put(tmp_path, 'src/http/server.ts', "import { query } from '../db';\n")
    assert checker.main(['--root', str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert 'src/http/server.ts:1' in err and 'forbidden: src/db/**' in err


@pytest.mark.parametrize(
    'rel,line,expected',
    [
        ('src/http/a.ts', "import { db } from '../db';", ['src/db']),
        ('src/http/a.ts', "import a from './x'; import b from '../y/z';", ['src/http/x', 'src/y/z']),
        ('src/http/a.ts', "import a from '../db'; import b from '../db';", ['src/db']),
        ('src/http/a.ts', "import { up } from '.';", ['src/http']),
        ('src/http/a.ts', "import { up } from '..';", ['src']),
        ('src/http/a.ts', "import { up } from '../..';", []),
        ('src/http/a.ts', "import x from 'lodash';", []),
        ('src/http/a.ts', "import x from '@/db';", []),
        ('src/http/a.ts', "import x from '/abs/db';", []),
        ('a.ts', "import x from '../db';", []),
        (
            'src/http/a.py',
            'from src.db.models import (User, Order)  # noqa',
            ['src/db/models', 'src/db/models/User', 'src/db/models/Order'],
        ),
        ('src/http/a.py', 'from src.db import *', ['src/db']),
        ('src/http/a.py', 'from ..db.client import connect as c', ['src/db/client', 'src/db/client/connect']),
        ('src/http/a.py', 'from . import routes', ['src/http/routes']),
        ('src/http/a.py', 'from .. import db, cache', ['src/db', 'src/cache']),
        ('src/http/a.py', 'from ... import db', ['db']),
        ('src/http/a.py', 'from .... import db', []),
        ('a.py', 'from . import db', ['db']),
        ('a.py', 'from .. import db', []),
        ('src/http/a.py', 'import src.db.client as c, os', ['src/db/client', 'os']),
        ('src/http/a.py', 'import (bad)', []),
        ('src/http/a.dart', "import '../db/a.dart';", []),
        ('src/http/a.go', 'import "../db"', []),
        ('src/http/README.md', "import x from '../db';", []),
    ],
)
def test_import_targets(rel, line, expected):
    assert checker.import_targets(rel, line) == expected


@pytest.mark.parametrize(
    'target,pattern,expected',
    [
        ('src/db', 'src/db/**', True),
        ('src/db', 'src/db/*', True),
        ('src/db/index', 'src/db/**', True),
        ('src/db/a/b.js', 'src/db/**', True),
        ('src/db', 'src/db', True),
        ('src/a/db', 'src/*/db/**', True),
        ('src/dbutils', 'src/db/**', False),
        ('src/db-utils', 'src/db/**', False),
        ('src/db.json', 'src/db/**', False),
        ('src/http/db', 'src/db/**', False),
        ('src', 'src/db/**', False),
        ('SRC/DB', 'src/db/**', False),
        ('', 'src/db/**', False),
        ('.', '**', False),
        ('..', '**', False),
        ('../db', '**', False),
        ('/src/db', '**', False),
        ('src/db/', 'src/db/**', False),
        ('src/db', '', False),
        ('src/db', '/**', False),
    ],
)
def test_target_matches(target, pattern, expected):
    assert checker._target_matches(target, pattern) is expected

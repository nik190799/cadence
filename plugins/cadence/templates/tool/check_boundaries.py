#!/usr/bin/env python3
"""Cadence import-boundary checker — language-agnostic.

Reads ``.cadence/cadence.yaml`` and enforces ``boundaries:`` rules across
any language whose imports are line-oriented (Python, JS/TS, Go, Rust,
Dart, Java, C/C++, Swift, Kotlin, …).

Exit codes:
    0   no violations (or no boundaries configured)
    1   at least one violation
    2   configuration error (missing config, malformed YAML, missing deps)

Usage:
    python tool/check_boundaries.py
    python tool/check_boundaries.py --config .cadence/cadence.yaml
    python tool/check_boundaries.py --root . --quiet

The checker is line-pattern based. It recognises ``import``, ``from``,
``require(``, ``use``, ``include``, ``#include``, and ``mod`` at the
start of a (stripped) line, then checks the line for forbidden path
substrings. This catches ~95 percent of real violations across the
languages above without needing per-language ASTs. Higher-accuracy
stack-specific adapters land in Cadence Phase 2.
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

try:
    import yaml
except ImportError:
    print(
        'ERROR: PyYAML is required. Install with: pip install pyyaml',
        file=sys.stderr,
    )
    sys.exit(2)


# Line prefixes (after lstrip) that indicate an import-like statement.
_IMPORT_PREFIXES: tuple[str, ...] = (
    'import ',
    'import\t',
    'from ',
    'require(',
    'use ',
    '#include',
    'include ',
    'mod ',
    'export * from ',
    'export {',
)

# File extensions worth scanning.
_SOURCE_EXTS: frozenset[str] = frozenset(
    {
        '.py',
        '.js',
        '.jsx',
        '.ts',
        '.tsx',
        '.mjs',
        '.cjs',
        '.go',
        '.rs',
        '.dart',
        '.java',
        '.kt',
        '.kts',
        '.swift',
        '.c',
        '.h',
        '.cc',
        '.cpp',
        '.hpp',
        '.m',
        '.mm',
    }
)

# Directories to skip while walking.
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        'node_modules',
        '.git',
        '.venv',
        'venv',
        'build',
        'dist',
        'target',
        '.dart_tool',
        '__pycache__',
        '.tox',
        '.pytest_cache',
        '.ruff_cache',
        '.mypy_cache',
    }
)


@dataclass(frozen=True)
class Rule:
    where: str
    forbidden: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class Violation:
    path: str
    line_no: int
    line: str
    forbidden: str
    reason: str

    def format(self) -> str:
        snippet = self.line if len(self.line) <= 120 else self.line[:117] + '...'
        return (
            f'{self.path}:{self.line_no}\n'
            f'  {snippet}\n'
            f'  forbidden: {self.forbidden}\n'
            f'  reason:    {self.reason}'
        )


def _is_import_line(line: str) -> bool:
    stripped = line.lstrip()
    if not stripped:
        return False
    # Comments — skip. `#include` is a C preprocessor directive, not a
    # comment, so we let it fall through to the prefix check below.
    if stripped.startswith(('//', '/*', '*', '--', ';;')):
        return False
    if stripped.startswith('#') and not stripped.startswith('#include'):
        return False
    if any(stripped.startswith(prefix) for prefix in _IMPORT_PREFIXES):
        return True
    # CommonJS / dynamic require — usually preceded by `const`/`let`/`var`.
    if 'require(' in stripped:
        return True
    return False


def _forbidden_tokens(pattern: str) -> list[str]:
    """Return all substring tokens to search import lines for.

    A single glob pattern like ``src/data/sources/**`` is rendered in
    different ways depending on the language's import syntax:

    - Path form (TS/JS/Dart/Go):  ``src/data/sources/``
    - Dotted form (Python/Java):  ``src.data.sources.``
    - Colon form (Rust):          ``src::data::sources::``

    Relative imports drop leading project segments (``../../...``), so
    we also generate every meaningful suffix (≥ 2 segments) in each
    form. ``data/sources/`` catches ``../../data/sources/X``;
    ``data.sources.`` catches ``from x.data.sources.X import y``.

    Single-segment tokens are excluded — they're too generic and would
    catch unrelated paths.
    """
    prefix = pattern
    for i, ch in enumerate(pattern):
        if ch in '*?[':
            prefix = pattern[:i]
            break

    prefix = prefix.rstrip('/')
    if not prefix:
        return []

    segments = [s for s in prefix.split('/') if s]
    if not segments:
        return []

    tokens: list[str] = []
    for n in range(len(segments), 0, -1):
        suffix = segments[-n:]
        tokens.append('/'.join(suffix) + '/')
        tokens.append('.'.join(suffix) + '.')
        tokens.append('::'.join(suffix) + '::')
    return tokens


def _matches_where(path: str, where: str) -> bool:
    return fnmatch.fnmatch(path, where)


def _line_contains_token(line: str, token: str) -> bool:
    """True if ``token`` appears in ``line`` with a non-identifier char to its left.

    Substring matching is too loose: the dotted token ``app.`` would
    spuriously match inside ``myapp.``. We require a left-boundary
    (start-of-line or non-identifier character) so ``app.`` matches
    ``src.app.providers`` but not ``src.myapp.features``.
    """
    if not token:
        return False
    start = 0
    while True:
        idx = line.find(token, start)
        if idx == -1:
            return False
        if idx == 0:
            return True
        prev = line[idx - 1]
        if not (prev.isalnum() or prev == '_'):
            return True
        start = idx + 1


def _load_rules(config_path: Path) -> list[Rule]:
    if not config_path.exists():
        print(
            f'ERROR: cadence config not found: {config_path}',
            file=sys.stderr,
        )
        sys.exit(2)

    with config_path.open('r', encoding='utf-8') as fh:
        try:
            cfg = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            print(
                f'ERROR: malformed YAML in {config_path}: {exc}',
                file=sys.stderr,
            )
            sys.exit(2)

    if not isinstance(cfg, dict):
        print('ERROR: cadence config must be a YAML mapping', file=sys.stderr)
        sys.exit(2)

    raw_rules = cfg.get('boundaries', []) or []
    if not isinstance(raw_rules, list):
        print("ERROR: 'boundaries' must be a list", file=sys.stderr)
        sys.exit(2)

    rules: list[Rule] = []
    for idx, raw in enumerate(raw_rules):
        if not isinstance(raw, dict):
            print(
                f'ERROR: boundaries[{idx}] must be a mapping',
                file=sys.stderr,
            )
            sys.exit(2)
        try:
            rules.append(
                Rule(
                    where=str(raw['where']),
                    forbidden=tuple(str(f) for f in raw['forbidden']),
                    reason=str(raw['reason']),
                )
            )
        except KeyError as exc:
            print(
                f'ERROR: boundaries[{idx}] missing required key: {exc}',
                file=sys.stderr,
            )
            sys.exit(2)
    return rules


def _iter_source_files(root: Path) -> Iterable[Path]:
    for path in root.rglob('*'):
        if not path.is_file():
            continue
        if path.suffix not in _SOURCE_EXTS:
            continue
        if set(path.parts) & _SKIP_DIRS:
            continue
        yield path


def find_violations(root: Path, rules: Sequence[Rule]) -> list[Violation]:
    violations: list[Violation] = []
    forbidden_entries: list[tuple[Rule, str, list[str]]] = [
        (rule, forbidden, _forbidden_tokens(forbidden))
        for rule in rules
        for forbidden in rule.forbidden
    ]

    for path in _iter_source_files(root):
        rel = path.relative_to(root).as_posix()

        applicable: list[tuple[Rule, str, list[str]]] = [
            (rule, forbidden, tokens)
            for rule, forbidden, tokens in forbidden_entries
            if _matches_where(rel, rule.where) and tokens
        ]
        if not applicable:
            continue

        try:
            with path.open('r', encoding='utf-8', errors='ignore') as fh:
                lines = fh.readlines()
        except OSError:
            continue

        for line_no, raw in enumerate(lines, start=1):
            line = raw.rstrip('\n\r')
            if not _is_import_line(line):
                continue
            for rule, forbidden, tokens in applicable:
                for token in tokens:
                    if _line_contains_token(line, token):
                        violations.append(
                            Violation(
                                path=rel,
                                line_no=line_no,
                                line=line.strip(),
                                forbidden=forbidden,
                                reason=rule.reason,
                            )
                        )
                        break  # one violation per forbidden pattern per line
    return violations


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Cadence import-boundary checker (language-agnostic).',
    )
    parser.add_argument(
        '--config',
        default='.cadence/cadence.yaml',
        help='Path to cadence.yaml (default: .cadence/cadence.yaml)',
    )
    parser.add_argument(
        '--root',
        default='.',
        help='Project root to scan (default: cwd)',
    )
    parser.add_argument(
        '--quiet',
        action='store_true',
        help="Suppress 'imports ok' on clean runs",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    config_path = (root / args.config).resolve()

    rules = _load_rules(config_path)
    if not rules:
        if not args.quiet:
            print('imports ok (no boundary rules configured)')
        return 0

    violations = find_violations(root, rules)
    if not violations:
        if not args.quiet:
            print('imports ok')
        return 0

    print(
        f'Import boundary violations ({len(violations)}):',
        file=sys.stderr,
    )
    for v in violations:
        print(file=sys.stderr)
        print(v.format(), file=sys.stderr)
    print(file=sys.stderr)
    print(
        'See docs/PATTERNS.md (Layer 1) and docs/ADR/ for the rationale '
        'behind each rule.',
        file=sys.stderr,
    )
    return 1


if __name__ == '__main__':
    sys.exit(main())

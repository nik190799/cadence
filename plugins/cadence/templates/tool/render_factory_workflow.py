#!/usr/bin/env python3
"""Render the Cadence factory workflow for one repository.

The factory workflow template (``templates/.github/workflows/
cadence-factory.yml.tmpl``) leaves five slots for the project's own runtime
setup, each marked "Add your stack's runtime setup here": the ``agent`` and
``agent-retry`` jobs (so the agent can run the tests), ``verify`` and
``verify-retry`` (the Definition of Done gate) and ``retro-plan`` (which
runs ``scripts/verify.sh`` on a learned check). Filling them by hand was
most of an hour per repository and easy to get subtly wrong. This tool
fills them from a small declarative stack profile, applies the rules every
slot needs, checks the result against the workflow's security design, and
writes it only when every check passes.

Contract:
    render_factory_workflow.py [--template PATH] [--out PATH]
        (--profile PATH | STACK FLAGS) [--save-profile PATH]
        [--repo-root DIR]

    --template   the workflow template. Default: the plugin's own copy,
                 ``../.github/workflows/cadence-factory.yml.tmpl`` next to
                 this file.
    --out        where to write the workflow. Default
                 ``.github/workflows/cadence-factory.yml``; ``-`` prints it.
    --profile    a stack profile, YAML or JSON (PROFILE below).
    STACK FLAGS  the same profile from the command line, without custom
                 steps: --python VERSION | --python-version-file PATH,
                 --requirements PATH (repeat), --editable PATH (repeat),
                 --node VERSION | --node-version-file PATH, --node-dir DIR
                 (repeat; '.' is the repository root), --lockfile PATH
                 (repeat, one per --node-dir), --package-manager
                 {npm,pnpm,yarn}, --node-install COMMAND, --no-node-cache.
    --save-profile  also write the profile used, normalized, as YAML (the
                 setup skill commits it as .cadence/factory-stack.yaml so a
                 later render is reproducible).
    --repo-root  the repository the paths refer to (default: the current
                 directory); a missing file is a warning, never an error.

    Prints a summary: the steps put in each slot, the actions they use and
    the checks that passed. Exit 0 when the workflow was written, 2 on any
    refusal or bad input (nothing is written then).

PROFILE:
    schema: 1                      # optional; 1 is the only version
    python:
      version: "3.12"              # quoted; or version_file: .python-version
      requirements: [server/requirements-dev.txt]   # pip install -r, each
      editable: [server]           # pip install -e, each ('.' allowed)
    node:
      version: "20"                # or version_file: web/.nvmrc
      dirs: [web]                  # package directories; '.' = the root
      lockfiles: [web/package-lock.json]   # default: <dir>/<lockfile>
      package_manager: npm         # npm (default), pnpm or yarn
      install: npm ci              # optional; default per package manager
      cache: true                  # npm only: setup-node's npm cache
    custom:                        # the escape hatch, after the above
      - name: Set up Go
        uses: actions/setup-go@<40-character commit SHA>
        tag: v5                    # the tag that SHA is (YAML drops comments)
        with: {go-version-file: go.mod}
        slots: [agent, agent-retry, verify, verify-retry, retro-plan]
      - name: Build the shared package
        run: make -C shared build
        working-directory: .
    pins:                          # optional: another pin for an action the
      actions/setup-node: {sha: <40 hex>, tag: v4}   # template does not use

What the tool does to every step, so the profile never has to:
    - names it ``Stack: ...``, so a later render (and a reviewer) can tell
      the rendered steps from the template's;
    - in verify and verify-retry, gives it ``if: steps.apply.outputs.ok ==
      'true'`` (joined with the step's own ``if``), so nothing runs once
      the gate has failed on an empty or refused patch, and puts the same
      steps in both jobs, byte for byte;
    - in retro-plan, which checks the repository out under ``repo/``,
      points it there (``working-directory``, requirement and lockfile
      paths, ``*-version-file`` and ``cache-dependency-path``) and runs it
      only when the ladder plan changed (``if: steps.plan.outputs.changed
      == 'true'``);
    - when the project's Python is not the template's (3.12), also installs
      the factory tools' PyYAML and jsonschema into it, since
      ``scripts/verify.sh`` and the learning tools run on whichever
      ``python`` is set up last.

What it refuses (exit 2, nothing written):
    - a ``uses:`` that is not ``owner/repo@<40-character commit SHA>`` with
      a ``tag`` (a moved tag would change the code that runs), a second SHA
      for an action the workflow already pins, a local or docker action;
    - actions that would undo the job split, in any letter case and with
      any sub-path: checkout (a second checkout would replace the tree the
      gate tests), up/download-artifact (the factory's own data channel
      between jobs), create-github-app-token and claude-code-action; one
      action spelled two ways (GitHub reads them as one);
    - ``${{ }}`` anywhere in a custom step, ``secrets.``, ``github.token``,
      ``continue-on-error`` (only verify.sh's own step may fail without
      failing the job), a status function (always(), failure(), ...) in an
      ``if``, an ``if`` with unbalanced parentheses or quotes (it could
      close the guard's parenthesis: ``false) || (true``), a step that
      names scripts/verify.sh, an ``id`` the job already uses, an unknown
      key;
    - a slot list that splits a pair (agent and agent-retry, verify and
      verify-retry run the same setup);
    - a path that is absolute, holds ``..`` or anything but
      ``[A-Za-z0-9_.-]`` segments, and an unquoted YAML number as a version
      (``3.10`` reads as 3.1);
    - a template that is already rendered, or whose five slots moved.

    After rendering it parses the result and checks: every job but the
    "Stack:" steps is the template's, unchanged; the steps sit in their
    slots; every ``uses:`` line is pinned to a SHA with a tag comment and
    each action has one SHA; the verify guard and the retro-plan guard are
    each the whole ``if`` or ``guard && (X)`` with X balanced, and the
    ``repo/`` prefix is in place.

Stdlib + PyYAML.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only without PyYAML
    sys.stderr.write(
        "render_factory_workflow.py needs PyYAML: python -m pip install 'pyyaml>=6,<7'\n"
    )
    raise SystemExit(2)


SLOTS = ("agent", "agent-retry", "verify", "verify-retry", "retro-plan")
PAIRS = (("agent", "agent-retry"), ("verify", "verify-retry"))
SLOT_MARKER = "# Add your stack's runtime setup here"
RENDERED_MARK = "# Rendered by tool/render_factory_workflow.py"
STEP_PREFIX = "Stack: "
VERIFY_GUARD = "steps.apply.outputs.ok == 'true'"
RETRO_GUARD = "steps.plan.outputs.changed == 'true'"
RETRO_ROOT = "repo"
TOOL_PIP = "python -m pip install --quiet --disable-pip-version-check"

# Pins for the actions the profile's built-in stacks use and the template
# does not. actions/setup-python is always taken from the template.
DEFAULT_PINS = {
    "actions/setup-node": ("49933ea5288caeca8642d1e84afbd3f7d6820020", "v4"),
}

# Custom steps may not use these: they would undo the job split.
FORBIDDEN_ACTIONS = {
    "actions/checkout": "a second checkout would replace the tree the gate tests",
    "actions/upload-artifact": "artifacts are the factory's own data channel between jobs",
    "actions/download-artifact": "artifacts are the factory's own data channel between jobs",
    "actions/create-github-app-token": "only the bookkeeping jobs may mint the App token",
    "anthropics/claude-code-action": "only the model jobs' own step runs the model",
}

# Nor these words: no secret or token reaches a stack step, only verify.sh's
# own step may fail without failing the job, and no step dispatches a run.
FORBIDDEN_WORDS = (
    "secrets.", "github.token", "continue-on-error", "gh workflow run", "/dispatches",
    "createWorkflowDispatch",
)

LOCKFILES = {"npm": "package-lock.json", "pnpm": "pnpm-lock.yaml", "yarn": "yarn.lock"}
INSTALLS = {
    "npm": "npm ci",
    "pnpm": "pnpm install --frozen-lockfile",
    "yarn": "yarn install --frozen-lockfile",
}

PINNED_LINE = re.compile(
    r"^\s*(-\s+)?uses:\s+[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40} # v\d+(\.\d+){0,2}\s*$"
)
USES_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_./-]{0,199}@[0-9a-f]{40}")
TAG_RE = re.compile(r"v\d+(\.\d+){0,2}")
SEGMENT = r"[A-Za-z0-9_.-]{1,100}"
PATH_RE = re.compile(rf"{SEGMENT}(/{SEGMENT}){{0,15}}")
VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.*/_+-]{0,31}")
ID_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}")
KEY_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,63}")
ENV_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
STATUS_FN = re.compile(r"\b(always|success|failure|cancelled)\s*\(")
SHELLS = ("bash", "sh", "python", "pwsh")
STEP_KEYS = (
    "name", "id", "if", "uses", "tag", "working-directory", "shell", "timeout-minutes",
    "with", "env", "run", "slots",
)
EMIT_ORDER = ("name", "id", "if", "uses", "working-directory", "shell", "timeout-minutes",
              "with", "env", "run")


class RenderError(Exception):
    """A refusal: bad input, or a result that would weaken the workflow."""


# --- the profile -------------------------------------------------------------


@dataclass
class Profile:
    python: dict[str, Any] | None = None
    node: dict[str, Any] | None = None
    custom: list[dict[str, Any]] = field(default_factory=list)
    pins: dict[str, tuple[str, str]] = field(default_factory=dict)

    def to_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {"schema": 1}
        if self.python is not None:
            data["python"] = dict(self.python)
        if self.node is not None:
            data["node"] = dict(self.node)
        if self.custom:
            data["custom"] = [dict(step) for step in self.custom]
        if self.pins:
            data["pins"] = {a: {"sha": s, "tag": t} for a, (s, t) in self.pins.items()}
        return data

    def describe(self) -> str:
        parts = []
        if self.python is not None:
            py = self.python
            what = py.get("version") or f"version from {py.get('version_file')}"
            extra = [f"-r {r}" for r in py["requirements"]] + [f"-e {e}" for e in py["editable"]]
            parts.append(f"python {what}" + (f" ({', '.join(extra)})" if extra else ""))
        if self.node is not None:
            node = self.node
            what = node.get("version") or f"version from {node.get('version_file')}"
            parts.append(f"node {what} ({node['package_manager']}; {', '.join(node['dirs'])})")
        if self.custom:
            parts.append(f"{len(self.custom)} custom step(s)")
        return "; ".join(parts) or "no runtime setup"


def _where(path: str, value: Any) -> str:
    return f"{path} ({value!r})" if value is not None else path


def _mapping(value: Any, path: str, allowed: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RenderError(f"{path} must be a mapping")
    unknown = sorted(str(k) for k in value if k not in allowed)
    if unknown:
        raise RenderError(f"{path}: unknown key(s) {', '.join(unknown)}; allowed: {', '.join(allowed)}")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RenderError(f"{path} must be a non-empty string")
    return value


def _version(value: Any, path: str) -> str:
    if isinstance(value, bool) or isinstance(value, float):
        raise RenderError(
            f"{_where(path, value)}: write the version in quotes, such as \"3.10\": YAML reads "
            "an unquoted 3.10 as the number 3.1"
        )
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str) or not VERSION_RE.fullmatch(value):
        raise RenderError(f"{_where(path, value)} is not a version (such as \"3.12\" or \"20\")")
    return value


def _path(value: Any, path: str, *, allow_root: bool = False) -> str:
    if not isinstance(value, str):
        raise RenderError(f"{_where(path, value)} must be a relative path")
    text = value.strip()
    if text.endswith("/") and text != "/":
        text = text.rstrip("/")
    if allow_root and text in (".", "./"):
        return "."
    if text.startswith("./"):
        text = text[2:]
    segments = text.split("/")
    if (
        not PATH_RE.fullmatch(text)
        or any(seg in (".", "..") for seg in segments)
        or text.startswith("-")  # never read as a command-line option
    ):
        raise RenderError(
            f"{_where(path, value)} must be a relative path of [A-Za-z0-9_.-] segments, with "
            "no '..', no leading '/' or '-' and no spaces"
            + (" ('.' for the repository root)" if allow_root else "")
        )
    return text


def _path_list(value: Any, path: str, *, allow_root: bool = False) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise RenderError(f"{path} must be a list of relative paths")
    out = [_path(v, f"{path}[{i}]", allow_root=allow_root) for i, v in enumerate(value)]
    if len(set(out)) != len(out):
        raise RenderError(f"{path} lists a path twice")
    return out


def _balanced(expr: str) -> bool:
    """Parentheses balance outside '...' literals (the expression language's
    only quote; '' inside one toggles twice). An `if` such as
    ``false) || (true`` would otherwise close the guard's own parenthesis
    and run the step whatever the guard says."""
    depth, quoted = 0, False
    for ch in expr:
        if ch == "'":
            quoted = not quoted
        elif not quoted and ch == "(":
            depth += 1
        elif not quoted and ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not quoted


def _action_key(action: str) -> str:
    """owner/repo, lower-cased: GitHub resolves action names without regard
    to case, and owner/repo/path runs code from the owner/repo repository."""
    return "/".join(action.lower().split("/")[:2])


def _no_expression(value: str, path: str) -> None:
    if "${{" in value:
        raise RenderError(
            f"{path} holds ${{{{ }}}}: a stack step uses no expressions (a value from an event "
            "or a secret must never reach a shell)"
        )


def _load_python(raw: Any) -> dict[str, Any]:
    data = _mapping(raw, "python", ("version", "version_file", "requirements", "editable"))
    if ("version" in data) == ("version_file" in data):
        raise RenderError("python needs exactly one of version and version_file")
    out: dict[str, Any] = {}
    if "version" in data:
        out["version"] = _version(data["version"], "python.version")
    else:
        out["version_file"] = _path(data["version_file"], "python.version_file")
    out["requirements"] = _path_list(data.get("requirements"), "python.requirements")
    out["editable"] = _path_list(data.get("editable"), "python.editable", allow_root=True)
    return out


def _load_node(raw: Any) -> dict[str, Any]:
    data = _mapping(
        raw, "node",
        ("version", "version_file", "dirs", "dir", "lockfiles", "lockfile", "package_manager",
         "install", "cache"),
    )
    if ("version" in data) == ("version_file" in data):
        raise RenderError("node needs exactly one of version and version_file")
    out: dict[str, Any] = {}
    if "version" in data:
        out["version"] = _version(data["version"], "node.version")
    else:
        out["version_file"] = _path(data["version_file"], "node.version_file")
    manager = data.get("package_manager", "npm")
    if manager not in LOCKFILES:
        raise RenderError(f"{_where('node.package_manager', manager)}: use npm, pnpm or yarn")
    out["package_manager"] = manager
    if "dir" in data and "dirs" in data:
        raise RenderError("node: give dirs (a list) or dir, not both")
    dirs = _path_list(data.get("dirs", data.get("dir", ".")), "node.dirs", allow_root=True)
    if not dirs:
        raise RenderError("node.dirs must name at least one package directory ('.' for the root)")
    out["dirs"] = dirs
    if "lockfile" in data and "lockfiles" in data:
        raise RenderError("node: give lockfiles (a list) or lockfile, not both")
    lockfiles = _path_list(data.get("lockfiles", data.get("lockfile")), "node.lockfiles")
    if not lockfiles:
        name = LOCKFILES[manager]
        lockfiles = [name if d == "." else f"{d}/{name}" for d in dirs]
    elif len(lockfiles) != len(dirs):
        raise RenderError("node.lockfiles needs one lockfile per entry of node.dirs")
    out["lockfiles"] = lockfiles
    install = data.get("install", INSTALLS[manager])
    install = _text(install, "node.install").strip()
    if "\n" in install or "\r" in install:
        raise RenderError("node.install must be one line")
    _no_expression(install, "node.install")
    for forbidden in (*FORBIDDEN_WORDS, "scripts/verify.sh"):
        if forbidden in install:
            raise RenderError(f"node.install mentions {forbidden}: refused")
    out["install"] = install
    cache = data.get("cache", True)
    if not isinstance(cache, bool):
        raise RenderError("node.cache must be true or false")
    out["cache"] = cache
    return out


def _scalar_value(value: Any, path: str) -> str | int | bool:
    if isinstance(value, float):
        raise RenderError(f"{_where(path, value)}: write it in quotes (YAML reads 3.10 as 3.1)")
    if isinstance(value, (bool, int)):
        return value
    if not isinstance(value, str):
        raise RenderError(f"{path} must be a string, a whole number or true/false")
    _no_expression(value, path)
    return value


def _block_text(value: str, path: str) -> str:
    """One line, or several lines normalized to end with one newline."""
    if "\r" in value:
        value = value.replace("\r\n", "\n").replace("\r", "\n")
    body = value.strip("\n")
    if "\n" not in body:
        return body.strip()
    lines = [line.rstrip() for line in body.split("\n")]
    if lines[0][:1].isspace():
        raise RenderError(f"{path}: the first line of a multi-line value may not be indented")
    return "\n".join(lines) + "\n"


def _load_custom(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise RenderError("custom must be a list of steps")
    steps = []
    for i, item in enumerate(raw):
        where = f"custom[{i}]"
        data = _mapping(item, where, STEP_KEYS)
        step: dict[str, Any] = {}
        if ("uses" in data) == ("run" in data):
            raise RenderError(f"{where} needs exactly one of uses and run")
        if "name" in data:
            name = _text(data["name"], f"{where}.name").strip()
            _no_expression(name, f"{where}.name")
            if "\n" in name:
                raise RenderError(f"{where}.name must be one line")
            step["name"] = name
        if "id" in data:
            if not isinstance(data["id"], str) or not ID_RE.fullmatch(data["id"]):
                raise RenderError(f"{_where(where + '.id', data['id'])} is not a step id")
            step["id"] = data["id"]
        if "if" in data:
            cond = _text(data["if"], f"{where}.if").strip()
            if "${{" in cond or "}}" in cond:
                raise RenderError(f"{where}.if: write the bare expression, without ${{{{ }}}}")
            if STATUS_FN.search(cond):
                raise RenderError(
                    f"{where}.if uses a status function (always(), failure(), ...): a setup "
                    "step runs only while its job is healthy"
                )
            if "secrets." in cond or "github.token" in cond:
                raise RenderError(f"{where}.if may not read a secret or a token")
            if "\n" in cond or "\r" in cond:
                raise RenderError(f"{where}.if must be one line")
            if not _balanced(cond):
                raise RenderError(
                    f"{_where(where + '.if', cond)} has unbalanced parentheses or quotes: joined "
                    "with the slot's guard, it could run the step whatever the guard says"
                )
            step["if"] = cond
        if "uses" in data:
            uses = _text(data["uses"], f"{where}.uses").strip()
            action = uses.partition("@")[0]
            if _action_key(action) in FORBIDDEN_ACTIONS:
                raise RenderError(
                    f"{where}.uses {action}: refused, {FORBIDDEN_ACTIONS[_action_key(action)]}"
                )
            if not USES_RE.fullmatch(uses) or ".." in action:
                raise RenderError(
                    f"{_where(where + '.uses', uses)} is not pinned: use owner/repo@<the full "
                    "40-character commit SHA> (a tag or branch can be moved; a SHA cannot), "
                    "and no local (./) or docker:// action"
                )
            tag = data.get("tag")
            if not isinstance(tag, str) or not TAG_RE.fullmatch(tag):
                raise RenderError(
                    f"{where} needs tag: the release tag of that SHA, such as v5 (YAML drops "
                    "the '# v5' comment, so the profile carries it as a key)"
                )
            step["uses"] = uses
            step["tag"] = tag
            if "working-directory" in data:
                raise RenderError(f"{where}: working-directory applies to run steps only")
            if "shell" in data:
                raise RenderError(f"{where}: shell applies to run steps only")
        elif "tag" in data:
            raise RenderError(f"{where}: tag belongs with uses")
        if "run" in data:
            run = _text(data["run"], f"{where}.run")
            _no_expression(run, f"{where}.run")
            if "scripts/verify.sh" in run:
                raise RenderError(
                    f"{where}.run names scripts/verify.sh: the gate's own step is the only one "
                    "that runs it"
                )
            step["run"] = _block_text(run, f"{where}.run")
            if "working-directory" in data:
                step["working-directory"] = _path(
                    data["working-directory"], f"{where}.working-directory", allow_root=True
                )
            if "shell" in data:
                if data["shell"] not in SHELLS:
                    raise RenderError(f"{where}.shell: one of {', '.join(SHELLS)}")
                step["shell"] = data["shell"]
        if "timeout-minutes" in data:
            minutes = data["timeout-minutes"]
            if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 60:
                raise RenderError(f"{where}.timeout-minutes must be a whole number from 1 to 60")
            step["timeout-minutes"] = minutes
        for key, key_re in (("with", KEY_RE), ("env", ENV_RE)):
            if key not in data:
                continue
            if key == "with" and "uses" not in data:
                raise RenderError(f"{where}: with belongs with uses")
            values = data[key]
            if not isinstance(values, dict) or not values:
                raise RenderError(f"{where}.{key} must be a non-empty mapping")
            clean: dict[str, Any] = {}
            for k, v in values.items():
                if not isinstance(k, str) or not key_re.fullmatch(k):
                    raise RenderError(f"{_where(f'{where}.{key}', k)}: not a valid name")
                if key == "env" and k.upper().startswith(("GITHUB_", "RUNNER_", "ACTIONS_")):
                    raise RenderError(f"{where}.env.{k}: the runner owns that name")
                value = _scalar_value(v, f"{where}.{key}.{k}")
                clean[k] = _block_text(value, f"{where}.{key}.{k}") if isinstance(value, str) else value
            step[key] = clean
        slots = data.get("slots", list(SLOTS))
        if isinstance(slots, str):
            slots = [slots]
        if not isinstance(slots, list) or not slots or any(s not in SLOTS for s in slots):
            raise RenderError(f"{where}.slots: a list from {', '.join(SLOTS)}")
        for a, b in PAIRS:
            if (a in slots) != (b in slots):
                raise RenderError(
                    f"{where}.slots names {a if a in slots else b} without "
                    f"{b if a in slots else a}: they run the same setup"
                )
        step["slots"] = [s for s in SLOTS if s in slots]
        blob = json.dumps(step)
        for forbidden in FORBIDDEN_WORDS:
            if forbidden in blob:
                raise RenderError(f"{where} mentions {forbidden}: refused")
        steps.append(step)
    return steps


def _load_pins(raw: Any) -> dict[str, tuple[str, str]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise RenderError("pins must map owner/repo to {sha, tag}")
    pins = {}
    for action, pin in raw.items():
        where = f"pins.{action}"
        if not isinstance(action, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_./-]{0,199}", action
        ):
            raise RenderError(f"{where}: not an owner/repo action name")
        data = _mapping(pin, where, ("sha", "tag"))
        sha, tag = data.get("sha"), data.get("tag")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise RenderError(f"{where}.sha must be a full 40-character commit SHA")
        if not isinstance(tag, str) or not TAG_RE.fullmatch(tag):
            raise RenderError(f"{where}.tag must be a release tag such as v4")
        pins[action] = (sha, tag)
    return pins


def load_profile(data: Any) -> Profile:
    """A validated, normalized profile from parsed YAML or JSON."""
    if data is None:
        data = {}
    data = _mapping(data, "the profile", ("schema", "python", "node", "custom", "pins"))
    if data.get("schema", 1) != 1:
        raise RenderError("schema: only 1 is known")
    return Profile(
        python=_load_python(data["python"]) if data.get("python") is not None else None,
        node=_load_node(data["node"]) if data.get("node") is not None else None,
        custom=_load_custom(data.get("custom")),
        pins=_load_pins(data.get("pins")),
    )


def profile_from_args(args: argparse.Namespace) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if args.python or args.python_version_file or args.requirements or args.editable:
        python: dict[str, Any] = {}
        if args.python:
            python["version"] = args.python
        if args.python_version_file:
            python["version_file"] = args.python_version_file
        if not python:
            raise RenderError("--requirements and --editable need --python or --python-version-file")
        if args.requirements:
            python["requirements"] = args.requirements
        if args.editable:
            python["editable"] = args.editable
        data["python"] = python
    node_flags = (args.node_dir or args.lockfile or args.package_manager or args.node_install
                  or args.no_node_cache)
    if args.node or args.node_version_file or node_flags:
        node: dict[str, Any] = {}
        if args.node:
            node["version"] = args.node
        if args.node_version_file:
            node["version_file"] = args.node_version_file
        if not node:
            raise RenderError("the node flags need --node or --node-version-file")
        if args.node_dir:
            node["dirs"] = args.node_dir
        if args.lockfile:
            node["lockfiles"] = args.lockfile
        if args.package_manager:
            node["package_manager"] = args.package_manager
        if args.node_install:
            node["install"] = args.node_install
        if args.no_node_cache:
            node["cache"] = False
        data["node"] = node
    return data


# --- the template ------------------------------------------------------------


@dataclass
class Template:
    text: str
    data: dict[str, Any]
    pins: dict[str, tuple[str, str]]
    python_version: str
    tool_pip: str
    job_lines: dict[str, tuple[int, int]]


def _pins_in(text: str) -> dict[str, set[tuple[str, str]]]:
    found: dict[str, set[tuple[str, str]]] = {}
    for line in text.splitlines():
        match = re.search(r"uses:\s+([^\s@]+)@([0-9a-f]{40}) # (v\d+(?:\.\d+){0,2})\s*$", line)
        if match:
            found.setdefault(match.group(1), set()).add((match.group(2), match.group(3)))
    return found


def load_template(text: str) -> Template:
    if text.startswith(RENDERED_MARK) or "\n" + RENDERED_MARK in text[:2000]:
        raise RenderError(
            "the template is already rendered: render from the plugin's "
            "cadence-factory.yml.tmpl, never from a rendered workflow"
        )
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RenderError(f"the template is not valid YAML: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
        raise RenderError("the template has no jobs")
    jobs = data["jobs"]
    for slot in SLOTS:
        if slot not in jobs:
            raise RenderError(f"the template has no {slot} job")
    pins = {}
    for action, refs in _pins_in(text).items():
        if len(refs) != 1:
            raise RenderError(f"the template pins {action} to more than one SHA")
        pins[action] = next(iter(refs))
    if "actions/setup-python" not in pins:
        raise RenderError("the template does not pin actions/setup-python")
    versions = {
        str(step["with"]["python-version"])
        for job in jobs.values() for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("actions/setup-python@")
        and isinstance(step.get("with"), dict) and "python-version" in step["with"]
    }
    if len(versions) != 1:
        raise RenderError(f"the template sets up more than one Python version: {sorted(versions)}")
    tool_pip = next(
        (str(s["run"]).strip() for s in jobs["agent"]["steps"]
         if str(s.get("run", "")).startswith(TOOL_PIP)),
        None,
    )
    if tool_pip is None:
        raise RenderError("the template's agent job does not install the factory tools' PyYAML")
    lines = text.split("\n")
    try:
        start = lines.index("jobs:")
    except ValueError as exc:
        raise RenderError("the template has no top-level 'jobs:' line") from exc
    heads = [
        (i, m.group(1)) for i in range(start + 1, len(lines))
        if (m := re.fullmatch(r"  ([A-Za-z0-9_-]+):\s*", lines[i]))
    ]
    job_lines = {}
    for n, (i, name) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        job_lines[name] = (i, end)
    if set(job_lines) != set(jobs):
        raise RenderError("the template's job lines do not match its parsed jobs")
    return Template(text, data, pins, versions.pop(), tool_pip, job_lines)


def _insert_after(template: Template, slot: str) -> int:
    """The line index after which the slot's steps go."""
    lines = template.text.split("\n")
    start, end = template.job_lines[slot]
    markers = [i for i in range(start, end) if SLOT_MARKER in lines[i]]
    if len(markers) != 1:
        raise RenderError(
            f"the template's {slot} job has {len(markers)} runtime setup markers, not one: "
            "the slots moved, so this renderer needs an update"
        )
    marker = markers[0]
    closing = next(
        (i for i in range(marker + 1, end) if re.fullmatch(r"\s*# -{10,}\s*", lines[i])), None
    )
    if closing is None:
        raise RenderError(f"the {slot} slot's comment block is not closed")
    if slot in ("agent", "agent-retry"):
        # After the template's own Python and the tools' PyYAML, so the
        # project's runtime is the one set up last.
        pip = next(
            (i for i in range(closing + 1, end)
             if lines[i].strip().startswith("- run: " + TOOL_PIP)),
            None,
        )
        if pip is None:
            raise RenderError(f"the {slot} slot is not followed by the tools' pip install")
        return pip
    return closing


# --- the steps ---------------------------------------------------------------


def _prefix(path: str, slot: str) -> str:
    if slot != "retro-plan":
        return path
    return RETRO_ROOT if path == "." else f"{RETRO_ROOT}/{path}"


def _python_steps(py: dict[str, Any], slot: str, template: Template) -> list[dict[str, Any]]:
    same = py.get("version") == template.python_version
    # Paths stay relative to the repository: in retro-plan the step runs in
    # repo/ (working-directory), where that job checks the repository out.
    installs = [f"-r {r}" for r in py["requirements"]] + [f"-e {e}" for e in py["editable"]]
    where = {"working-directory": RETRO_ROOT} if slot == "retro-plan" else {}
    steps: list[dict[str, Any]] = []
    if not same:
        sha, _tag = template.pins["actions/setup-python"]
        if "version" in py:
            name, with_ = f"set up Python {py['version']}", {"python-version": py["version"]}
        else:
            name = f"set up Python (version from {py['version_file']})"
            with_ = {"python-version-file": _prefix(py["version_file"], slot)}
        steps.append({"name": STEP_PREFIX + name, "uses": f"actions/setup-python@{sha}",
                      "with": with_})
        # scripts/verify.sh and the factory tools run on the python set up
        # last, so it needs their PyYAML and jsonschema too.
        steps.append({
            "name": STEP_PREFIX + "install Python dependencies and the factory tools' PyYAML",
            **where,
            "run": " ".join([template.tool_pip, *installs]),
        })
    elif installs:
        steps.append({
            "name": STEP_PREFIX + "install Python dependencies",
            **where,
            "run": " ".join([TOOL_PIP, *installs]),
        })
    return steps


def _node_steps(node: dict[str, Any], slot: str, pins: dict[str, tuple[str, str]]) -> list[dict[str, Any]]:
    sha, _tag = pins["actions/setup-node"]
    if "version" in node:
        name, with_ = f"set up Node.js {node['version']}", {"node-version": node["version"]}
    else:
        name = f"set up Node.js (version from {node['version_file']})"
        with_ = {"node-version-file": _prefix(node["version_file"], slot)}
    if node["package_manager"] == "npm" and node["cache"]:
        locks = [_prefix(lock, slot) for lock in node["lockfiles"]]
        with_["cache"] = "npm"
        with_["cache-dependency-path"] = locks[0] if len(locks) == 1 else "\n".join(locks) + "\n"
    steps: list[dict[str, Any]] = [
        {"name": STEP_PREFIX + name, "uses": f"actions/setup-node@{sha}", "with": with_}
    ]
    if node["package_manager"] != "npm":
        # corepack reads the packageManager field of package.json to pick
        # the pnpm or yarn release.
        corepack: dict[str, Any] = {"name": STEP_PREFIX + "enable corepack"}
        if slot == "retro-plan":
            corepack["working-directory"] = RETRO_ROOT
        corepack["run"] = "corepack enable"
        steps.append(corepack)
    for d in node["dirs"]:
        step: dict[str, Any] = {
            "name": STEP_PREFIX + f"{node['install']} in "
                    + ("the repository root" if d == "." else d),
        }
        wd = _prefix(d, slot)
        if wd != ".":
            step["working-directory"] = wd
        step["run"] = node["install"]
        steps.append(step)
    return steps


PATH_WITH_KEY = re.compile(r".+-version-file|cache-dependency-path")


def _custom_step(raw: dict[str, Any], n: int, slot: str) -> dict[str, Any]:
    step = {k: v for k, v in raw.items() if k not in ("slots", "tag")}
    step["name"] = STEP_PREFIX + raw.get("name", f"custom step {n}")
    if slot == "retro-plan":
        if "run" in step:
            step["working-directory"] = _prefix(step.get("working-directory", "."), slot)
        if "with" in step:
            fixed = {}
            for key, value in step["with"].items():
                if isinstance(value, str) and PATH_WITH_KEY.fullmatch(key):
                    parts = [p.strip() for p in value.split("\n") if p.strip()]
                    for part in parts:  # a path or a glob, relative to the repository
                        if part.startswith(("/", "~", "!")) or ".." in part.split("/"):
                            raise RenderError(
                                f"custom[{n - 1}].with.{key}: {part!r} must be relative to the "
                                "repository (retro-plan checks it out under repo/)"
                            )
                    joined = [_prefix(p, slot) for p in parts]
                    value = joined[0] if len(joined) == 1 else "\n".join(joined) + "\n"
                fixed[key] = value
            step["with"] = fixed
    elif step.get("working-directory") == ".":
        del step["working-directory"]
    return step


def _guard(step: dict[str, Any], slot: str) -> dict[str, Any]:
    guard = {"verify": VERIFY_GUARD, "verify-retry": VERIFY_GUARD, "retro-plan": RETRO_GUARD}.get(slot)
    if guard is None:
        return step
    own = step.get("if")
    ordered: dict[str, Any] = {}
    for key in ("name", "id"):
        if key in step:
            ordered[key] = step[key]
    ordered["if"] = f"{guard} && ({own})" if own else guard
    ordered.update({k: v for k, v in step.items() if k not in ("name", "id", "if")})
    return ordered


def plan_steps(profile: Profile, template: Template) -> tuple[dict[str, list[dict[str, Any]]], dict[str, tuple[str, str]]]:
    """The steps for each slot, and the pin (sha, tag) of every action they use.

    One SHA per action in the whole workflow. The first pin wins: the
    template's, then the profile's ``pins``, then the custom steps', then
    this tool's defaults; a later, different pin is refused."""
    pins: dict[str, tuple[str, str]] = dict(template.pins)

    def one_spelling(action: str, where: str) -> None:
        # GitHub reads Actions/Setup-Node and actions/setup-node as one
        # action: two spellings would slip a second SHA past the rule.
        for known in (*pins, *DEFAULT_PINS):
            if known != action and known.lower() == action.lower():
                raise RenderError(
                    f"{where}: {action} is spelled {known} elsewhere in this workflow; "
                    "use that spelling (one action, one SHA)"
                )

    for action, pin in profile.pins.items():
        one_spelling(action, f"pins.{action}")
        if action in template.pins and template.pins[action] != pin:
            raise RenderError(
                f"pins.{action}: the template already pins it to {template.pins[action][0]} "
                f"# {template.pins[action][1]}; one SHA per action"
            )
        pins[action] = pin
    for i, step in enumerate(profile.custom):
        if "uses" not in step:
            continue
        action, _, sha = step["uses"].partition("@")
        one_spelling(action, f"custom[{i}].uses")
        known = pins.get(action)
        if known is not None and known != (sha, step["tag"]):
            raise RenderError(
                f"custom[{i}].uses: {action} is already pinned to {known[0]} # {known[1]} in "
                "this workflow; one SHA (and tag) per action"
            )
        pins[action] = (sha, step["tag"])
    for action, pin in DEFAULT_PINS.items():
        pins.setdefault(action, pin)
    plan: dict[str, list[dict[str, Any]]] = {}
    jobs = template.data["jobs"]
    for slot in SLOTS:
        steps: list[dict[str, Any]] = []
        if profile.python is not None:
            steps += _python_steps(profile.python, slot, template)
        if profile.node is not None:
            steps += _node_steps(profile.node, slot, pins)
        for n, raw in enumerate(profile.custom, 1):
            if slot in raw["slots"]:
                steps.append(_custom_step(raw, n, slot))
        steps = [_guard(s, slot) for s in steps]
        taken = {s.get("id") for s in jobs[slot]["steps"] if s.get("id")}
        ids = [s["id"] for s in steps if "id" in s]
        clash = sorted((set(ids) & taken) | {i for i in ids if ids.count(i) > 1})
        if clash:
            raise RenderError(f"step id(s) {', '.join(clash)} already used in the {slot} job")
        plan[slot] = steps
    used = {s["uses"].partition("@")[0] for steps in plan.values() for s in steps if "uses" in s}
    return plan, {a: pins[a] for a in sorted(used | set(template.pins))}


# --- emitting YAML -----------------------------------------------------------


def _plain_ok(text: str) -> bool:
    if not text or text != text.strip() or "\n" in text or "#" in text or ": " in text:
        return False
    if text.endswith(":") or text[0] in "!&*[]{}|>'\"%@`,?-:":
        return False
    try:
        return yaml.safe_load(f"k: {text}") == {"k": text}
    except yaml.YAMLError:
        return False


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    return text if _plain_ok(text) else json.dumps(text, ensure_ascii=False)


def _emit_value(first: str, key: str, value: Any, content: str) -> list[str]:
    if isinstance(value, str) and "\n" in value:
        out = [f"{first}{key}: |"]
        out += [content + line if line else "" for line in value.rstrip("\n").split("\n")]
        return out
    return [f"{first}{key}: {_scalar(value)}"]


def emit_step(step: dict[str, Any], tags: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for key in EMIT_ORDER:
        if key not in step:
            continue
        first = "      - " if not lines else "        "
        value = step[key]
        if key == "uses":
            lines.append(f"{first}uses: {value} # {tags[value.partition('@')[0]]}")
        elif key in ("with", "env"):
            lines.append(f"{first}{key}:")
            for k, v in value.items():
                lines += _emit_value(" " * 10, k, v, " " * 12)
        else:
            lines += _emit_value(first, key, value, " " * 10)
    return lines


def render(template: Template, profile: Profile, *, source: str = "command-line flags") -> tuple[str, dict[str, list[dict[str, Any]]], dict[str, tuple[str, str]]]:
    plan, pins = plan_steps(profile, template)
    tags = {action: tag for action, (_sha, tag) in pins.items()}
    lines = template.text.split("\n")
    inserts = []
    for slot in SLOTS:
        if not plan[slot]:
            continue
        block = ["      # Rendered from the stack profile by tool/render_factory_workflow.py."]
        for n, step in enumerate(plan[slot]):
            if n:
                block.append("")
            block += emit_step(step, tags)
        if slot in ("agent", "agent-retry"):
            block = [""] + block
        inserts.append((_insert_after(template, slot), block))
    for at, block in sorted(inserts, reverse=True):
        lines[at + 1:at + 1] = block
    digest = hashlib.sha256(template.text.encode("utf-8")).hexdigest()[:12]
    header = [
        f"{RENDERED_MARK} from {source}",
        f"# and the Cadence factory template (sha256 {digest}). The steps named",
        '# "Stack: ..." in the five runtime setup slots come from that profile: to',
        "# change them, edit the profile and render again; never edit them by hand.",
        "#",
    ]
    return "\n".join(header + lines), plan, pins


# --- checking the result -----------------------------------------------------


def _guarded(cond: Any, guard: str) -> bool:
    """The guard alone, or ``guard && (X)`` with X balanced on its own, so
    nothing in X can reach past the guard."""
    if cond == guard:
        return True
    head = guard + " && ("
    if not isinstance(cond, str) or not cond.startswith(head) or not cond.endswith(")"):
        return False
    inner = cond[len(head):-1]
    return bool(inner.strip()) and _balanced(inner)


def _stack(steps: list[dict[str, Any]]) -> list[int]:
    return [i for i, s in enumerate(steps) if str(s.get("name", "")).startswith(STEP_PREFIX)]


def check_rendered(template: Template, rendered: str, plan: dict[str, list[dict[str, Any]]]) -> list[str]:
    """Every check the rendered workflow must pass; returns what each one proved."""
    proved: list[str] = []

    def need(ok: bool, message: str) -> None:
        if not ok:
            raise RenderError("the rendered workflow fails a check: " + message)

    data = yaml.safe_load(rendered)
    need(isinstance(data, dict), "it does not parse")
    need({k: v for k, v in data.items() if k != "jobs"}
         == {k: v for k, v in template.data.items() if k != "jobs"},
         "a top-level key changed")
    need(list(data["jobs"]) == list(template.data["jobs"]), "the jobs changed")
    for name, job in data["jobs"].items():
        base = template.data["jobs"][name]
        steps = job.get("steps", [])
        at = _stack(steps)
        need({k: v for k, v in job.items() if k != "steps"}
             == {k: v for k, v in base.items() if k != "steps"},
             f"{name}: something besides its steps changed")
        need([s for i, s in enumerate(steps) if i not in at] == base.get("steps", []),
             f"{name}: a template step changed")
        need([steps[i] for i in at] == plan.get(name, []), f"{name}: the stack steps are not the plan")
        if at:
            need(at == list(range(at[0], at[0] + len(at))), f"{name}: the stack steps are not together")
            before = steps[at[0] - 1]
            anchor = {
                "agent": str(before.get("run", "")).startswith(TOOL_PIP),
                "agent-retry": str(before.get("run", "")).startswith(TOOL_PIP),
                "verify": before.get("id") == "apply",
                "verify-retry": before.get("id") == "apply",
                "retro-plan": before.get("id") == "plan",
            }.get(name, False)
            need(anchor, f"{name}: the stack steps are not in the runtime setup slot")
        for i in at:
            step = steps[i]
            blob = json.dumps(step)
            need("${{" not in blob, f"{name}: a stack step holds an expression")
            for word in (*FORBIDDEN_WORDS, "scripts/verify.sh"):
                need(word not in blob, f"{name}: a stack step mentions {word}")
            need(not STATUS_FN.search(str(step.get("if", ""))), f"{name}: a status function")
            if name in ("verify", "verify-retry"):
                need(_guarded(step.get("if"), VERIFY_GUARD),
                     f"{name}: a stack step runs without {VERIFY_GUARD}")
            if name == "retro-plan":
                need(_guarded(step.get("if"), RETRO_GUARD),
                     f"retro-plan: a stack step runs without {RETRO_GUARD}")
                if "run" in step:
                    wd = str(step.get("working-directory", ""))
                    need(wd == RETRO_ROOT or wd.startswith(RETRO_ROOT + "/"),
                         "retro-plan: a run step outside repo/")
                for key, value in (step.get("with") or {}).items():
                    if PATH_WITH_KEY.fullmatch(key):
                        for part in str(value).split("\n"):
                            need(not part or part.startswith(RETRO_ROOT + "/"),
                                 f"retro-plan: with.{key} outside repo/")
    proved.append("every job outside its stack steps is the template's, unchanged")
    proved.append("the stack steps sit in their five slots and use no expressions or secrets")
    jobs = data["jobs"]
    for a, b in PAIRS:
        need([jobs[a]["steps"][i] for i in _stack(jobs[a]["steps"])]
             == [jobs[b]["steps"][i] for i in _stack(jobs[b]["steps"])],
             f"{a} and {b} differ in their stack steps")
    proved.append("verify and verify-retry (and agent and agent-retry) hold the same stack steps")
    proved.append(f"every verify and verify-retry stack step runs only if {VERIFY_GUARD}")
    proved.append(f"every retro-plan stack step runs under repo/ and only if {RETRO_GUARD}")
    shas: dict[str, set[str]] = {}
    for line in rendered.split("\n"):
        stripped = line.lstrip()
        if "uses:" in line and not stripped.startswith("#"):
            need(bool(PINNED_LINE.match(line)), f"an unpinned uses: line: {line.strip()}")
        elif re.match(r"#\s*-\s+uses:", stripped):
            need(bool(PINNED_LINE.match(re.sub(r"#\s*", "", line, count=1))),
                 f"an unpinned commented uses: line: {line.strip()}")
        match = re.search(r"uses:\s+(\S+)@(\S+)", line)
        if match and not stripped.startswith("#"):
            shas.setdefault(match.group(1).lower(), set()).add(match.group(2))
    for job in jobs.values():
        for step in job.get("steps", []):
            if "uses" in step:
                action, _, ref = str(step["uses"]).partition("@")
                need(bool(re.fullmatch(r"[0-9a-f]{40}", ref)), f"{step['uses']} is not a SHA")
                shas.setdefault(action.lower(), set()).add(ref)
    for action, refs in shas.items():
        need(len(refs) == 1, f"{action} is pinned to {len(refs)} SHAs")
    proved.append("every uses: is pinned to a 40-character commit SHA with its tag, one SHA per action")
    return proved


# --- the command line --------------------------------------------------------


def _source_name(profile_arg: str | None) -> str:
    if not profile_arg:
        return "command-line flags"
    path = profile_arg.replace("\\", "/")
    if PATH_RE.fullmatch(path) and ".." not in path.split("/"):
        return f"the profile {path}"
    return f"the profile {Path(profile_arg).name}"


def _missing(profile: Profile, root: Path) -> list[str]:
    paths: list[tuple[str, str]] = []
    if profile.python is not None:
        py = profile.python
        if "version_file" in py:
            paths.append(("python.version_file", py["version_file"]))
        paths += [("python.requirements", r) for r in py["requirements"]]
        paths += [("python.editable", e) for e in py["editable"]]
    if profile.node is not None:
        node = profile.node
        if "version_file" in node:
            paths.append(("node.version_file", node["version_file"]))
        paths += [("node.dirs", f"{d}/package.json" if d != "." else "package.json")
                  for d in node["dirs"]]
        paths += [("node.lockfiles", lock) for lock in node["lockfiles"]]
    return [f"{what}: {p} is not in {root.as_posix()}" for what, p in paths if not (root / p).exists()]


def _summary(out: str, template_path: Path, profile: Profile, plan: dict[str, list[dict[str, Any]]],
             pins: dict[str, tuple[str, str]], proved: list[str], warnings: list[str]) -> str:
    lines = [f"Rendered {out} from {template_path.name}.", f"Stack: {profile.describe()}."]
    notes = {
        "verify": f"each guarded by {VERIFY_GUARD}",
        "verify-retry": "the same steps as verify",
        "agent-retry": "the same steps as agent",
        "retro-plan": f"paths under repo/, each guarded by {RETRO_GUARD}",
    }
    for slot in SLOTS:
        names = [s["name"][len(STEP_PREFIX):] for s in plan[slot]]
        what = "; ".join(names) if names else "nothing"
        note = f" ({notes[slot]})" if names and slot in notes else ""
        lines.append(f"  {slot:<13} {len(names)} step(s): {what}{note}")
    used = sorted(a for a in pins if any(
        s.get("uses", "").startswith(a + "@") for steps in plan.values() for s in steps))
    if used:
        lines.append("Actions added: " + ", ".join(f"{a}@{pins[a][0]} # {pins[a][1]}" for a in used))
    lines.append("Checks passed:")
    lines += [f"  - {p}" for p in proved]
    for warning in warnings:
        lines.append(f"Warning: {warning}")
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render the Cadence factory workflow with a repository's runtime setup.",
    )
    default_template = (
        Path(__file__).resolve().parent.parent / ".github" / "workflows" / "cadence-factory.yml.tmpl"
    )
    parser.add_argument("--template", type=Path, default=default_template)
    parser.add_argument("--out", default=".github/workflows/cadence-factory.yml")
    parser.add_argument("--profile")
    parser.add_argument("--save-profile")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--python")
    parser.add_argument("--python-version-file")
    parser.add_argument("--requirements", action="append", default=[])
    parser.add_argument("--editable", action="append", default=[])
    parser.add_argument("--node")
    parser.add_argument("--node-version-file")
    parser.add_argument("--node-dir", action="append", default=[])
    parser.add_argument("--lockfile", action="append", default=[])
    parser.add_argument("--package-manager", choices=sorted(LOCKFILES))
    parser.add_argument("--node-install")
    parser.add_argument("--no-node-cache", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        from_flags = profile_from_args(args)
        if args.profile and from_flags:
            raise RenderError("give --profile or the stack flags, not both")
        if args.profile:
            try:
                raw = yaml.safe_load(Path(args.profile).read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as exc:
                raise RenderError(f"cannot read the profile {args.profile}: {exc}") from exc
        elif from_flags:
            raw = from_flags
        else:
            raise RenderError(
                "give --profile PATH or the stack flags (--python, --node, ...); an empty "
                "profile file renders the slots empty"
            )
        profile = load_profile(raw)
        try:
            template_text = args.template.read_text(encoding="utf-8")
        except OSError as exc:
            raise RenderError(f"cannot read the template {args.template}: {exc}") from exc
        template = load_template(template_text.replace("\r\n", "\n"))
        rendered, plan, pins = render(template, profile, source=_source_name(args.profile))
        proved = check_rendered(template, rendered, plan)
    except RenderError as exc:
        print(f"render_factory_workflow.py: refused: {exc}", file=sys.stderr)
        print("Nothing was written.", file=sys.stderr)
        return 2
    warnings = _missing(profile, args.repo_root)
    if args.out == "-":
        sys.stdout.write(rendered)
        report = sys.stderr
    else:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8", newline="\n")
        report = sys.stdout
    if args.save_profile:
        saved = Path(args.save_profile)
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_text(
            "# The runtime setup of the Cadence factory workflow. Render it with\n"
            "#   python <plugin>/templates/tool/render_factory_workflow.py "
            "--profile <this file>\n"
            + yaml.safe_dump(profile.to_data(), sort_keys=False, default_flow_style=False),
            encoding="utf-8", newline="\n",
        )
    print(_summary(args.out, args.template, profile, plan, pins, proved, warnings), file=report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

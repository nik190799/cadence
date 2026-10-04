"""The factory's own workflow, cut into the pieces the eval runs.

The eval runs the factory's real scripts, prompts and claude_args, byte for
byte: they are read from the workflow template at the pinned Cadence commit
(``git show <sha>:<template>``), loaded with PyYAML and cut out by job and
step name. Each piece keeps its text and sha256 (run.json and the
preregistration pin them).

The extraction fails closed (exit 2):

- a piece that is missing (a renamed step) stops the eval;
- every ``${{ }}`` expression in a piece may reference only the whitelisted
  contexts (``needs.route.outputs.{issue,guarded,test_roots,max_turns,
  per_run_usd}``, ``needs.retry-gate.outputs.step``, ``runner.temp``,
  ``github.repository`` and the ``steps.*`` values the emulator supplies);
- every name in a piece's step or job ``env:`` block must be one the
  emulator supplies.

Rendering substitutes only those values. Two mechanical rewrites apply to
the scripts that run the factory's tools: every ``python ... tool/<x>.py``
runs the pinned copy of the tools instead (a merged agent PR can change
``tool/`` on main; the eval flags that as tool drift), and every tool that
takes ``--now`` gets the logical clock.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from config import EvalError, sha256_bytes

TEMPLATE_PATH = "plugins/cadence/templates/.github/workflows/cadence-factory.yml.tmpl"
RUNNER_TEMP = "/home/runner/work/_temp"

# Contexts a piece may reference through ${{ }}.
WHITELIST = frozenset(
    {
        "needs.route.outputs.issue",
        "needs.route.outputs.guarded",
        "needs.route.outputs.test_roots",
        "needs.route.outputs.max_turns",
        "needs.route.outputs.per_run_usd",
        "needs.retry-gate.outputs.step",
        "runner.temp",
        "github.repository",
    }
)

# Env names (job and step env blocks) the emulator supplies, per job.
JOB_ENV: dict[str, frozenset[str]] = {
    "route": frozenset(),
    "verify": frozenset({"BASE_SHA", "GUARDED", "TEST_ROOTS"}),
    "intake": frozenset({"ISSUE", "GH_TOKEN", "EXECUTION_FILE", "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"}),
    "agent": frozenset({"BASE_SHA", "EXECUTION_FILE", "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"}),
    "agent-retry": frozenset({"BASE_SHA", "EXECUTION_FILE", "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"}),
    "retry-gate": frozenset({"ISSUE", "GH_TOKEN", "FAILED_STEP"}),
    "observe": frozenset(
        {"ISSUE", "AGENT_RESULT", "VERIFY_RESULT", "TRY_RUN_ID", "APPLY_STATUS", "SPEC_SHA256"}
    ),
    "publish": frozenset(
        {
            "ISSUE", "BRANCH", "BASE_BRANCH", "GH_TOKEN", "RUN_URL",
            "VERIFY_RESULT", "VERIFY_VERDICT", "RETRY_VERIFY_RESULT", "RETRY_VERIFY_VERDICT",
            "TREE_1", "TREE_2", "GUARDED_1", "GUARDED_2", "FAILED_1", "FAILED_2",
        }
    ),
    "retro-plan": frozenset(
        {
            "PYTHONDONTWRITEBYTECODE", "RUN_URL", "OPEN_PLAN",
            "APPLIED", "VERIFY_OK", "DEMOTE_APPLIED", "VERIFY2_OK", "PLAN_SHA",
        }
    ),
    "retro-publish": frozenset({"PLAN_SHA", "BASE_BRANCH", "RUN_URL"}),
    "retro-failed": frozenset({"PLAN_SHA"}),
}

POST_SPEC_FROM = 'file="$RUNNER_TEMP/intake/intake.md"'
POST_SPEC_TO = "printf '\\n\\n<sub>Cadence factory, run %s</sub>\\n' \"$RUN_URL\" >> \"$RUNNER_TEMP/comment.md\""
FAILED_JQ_FROM = 'jq -n --arg plan_sha "$PLAN_SHA"'
FAILED_JQ_TO = '> "$rel"'


@dataclass(frozen=True)
class PieceSpec:
    key: str
    job: str
    step: str | None
    kind: str  # run | prompt | claude_args | outputs | post_spec | failed_jq


SPECS: tuple[PieceSpec, ...] = (
    PieceSpec("route.caps", "route", "Read the per-run caps from .cadence/factory.yaml", "run"),
    PieceSpec("route.paths", "route", "Read the guarded paths (base config, before the patch)", "run"),
    PieceSpec("verify.paths", "verify", "Read the guarded paths (base config, before the patch)", "run"),
    PieceSpec("verify.apply", "verify", "Apply the diff, then restore guarded paths from the base", "run"),
    PieceSpec("verify.verify", "verify", "Run verify", "run"),
    PieceSpec("verify.outputs", "verify", None, "outputs"),
    PieceSpec("intake.prompt", "intake", "Write the spec", "prompt"),
    PieceSpec("intake.claude_args", "intake", "Write the spec", "claude_args"),
    PieceSpec("intake.keep", "intake", "Keep the result for the ledger", "run"),
    PieceSpec("agent.prompt", "agent", "Run the agent team", "prompt"),
    PieceSpec("agent.claude_args", "agent", "Run the agent team", "claude_args"),
    PieceSpec("agent.package", "agent", "Package the diff", "run"),
    PieceSpec("agent.keep", "agent", "Keep the result for the ledger", "run"),
    PieceSpec("agent-retry.prompt", "agent-retry", "Run the agent team again", "prompt"),
    PieceSpec("agent-retry.claude_args", "agent-retry", "Run the agent team again", "claude_args"),
    PieceSpec("agent-retry.apply_first", "agent-retry", "Apply the first attempt", "run"),
    PieceSpec("agent-retry.package", "agent-retry", "Package the diff", "run"),
    PieceSpec("agent-retry.keep", "agent-retry", "Keep the result for the ledger", "run"),
    PieceSpec("retry-gate.map_step", "retry-gate", "Map the failed step", "run"),
    PieceSpec("observe.apply", "observe", "Apply the patch to a scratch worktree of the base", "run"),
    PieceSpec("observe.scan", "observe", "Scan the attempt", "run"),
    PieceSpec("publish.post_spec", "publish", "Post the spec or the questions", "post_spec"),
    PieceSpec("publish.pick", "publish", "Pick the attempt that passed", "run"),
    PieceSpec("retro-plan.plan", "retro-plan", "Plan the ladder", "run"),
    PieceSpec("retro-plan.apply", "retro-plan", "Apply the plan", "run"),
    PieceSpec("retro-plan.verify", "retro-plan", "Run verify on the result", "run"),
    PieceSpec("retro-plan.demote", "retro-plan", "Demote the checks and apply again", "run"),
    PieceSpec("retro-plan.verify2", "retro-plan", "Run verify on the demoted result", "run"),
    PieceSpec("retro-plan.decide", "retro-plan", "Decide what to publish", "run"),
    PieceSpec("retro-plan.build", "retro-plan", "Guard, then build the retro patch and PR body", "run"),
    PieceSpec("retro-publish.guard", "retro-publish", "Guard the patch, then write the PR body", "run"),
    PieceSpec("retro-failed.record", "retro-failed", "Record the failed plan on cadence/state", "failed_jq"),
)

# Pieces whose twin in the retry jobs must be the same, byte for byte.
TWINS = (
    ("verify", "verify-retry", ("Read the guarded paths (base config, before the patch)",
                                "Apply the diff, then restore guarded paths from the base", "Run verify")),
    ("observe", "observe-retry", ("Apply the patch to a scratch worktree of the base", "Scan the attempt")),
)


@dataclass
class Piece:
    key: str
    job: str
    step: str | None
    kind: str
    text: str
    sha256: str
    env: dict[str, str] = field(default_factory=dict)          # the step's env block (raw)
    job_env: dict[str, str] = field(default_factory=dict)      # the job's env block (raw)
    condition: str | None = None                                # the step's if:
    continue_on_error: bool = False
    working_directory: str | None = None
    step_id: str | None = None
    outputs: dict[str, str] = field(default_factory=dict)       # kind outputs only


@dataclass
class Workflow:
    sha: str
    template_sha256: str
    pieces: dict[str, Piece]

    def piece(self, key: str) -> Piece:
        return self.pieces[key]

    def shas(self) -> dict[str, str]:
        return {key: piece.sha256 for key, piece in sorted(self.pieces.items())}


# --- reading the template -------------------------------------------------------------


def template_text(repo: Path, sha: str) -> str:
    import sandbox as sb  # the runner's git: an explicit environment, hooks off

    done = sb.git(repo, "show", f"{sha}:{TEMPLATE_PATH}", check=False)
    if not done.ok:
        raise EvalError(f"cannot read the workflow template at {sha}: {done.stderr.decode(errors='replace').strip()}")
    return done.stdout.decode("utf-8")


def _jobs(text: str) -> dict[str, Any]:
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise EvalError(f"the workflow template is not YAML: {exc}") from exc
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, dict):
        raise EvalError("the workflow template has no jobs")
    return jobs


def _find_step(job: dict[str, Any], job_name: str, name: str) -> dict[str, Any]:
    matches = [s for s in job.get("steps") or [] if isinstance(s, dict) and s.get("name") == name]
    if len(matches) != 1:
        raise EvalError(f"workflow piece missing: job {job_name!r} step {name!r} ({len(matches)} found)")
    return matches[0]


def _between(text: str, start: str, end: str, key: str) -> str:
    lines = text.split("\n")
    first = next((i for i, line in enumerate(lines) if line.strip().startswith(start)), None)
    if first is None:
        raise EvalError(f"workflow piece {key}: start marker not found")
    last = next((i for i in range(first, len(lines)) if lines[i].rstrip().endswith(end)), None)
    if last is None:
        raise EvalError(f"workflow piece {key}: end marker not found")
    return "\n".join(lines[first : last + 1]) + "\n"


_TEMPLATE = re.compile(r"\$\{\{(.*?)\}\}", re.S)
_CTX = re.compile(r"(?<![A-Za-z0-9_.'-])([A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)+)")


def expressions(text: str) -> list[str]:
    return [m.group(1).strip() for m in _TEMPLATE.finditer(text)]


def contexts_of(expr: str) -> list[str]:
    """The dotted context paths an expression reads (string literals removed)."""
    stripped = re.sub(r"'(?:[^']|'')*'", "''", expr)
    return _CTX.findall(stripped)


def _allowed_context(name: str) -> bool:
    if name in WHITELIST:
        return True
    return bool(re.fullmatch(r"steps\.[A-Za-z_][A-Za-z0-9_-]*\.(outcome|conclusion|outputs\.[A-Za-z_][A-Za-z0-9_-]*)", name))


def check_expressions(text: str, where: str) -> None:
    for expr in expressions(text):
        for name in contexts_of(expr):
            if not _allowed_context(name):
                raise EvalError(f"{where}: ${{{{ {expr} }}}} reads {name}, which is not whitelisted")


def _check_env(names: Any, allowed: frozenset[str], where: str) -> dict[str, str]:
    if names is None:
        return {}
    if not isinstance(names, dict):
        raise EvalError(f"{where}: env is not a mapping")
    for name in names:
        if name not in allowed:
            raise EvalError(f"{where}: env name {name!r} is not one the emulator supplies")
    return {str(k): str(v) for k, v in names.items()}


def extract(text: str, sha: str = "") -> Workflow:
    jobs = _jobs(text)
    pieces: dict[str, Piece] = {}
    for spec in SPECS:
        job = jobs.get(spec.job)
        if not isinstance(job, dict):
            raise EvalError(f"workflow piece missing: job {spec.job!r}")
        allowed = JOB_ENV.get(spec.job, frozenset())
        job_env = _check_env(job.get("env"), allowed, f"job {spec.job}")
        where = f"{spec.key} ({spec.job} / {spec.step or 'outputs'})"
        if spec.kind == "outputs":
            outputs = {k: str(v) for k, v in (job.get("outputs") or {}).items()}
            for name in ("verdict", "failed_step", "tree", "guarded"):
                if name not in outputs:
                    raise EvalError(f"workflow piece missing: {spec.job} output {name}")
                check_expressions(outputs[name], f"{where} output {name}")
            body = "\n".join(f"{k}: {outputs[k]}" for k in ("verdict", "failed_step", "tree", "guarded"))
            pieces[spec.key] = Piece(spec.key, spec.job, None, spec.kind, body, sha256_bytes(body.encode()),
                                     job_env=job_env, outputs={k: outputs[k] for k in ("verdict", "failed_step", "tree", "guarded")})
            continue
        step = _find_step(job, spec.job, spec.step or "")
        # Env values are computed by the emulator, by name.
        env = _check_env(step.get("env"), allowed, where)
        if spec.kind in ("prompt", "claude_args"):
            with_ = step.get("with") or {}
            body = with_.get(spec.kind)
            if not isinstance(body, str) or not body.strip():
                raise EvalError(f"workflow piece missing: {where} has no with.{spec.kind}")
        else:
            body = step.get("run")
            if not isinstance(body, str) or not body.strip():
                raise EvalError(f"workflow piece missing: {where} has no run script")
            if spec.kind == "post_spec":
                body = _between(body, POST_SPEC_FROM, POST_SPEC_TO, spec.key)
            elif spec.kind == "failed_jq":
                body = _between(body, FAILED_JQ_FROM, FAILED_JQ_TO, spec.key)
        check_expressions(body, where)
        if spec.kind not in ("prompt", "claude_args") and expressions(body):
            raise EvalError(f"{where}: a run script holds ${{{{ }}}}; the emulator does not render scripts")
        condition = step.get("if")
        if condition is not None:
            names = contexts_of(_strip_braces(str(condition)))
            if names and all(n.startswith("steps.") for n in names):
                Expr(str(condition))  # parses, or fails closed
            else:
                # The job graph (needs.*, the stage) is the emulator's own
                # control flow; it never evaluates such a condition.
                condition = None
        pieces[spec.key] = Piece(
            key=spec.key,
            job=spec.job,
            step=spec.step,
            kind=spec.kind,
            text=body,
            sha256=sha256_bytes(body.encode("utf-8")),
            env=env,
            job_env=job_env,
            condition=None if condition is None else str(condition),
            continue_on_error=bool(step.get("continue-on-error")),
            working_directory=step.get("working-directory"),
            step_id=step.get("id"),
        )
    for job_a, job_b, names in TWINS:
        for name in names:
            a = _find_step(jobs[job_a], job_a, name)
            b = _find_step(jobs[job_b], job_b, name)
            if a.get("run") != b.get("run"):
                raise EvalError(f"{job_b} step {name!r} is not {job_a}'s, byte for byte")
    return Workflow(sha=sha, template_sha256=sha256_bytes(text.encode("utf-8")), pieces=pieces)


def load(repo: Path, sha: str) -> Workflow:
    return extract(template_text(repo, sha), sha)


# --- expressions -------------------------------------------------------------------------

_TOKEN = re.compile(
    r"\s*(?:(?P<str>'(?:[^']|'')*')|(?P<num>\d+(?:\.\d+)?)"
    r"|(?P<op>==|!=|&&|\|\||[!()])|(?P<name>[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)*))"
)


def _strip_braces(text: str) -> str:
    text = text.strip()
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2]
    return text.strip()


class Expr:
    """GitHub expressions, the subset the pieces use: string, number and
    boolean literals, dotted contexts, ==, !=, &&, ||, !, parentheses and the
    status functions success(), always(), failure(), cancelled()."""

    _LEVELS = (("||",), ("&&",), ("==", "!="))

    def __init__(self, text: str) -> None:
        text = _strip_braces(text)
        self.text = text
        self.toks: list[tuple[str, str]] = []
        pos = 0
        while pos < len(text):
            if text[pos:].strip() == "":
                break
            m = _TOKEN.match(text, pos)
            if not m or m.end() == pos:
                raise EvalError(f"cannot read expression {text!r} at {text[pos:pos + 20]!r}")
            self.toks.append((m.lastgroup or "", m.group(m.lastgroup or 0)))
            pos = m.end()
        self.i = 0
        self.tree = self._binary(0)
        if self.i != len(self.toks):
            raise EvalError(f"trailing tokens in expression {text!r}")

    def _peek(self) -> str | None:
        return self.toks[self.i][1] if self.i < len(self.toks) else None

    def _take(self, want: str | None = None) -> tuple[str, str]:
        if self.i >= len(self.toks):
            raise EvalError(f"expression {self.text!r} ends early")
        tok = self.toks[self.i]
        if want is not None and tok[1] != want:
            raise EvalError(f"expression {self.text!r}: expected {want!r}, got {tok[1]!r}")
        self.i += 1
        return tok

    def _binary(self, level: int) -> Any:
        if level == len(self._LEVELS):
            return self._unary()
        node = self._binary(level + 1)
        while self._peek() in self._LEVELS[level]:
            op = self._take()[1]
            node = (op, node, self._binary(level + 1))
        return node

    def _unary(self) -> Any:
        if self._peek() == "!":
            self._take()
            return ("!", self._unary())
        kind, value = self._take()
        if kind == "str":
            return ("lit", value[1:-1].replace("''", "'"))
        if kind == "num":
            return ("lit", float(value))
        if value == "(":
            node = self._binary(0)
            self._take(")")
            return node
        if kind != "name":
            raise EvalError(f"expression {self.text!r}: unexpected {value!r}")
        if value in ("true", "false", "null"):
            return ("lit", {"true": True, "false": False, "null": None}[value])
        if self._peek() == "(":
            self._take()
            self._take(")")
            if value.lower() not in ("success", "always", "failure", "cancelled"):
                raise EvalError(f"expression {self.text!r}: function {value}() is not supported")
            return ("call", value.lower())
        return ("ctx", value)

    def evaluate(self, ctx: dict[str, Any], status: dict[str, bool] | None = None) -> Any:
        return _eval(self.tree, ctx, status or {"success": True, "failure": False, "cancelled": False})

    def has_status_function(self) -> bool:
        return any(t[1].lower() in ("success", "always", "failure", "cancelled") for t in self.toks)


def _num(v: Any) -> float:
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip() or "0")
        except ValueError:
            return math.nan
    return math.nan


def truthy(v: Any) -> bool:
    if isinstance(v, float):
        return not (v == 0 or math.isnan(v))
    if v is None:
        return False
    return bool(v)


def as_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else str(v)
    return str(v)


def _eq(a: Any, b: Any) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return a.casefold() == b.casefold()
    if type(a) is type(b):
        return a == b
    return _num(a) == _num(b)


def lookup(ctx: dict[str, Any], dotted: str) -> Any:
    node: Any = ctx
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        if part in node:
            node = node[part]
        else:
            node = next((v for k, v in node.items() if k.lower() == part.lower()), None)
    return node


def _eval(node: Any, ctx: dict[str, Any], status: dict[str, bool]) -> Any:
    op = node[0]
    if op == "lit":
        return node[1]
    if op == "ctx":
        return lookup(ctx, node[1])
    if op == "call":
        return True if node[1] == "always" else status[node[1]]
    if op == "!":
        return not truthy(_eval(node[1], ctx, status))
    if op in ("&&", "||"):
        left = _eval(node[1], ctx, status)
        if truthy(left) == (op == "&&"):
            return _eval(node[2], ctx, status)
        return left
    same = _eq(_eval(node[1], ctx, status), _eval(node[2], ctx, status))
    return same if op == "==" else not same


def step_should_run(condition: str | None, ctx: dict[str, Any], job_failed: bool) -> bool:
    """A step's if:, with GitHub's implicit success() when no status function is named."""
    status = {"success": not job_failed, "failure": job_failed, "cancelled": False}
    if condition is None or not str(condition).strip():
        return not job_failed
    expr = Expr(str(condition))
    if not expr.has_status_function():
        return (not job_failed) and truthy(expr.evaluate(ctx, status))
    return truthy(expr.evaluate(ctx, status))


def render(text: str, ctx: dict[str, Any]) -> str:
    """Substitute every ${{ }} from ``ctx``; any context outside the whitelist fails."""

    def one(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        for name in contexts_of(expr):
            if not _allowed_context(name):
                raise EvalError(f"render: {name} is not whitelisted")
            if lookup(ctx, name) is None and not name.startswith("steps."):
                raise EvalError(f"render: no value for {name}")
        return as_text(Expr(expr).evaluate(ctx))

    return _TEMPLATE.sub(one, text)


# --- rewrites for the tools ---------------------------------------------------------------

# (tool, subcommand) pairs that take --now.
NOW_TOOLS = {
    "signals": ("observe", "finalize", "harvest", "due"),
    "ladder": ("plan", "apply"),
    "metrics": ("report",),
}
_PY_TOOL = re.compile(
    r"(python3?(?:\s+-I)?\s+)\"?(?:\$GITHUB_WORKSPACE/)?(?:base/|repo/)?tool/([a-z_]+)\.py\"?"
)
_SYS_PATH = 'sys.path.insert(0, "tool")'
# Runtime setup inside a piece: the eval's venv already holds both packages
# (the tools sandbox has no network to install them).
_PIP = re.compile(r'python3? -m pip install --quiet --disable-pip-version-check "pyyaml>=6,<7" "jsonschema>=4\.18,<5"')
_PIP_DONE = ": # the eval venv provides pyyaml and jsonschema"
_DATE_ISO = "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
_DATE_DAY = "$(date -u +%F)"


def calls_tools(text: str) -> bool:
    """Does a script run one of the factory's tools?"""
    return bool(_PY_TOOL.search(text)) or _SYS_PATH in text or bool(_PIP.search(text))


@dataclass
class Rewritten:
    text: str
    counts: dict[str, int]


def rewrite_tools(text: str, tools_dir: str, now: int | None, now_iso: str | None = None) -> Rewritten:
    """Point every tool call at the pinned copy and give it the logical clock."""
    counts = {"python": 0, "sys_path": 0, "now": 0, "date": 0, "pip": 0}
    quoted = f'"{tools_dir}'
    out, counts["pip"] = _PIP.subn(_PIP_DONE, text)

    def py(match: re.Match[str]) -> str:
        counts["python"] += 1
        return f'{match.group(1)}{quoted}/{match.group(2)}.py"'

    out = _PY_TOOL.sub(py, out)
    if _SYS_PATH in out:
        counts["sys_path"] = out.count(_SYS_PATH)
        out = out.replace(_SYS_PATH, f'sys.path.insert(0, "{tools_dir}")')
    if now is not None:
        for tool, subs in NOW_TOOLS.items():
            pattern = re.compile(
                r"(" + re.escape(quoted) + "/" + tool + r'\.py")(\s+)(' + "|".join(subs) + r")\b"
            )

            def add(match: re.Match[str]) -> str:
                counts["now"] += 1
                return f"{match.group(1)}{match.group(2)}{match.group(3)} --now {int(now)}"

            out = pattern.sub(add, out)
    if now_iso is not None:
        for token, value in ((_DATE_ISO, now_iso), (_DATE_DAY, now_iso[:10])):
            if token in out:
                counts["date"] += out.count(token)
                out = out.replace(token, value)
    return Rewritten(out, counts)


# Tool calls the eval makes itself (the ledger, harvest and learn-record jobs
# are emulated, not extracted), with every flag each may pass. Each of these
# flags also appears in the workflow template on a line that runs the same
# tool (tests/test_eval_workflow.py checks it); --now is the logical clock.
DIRECT_CALLS: dict[str, tuple[str, ...]] = {
    "ledger.py record": ("--config", "--records-dir", "--run-id", "--run-attempt", "--issue", "--outcome",
                         "--dod", "--stage", "--base-sha", "--pr", "--published-sha", "--result-json"),
    "ledger.py check": ("--config", "--records-dir", "--in-flight"),
    "signals.py finalize": ("--bundle-file", "--bundle-sha256", "--run-id", "--run-attempt", "--issue",
                            "--pr", "--published-sha", "--patch", "--schema-dir", "--out-dir"),
    "signals.py put": ("--staged", "--state"),
    "signals.py harvest": ("--repo", "--state-dir", "--clone", "--bot-login", "--config", "--run-id",
                           "--run-attempt", "--out-dir"),
    "signals.py excerpt": ("--verify-log-dir", "--out"),
    "metrics.py report": ("--state-dir", "--repo-root", "--out"),
    "intake_sanitize.py": ("--event-path", "--out"),
}
# Calls the workflow never makes: the eval's own scoring of the two arms
# (docs/LEARNING.md, "Rules-on versus rules-frozen eval"). These flags are
# checked against the tool's own parser instead.
EVAL_ONLY_CALLS: dict[str, tuple[str, ...]] = {
    "metrics.py report": ("--order", "--window"),
    "metrics.py compare": ("--on", "--frozen", "--ticket-map", "--detector-set", "--order", "--window",
                           "--resamples", "--seed", "--config", "--out"),
}
CLOCK_FLAG = "--now"


def tool_argv(tools_dir: str, call: str, *args: str, isolated: bool = False) -> list[str]:
    """``python3 [-I] <tools_dir>/tool/<x>.py <args>``, refusing any flag
    not listed for that call."""
    allowed = set(DIRECT_CALLS.get(call, ())) | set(EVAL_ONLY_CALLS.get(call, ())) | {CLOCK_FLAG}
    if call not in DIRECT_CALLS and call not in EVAL_ONLY_CALLS:
        raise EvalError(f"tool call {call!r} is not a known direct call")
    for arg in args:
        if arg.startswith("--") and arg not in allowed:
            raise EvalError(f"tool call {call}: flag {arg} is not listed (flag parity with the workflow)")
    script = call.split()[0]
    return ["python3", *(["-I"] if isolated else []), f"{tools_dir}/tool/{script}", *args]


def claude_args(rendered: str) -> list[str]:
    """claude_args as claude-code-action reads them (shell words, one flag per line)."""
    import shlex

    return shlex.split(rendered.replace("\r", ""), posix=True)


def evaluate_outputs(piece: Piece, steps: dict[str, Any]) -> dict[str, str]:
    """The verify job's outputs (verdict, failed_step, tree, guarded), with the
    template's own expressions over the emulated steps."""
    ctx = {"steps": steps}
    return {name: as_text(Expr(expr).evaluate(ctx)) for name, expr in piece.outputs.items()}


Runner = Callable[..., Any]

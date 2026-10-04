"""Model sessions: the live headless Claude Code run, or the free stub.

Live: ``claude -p "<prompt>" --model M --output-format stream-json
--verbose <claude_args>`` in the agent sandbox, with the factory's plugin
installed into the session's own HOME (factory arms only). The stream's
objects become the execution file the workflow's "Keep the result for the
ledger" jq reads.

Stub: no model. Intake writes a fixed spec (or the questions marker and one
generic question); a build applies its configured patch to the working tree
(an empty diff and a flag when it does not apply). Every other step of the
pipeline is real. A ``claude`` that exits 99 sits first on every sandbox's
PATH, so a stub run cannot reach a model by accident.

Voids are infrastructure failures, never booked, at most two per session
(sandbox start, a network failure, a 429, a 5xx or an overload before the
first assistant turn, a runner crash); a third makes the attempt
infra-failed. Model outcomes are booked as on GitHub. Before each session
the budget must hold: booked + caps in flight + this cap <= the budget.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import sandbox as sb
from config import EvalError

VOID_TEXT = re.compile(
    r"\b(429|5\d\d|overloaded|rate.?limit|ECONNRESET|ECONNREFUSED|ENOTFOUND|ETIMEDOUT|EAI_AGAIN|"
    r"network|fetch failed|socket hang up|Connection error)\b",
    re.I,
)
STUB_QUESTION = "Which behaviour should the change keep when the issue and the repository disagree?"


class BudgetStop(Exception):
    pass


class Budget:
    """booked + caps in flight + this session's cap <= limit, or stop."""

    def __init__(self, limit: float, booked: float = 0.0) -> None:
        self.limit = float(limit)
        self.booked = float(booked)
        self.in_flight = 0.0
        self.stopped = False
        self._lock = threading.Lock()

    def reserve(self, cap: float) -> None:
        with self._lock:
            if self.stopped or self.booked + self.in_flight + cap > self.limit + 1e-9:
                self.stopped = True
                raise BudgetStop(
                    f"budget: booked ${self.booked:.2f} + in flight ${self.in_flight:.2f} + cap ${cap:.2f} "
                    f"exceeds ${self.limit:.2f}"
                )
            self.in_flight += cap

    def settle(self, cap: float, booked: float) -> None:
        with self._lock:
            self.in_flight = max(0.0, self.in_flight - cap)
            self.booked += booked


@dataclass
class Session:
    role: str
    run_id: str
    exit: int | None = None
    timed_out: bool = False
    result: dict[str, Any] | None = None
    execution_file: Path | None = None
    voids: list[str] = field(default_factory=list)
    infra_failed: bool = False
    flags: list[str] = field(default_factory=list)
    booked_usd: float | None = None
    cost_source: str | None = None

    @property
    def succeeded(self) -> bool:
        """The workflow's model step succeeded (exit 0, no error result)."""
        return (
            not self.infra_failed and not self.timed_out and self.exit == 0
            and self.result is not None and self.result.get("is_error") is False
        )

    def record(self) -> dict[str, Any]:
        r = self.result or {}
        return {
            "role": self.role,
            "run_id": self.run_id,
            "exit": self.exit,
            "subtype": r.get("subtype"),
            "is_error": r.get("is_error"),
            "total_cost_usd": r.get("total_cost_usd"),
            "num_turns": r.get("num_turns"),
            "duration_ms": r.get("duration_ms"),
            "booked_usd": self.booked_usd,
            "cost_source": self.cost_source,
            "voids": list(self.voids)[:3],
            "timeout": self.timed_out,
            "flags": list(self.flags),
        }


def parse_stream(data: bytes) -> tuple[list[dict[str, Any]], dict[str, Any] | None, bool]:
    """(objects, the last result object, whether an assistant turn happened)."""
    objects: list[dict[str, Any]] = []
    result = None
    assistant = False
    for line in data.decode("utf-8", "replace").split("\n"):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        objects.append(obj)
        if obj.get("type") == "assistant":
            assistant = True
        if "total_cost_usd" in obj:
            result = obj
    return objects, result, assistant


def is_void(res: sb.Result, result: dict[str, Any] | None, assistant: bool) -> str | None:
    if res.start_failed:
        return "sandbox-start"
    err = res.stderr.decode("utf-8", "replace")
    if res.exit not in (0, None) and err.lstrip().startswith("bwrap:"):
        return "sandbox-start"
    if assistant:
        return None
    text = err + "\n" + json.dumps(result or {})
    failed = res.exit != 0 or (result or {}).get("is_error") is True
    if failed and VOID_TEXT.search(text):
        return "network-before-first-turn"
    return None


def stub_intake(out_file: Path, title: str, shape: str) -> int:
    """The stub intake: a fixed spec, or the questions marker and one question."""
    if shape == "fail":
        return 1
    out_file.parent.mkdir(parents=True, exist_ok=True)
    if shape == "questions":
        text = f"<!-- cadence-intake:questions -->\n# Questions: {title}\n\n1. {STUB_QUESTION}\n"
    else:
        text = f"<!-- cadence-intake:spec -->\n# Spec: {title}\n\nBuild the issue as written.\n"
    out_file.write_text(text, encoding="utf-8", newline="\n")
    return 0


def stub_build(checkout: Path, patch: bytes) -> bool:
    """Apply the stub's patch to the working tree; False when it does not apply."""
    if not patch.strip():
        return True
    tmp = checkout.parent / f".stub-{threading.get_ident()}.patch"
    tmp.write_bytes(patch)
    try:
        res = sb.git(checkout, "apply", "--whitespace=nowarn", str(tmp), check=False)
        return res.ok
    finally:
        tmp.unlink()


def stub_result(cost: float) -> dict[str, Any]:
    return {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": cost,
            "num_turns": 1, "duration_ms": 0}


def write_execution(path: Path, objects: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(objects), encoding="utf-8")


def install_plugin(box: sb.Box, cwd: Path) -> list[str]:
    """Install the pinned plugin into the session HOME; fall back to --plugin-dir."""
    plugin = sb.OPT_PLUGIN if box.mode == "bwrap" else box.opt(sb.OPT_PLUGIN)
    add = box.run(["claude", "plugin", "marketplace", "add", plugin], cwd=cwd, timeout=300)
    if add.ok:
        inst = box.run(["claude", "plugin", "install", "cadence@cadence"], cwd=cwd, timeout=300)
        if inst.ok:
            return []
    return ["--plugin-dir", f"{plugin}/plugins/cadence"]


def run_live(
    *, role: str, run_id: str, box: sb.Box, cwd: Path, prompt: str, args: list[str], model: str,
    timeout_s: int, exec_file: Path, stream_file: Path, plugin: bool, cap: float, budget: Budget,
    log: Callable[[str], None], key: str | None,
) -> Session:
    if not key:
        raise EvalError("a live session needs the key")
    session = Session(role, run_id)
    for attempt in range(3):
        budget.reserve(cap)
        booked = 0.0
        # The key and the env scrub, for this session's processes only.
        box.secret_env = {"ANTHROPIC_API_KEY": key, "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1"}
        try:
            extra = install_plugin(box, cwd) if plugin else []
            argv = ["claude", "-p", prompt, "--model", model, "--output-format", "stream-json", "--verbose",
                    *args, *extra]
            res = box.run(argv, cwd=cwd, timeout=timeout_s, stdout_path=stream_file)
            data = stream_file.read_bytes() if stream_file.is_file() else b""
            objects, result, assistant = parse_stream(data)
            void = is_void(res, result, assistant)
            if void is None:
                session.exit, session.timed_out, session.result = res.exit, res.timed_out, (
                    {k: result.get(k) for k in ("type", "subtype", "is_error", "total_cost_usd", "num_turns", "duration_ms")}
                    if result else None
                )
                write_execution(exec_file, objects)
                session.execution_file = exec_file
                cost = (result or {}).get("total_cost_usd")
                if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0:
                    booked, session.cost_source = float(cost), "reported"
                else:
                    booked, session.cost_source = cap, "cap"
                session.booked_usd = booked
                return session
            session.voids.append(void)
            log(f"{run_id}: void ({void}), attempt {attempt + 1}")
        except BudgetStop:
            raise
        except Exception as exc:  # noqa: BLE001 - a runner crash is a void
            session.voids.append(f"runner-crash: {type(exc).__name__}")
            log(f"{run_id}: void (runner crash: {exc})")
        finally:
            box.secret_env = {}
            budget.settle(cap, booked)
    session.infra_failed = True
    return session


def run_stub(
    *, role: str, run_id: str, cost: float, exec_file: Path, action: Callable[[], int], cap: float,
    budget: Budget,
) -> Session:
    """A stub session: ``action`` does the work and returns an exit code."""
    session = Session(role, run_id)
    budget.reserve(cap)
    booked = 0.0
    try:
        session.exit = action()
        session.result = stub_result(cost) if session.exit == 0 else None
        if session.result is not None:
            write_execution(exec_file, [session.result])
            session.execution_file = exec_file
            booked, session.cost_source = cost, "reported"
        else:
            booked, session.cost_source = cap, "cap"
        session.booked_usd = booked
        return session
    finally:
        budget.settle(cap, booked)


class SessionCache:
    """A finished session's numbers and outputs, keyed by its inputs.

    Written after every session (in the attempt's results, never under an
    agent's reach); read only on --resume, so a run that stopped mid-ticket
    replays the session instead of paying for it twice. A replayed session
    is flagged ``cached``.
    """

    def __init__(self, root: Path, enabled: bool) -> None:
        self.root = root
        self.enabled = enabled

    @staticmethod
    def key(**inputs: Any) -> str:
        import hashlib

        return hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()

    def load(self, role: str, key: str) -> tuple[Session, dict[str, bytes]] | None:
        meta = self.root / role / "session.json"
        if not self.enabled or not meta.is_file():
            return None
        data = json.loads(meta.read_text(encoding="utf-8"))
        if data.get("key") != key or data.get("voids_only"):
            return None
        s = data["session"]
        session = Session(role, s["run_id"], exit=s["exit"], timed_out=s["timed_out"], result=s["result"],
                          voids=list(s["voids"]), infra_failed=False, flags=[*s["flags"], "cached"],
                          booked_usd=s["booked_usd"], cost_source=s["cost_source"])
        files = {name: (self.root / role / name).read_bytes() for name in data["files"]
                 if (self.root / role / name).is_file()}
        return session, files

    def save(self, role: str, key: str, session: Session, files: dict[str, bytes]) -> None:
        if session.infra_failed:
            return
        d = self.root / role
        d.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (d / name).write_bytes(data)
        record = {"run_id": session.run_id, "exit": session.exit, "timed_out": session.timed_out,
                  "result": session.result, "voids": session.voids, "flags": session.flags,
                  "booked_usd": session.booked_usd, "cost_source": session.cost_source}
        (d / "session.json").write_text(json.dumps({"key": key, "session": record, "files": sorted(files)},
                                                   sort_keys=True), encoding="utf-8")


def check_guard(box: sb.Box) -> None:
    """The guard is first on PATH: `claude` must exit 99."""
    res = box.run(["claude", "--version"], timeout=60)
    if res.exit != 99:
        raise EvalError(f"stub guard: `claude` exited {res.exit} in the {box.profile} sandbox, not 99")

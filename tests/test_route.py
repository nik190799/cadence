"""Tests for the factory router (tool/route.py).

The router is the factory's front door: it decides, from the event
payload alone, whether a workflow run writes a spec, builds, reconciles
or does nothing. Every way to wake an agent (a label, an exact
``/approve``, a dispatch) needs write access, and anything a bot did is
ignored so the factory cannot trigger itself. The one exception is the
reconciler's spec retry, a dispatch from the factory App itself, which can
never start a build. These tests pin each rule,
the loop guard, malformed input and the GITHUB_OUTPUT file.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ROUTE_PATH = REPO_ROOT / "plugins" / "cadence" / "templates" / "tool" / "route.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, name
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


route = _load_module("cadence_route", ROUTE_PATH)

BOT_LOGIN = "cadence-app[bot]"
ZERO_WIDTH_SPACE = chr(0x200B)


@pytest.fixture(autouse=True)
def _no_github_output(monkeypatch: pytest.MonkeyPatch) -> None:
    # Under GitHub Actions GITHUB_OUTPUT is set for the test job itself;
    # the router must never append to that file from a test.
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)


# --- Payload builders ------------------------------------------------------


def _user(login: str = "alice", kind: str = "User") -> dict[str, Any]:
    return {"login": login, "type": kind}


HUMAN = _user()
APP_BOT = _user(BOT_LOGIN, "Bot")


def _issue(number: int = 42, labels: tuple[str, ...] = (), **extra: Any) -> dict[str, Any]:
    return {
        "number": number,
        "title": "Add CSV export",
        "labels": [{"name": name} for name in labels],
        **extra,
    }


def labeled(
    label: str = "factory",
    *,
    action: str = "labeled",
    sender: dict[str, Any] = HUMAN,
    number: int = 42,
) -> dict[str, Any]:
    return {
        "action": action,
        "issue": _issue(number, (label,)),
        "label": {"name": label},
        "sender": sender,
    }


def comment(
    body: str = "/approve",
    *,
    labels: tuple[str, ...] = ("factory", "spec-ready"),
    action: str = "created",
    sender: dict[str, Any] = HUMAN,
    author: dict[str, Any] | None = None,
    pull_request: bool = False,
    number: int = 42,
) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if pull_request:
        extra["pull_request"] = {"url": f"https://api.github.com/repos/o/r/pulls/{number}"}
    return {
        "action": action,
        "issue": _issue(number, labels, **extra),
        "comment": {"body": body, "user": author if author is not None else sender},
        "sender": sender,
    }


def dispatch(issue: Any = "42", stage: Any = "spec", *, sender: dict[str, Any] = HUMAN) -> dict[str, Any]:
    return {"inputs": {"issue": issue, "stage": stage}, "sender": sender, "ref": "refs/heads/main"}


def schedule(**extra: Any) -> dict[str, Any]:
    return {"schedule": "17 * * * *", "workflow": ".github/workflows/cadence-factory.yml", **extra}


def _run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    event_name: str,
    payload: Any,
    permission: str = "write",
    bot_login: str = BOT_LOGIN,
) -> tuple[int, dict[str, Any] | None]:
    event = tmp_path / "event.json"
    event.write_text(json.dumps(payload), encoding="utf-8")
    code = route.main(
        [
            "--event-name",
            event_name,
            "--event-path",
            str(event),
            "--bot-login",
            bot_login,
            "--sender-permission",
            permission,
        ]
    )
    out = capsys.readouterr().out.strip()
    return code, json.loads(out) if out else None


# --- The decision table ----------------------------------------------------

# (id, event name, payload, sender permission, expected stage, expected issue,
#  text the reason must contain)
CASES: list[tuple[str, str, Any, str, str, int | None, str]] = [
    # issues: labeled 'factory'
    ("factory-label-write", "issues", labeled(), "write", "spec", 42, "labelled 'factory'"),
    ("factory-label-maintain", "issues", labeled(), "maintain", "spec", 42, "maintain"),
    ("factory-label-admin", "issues", labeled(number=7), "admin", "spec", 7, "#7"),
    ("factory-label-triage", "issues", labeled(), "triage", "none", None, "insufficient permission"),
    ("factory-label-read", "issues", labeled(), "read", "none", None, "insufficient permission"),
    ("factory-label-no-access", "issues", labeled(), "none", "none", None, "insufficient permission"),
    ("other-label", "issues", labeled("bug"), "write", "none", None, "'bug'"),
    ("label-case-differs", "issues", labeled("Factory"), "write", "none", None, "is not 'factory'"),
    ("spec-ready-label", "issues", labeled("spec-ready"), "write", "none", None, "is not 'factory'"),
    ("issue-opened", "issues", labeled(action="opened"), "write", "none", None, "'opened'"),
    ("factory-unlabeled", "issues", labeled(action="unlabeled"), "write", "none", None, "'unlabeled'"),
    # issue_comment: /approve
    ("approve", "issue_comment", comment(), "write", "build", 42, "'/approve'"),
    ("approve-admin", "issue_comment", comment(), "admin", "build", 42, "admin"),
    ("approve-whitespace", "issue_comment", comment("  /approve\r\n\n"), "write", "build", 42, "'/approve'"),
    ("approve-please", "issue_comment", comment("/approve please"), "write", "none", None, "not exactly"),
    ("approve-prefixed", "issue_comment", comment("please /approve"), "write", "none", None, "not exactly"),
    ("approve-capitalised", "issue_comment", comment("/Approve"), "write", "none", None, "not exactly"),
    ("approve-two-lines", "issue_comment", comment("/approve\n/approve"), "write", "none", None, "not exactly"),
    (
        "approve-zero-width",
        "issue_comment",
        comment("/approve" + ZERO_WIDTH_SPACE),
        "write",
        "none",
        None,
        "not exactly",
    ),
    ("approve-on-pr", "issue_comment", comment(pull_request=True), "write", "none", None, "pull request"),
    ("approve-without-spec", "issue_comment", comment(labels=("factory",)), "write", "none", None, "no 'spec-ready'"),
    (
        "approve-while-building",
        "issue_comment",
        comment(labels=("spec-ready", "building")),
        "write",
        "none",
        None,
        "already 'building'",
    ),
    ("approve-triage", "issue_comment", comment(), "triage", "none", None, "insufficient permission"),
    ("approve-read", "issue_comment", comment(), "read", "none", None, "insufficient permission"),
    ("approve-edited", "issue_comment", comment(action="edited"), "write", "none", None, "'edited'"),
    ("approve-deleted", "issue_comment", comment(action="deleted"), "write", "none", None, "'deleted'"),
    # workflow_dispatch
    ("dispatch-spec", "workflow_dispatch", dispatch("42", "spec"), "write", "spec", 42, "workflow_dispatch"),
    ("dispatch-build", "workflow_dispatch", dispatch("9", "build"), "maintain", "build", 9, "#9"),
    ("dispatch-padded", "workflow_dispatch", dispatch(" 12 ", " build "), "write", "build", 12, "#12"),
    ("dispatch-int-input", "workflow_dispatch", dispatch(15, "spec"), "admin", "spec", 15, "#15"),
    ("dispatch-zero", "workflow_dispatch", dispatch("0"), "write", "none", None, "not a positive integer"),
    ("dispatch-negative", "workflow_dispatch", dispatch("-3"), "write", "none", None, "not a positive integer"),
    ("dispatch-text", "workflow_dispatch", dispatch("abc"), "write", "none", None, "'abc'"),
    ("dispatch-decimal", "workflow_dispatch", dispatch("4.0"), "write", "none", None, "not a positive integer"),
    ("dispatch-hash", "workflow_dispatch", dispatch("#42"), "write", "none", None, "not a positive integer"),
    ("dispatch-empty", "workflow_dispatch", dispatch(""), "write", "none", None, "not a positive integer"),
    ("dispatch-bool", "workflow_dispatch", dispatch(True), "write", "none", None, "not a positive integer"),
    ("dispatch-huge", "workflow_dispatch", dispatch("12345678901"), "write", "none", None, "not a positive integer"),
    ("dispatch-bad-stage", "workflow_dispatch", dispatch("42", "deploy"), "write", "none", None, "'deploy'"),
    ("dispatch-reconcile", "workflow_dispatch", dispatch("42", "reconcile"), "write", "none", None, "'reconcile'"),
    ("dispatch-stage-case", "workflow_dispatch", dispatch("42", "Build"), "write", "none", None, "'Build'"),
    ("dispatch-read", "workflow_dispatch", dispatch(), "read", "none", None, "insufficient permission"),
    ("dispatch-no-inputs", "workflow_dispatch", {"sender": HUMAN}, "write", "none", None, "without inputs"),
    # schedule
    ("schedule", "schedule", schedule(), "none", "reconcile", None, "reconciler"),
    ("schedule-bot-actor", "schedule", schedule(sender=APP_BOT), "none", "reconcile", None, "reconciler"),
    # loop guard
    ("bot-label", "issues", labeled(sender=APP_BOT), "write", "none", None, "bot event"),
    ("bot-approve", "issue_comment", comment(sender=APP_BOT), "admin", "none", None, "bot event"),
    # rule 0: the reconciler's spec retry, dispatched as the factory App
    ("app-dispatch-spec", "workflow_dispatch", dispatch(sender=APP_BOT), "none", "spec", 42, "factory App"),
    (
        "app-dispatch-spec-login-case",
        "workflow_dispatch",
        dispatch("7", sender=_user("Cadence-App[BOT]", "Bot")),
        "none",
        "spec",
        7,
        "reconciler",
    ),
    ("app-dispatch-build", "workflow_dispatch", dispatch("42", "build", sender=APP_BOT), "admin", "none", None, "only retry a spec"),
    ("app-dispatch-bad-issue", "workflow_dispatch", dispatch("abc", sender=APP_BOT), "admin", "none", None, "'abc'"),
    ("app-dispatch-bad-stage", "workflow_dispatch", dispatch("42", "reconcile", sender=APP_BOT), "admin", "none", None, "'reconcile'"),
    (
        "other-bot-dispatch",
        "workflow_dispatch",
        dispatch(sender=_user("github-actions[bot]", "Bot")),
        "admin",
        "none",
        None,
        "bot event",
    ),
    (
        "lookalike-app-dispatch",
        "workflow_dispatch",
        dispatch(sender=_user("cadence-app-2[bot]", "Bot")),
        "admin",
        "none",
        None,
        "bot event",
    ),
    (
        "bot-login-typed-user",
        "issue_comment",
        comment(sender=_user("Cadence-App[bot]", "User")),
        "write",
        "none",
        None,
        "bot event",
    ),
    (
        "other-bot",
        "issue_comment",
        comment(sender=_user("dependabot[bot]", "Bot")),
        "write",
        "none",
        None,
        "bot event",
    ),
    (
        "bot-type-only",
        "issues",
        labeled(sender=_user("some-integration", "Bot")),
        "admin",
        "none",
        None,
        "bot event",
    ),
    (
        "bot-authored-comment",
        "issue_comment",
        comment(sender=HUMAN, author=APP_BOT),
        "write",
        "none",
        None,
        "bot event",
    ),
    # everything else
    ("push", "push", {"ref": "refs/heads/main", "sender": HUMAN}, "write", "none", None, "'push'"),
    ("pull-request", "pull_request", {"action": "opened", "sender": HUMAN}, "admin", "none", None, "'pull_request'"),
]


@pytest.mark.parametrize(
    "event_name,payload,permission,stage,issue,reason",
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_decision_table(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    event_name: str,
    payload: Any,
    permission: str,
    stage: str,
    issue: int | None,
    reason: str,
) -> None:
    code, decision = _run(tmp_path, capsys, event_name, payload, permission)
    assert code == 0
    assert decision is not None
    assert set(decision) == {"stage", "issue", "reason"}
    assert decision["stage"] == stage
    assert decision["issue"] == issue
    assert reason in decision["reason"]


def test_table_covers_every_stage() -> None:
    assert len(CASES) >= 20
    assert {case[4] for case in CASES} == set(route.STAGES)


def test_issue_is_set_only_for_spec_and_build() -> None:
    for case_id, event_name, payload, permission, *_ in CASES:
        decision = route.decide(event_name, payload, BOT_LOGIN, permission)
        has_issue = decision.issue is not None
        assert has_issue == (decision.stage in ("spec", "build")), case_id


def test_bot_login_without_suffix_still_matches(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = comment(sender=_user("cadence-app[bot]", "User"))
    code, decision = _run(tmp_path, capsys, "issue_comment", payload, bot_login="cadence-app")
    assert code == 0
    assert decision is not None and decision["stage"] == "none"
    assert "bot event" in decision["reason"]


def test_empty_bot_login_still_guards_by_type(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, decision = _run(tmp_path, capsys, "issues", labeled(sender=APP_BOT), bot_login="")
    assert code == 0
    assert decision is not None and decision["stage"] == "none"


def test_app_dispatch_needs_the_bot_login(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Without CADENCE_BOT_LOGIN no bot is trusted: the retry is dropped.
    code, decision = _run(
        tmp_path, capsys, "workflow_dispatch", dispatch(sender=APP_BOT), "admin", bot_login=""
    )
    assert code == 0
    assert decision is not None and decision["stage"] == "none"
    assert "bot event" in decision["reason"]


def test_bot_login_without_suffix_trusts_only_the_bot_account(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A human account named like the App's slug is never the App.
    human_named_like_app = _user("cadence-app", "User")
    code, decision = _run(
        tmp_path,
        capsys,
        "workflow_dispatch",
        dispatch(sender=human_named_like_app),
        "read",
        bot_login="cadence-app",
    )
    assert code == 0
    assert decision is not None and decision["stage"] == "none"
    code, decision = _run(
        tmp_path, capsys, "workflow_dispatch", dispatch(sender=APP_BOT), "none", bot_login="cadence-app"
    )
    assert code == 0
    assert decision is not None and decision["stage"] == "spec"


def test_app_comment_is_still_loop_guarded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Rule 0 covers dispatches only: an /approve or label from the App is ignored.
    for event_name, payload in (
        ("issue_comment", comment(sender=APP_BOT)),
        ("issues", labeled(sender=APP_BOT)),
    ):
        code, decision = _run(tmp_path, capsys, event_name, payload, "admin")
        assert code == 0
        assert decision is not None and decision["stage"] == "none", event_name
        assert "bot event" in decision["reason"]


def test_empty_bot_login_lets_humans_through(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, decision = _run(tmp_path, capsys, "issues", labeled(), bot_login="")
    assert code == 0
    assert decision is not None and decision["stage"] == "spec"


def test_permission_is_case_and_whitespace_insensitive(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, decision = _run(tmp_path, capsys, "issues", labeled(), permission=" WRITE\n")
    assert code == 0
    assert decision is not None and decision["stage"] == "spec"


def test_permission_defaults_to_none(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps(labeled()), encoding="utf-8")
    code = route.main(
        ["--event-name", "issues", "--event-path", str(event), "--bot-login", BOT_LOGIN]
    )
    decision = json.loads(capsys.readouterr().out)
    assert code == 0
    assert decision["stage"] == "none"
    assert "insufficient permission" in decision["reason"]


def test_reason_quotes_payload_text_on_one_printable_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    hostile = "bug\n::error::injected" + chr(0x202E) + "x" * 200
    code, decision = _run(tmp_path, capsys, "issues", labeled(hostile))
    assert code == 0
    assert decision is not None
    assert "\n" not in decision["reason"]
    assert chr(0x202E) not in decision["reason"]
    assert "x" * 61 not in decision["reason"]
    assert decision["reason"].startswith("label 'bug?::error::injected?")


# --- Malformed input --------------------------------------------------------


def _run_raw(
    tmp_path: Path, event_name: str, text: str, permission: str = "write"
) -> int:
    event = tmp_path / "event.json"
    event.write_text(text, encoding="utf-8")
    return route.main(
        [
            "--event-name",
            event_name,
            "--event-path",
            str(event),
            "--bot-login",
            BOT_LOGIN,
            "--sender-permission",
            permission,
        ]
    )


@pytest.mark.parametrize(
    "event_name,text",
    [
        ("issues", "{not json"),
        ("issues", ""),
        ("issues", "[1, 2, 3]"),
        ("issues", '"a string"'),
        ("issues", "[" * 100_000 + "]" * 100_000),
        ("issues", json.dumps({"action": "labeled", "label": {"name": "factory"}, "sender": HUMAN})),
        ("issues", json.dumps({**labeled(), "issue": {**_issue(), "number": "42"}})),
        ("issues", json.dumps({**labeled(), "issue": {**_issue(), "number": True}})),
        ("issues", json.dumps({**labeled(), "issue": {**_issue(), "number": 0}})),
        ("issues", json.dumps({**labeled(), "label": "factory"})),
        ("issues", json.dumps({**labeled(), "action": None})),
        ("issues", json.dumps({**labeled(), "sender": "alice"})),
        ("issues", json.dumps({k: v for k, v in labeled().items() if k != "sender"})),
        ("issue_comment", json.dumps({**comment(), "issue": {**_issue(), "labels": "spec-ready"}})),
        ("issue_comment", json.dumps({**comment(), "issue": {**_issue(), "labels": ["spec-ready"]}})),
        ("issue_comment", json.dumps({**comment(), "comment": {"body": 5, "user": HUMAN}})),
        ("issue_comment", json.dumps({**comment(), "comment": None})),
        ("workflow_dispatch", json.dumps({"inputs": ["42", "spec"], "sender": HUMAN})),
    ],
    ids=[
        "invalid-json",
        "empty-file",
        "top-level-array",
        "top-level-string",
        "deeply-nested",
        "issue-missing",
        "issue-number-string",
        "issue-number-bool",
        "issue-number-zero",
        "label-not-object",
        "action-null",
        "sender-not-object",
        "sender-missing",
        "labels-not-list",
        "labels-not-objects",
        "comment-body-not-string",
        "comment-missing",
        "inputs-not-object",
    ],
)
def test_malformed_payload_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], event_name: str, text: str
) -> None:
    assert _run_raw(tmp_path, event_name, text) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ERROR:" in captured.err


def test_missing_event_file_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = route.main(
        [
            "--event-name",
            "issues",
            "--event-path",
            str(tmp_path / "absent.json"),
            "--bot-login",
            BOT_LOGIN,
        ]
    )
    assert code == 2
    assert "cannot read" in capsys.readouterr().err


def test_payload_with_byte_order_mark_is_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run_raw(tmp_path, "issues", chr(0xFEFF) + json.dumps(labeled())) == 0
    assert json.loads(capsys.readouterr().out)["stage"] == "spec"


@pytest.mark.parametrize("event_name", ["", "Issues", "issues comment", "issues\n", "a" * 65])
def test_bad_event_name_exits_2(tmp_path: Path, event_name: str) -> None:
    assert _run_raw(tmp_path, event_name, json.dumps(labeled())) == 2


@pytest.mark.parametrize("bot_login", ["my app[bot]", "app[bot]\nx", "-app", "app[BOT]x"])
def test_bad_bot_login_exits_2(tmp_path: Path, bot_login: str) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps(labeled()), encoding="utf-8")
    code = route.main(
        ["--event-name", "issues", "--event-path", str(event), f"--bot-login={bot_login}"]
    )
    assert code == 2


def test_unknown_permission_is_a_usage_error(tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps(labeled()), encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        route.main(
            [
                "--event-name",
                "issues",
                "--event-path",
                str(event),
                "--bot-login",
                BOT_LOGIN,
                "--sender-permission",
                "owner",
            ]
        )
    assert excinfo.value.code == 2


# --- GITHUB_OUTPUT ----------------------------------------------------------


def _with_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    event_name: str,
    payload: Any,
    existing: bytes | None = None,
) -> bytes:
    output = tmp_path / "github_output"
    if existing is not None:
        output.write_bytes(existing)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    code, _ = _run(tmp_path, capsys, event_name, payload)
    assert code == 0
    return output.read_bytes()


def test_github_output_for_a_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    data = _with_output(tmp_path, monkeypatch, capsys, "issue_comment", comment())
    assert data == b"stage=build\nissue=42\n"


def test_github_output_for_none_leaves_issue_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    data = _with_output(tmp_path, monkeypatch, capsys, "issues", labeled("bug"))
    assert data == b"stage=none\nissue=\n"


def test_github_output_for_schedule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    data = _with_output(tmp_path, monkeypatch, capsys, "schedule", schedule())
    assert data == b"stage=reconcile\nissue=\n"


def test_github_output_appends_on_a_fresh_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    data = _with_output(
        tmp_path, monkeypatch, capsys, "issues", labeled(), existing=b"earlier=1"
    )
    assert data == b"earlier=1\nstage=spec\nissue=42\n"


def test_github_output_keeps_earlier_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    data = _with_output(
        tmp_path, monkeypatch, capsys, "issues", labeled(), existing=b"earlier=1\n"
    )
    assert data == b"earlier=1\nstage=spec\nissue=42\n"


@pytest.mark.parametrize(
    "event_name,payload",
    [
        ("issues", labeled("factory\nstage=build\nissue=1")),
        ("issue_comment", comment("/approve\nstage=build")),
        ("workflow_dispatch", dispatch("42\nstage=build", "spec")),
        ("workflow_dispatch", dispatch("42", "build\nissue=7")),
        ("workflow_dispatch", dispatch("42", "spec\r\nissue=7")),
    ],
    ids=["label", "comment", "dispatch-issue", "dispatch-stage", "dispatch-stage-crlf"],
)
def test_payload_newlines_never_reach_github_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    event_name: str,
    payload: Any,
) -> None:
    data = _with_output(tmp_path, monkeypatch, capsys, event_name, payload)
    assert data == b"stage=none\nissue=\n"


def test_output_lines_refuse_values_outside_the_contract() -> None:
    with pytest.raises(ValueError):
        route.output_lines(route.Decision("deploy", None, "x"))
    with pytest.raises(ValueError):
        route.output_lines(route.Decision("build", "42\nstage=spec", "x"))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        route.output_lines(route.Decision("build", True, "x"))  # type: ignore[arg-type]


def test_unwritable_github_output_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path))  # a directory, not a file
    code, decision = _run(tmp_path, capsys, "issues", labeled())
    assert code == 2
    assert decision is None


def test_script_runs_standalone(tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps(comment()), encoding="utf-8")
    output = tmp_path / "github_output"
    env = {**os.environ, "GITHUB_OUTPUT": str(output)}
    proc = subprocess.run(
        [
            sys.executable,
            str(ROUTE_PATH),
            "--event-name",
            "issue_comment",
            "--event-path",
            str(event),
            "--bot-login",
            BOT_LOGIN,
            "--sender-permission",
            "write",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {
        "stage": "build",
        "issue": 42,
        "reason": "'/approve' on spec-ready issue #42 by a user with write access",
    }
    assert output.read_bytes() == b"stage=build\nissue=42\n"

"""An Orca-hosted agent's usage limit is a limit, not a failure, and an Orca-hosted Claude moves seats on it.

Two gaps, both verified on main at 790b8fb. `limits.yaml` carried no entries for the Orca-hosted ids, so
`classify("orca:claude", "You've hit your session limit ...", 1)` read "failed" and the router recorded a loss
against a route that had done nothing wrong. And `OrcaAgent.run` had no seat failover at all: `ClaudeCodeAgent`
moves to the reserve seat on a limit, an Orca terminal running the same CLI on the same seat did not.

The parity tests below drive `ClaudeCodeAgent` and `OrcaAgent` through the SAME scenarios and assert the same
observable outcome, so the two paths cannot drift apart quietly.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from pravrudhi.agents import account, orca_agent
from pravrudhi.application import availability

SESSION = "You've hit your session limit · resets 3:20am"
REFUSALS = [
    SESSION,
    "You’ve hit your session limit · resets 3:20am",
    "You've hit your weekly limit · resets Sep 28, 9am",
    "You’ve hit your weekly limit · resets Sep 28, 9am",
    "You've hit your usage limit · resets 11pm",
    "You’ve hit your usage limit · resets 11pm",
]


# --------------------------------------------------------------------------------------------- classification


@pytest.mark.parametrize("text", REFUSALS)
def test_the_claude_cli_refusals_classify_as_limited_under_orca(text: str) -> None:
    assert availability.classify("orca:claude", text, 1) == "limited"
    assert availability.classify("orca:claude", text, 0) == "limited"


@pytest.mark.parametrize("text", [
    "You've exceeded your usage limit. Try again later.",
    "stream error: 429 Too Many Requests",
    "ERROR: quota exceeded for this billing period",
])
def test_the_codex_wording_classifies_as_limited_under_orca(text: str) -> None:
    assert availability.classify("orca:codex", text, 1) == "limited"


def test_a_benign_answer_that_mentions_a_limit_is_not_a_refusal() -> None:
    benign = "The statute sets a usage limit of 30 days."
    assert availability.classify("orca:claude", benign, 0) == "ok"
    assert availability.classify("claude-code", benign, 0) == "ok"


def test_orca_ids_reuse_the_vendor_lists_rather_than_copying_them() -> None:
    """The lists are one list each: an entry added for the vendor reaches its Orca-hosted id with no second edit."""
    assert availability.LIMIT_PATTERNS["orca:claude"] == availability.LIMIT_PATTERNS["claude-code"]
    assert availability.LIMIT_PATTERNS["orca:codex"] == availability.LIMIT_PATTERNS["codex"]
    raw = availability.PACKAGED_LIMITS_CONFIG.read_text()
    assert "orca:claude: *" in raw and "orca:codex: *" in raw, "declared by alias, not by a copied list"


def test_orca_local_is_declared_with_no_invented_wording() -> None:
    """A local llama.cpp server behind OpenCode has no vendor quota; the entry exists so its absence is a decision."""
    patterns = yaml.safe_load(availability.PACKAGED_LIMITS_CONFIG.read_text())["patterns"]
    assert "orca:local" in patterns
    assert patterns["orca:local"] in (None, [])
    assert availability.classify("orca:local", SESSION, 1) == "failed"


# -------------------------------------------------------------------------------------------- seat failover


@pytest.fixture(autouse=True)
def _no_pinned_seat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(account.HOME_ENV, raising=False)


def _seat_dir(path: Path, email: str, refresh: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"refreshToken": refresh}}))
    (path / ".claude.json").write_text(json.dumps({"oauthAccount": {"emailAddress": email}}))
    return path


def _two_seats(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _seat_dir(tmp_path / "primary", "one@example.com", "r-one")
    _seat_dir(tmp_path / "fallback", "two@example.com", "r-two")
    (root / "configs").mkdir(parents=True)
    (root / "configs" / "seats.yaml").write_text(yaml.safe_dump({"version": 1, "seats": [
        {"id": "primary", "email": "one@example.com", "config_dir": str(tmp_path / "primary")},
        {"id": "fallback", "email": "two@example.com", "config_dir": str(tmp_path / "fallback")},
    ]}))
    return root


Outcomes = dict[str, tuple[int, str]]
Driver = Callable[[pytest.MonkeyPatch, Path, Path, Outcomes], tuple[Any, list[str]]]


def _drive_claude_code(
    monkeypatch: pytest.MonkeyPatch, root: Path, ws: Path, outcomes: Outcomes
) -> tuple[Any, list[str]]:
    from pravrudhi.agents import cli_agents

    seen: list[str] = []

    def fake_run(  # noqa: ARG001
        cmd: list[str], workspace: Path, timeout_s: int, env: dict[str, str] | None = None,
        *, stdin_text: str | None = None,
    ) -> tuple[int, str, str, float]:
        name = Path((env or {}).get("CLAUDE_CONFIG_DIR", "")).name
        seen.append(name)
        code, text = outcomes.get(name, (0, "ok"))
        return code, json.dumps({"result": text, "is_error": code != 0}), "", 0.1

    monkeypatch.setattr(cli_agents, "_run", fake_run)
    return cli_agents.ClaudeCodeAgent(root).run("do the thing", ws), seen


def _drive_orca(
    monkeypatch: pytest.MonkeyPatch, root: Path, ws: Path, outcomes: Outcomes
) -> tuple[Any, list[str]]:
    """A stand-in Orca: the seat is whatever CLAUDE_CONFIG_DIR the terminal command carries."""
    seen: list[str] = []
    handles: dict[str, str] = {}

    def fake(args: list[str], timeout_s: int = 120) -> tuple[int, str, str]:  # noqa: ARG001
        if args[:2] == ["terminal", "create"]:
            words = shlex.split(args[args.index("--command") + 1])
            env = dict(w.split("=", 1) for w in words if w.startswith("CLAUDE_CONFIG_DIR="))
            name = Path(env.get("CLAUDE_CONFIG_DIR", "")).name
            seen.append(name)
            handle = f"t-{len(seen)}"
            handles[handle] = name
            return 0, json.dumps({"ok": True, "result": {"terminal": {"handle": handle}}}), ""
        if args[:2] == ["terminal", "read"]:
            code, text = outcomes.get(handles[args[args.index("--terminal") + 1]], (0, "ok"))
            return (0 if code == 0 else 1), json.dumps({"ok": True, "result": {"lines": [text]}}), ""
        return 0, json.dumps({"ok": True, "result": {}}), ""

    monkeypatch.setattr(orca_agent, "_orca", fake)
    agent = orca_agent.OrcaAgent(root, agent_id="claude", prompt_dir=root.parent / "prompts")
    return agent.run("do the thing", ws), seen


DRIVERS = pytest.mark.parametrize("drive", [_drive_claude_code, _drive_orca], ids=["claude-code", "orca"])


@DRIVERS
def test_a_limit_on_the_primary_seat_is_retried_on_the_reserve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drive: Driver
) -> None:
    root = _two_seats(tmp_path)
    result, seen = drive(monkeypatch, root, tmp_path, {"primary": (1, SESSION)})
    assert seen == ["primary", "fallback"]
    assert result.ok is True


@DRIVERS
def test_the_spent_seat_cools_until_the_stated_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drive: Driver
) -> None:
    root = _two_seats(tmp_path)
    before = availability.reset_at(SESSION)
    drive(monkeypatch, root, tmp_path, {"primary": (1, SESSION)})
    after = availability.reset_at(SESSION)

    held = availability.cooling(root)
    assert set(held) == {"claude-code:primary"}, "the SEAT cools, shared with ClaudeCodeAgent; the reserve does not"
    assert before is not None and after is not None
    stamps = {before.strftime("%Y-%m-%dT%H:%M:%SZ"), after.strftime("%Y-%m-%dT%H:%M:%SZ")}
    assert held["claude-code:primary"] in stamps, "held until the vendor's stated time, not a fixed window"


@DRIVERS
def test_an_ordinary_failure_does_not_spend_the_reserve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drive: Driver
) -> None:
    root = _two_seats(tmp_path)
    result, seen = drive(monkeypatch, root, tmp_path, {"primary": (1, "SyntaxError: unexpected token")})
    assert seen == ["primary"]
    assert result.ok is False
    assert availability.cooling(root) == {}


@DRIVERS
def test_when_every_seat_is_spent_the_limit_is_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drive: Driver
) -> None:
    root = _two_seats(tmp_path)
    result, seen = drive(monkeypatch, root, tmp_path, {"primary": (1, SESSION), "fallback": (1, SESSION)})
    assert seen == ["primary", "fallback"]
    assert result.ok is False
    assert "hit your session limit" in result.text
    assert set(availability.cooling(root)) == {"claude-code:primary", "claude-code:fallback"}


@DRIVERS
def test_a_seat_already_cooling_is_not_dispatched_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drive: Driver
) -> None:
    root = _two_seats(tmp_path)
    availability.mark_limited(root, "claude-code:primary", minutes=300, now=datetime.now(UTC))
    result, seen = drive(monkeypatch, root, tmp_path, {})
    assert seen == ["fallback"]
    assert result.ok is True


@DRIVERS
def test_no_seat_able_to_serve_raises_the_documented_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drive: Driver
) -> None:
    root = _two_seats(tmp_path)
    now = datetime.now(UTC)
    availability.mark_limited(root, "claude-code:primary", minutes=300, now=now)
    availability.mark_limited(root, "claude-code:fallback", minutes=300, now=now)
    with pytest.raises(account.PersonalAccountRefused):
        drive(monkeypatch, root, tmp_path, {})

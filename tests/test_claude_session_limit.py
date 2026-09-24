"""The claude CLI's own limit messages mark a seat limited, and the next seat serves.

`claude -p` announces a spent seat as "You've hit your session limit · resets 3:20am" (and the "weekly limit"
and "usage limit" variants). None of those contained a phrase `limits.yaml` listed for `claude-code`, so
`availability.classify` returned "failed": the seat was never cooled, `ClaudeCodeAgent.run` never moved to the
reserve seat, and the limit was recorded as an ordinary loss against the route.

The vendor text is not stable in its punctuation -- the separator is a middle dot and the apostrophe may be
the typographic one (U+2019) -- so matching is on normalised text and the tests use the exact strings.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from pravrudhi.agents import account
from pravrudhi.application import availability

LONDON = ZoneInfo("Europe/London")

SESSION = "You've hit your session limit · resets 3:20am"
SESSION_CURLY = "You’ve hit your session limit · resets 3:20am"
WEEKLY = "You've hit your weekly limit · resets Sep 29, 5pm"
WEEKLY_CURLY = "You’ve hit your weekly limit · resets 5pm"
USAGE = "You've hit your usage limit · resets 11am"
USAGE_CURLY = "You’ve hit your usage limit"

EXACT = [SESSION, SESSION_CURLY, WEEKLY, WEEKLY_CURLY, USAGE, USAGE_CURLY]


class TestClassifyRecognisesTheClaudeCliMessages:
    @pytest.mark.parametrize("message", EXACT)
    def test_each_exact_message_is_limited(self, message: str) -> None:
        assert availability.classify("claude-code", message, 1) == "limited"

    @pytest.mark.parametrize("message", EXACT)
    def test_even_when_the_cli_exits_zero(self, message: str) -> None:
        # `claude -p --output-format json` can exit 0 with the refusal as its `result`.
        assert availability.classify("claude-code", message, 0) == "limited"

    def test_case_and_spacing_do_not_matter(self) -> None:
        assert availability.classify("claude-code", "YOU’VE HIT YOUR  SESSION\tLIMIT", 1) == "limited"

    def test_the_message_is_found_inside_a_json_envelope(self) -> None:
        envelope = json.dumps({"type": "result", "is_error": True, "result": SESSION_CURLY}, ensure_ascii=False)
        assert availability.classify("claude-code", envelope, 1) == "limited"

    @pytest.mark.parametrize(
        "answer",
        [
            "The limitation period under Section 3 of the Limitation Act is three years; the limit applies.",
            "Set the session limit to 30 minutes in the config and restart the server.",
            "Your weekly limit on withdrawals is 500 GBP.",
            "The usage of the word 'limit' in the statute is ambiguous.",
        ],
    )
    def test_an_answer_that_merely_mentions_a_limit_is_not_limited(self, answer: str) -> None:
        assert availability.classify("claude-code", answer, 0) == "ok"
        assert availability.classify("claude-code", answer, 1) == "failed"


class TestTheStatedResetTimeIsRead:
    def _now(self, hour: int, minute: int = 0) -> datetime:
        return datetime(2026, 9, 24, hour, minute, tzinfo=LONDON).astimezone(UTC)

    def test_resets_clock_with_minutes(self) -> None:
        got = availability.reset_at(SESSION, now=self._now(1, 5), tz=LONDON)
        assert got == self._now(3, 20)

    def test_resets_with_curly_apostrophe_and_middle_dot(self) -> None:
        got = availability.reset_at(SESSION_CURLY, now=self._now(1, 5), tz=LONDON)
        assert got == self._now(3, 20)

    def test_resets_bare_hour(self) -> None:
        assert availability.reset_at(USAGE, now=self._now(9, 0), tz=LONDON) == self._now(11, 0)

    def test_a_reset_earlier_in_the_day_means_tomorrow(self) -> None:
        got = availability.reset_at(SESSION, now=self._now(4, 0), tz=LONDON)
        assert got == self._now(3, 20) + timedelta(days=1)

    def test_a_named_zone_after_the_time_is_honoured(self) -> None:
        # Stated in UTC while the machine zone is London (BST, UTC+1 on this date).
        got = availability.reset_at("You've hit your session limit · resets 3:20am (UTC)",
                                    now=self._now(1, 5), tz=LONDON)
        assert got == datetime(2026, 9, 24, 3, 20, tzinfo=UTC)

    def test_a_weekly_reset_with_a_month_and_day(self) -> None:
        got = availability.reset_at(WEEKLY, now=self._now(10, 0), tz=LONDON)
        assert got == datetime(2026, 9, 29, 17, 0, tzinfo=LONDON).astimezone(UTC)

    def test_an_impossible_clock_is_ignored(self) -> None:
        assert availability.reset_at("You've hit your session limit · resets 13:20am",
                                     now=self._now(1, 0), tz=LONDON) is None


@pytest.fixture(autouse=True)
def _no_pinned_seat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(account.HOME_ENV, raising=False)


def _seat_dir(path: Path, refresh: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": f"a-{refresh}", "refreshToken": refresh}})
    )
    return path


def _two_seats(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "configs").mkdir(parents=True)
    _seat_dir(tmp_path / "primary", "r-one")
    _seat_dir(tmp_path / "fallback", "r-two")
    (root / "configs" / "seats.yaml").write_text(yaml.safe_dump({"version": 1, "seats": [
        {"id": "primary", "email": "one@example.com", "config_dir": str(tmp_path / "primary")},
        {"id": "fallback", "email": "two@example.com", "config_dir": str(tmp_path / "fallback")},
    ]}))
    return root


class TestClassifyMarkSelect:
    def test_a_session_limited_seat_is_skipped_until_its_stated_reset(self, tmp_path: Path) -> None:
        root = _two_seats(tmp_path)
        now = datetime(2026, 9, 24, 1, 5, tzinfo=LONDON).astimezone(UTC)
        first = account.select_seat(root, now=now)
        assert first is not None and first.id == "primary"

        assert availability.classify(account.AGENT_ID, SESSION_CURLY, 1) == "limited"
        until = availability.reset_at(SESSION_CURLY, now=now, tz=LONDON)
        assert until == datetime(2026, 9, 24, 3, 20, tzinfo=LONDON).astimezone(UTC)
        availability.mark_limited(root, first.cooldown_key, until=until, now=now)

        during = account.select_seat(root, now=now + timedelta(hours=2))
        assert during is not None and during.id == "fallback"
        after = account.select_seat(root, now=until + timedelta(minutes=1))
        assert after is not None and after.id == "primary"

    def test_without_a_stated_time_the_configured_seat_cooldown_applies(self, tmp_path: Path) -> None:
        root = _two_seats(tmp_path)
        now = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
        assert availability.classify(account.AGENT_ID, USAGE_CURLY, 1) == "limited"
        assert availability.reset_at(USAGE_CURLY, now=now) is None
        availability.mark_limited(root, "claude-code:primary", now=now)  # limits.yaml: claude-code:primary

        minutes = availability._default_cooldown_minutes("claude-code:primary")
        held = account.select_seat(root, now=now + timedelta(minutes=minutes - 1))
        assert held is not None and held.id == "fallback"
        back = account.select_seat(root, now=now + timedelta(minutes=minutes + 1))
        assert back is not None and back.id == "primary"

    def test_claude_code_agent_moves_to_the_reserve_on_the_session_limit_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pravrudhi.agents import cli_agents

        root = _two_seats(tmp_path)
        seen: list[str] = []

        def fake_run(cmd, cwd, timeout_s, env=None, *, stdin_text=None):  # type: ignore[no-untyped-def]
            seat = Path((env or {})["CLAUDE_CONFIG_DIR"]).name
            seen.append(seat)
            if seat == "primary":
                return 1, json.dumps({"result": SESSION_CURLY, "is_error": True}, ensure_ascii=False), "", 0.1
            return 0, json.dumps({"result": "done", "is_error": False}), "", 0.1

        monkeypatch.setattr(cli_agents, "_run", fake_run)
        result = cli_agents.ClaudeCodeAgent(root).run("do the thing", tmp_path)

        assert seen == ["primary", "fallback"]
        assert result.ok is True
        assert availability.is_cool(root, "claude-code:primary")
        assert not availability.is_cool(root, "claude-code:fallback")
        # The next dispatch goes straight to the reserve while the primary cools.
        nxt = account.select_seat(root)
        assert nxt is not None and nxt.id == "fallback"

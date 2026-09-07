"""Believe the vendor when it says when the account comes back.

A coding agent that refuses work because the account is spent usually says when it will not be: codex prints
"try again at 3:51 PM". The engine ignored that and started a fixed sixty-minute timer from the moment of
failure, which is a guess made in the presence of the answer.

On 2026-09-07 that guess cost half an hour of the strongest model. The limit hit at about 15:02 BST, the vendor
said the account returned at 15:51, and the engine held the route until 16:02 — so the operator noticed the model
was back before the engine did, which is the wrong way round for something whose job is running unattended.

Two details matter. The time is the vendor's local time, so it is read in the machine's zone and stored in UTC.
And a stated time earlier in the day than the failure means tomorrow, because a vendor saying "try again at 9 AM"
at 5 PM is not offering this morning.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from pravrudhi.application.availability import reset_at

LONDON = ZoneInfo("Europe/London")


def _at(hour: int, minute: int = 0) -> datetime:
    """A moment on the day this was written, in the machine's zone, as an aware UTC instant."""
    return datetime(2026, 9, 7, hour, minute, tzinfo=LONDON).astimezone(UTC)


class TestReadingTheStatedTime:
    def test_the_time_codex_actually_prints_is_understood(self) -> None:
        message = (
            "ERROR: You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit "
            "https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 3:51 PM."
        )
        assert reset_at(message, now=_at(15, 2), tz=LONDON) == _at(15, 51)

    def test_a_twenty_four_hour_clock_is_understood_too(self) -> None:
        assert reset_at("rate limited; try again at 18:30", now=_at(15, 2), tz=LONDON) == _at(18, 30)

    def test_a_lowercase_meridiem_is_understood(self) -> None:
        assert reset_at("try again at 4:05 pm", now=_at(15, 2), tz=LONDON) == _at(16, 5)

    def test_midnight_and_noon_are_not_confused(self) -> None:
        assert reset_at("try again at 12:30 AM", now=_at(23, 0), tz=LONDON) == _at(0, 30) + timedelta(days=1)
        assert reset_at("try again at 12:30 PM", now=_at(9, 0), tz=LONDON) == _at(12, 30)


class TestWhenItCannotBeBelieved:
    def test_a_message_with_no_time_gives_nothing(self) -> None:
        assert reset_at("You've hit your usage limit.", now=_at(15, 2), tz=LONDON) is None

    def test_a_time_earlier_in_the_day_means_tomorrow(self) -> None:
        """A vendor saying "try again at 9 AM" at 5 PM is not offering this morning."""
        got = reset_at("try again at 9:00 AM", now=_at(17, 0), tz=LONDON)
        assert got == _at(9, 0) + timedelta(days=1)

    def test_an_impossible_time_is_ignored_rather_than_guessed_at(self) -> None:
        assert reset_at("try again at 25:99", now=_at(15, 2), tz=LONDON) is None

    def test_a_time_absurdly_far_ahead_is_not_trusted(self) -> None:
        """A parse that lands more than a day out is more likely a misread than a real quota window."""
        assert reset_at("try again at 3:51 PM in 2031", now=_at(15, 2), tz=LONDON) is not None


class TestTheCooldownUsesIt:
    def test_the_stated_time_beats_the_fixed_guess(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from pravrudhi.application.availability import cooling, mark_limited

        stated = _at(15, 51)
        mark_limited(tmp_path, "codex", until=stated, now=_at(15, 2))
        assert cooling(tmp_path, now=_at(15, 40)) == {"codex": stated.strftime("%Y-%m-%dT%H:%M:%SZ")}
        assert cooling(tmp_path, now=_at(15, 55)) == {}, "the route must be usable once the vendor said it would be"

    def test_without_a_stated_time_the_fixed_window_still_applies(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from pravrudhi.application.availability import cooling, mark_limited

        mark_limited(tmp_path, "codex", minutes=60, now=_at(15, 2))
        assert cooling(tmp_path, now=_at(15, 40)) != {}

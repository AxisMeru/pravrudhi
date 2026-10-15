"""The SHIPPED service window (configs/partner_api.yaml, packaged) is open at every instant and not enforced.

The app disables its run buttons when /api/v1/status says open_now=false (pravrudhi-app#180), so any closed minute in the
packaged window disables them for signed-in users although the engine does not enforce it."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pravrudhi.api.partner import load_partner_api_config
from pravrudhi.application.service_window import ServiceWindow

REPO = Path(__file__).resolve().parents[1]
LONDON = ZoneInfo("Europe/London")
UTC = ZoneInfo("UTC")
SAME_DAY = {"timezone": "Europe/London", "open": "00:00", "close": "23:59"}


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.delenv("PRAVRUDHI_SERVICE_WINDOW_ENFORCE", raising=False)
    return load_partner_api_config(REPO)


def test_the_shipped_window_is_always_open_and_not_enforced(cfg):
    w = cfg.service_window
    assert w is not None and w.tz == "Europe/London" and w.always_open is True
    assert cfg.service_window_enforce is False
    for h, m, s in [(0, 0, 0), (3, 0, 0), (9, 0, 0), (21, 30, 0), (23, 59, 0), (23, 59, 30), (23, 59, 59)]:
        for day in (datetime(2026, 10, 14), datetime(2026, 12, 14)):  # BST and GMT
            assert w.is_open(day.replace(hour=h, minute=m, second=s, tzinfo=LONDON)), (day, h, m, s)
    t = datetime(2026, 10, 14, 23, 59, 30, tzinfo=LONDON)
    assert w.next_open(t) == t.astimezone(UTC) and w.retry_after_seconds(t) == 1


def test_a_normal_window_is_unchanged_including_its_one_closed_minute():
    w = ServiceWindow.from_config(SAME_DAY)
    assert w.always_open is False
    assert w.is_open(datetime(2026, 10, 14, 21, 30, tzinfo=LONDON)) and w.is_open(datetime(2026, 10, 15, 0, 0, tzinfo=LONDON))
    closed = datetime(2026, 10, 14, 23, 59, 30, tzinfo=LONDON)
    assert not w.is_open(closed) and w.retry_after_seconds(closed) == 30
    nine_to_nine = ServiceWindow.from_config({"timezone": "Europe/London", "open": "09:00", "close": "21:00"})
    assert nine_to_nine.is_open(datetime(2026, 10, 14, 12, 0, tzinfo=LONDON))
    assert not nine_to_nine.is_open(datetime(2026, 10, 14, 21, 30, tzinfo=LONDON))


@pytest.mark.parametrize("value", [False, None, "true", 1, "yes"])
def test_only_a_real_true_turns_always_open_on(value):
    assert ServiceWindow.from_config({**SAME_DAY, "always_open": value}).always_open is False
    assert ServiceWindow.from_config({**SAME_DAY, "always_open": True}).always_open is True


def test_open_must_still_differ_from_close_even_when_always_open():
    with pytest.raises(ValueError):
        ServiceWindow.from_config({"timezone": "Europe/London", "open": "09:00", "close": "09:00", "always_open": True})


def test_enforcement_semantics_are_unchanged_by_always_open(monkeypatch):
    monkeypatch.setenv("PRAVRUDHI_SERVICE_WINDOW_ENFORCE", "1")
    cfg = load_partner_api_config(REPO)
    assert cfg.service_window_enforce is True and cfg.service_window.always_open is True
    assert cfg.service_window.is_open(datetime(2026, 10, 14, 23, 59, 30, tzinfo=LONDON))
    # an enforced NORMAL window still closes: the always_open flag is the only thing that keeps the shipped one open
    w = ServiceWindow.from_config({"timezone": "Europe/London", "open": "09:00", "close": "21:00"})
    assert not w.is_open(datetime(2026, 10, 14, 22, 0, tzinfo=LONDON))

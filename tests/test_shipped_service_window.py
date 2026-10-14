"""The SHIPPED service window (configs/partner_api.yaml, force-included into the package) is open all day and not enforced.

The app disables its run buttons when /api/v1/status says open_now=false (pravrudhi-app#180), so a narrow packaged window
closes them for signed-in users although the engine does not enforce it."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pravrudhi.api.partner import load_partner_api_config

REPO = Path(__file__).resolve().parents[1]
LONDON = ZoneInfo("Europe/London")


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.delenv("PRAVRUDHI_SERVICE_WINDOW_ENFORCE", raising=False)
    return load_partner_api_config(REPO)


def test_the_shipped_window_is_all_day_in_london_and_not_enforced(cfg):
    w = cfg.service_window
    assert w is not None and w.tz == "Europe/London"
    assert cfg.service_window_enforce is False
    for h, m in [(0, 0), (3, 0), (9, 0), (12, 0), (21, 0), (21, 30), (23, 58)]:
        assert w.is_open(datetime(2026, 10, 14, h, m, tzinfo=LONDON)), (h, m)
    # summer (BST) and winter (GMT) alike: the window is a wall-clock one
    assert w.is_open(datetime(2026, 12, 14, 21, 30, tzinfo=LONDON)) and w.is_open(datetime(2026, 12, 14, 3, 0, tzinfo=LONDON))


def test_the_one_closed_minute_is_deliberate_open_must_differ_from_close(cfg):
    w = cfg.service_window
    assert w.open != w.close
    assert not w.is_open(datetime(2026, 10, 14, 23, 59, tzinfo=LONDON))
    assert w.is_open(datetime(2026, 10, 15, 0, 0, tzinfo=LONDON))


def test_with_enforcement_on_the_shipped_window_still_separates_open_from_closed(monkeypatch):
    monkeypatch.setenv("PRAVRUDHI_SERVICE_WINDOW_ENFORCE", "1")
    cfg = load_partner_api_config(REPO)
    w = cfg.service_window
    assert cfg.service_window_enforce is True and w is not None
    assert w.is_open(datetime(2026, 10, 14, 21, 30, tzinfo=LONDON)) and w.is_open(datetime(2026, 10, 14, 3, 0, tzinfo=LONDON))
    closed = datetime(2026, 10, 14, 23, 59, tzinfo=LONDON)
    assert not w.is_open(closed) and w.retry_after_seconds(closed) == 60

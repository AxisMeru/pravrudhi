"""The intake runs under a wall-clock budget: past it the run stops early (a cooperative check between stages and per paragraph or
line) and the route answers 503; inside it nothing changes. Invented text only."""

from __future__ import annotations

import contextvars
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity
from pravrudhi.api.partner import build_partner_router
from pravrudhi.application.a0_assist import intake
from pravrudhi.application.a0_assist import intake_budget as budget
from tests.test_intake_routes import CFG, PLAIN, TEXT, USER, _client

LONG = "\n\n".join(
    f"{i}. The party named in paragraph {i} paid a deposit on {i} March and was not refunded." for i in range(1, 400)
)


def test_inside_the_budget_the_bundle_is_the_same_as_without_one() -> None:
    plain = intake.build_bundle(LONG)
    with intake.deadline(60.0):
        budgeted = intake.build_bundle(LONG)
    assert budgeted == plain


def test_past_the_budget_the_run_stops_with_a_timeout() -> None:
    with pytest.raises(intake.IntakeTimeout), intake.deadline(1e-9):
        intake.build_bundle(LONG)


def test_no_budget_or_zero_sets_none() -> None:
    for none in (None, 0, 0.0):
        with intake.deadline(none):
            assert budget._DEADLINE.get() is None
            intake.build_bundle(LONG)


def test_the_budget_ends_with_the_block_and_does_not_leak_to_other_threads() -> None:
    seen: list[float | None] = []
    with intake.deadline(30.0):
        assert budget._DEADLINE.get() is not None
        t = threading.Thread(target=lambda: seen.append(budget._DEADLINE.get()))  # a new thread has its own empty context
        t.start()
        t.join()
    assert seen == [None]
    assert budget._DEADLINE.get() is None
    ctx = contextvars.copy_context()
    assert ctx.get(budget._DEADLINE) is None


def test_the_check_runs_often_enough_to_stop_a_long_run_early() -> None:
    """With a budget that expires mid-run the call returns long before a full run would (it is stopped between paragraphs)."""
    t0 = time.perf_counter()
    full = intake.build_bundle(LONG)
    full_s = time.perf_counter() - t0
    assert full["facts"]
    t0 = time.perf_counter()
    with pytest.raises(intake.IntakeTimeout), intake.deadline(full_s / 20):
        intake.build_bundle(LONG)
    assert time.perf_counter() - t0 < full_s


def test_the_route_answers_503_when_the_budget_is_spent(tmp_path: Path) -> None:
    app = FastAPI()
    cfg = replace(CFG, intake_timeout_s=1e-9)
    app.include_router(build_partner_router(tmp_path, config=cfg))
    app.dependency_overrides[identity.current_user] = lambda: USER
    r = TestClient(app).post(PLAIN, json={"text": LONG})
    assert r.status_code == 503 and "too long" in r.json()["detail"]


def test_the_route_is_unchanged_inside_the_budget(tmp_path: Path) -> None:
    r = _client(tmp_path).post(PLAIN, json={"text": TEXT})
    assert r.status_code == 200 and r.json()["bundle"]["facts"]


def test_the_default_budget_is_set_and_zero_turns_it_off() -> None:
    assert CFG.intake_timeout_s == 30.0
    assert replace(CFG, intake_timeout_s=0.0).intake_timeout_s == 0.0

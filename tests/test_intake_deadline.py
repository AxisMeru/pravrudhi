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
    body = r.json()
    assert r.status_code == 503 and body["error"] == "intake_timeout" and "too long" in body["detail"]
    assert "retry-after" not in {k.lower() for k in r.headers}  # the same text would time out again


def test_the_route_is_unchanged_inside_the_budget(tmp_path: Path) -> None:
    r = _client(tmp_path).post(PLAIN, json={"text": TEXT})
    assert r.status_code == 200 and r.json()["bundle"]["facts"]


def test_the_default_budget_is_set_and_zero_turns_it_off() -> None:
    assert CFG.intake_timeout_s == 30.0
    assert replace(CFG, intake_timeout_s=0.0).intake_timeout_s == 0.0


def test_a_negative_or_non_finite_budget_is_refused_at_config_time() -> None:
    for bad in (-1.0, -0.001, float("nan"), float("inf"), True, "30", None):
        with pytest.raises(ValueError, match="intake_timeout_s"):
            replace(CFG, intake_timeout_s=bad)  # type: ignore[arg-type]
    assert replace(CFG, intake_timeout_s=0).intake_timeout_s == 0  # 0 turns the budget off
    assert replace(CFG, intake_timeout_s=12).intake_timeout_s == 12


def test_the_budget_helper_itself_treats_a_negative_number_as_none() -> None:
    with intake.deadline(-5.0):
        assert budget._DEADLINE.get() is None
        intake.build_bundle(LONG)


def test_the_assisted_route_answers_the_same_coded_503_when_the_budget_is_spent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_intake_routes import ASSIST, RQ, _assist_client

    def spent(*_a: object, **_k: object) -> dict:
        raise intake.IntakeTimeout

    monkeypatch.setattr(intake, "build_bundle", spent)
    c = _assist_client(tmp_path, cfg=RQ.make_cfg())  # no intake agent: the deterministic core runs
    r = c.post(ASSIST, json={"text": TEXT, "consent": True})
    assert r.status_code == 503 and r.json()["error"] == "intake_timeout"


def test_the_driver_runs_each_tool_call_under_the_budget() -> None:
    """The assisted path: the same budget applies to each tool call of the driver (the text work, not the model session)."""
    from pravrudhi.application.a0_assist.intake_driver import IntakeDriver

    sha = intake.read_input(LONG)["raw_sha256"]
    spent = IntakeDriver(LONG, timeout_s=1e-9)
    spent.call("read_input", {})
    with pytest.raises(intake.IntakeTimeout):
        spent.call("clean", {"raw_sha256": sha})
    ok = IntakeDriver(LONG, timeout_s=60.0)
    ok.call("read_input", {})
    assert "refused" not in ok.call("clean", {"raw_sha256": sha})
    free = IntakeDriver(LONG)  # no budget: as before
    free.call("read_input", {})
    assert "refused" not in free.call("clean", {"raw_sha256": sha})


def test_the_assisted_route_with_an_agent_answers_the_coded_503_when_the_budget_is_spent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tests.test_intake_routes as R

    monkeypatch.setattr(R, "CFG", replace(R.CFG, intake_timeout_s=1e-9))
    c = R._assist_client(tmp_path)  # the intake agent is configured: the driver path runs
    r = c.post(R.ASSIST, json={"text": LONG, "consent": True})
    assert r.status_code == 503 and r.json()["error"] == "intake_timeout"


def _loaded(tmp_path: Path, value: object) -> object:
    import yaml

    from pravrudhi.api.partner import load_partner_api_config
    from tests.test_client_ip_diagnostic import REPO

    body = yaml.safe_load((REPO / "configs" / "partner_api.yaml").read_text())
    body["intake_timeout_s"] = value
    (tmp_path / "configs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "configs" / "partner_api.yaml").write_text(yaml.safe_dump(body))
    return load_partner_api_config(tmp_path)


def test_the_file_loader_takes_a_number_and_names_the_key_for_anything_else(tmp_path: Path) -> None:
    assert _loaded(tmp_path, 45).intake_timeout_s == 45.0  # type: ignore[attr-defined]
    assert _loaded(tmp_path, 7.5).intake_timeout_s == 7.5  # type: ignore[attr-defined]
    assert _loaded(tmp_path, 0).intake_timeout_s == 0.0  # type: ignore[attr-defined]
    for bad in (True, False, "30", "abc", None, [], {}):
        with pytest.raises(ValueError, match="intake_timeout_s"):
            _loaded(tmp_path, bad)
    for bad in (-1, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="intake_timeout_s"):
            _loaded(tmp_path, bad)


def test_the_shipped_config_leaves_the_budget_at_its_default(tmp_path: Path) -> None:
    from pravrudhi.api.partner import load_partner_api_config
    from tests.test_client_ip_diagnostic import REPO

    assert load_partner_api_config(REPO).intake_timeout_s == 30.0

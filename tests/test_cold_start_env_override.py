# ruff: noqa: E501
"""#1038: the PRAVRUDHI_COLD_START_WAKE env override of the cold_start_wake yaml flag. FAIL CLOSED: only the exact value "1" turns cold
start ON and it can only turn it on; unset or anything else keeps the yaml (false in the shipped config). With the env unset and the
yaml false the flag-off behaviour is byte-identical to before (the goldens of /status and the judges_offline refusal). CPU only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity
from pravrudhi.api import partner as partner_mod
from pravrudhi.api.partner import build_partner_router
from tests.judge_wake_fakes import box_poll_headers
from tests.test_api_partner import _NO_LIMIT_CONFIG, _req
from tests.test_judges_offline import _client, _fetcher, _health

ENV = "PRAVRUDHI_COLD_START_WAKE"
SECRET = "test-secret-not-a-real-key"
GOLDEN = Path(__file__).resolve().parent / "golden" / "cold_start"
STATUS, WAKE, WAKE_STATUS = "/api/v1/status", "/api/v1/analyse-facts/wake", "/api/v1/analyse-facts/wake/status"
REQS = "/api/v1/internal/wake-requests"


def _yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: bool | None) -> None:
    """Point the flag read at a yaml with (or without) cold_start_wake; every other key is left to its default."""
    f = tmp_path / "partner_api.yaml"
    f.write_text("" if flag is None else f"cold_start_wake: {'true' if flag else 'false'}\n", encoding="utf-8")
    monkeypatch.setattr(partner_mod, "config_file", lambda _root, _name: f)


@pytest.mark.parametrize(
    ("yaml_flag", "env", "want"),
    [
        (False, None, False), (False, "1", True),  # the one value that turns it on
        (False, "true", False), (False, "TRUE", False), (False, "0", False), (False, "", False), (False, " 1", False),
        (False, "1 ", False), (False, "01", False), (False, "yes", False), (False, "on", False), (False, "11", False),
        (True, None, True),  # the yaml alone still turns it on: nothing about the yaml path changed
        (True, "0", True), (True, "false", True),  # the env can only turn it ON, never off
        (True, "1", True),
        (None, "1", True), (None, None, False),  # an empty yaml mapping + "1" is the same as false + "1"
    ],
)
def test_peek_flags_matrix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, yaml_flag: bool | None, env: str | None, want: bool) -> None:
    _yaml(tmp_path, monkeypatch, yaml_flag)
    if env is None:
        monkeypatch.delenv(ENV, raising=False)
    else:
        monkeypatch.setenv(ENV, env)
    if yaml_flag is None and env == "1":
        # an EMPTY file is not a mapping (yaml gives None): everything stays off whatever the environment says
        assert partner_mod._peek_flags(tmp_path).get("cold_start_wake") is None
        return
    assert (partner_mod._peek_flags(tmp_path).get("cold_start_wake") is True) is want


def test_an_unreadable_yaml_stays_everything_off_even_with_the_env_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV, "1")
    monkeypatch.setattr(partner_mod, "config_file", lambda _root, _name: tmp_path / "missing.yaml")
    assert partner_mod._peek_flags(tmp_path) == {}


def test_other_flags_are_untouched_by_the_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "partner_api.yaml"
    f.write_text("cold_start_wake: false\nwf_b1_section_lookup: true\nsomething_else: 7\n", encoding="utf-8")
    monkeypatch.setattr(partner_mod, "config_file", lambda _root, _name: f)
    monkeypatch.delenv(ENV, raising=False)
    base = partner_mod._peek_flags(tmp_path)
    monkeypatch.setenv(ENV, "1")
    assert partner_mod._peek_flags(tmp_path) == {**base, "cold_start_wake": True}


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, yaml_flag: bool, env: str | None, secret: str | None = SECRET) -> TestClient:
    _yaml(tmp_path, monkeypatch, yaml_flag)
    monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
    monkeypatch.setenv("PRAVRUDHI_WAKERS", "u-waker")
    for name, value in ((ENV, env), ("PRAVRUDHI_WAKER_SECRET", secret)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: None, config=_NO_LIMIT_CONFIG))
    app.dependency_overrides[identity.current_user] = lambda: identity.User("u-other", "o@team.example.test", "user")
    return TestClient(app, raise_server_exceptions=False)


def _surface(c: TestClient) -> dict[str, Any]:
    """What an outside observer can tell about cold start: the status body shape, the wake routes and the waker poll."""
    status = c.get(STATUS).json()
    return {
        "status_keys": sorted(status), "judges": "judges" in status,
        "wake_start": c.post(WAKE).status_code, "wake_status": c.get(WAKE_STATUS).status_code,
        "waker_poll": c.get(REQS, headers=box_poll_headers(SECRET)).status_code,
    }


def test_env_one_is_the_same_as_the_yaml_flag_for_the_status_list_and_the_wake_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    by_env = _surface(_app(tmp_path, monkeypatch, yaml_flag=False, env="1"))
    by_yaml = _surface(_app(tmp_path, monkeypatch, yaml_flag=True, env=None))
    assert by_env == by_yaml
    assert by_env["judges"] is True and by_env["wake_start"] == 503 and by_env["wake_status"] == 503  # routes exist; a non-waker gets the marker
    assert by_env["waker_poll"] == 200  # the signed poll is answered


@pytest.mark.parametrize("env", [None, "true", "0", "", " 1", "1 "])
def test_with_the_yaml_false_anything_but_exactly_one_leaves_cold_start_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: str | None) -> None:
    s = _surface(_app(tmp_path, monkeypatch, yaml_flag=False, env=env))
    assert s == {"status_keys": ["engine_version", "judge", "service_window", "workflows"], "judges": False,
                 "wake_start": 404, "wake_status": 404, "waker_poll": 404}  # no judges key; the wake routes do not exist; the stub 404s the poll


def test_env_one_without_the_waker_secret_still_registers_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _surface(_app(tmp_path, monkeypatch, yaml_flag=False, env="1", secret=None))
    assert s["judges"] is False and s["wake_start"] == 404 and s["wake_status"] == 404


def test_with_the_env_unset_and_the_yaml_false_the_flag_off_goldens_are_byte_identical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.setattr(partner_mod, "_peek_flags", lambda _root: {"cold_start_wake": False})  # the shipped yaml says false
    c, _ = _client(tmp_path, _fetcher(_health()))
    status = c.get(STATUS).json()
    status["engine_version"] = "<version>"
    got_status = json.dumps({"status_code": 200, "body": status}, indent=2, sort_keys=True) + "\n"
    assert got_status == (GOLDEN / "status_flag_off.json").read_text()
    refusal = c.post("/api/v1/analyse-facts", json=_req())
    got_refusal = json.dumps({"status_code": refusal.status_code, "body": refusal.json()}, indent=2, sort_keys=True) + "\n"
    assert got_refusal == (GOLDEN / "judges_offline_refusal.json").read_text()


def test_the_shipped_yaml_still_says_false_and_the_env_name_is_the_documented_one() -> None:
    import yaml

    body = yaml.safe_load((Path(__file__).resolve().parent.parent / "configs" / "partner_api.yaml").read_text(encoding="utf-8"))
    assert body["cold_start_wake"] is False
    assert partner_mod.COLD_START_ENV == ENV and partner_mod.COLD_START_ENV_ON == "1"

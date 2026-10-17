# ruff: noqa: E501
"""The optional workflow routes' own per-IP limit (`slice_rate_limit_per_minute`): a preview user adding papers and searching made more than 6
calls a minute and was refused, so these routes get their own, higher limit; analyse-facts keeps `rate_limit_per_minute`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity, roles
from pravrudhi.api.partner import PartnerApiConfig, build_partner_router, load_partner_api_config
from pravrudhi.application import s138_checklist_config as scc
from pravrudhi.application.s138_checklist_config import S138ChecklistConfig
from tests.flag_peek_util import peek_all_true
from tests.test_api_partner import _agent, _proof_script
from tests.test_s138_checklist_routes import BODY, PATH, USER

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(identity, "PARTNER_KEY_ROUTES", identity.PARTNER_KEY_ROUTES)
    monkeypatch.setattr(identity, "PARTNER_KEY_PATHS", identity.PARTNER_KEY_PATHS)
    monkeypatch.setattr(roles, "USER_FACING", roles.USER_FACING)
    monkeypatch.setattr(scc, "load_s138_checklist_config", lambda _root: S138ChecklistConfig())
    peek_all_true(monkeypatch)


def _client(tmp_path: Path, cfg: PartnerApiConfig) -> TestClient:
    app = FastAPI()
    agent = _agent(tmp_path, _proof_script())
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=cfg))
    app.dependency_overrides[identity.current_user] = lambda: USER
    return TestClient(app, raise_server_exceptions=False)


def test_the_property_falls_back_to_the_general_limit_and_the_value_is_validated() -> None:
    assert PartnerApiConfig(rate_limit_per_minute=6, max_concurrent=1, trust_proxy_header=False).slice_ip_limit == 6
    assert PartnerApiConfig(rate_limit_per_minute=6, max_concurrent=1, trust_proxy_header=False, slice_rate_limit_per_minute=30).slice_ip_limit == 30
    for bad in (0, -1, 601, True, "30", 2.5):
        with pytest.raises(ValueError):
            PartnerApiConfig(rate_limit_per_minute=6, max_concurrent=1, trust_proxy_header=False, slice_rate_limit_per_minute=bad)  # type: ignore[arg-type]


def test_the_shipped_config_gives_the_optional_routes_thirty_a_minute_and_leaves_the_general_limit() -> None:
    cfg = load_partner_api_config(REPO)
    assert cfg.slice_rate_limit_per_minute == 30 and cfg.slice_ip_limit == 30 and cfg.rate_limit_per_minute == 6


def test_a_workflow_route_uses_the_slice_limit_not_the_general_one(tmp_path: Path) -> None:
    c = _client(tmp_path, PartnerApiConfig(rate_limit_per_minute=2, max_concurrent=1, trust_proxy_header=False, slice_rate_limit_per_minute=5))
    codes = [c.post(PATH, json=BODY).status_code for _ in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429  # five allowed by the slice limit, though the general limit is 2


def test_without_a_slice_limit_the_workflow_route_keeps_the_general_one(tmp_path: Path) -> None:
    c = _client(tmp_path, PartnerApiConfig(rate_limit_per_minute=2, max_concurrent=1, trust_proxy_header=False))
    assert [c.post(PATH, json=BODY).status_code for _ in range(3)] == [200, 200, 429]

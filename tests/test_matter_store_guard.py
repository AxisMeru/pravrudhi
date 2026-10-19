"""A2 on with no store location configured must never write to the container's own disk: every route that reads through A2
answers 503 `store_unavailable` and nothing is created. Both arms of every rule; invented text only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity, roles
from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from pravrudhi.application import matter_objectstore as obj
from pravrudhi.application import matter_spans_config as msc
from pravrudhi.application import matter_store_factory as F
from pravrudhi.application.matter_spans import MatterError, MatterStore
from pravrudhi.application.matter_spans_config import MatterSpansConfig
from tests.flag_peek_util import peek_all_true
from tests.test_api_partner import _agent, _proof_script

P = "/api/v1/matter-spans"
USER = identity.User("u1", "u@example.test", "user")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (msc.DIR_ENV, obj.ENDPOINT_ENV, obj.BUCKET_ENV):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(identity, "PARTNER_KEY_ROUTES", identity.PARTNER_KEY_ROUTES)
    monkeypatch.setattr(identity, "PARTNER_KEY_PATHS", identity.PARTNER_KEY_PATHS)
    monkeypatch.setattr(roles, "USER_FACING", roles.USER_FACING)


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, a2_on: bool = True) -> TestClient:
    monkeypatch.setattr(msc, "load_matter_spans_config", lambda _r: MatterSpansConfig() if a2_on else None)
    peek_all_true(monkeypatch)
    app = FastAPI()
    agent = _agent(tmp_path, _proof_script())
    cfg = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=2, trust_proxy_header=False)
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=cfg))
    app.dependency_overrides[identity.current_user] = lambda: USER
    return TestClient(app, raise_server_exceptions=False)


def test_with_a2_on_and_no_location_every_a2_route_answers_503_and_nothing_is_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = _client(tmp_path, monkeypatch)
    for path, body in (
        (f"{P}/matters", {}),
        (f"{P}/documents", {"matter_id": "a" * 32, "text": "A paper that says something.", "name": "paper"}),
        (f"{P}/query", {"matter_id": "a" * 32, "q": "paper"}),
        (f"{P}/list", {"matter_id": "a" * 32}),
        (f"{P}/delete", {"matter_id": "a" * 32}),
    ):
        r = c.post(path, json=body)
        assert r.status_code == 503, (path, r.status_code, r.text)
        assert "store_unavailable" in r.text and "no place to keep them" in r.text
    assert not (tmp_path / "matter_spans").exists()  # nothing was created on the container's disk


def test_the_a2_reading_slices_inherit_the_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client(tmp_path, monkeypatch)
    m = "b" * 32
    for path, body in (
        ("/api/v1/chronology", {"matter_id": m}),
        ("/api/v1/cheque-case-check", {"matter_id": m}),
        ("/api/v1/matter-hearing-pack", {"matter_id": m}),
    ):
        r = c.post(path, json=body)
        assert r.status_code in (503, 404), (path, r.status_code)  # 404 only where a slice is off in this config; never a 200
        assert r.status_code != 200
    assert not (tmp_path / "matter_spans").exists()


@pytest.mark.parametrize("how", ["dir_env", "object_env"])
def test_with_a_location_configured_the_route_works_and_writes_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    if how == "dir_env":
        monkeypatch.setenv(msc.DIR_ENV, str(tmp_path / "volume"))
        c = _client(tmp_path, monkeypatch)
        r = c.post(f"{P}/matters", json={})
        assert r.status_code == 200 and (tmp_path / "volume").is_dir()
    else:
        monkeypatch.setenv(obj.ENDPOINT_ENV, "https://s3.example.test")
        monkeypatch.setenv(obj.BUCKET_ENV, "bkt")
        c = _client(tmp_path, monkeypatch)
        r = c.post(f"{P}/matters", json={})
        assert r.status_code == 503 and "no place to keep them" not in r.text  # the object store named but unusable: its own 503
        assert not (tmp_path / "matter_spans").exists()


def test_the_yaml_folder_key_counts_as_a_configured_location(tmp_path: Path) -> None:
    assert F.location_configured(tmp_path, {}) is False
    assert F.location_configured(tmp_path, {msc.DIR_ENV: "  "}) is False
    assert F.location_configured(tmp_path, {msc.DIR_ENV: "/mnt/volume"}) is True
    assert F.location_configured(tmp_path, {obj.ENDPOINT_ENV: "https://x", obj.BUCKET_ENV: "b"}) is True
    assert F.location_configured(tmp_path, {obj.ENDPOINT_ENV: "https://x"}) is False


def test_the_factory_returns_the_right_store_and_the_refusing_store_refuses_every_call(tmp_path: Path) -> None:
    cfg = MatterSpansConfig()
    assert isinstance(F.open_store(tmp_path, cfg, {msc.DIR_ENV: str(tmp_path / "v")}), MatterStore)
    s: Any = F.open_store(tmp_path, cfg, {})
    assert isinstance(s, F.UnconfiguredStore)
    for call in (lambda: s.create_matter("org:a"), lambda: s.query("org:a", "m", "q"), lambda: s.delete_matter("org:a", "m")):
        with pytest.raises(MatterError) as e:
            call()
        assert e.value.code == "store_unavailable" and e.value.status_code == 503
    assert not (tmp_path / "matter_spans").exists()


def test_with_a2_off_an_existing_default_folder_stays_deletable_but_none_is_created(tmp_path: Path) -> None:
    cfg = MatterSpansConfig()
    assert isinstance(
        F.open_store(tmp_path, cfg, {}, delete_only=True), F.UnconfiguredStore
    )  # no folder: nothing to delete, nothing made
    (tmp_path / "matter_spans").mkdir()
    assert isinstance(F.open_store(tmp_path, cfg, {}, delete_only=True), MatterStore)  # a folder is there: it stays deletable
    assert isinstance(
        F.open_store(tmp_path, cfg, {}, delete_only=False), F.UnconfiguredStore
    )  # with A2 on it is never used unconfigured


def test_one_store_per_process_for_one_configuration_and_a_fresh_one_when_the_location_changes(tmp_path: Path) -> None:
    cfg = MatterSpansConfig()
    env = {msc.DIR_ENV: str(tmp_path / "v")}
    a, b = F.open_store(tmp_path, cfg, env), F.open_store(tmp_path, cfg, env)
    assert a is b  # the same object: an object-backed store keeps its probe and its locks across requests
    assert F.open_store(tmp_path, cfg, {msc.DIR_ENV: str(tmp_path / "w")}) is not a
    assert F.open_store(tmp_path, MatterSpansConfig(max_docs=5), env) is not a
    o = {obj.ENDPOINT_ENV: "https://s3.example.test", obj.BUCKET_ENV: "bkt", obj.ACCESS_KEY_ENV: "k", obj.SECRET_KEY_ENV: "s"}
    s1, s2 = F.open_store(tmp_path, cfg, o), F.open_store(tmp_path, cfg, o)
    assert s1 is s2 and isinstance(s1, obj.ObjectBackedMatterStore)
    assert F.open_store(tmp_path, cfg, {}) is F.open_store(tmp_path, cfg, {})  # the refusing store is cached too
    F.clear_cache()
    assert F.open_store(tmp_path, cfg, env) is not a

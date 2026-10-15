"""A Studio engine with authentication off, reachable from beyond this machine, would answer anyone as the operator."""

from __future__ import annotations

from pathlib import Path

import pytest

from pravrudhi.application import app_serve, tenant_vendors


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in ("PRAVRUDHI_EDITION", "PRAVRUDHI_STUDIO_LOOPBACK_ONLY", "PRAVRUDHI_AUTH"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(tenant_vendors, "_bind_host", None)


def _boot(host: str) -> None:
    tenant_vendors.record_bind(host)
    tenant_vendors.guard_studio_boot()


def test_studio_with_auth_disabled_on_a_public_bind_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_EDITION", "studio")
    for auth in (None, "disabled", ""):  # an unset or blank mode is `disabled` (an unknown one is not: see test_fail_closed_auth)
        if auth:
            monkeypatch.setenv("PRAVRUDHI_AUTH", auth)
        for host in ("0.0.0.0", "192.0.2.5", "::", "example.internal"):
            with pytest.raises(RuntimeError, match="refusing to start"):
                _boot(host)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_studio_on_loopback_with_auth_off_still_starts(monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    monkeypatch.setenv("PRAVRUDHI_EDITION", "studio")
    _boot(host)


def test_the_container_assertion_of_loopback_publication_allows_a_wildcard_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_EDITION", "studio")
    monkeypatch.setenv("PRAVRUDHI_STUDIO_LOOPBACK_ONLY", "1")
    _boot("0.0.0.0")


@pytest.mark.parametrize("auth", ["required", "optional"])
def test_studio_with_authentication_on_may_bind_anywhere(monkeypatch: pytest.MonkeyPatch, auth: str) -> None:
    monkeypatch.setenv("PRAVRUDHI_EDITION", "studio")
    monkeypatch.setenv("PRAVRUDHI_AUTH", auth)
    _boot("0.0.0.0")


@pytest.mark.parametrize("edition", [None, "product", "dev"])
def test_other_editions_are_not_affected(monkeypatch: pytest.MonkeyPatch, edition: str | None) -> None:
    if edition:
        monkeypatch.setenv("PRAVRUDHI_EDITION", edition)
    _boot("0.0.0.0")


def test_both_serve_entrypoints_refuse_before_starting_anything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    from pravrudhi.api import server

    started: list[object] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: started.append(a))
    monkeypatch.setenv("PRAVRUDHI_EDITION", "studio")
    with pytest.raises(RuntimeError, match="refusing to start"):
        server.serve(tmp_path, host="0.0.0.0", port=1)
    with pytest.raises(RuntimeError, match="refusing to start"):
        app_serve.serve(tmp_path, host="0.0.0.0", port=1, open_browser=False)
    assert started == []
    server.serve(tmp_path, host="127.0.0.1", port=1)  # the loopback Studio starts
    assert len(started) == 1

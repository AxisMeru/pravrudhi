"""The release probe, run against a real in-process engine in both editions (and against a deliberately broken one)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pravrudhi.api import identity, roles
from pravrudhi.api.localguard import TOKEN_HEADER, app_token
from pravrudhi.api.server import create_app

spec = importlib.util.spec_from_file_location("release_probe", Path(__file__).parent.parent / "scripts" / "release_probe.py")
probe = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["release_probe"] = probe
spec.loader.exec_module(probe)  # type: ignore[union-attr]

ADMIN_TOKEN, USER_TOKEN = "probe-admin-token-value", "probe-user-token-value"
CLAIMS = {
    ADMIN_TOKEN: {"sub": "op-1", "email": "op@example.com", "role": "authenticated"},
    USER_TOKEN: {"sub": "u-2", "email": "someone@example.com", "role": "authenticated"},
}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in ("PRAVRUDHI_EDITION", "PRAVRUDHI_STUDIO_LOOPBACK_ONLY", "PRAVRUDHI_IDENTITY_HEADER"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("PRAVRUDHI_ADMINS", "op-1")

    def verify(token: str, **_k: Any) -> dict[str, Any]:
        if token not in CLAIMS:
            raise identity.HTTPException(status_code=401, detail="Invalid token")
        return CLAIMS[token]

    monkeypatch.setattr(identity, "verify_token", verify)


def _sender(tmp_path: Path, edition: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("PRAVRUDHI_EDITION", edition)
    client = TestClient(create_app(tmp_path), base_url="http://localhost", raise_server_exceptions=False)

    def send(method: str, path: str, token: str | None, body: dict | None) -> int:
        headers = {TOKEN_HEADER: app_token(tmp_path)}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return client.request(method, path, headers=headers, json=body).status_code

    return send


def _env_for(edition: str) -> dict[str, str]:
    return {"PROBE_BASE_URL": "https://engine.example.test", "PROBE_ADMIN_TOKEN": ADMIN_TOKEN,
            "PROBE_USER_TOKEN": USER_TOKEN, "PROBE_EDITION": edition}


@pytest.mark.parametrize("edition", ["product", "studio"])
def test_a_correctly_gated_engine_passes_every_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], edition: str
) -> None:
    code = probe.main(_env_for(edition), _sender(tmp_path, edition, monkeypatch))
    out = capsys.readouterr()
    assert code == 0, out.out
    assert "FAIL" not in out.out and "passed" in out.out
    assert ADMIN_TOKEN not in out.out + out.err and USER_TOKEN not in out.out + out.err  # tokens are never printed


def test_a_missing_gate_is_reported_as_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(roles, "gate", lambda app: [])  # an engine that forgot the operator gates
    code = probe.main(_env_for("product"), _sender(tmp_path, "product", monkeypatch))
    out = capsys.readouterr().out
    assert code == 1 and "FAIL" in out and "POST /api/runs" in out and "FAILED:" in out


def test_the_probe_never_sends_the_admin_token_to_a_state_changing_route() -> None:
    sent: list[tuple[str, str, str | None]] = []

    def send(method: str, path: str, token: str | None, body: dict | None) -> int:
        sent.append((method, path, token))
        return 200

    probe.run(send, "product", "w", {"admin": "A", "user": "U", "anonymous": None})
    assert sent and all(t != "A" for m, _p, t in sent if m != "GET")
    assert [p for m, p, t in sent if t == "A"] == ["/api/me"]  # the admin token only ever reads /api/me


def test_every_write_probe_carries_an_invalid_body_or_no_body() -> None:
    for c in probe.checks("product", "w"):
        if c.method in ("POST", "PUT") and c.who != "admin":
            assert c.body is None or c.body in (probe.INVALID_RUN, probe.INVALID_UPDATE, probe.ASK_CLI, {"channel": "x"})
    assert probe.INVALID_RUN["target"] not in ("model", "harness")


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"PROBE_BASE_URL": "https://e.example.test", "PROBE_ADMIN_TOKEN": "a"},
        {"PROBE_BASE_URL": "https://e.example.test", "PROBE_ADMIN_TOKEN": "same", "PROBE_USER_TOKEN": "same"},
        {"PROBE_BASE_URL": "http://engine.example.test", "PROBE_ADMIN_TOKEN": "a", "PROBE_USER_TOKEN": "b"},
        {"PROBE_BASE_URL": "https://e.example.test", "PROBE_ADMIN_TOKEN": "a", "PROBE_USER_TOKEN": "b", "PROBE_EDITION": "prod"},
    ],
)
def test_configuration_errors_exit_2_and_say_why(env: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    assert probe.main(env, lambda *a: 200) == 2
    assert "CONFIG ERROR" in capsys.readouterr().err


def test_http_is_allowed_for_loopback_only(capsys: pytest.CaptureFixture[str]) -> None:
    ok = {"PROBE_BASE_URL": "http://127.0.0.1:8765", "PROBE_ADMIN_TOKEN": "a", "PROBE_USER_TOKEN": "b"}
    assert probe.main(ok, lambda *a: 200) in (0, 1)  # configuration accepted (the stub answers 200 to everything)
    assert probe.main({**ok, "PROBE_BASE_URL": "http://10.0.0.5"}, lambda *a: 200) == 2


def test_an_unreachable_engine_is_exit_2_not_a_pass(capsys: pytest.CaptureFixture[str]) -> None:
    import urllib.error

    def down(*a: Any) -> int:
        raise urllib.error.URLError("refused")

    assert probe.main(_env_for("product"), down) == 2
    assert "could not be reached" in capsys.readouterr().err

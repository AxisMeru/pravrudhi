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

    def send(method: str, path: str, token: str | None, body: dict | None, want_json: bool) -> tuple[int, dict | None]:
        headers = {TOKEN_HEADER: app_token(tmp_path)}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        r = client.request(method, path, headers=headers, json=body)
        parsed = r.json() if want_json and r.headers.get("content-type", "").startswith("application/json") else None
        return r.status_code, parsed if isinstance(parsed, dict) else None

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


def _recording(status: int = 200, body: dict | None = None) -> tuple[list[tuple[str, str, str | None]], Any]:
    sent: list[tuple[str, str, str | None]] = []

    def send(method: str, path: str, token: str | None, req: dict | None, want_json: bool) -> tuple[int, dict | None]:
        sent.append((method, path, token))
        return status, body

    return sent, send


def test_the_probe_never_sends_the_admin_token_to_a_state_changing_route() -> None:
    sent, send = _recording(200, {"access": "admin"})
    probe.run(send, "product", "w", {"admin": "A", "user": "U", "anonymous": None}, allow_cli_ask=True)
    assert sent and all(t != "A" for m, _p, t in sent if m != "GET")
    assert [p for m, p, t in sent if t == "A"] == ["/api/me"]  # the admin token only ever reads /api/me


def test_the_cli_ask_probes_are_opt_in_and_not_sent_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    sent, send = _recording()
    probe.run(send, "product", "w", {"admin": "A", "user": "U", "anonymous": None})
    assert not [p for _m, p, _t in sent if p.startswith("/api/nyaya/ask")]
    sent_on, send_on = _recording()
    probe.run(send_on, "product", "w", {"admin": "A", "user": "U", "anonymous": None}, allow_cli_ask=True)
    assert len([p for _m, p, _t in sent_on if p.startswith("/api/nyaya/ask")]) == 2
    # through main: skipped lines are reported as SKIP and do not count
    probe.main(_env_for("product"), send)
    out = capsys.readouterr().out
    assert out.count("SKIP") == 2 and "PROBE_ALLOW_CLI_ASK=1" in out and "(2 skipped)" in out


def test_the_default_probes_name_no_workspace_and_use_a_nonexistent_objective() -> None:
    first = [c for c in probe.checks("product", "w") if not c.opt_in]
    assert not [c for c in first if "workspace=" in c.path]
    ghost = next(c for c in first if c.path.endswith("/subagents"))
    again = next(c for c in probe.checks("product", "w") if c.path.endswith("/subagents"))
    assert "release-probe-nonexistent-" in ghost.path and ghost.path != again.path  # a fresh unguessable id each run


def test_the_admin_token_must_really_be_an_admin_and_the_access_word_is_never_printed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _sent, member = _recording(200, {"access": "member"})
    results = probe.run(member, "product", "w", {"admin": "A", "user": "U", "anonymous": None})
    assert not results[0][2]  # status 200 but not admin: the first check fails
    _sent2, admin = _recording(200, {"access": "admin"})
    assert probe.run(admin, "product", "w", {"admin": "A", "user": "U", "anonymous": None})[0][2]
    probe.main(_env_for("product"), member)
    assert "member" not in capsys.readouterr().out


def test_the_non_admin_must_not_be_reported_as_admin_on_a_product_engine() -> None:
    _sent, everyone_admin = _recording(200, {"access": "admin"})
    ran = probe.run(everyone_admin, "product", "w", {"admin": "A", "user": "U", "anonymous": None})
    results = {c.name: ok for c, _s, ok in ran}
    assert results["product serves a non-admin /api/me, not as admin"] is False


def test_the_report_names_the_target_and_label(capsys: pytest.CaptureFixture[str]) -> None:
    _sent, send = _recording(200, {"access": "admin"})
    probe.main({**_env_for("product"), "PROBE_TARGET_LABEL": "worker"}, send)
    first = capsys.readouterr().out.splitlines()[0]
    assert first == "target: https://engine.example.test (worker), edition product"


def test_any_unexpected_error_is_exit_2_with_no_traceback(capsys: pytest.CaptureFixture[str]) -> None:
    def boom(*a: Any) -> tuple[int, dict | None]:
        raise RuntimeError("secret-looking detail Bearer abc")

    assert probe.main(_env_for("product"), boom) == 2
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "Traceback" not in err and "Bearer" not in err


def test_every_write_probe_carries_an_invalid_body_or_no_body() -> None:
    for c in probe.checks("product", "w"):
        if c.method in ("POST", "PUT") and c.who != "admin" and not c.opt_in:
            assert c.body is None or c.body in (probe.INVALID_RUN, probe.INVALID_UPDATE, {"channel": "x"})
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
    assert probe.main(env, lambda *a: (200, None)) == 2
    assert "CONFIG ERROR" in capsys.readouterr().err


def test_http_is_allowed_for_loopback_only(capsys: pytest.CaptureFixture[str]) -> None:
    ok = {"PROBE_BASE_URL": "http://127.0.0.1:8765", "PROBE_ADMIN_TOKEN": "a", "PROBE_USER_TOKEN": "b"}
    assert probe.main(ok, lambda *a: (200, None)) in (0, 1)  # configuration accepted (the stub answers 200 to everything)
    assert probe.main({**ok, "PROBE_BASE_URL": "http://10.0.0.5"}, lambda *a: (200, None)) == 2


def test_an_unreachable_engine_is_exit_2_not_a_pass(capsys: pytest.CaptureFixture[str]) -> None:
    import urllib.error

    def down(*a: Any) -> tuple[int, dict | None]:
        raise urllib.error.URLError("refused")

    assert probe.main(_env_for("product"), down) == 2
    assert "could not be reached" in capsys.readouterr().err

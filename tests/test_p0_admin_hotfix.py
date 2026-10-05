"""P0 hotfix (R2, reproduced at v0.5.29): any signed-in account that reaches the engine could rewrite the engine-wide
update config, read the engine's local token, and start runs that spawn processes with the engine's secrets.

These routes are operator-only in EVERY edition: a non-admin gets 403 (an anonymous caller 401), the admin passes."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pravrudhi.api import identity
from pravrudhi.api.localguard import TOKEN_HEADER, app_token
from pravrudhi.api.runs import (
    DEFAULT_PASSTHROUGH,
    PASSTHROUGH_ENV,
    RunManager,
    RunRequest,
    child_env,
    passthrough_names,
    proposer_endpoint_allowed,
)
from pravrudhi.api.server import create_app

TOKENS = {
    "admin": {"sub": "op-1", "email": "op@example.com", "role": "authenticated"},
    "plain": {"sub": "u-2", "email": "someone@example.com", "role": "authenticated"},
}
# (method, path, body): every write under /api/update*, the local token, and the whole /api/runs family.
GATED = [
    ("PUT", "/api/update/config", {"channel": "dev", "auto_apply": True, "check_interval_min": 1, "keep_previous": 1}),
    ("POST", "/api/update/apply", {"channel": "dev"}),
    ("POST", "/api/update/rollback", None),
    ("GET", "/api/app-token", None),
    ("GET", "/api/runs", None),
    ("POST", "/api/runs", {"target": "model", "budget_gpu_h": 0.01}),
    ("GET", "/api/runs/abc", None),
    ("POST", "/api/runs/abc/stop", None),
    ("GET", "/api/runs/abc/events", None),
]
EDITIONS = ["product", "studio"]


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in ("PRAVRUDHI_EDITION", "PRAVRUDHI_STUDIO_LOOPBACK_ONLY", "PRAVRUDHI_IDENTITY_HEADER"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("PRAVRUDHI_ADMINS", "op-1")
    monkeypatch.setattr(identity, "verify_token", lambda token, **_k: TOKENS[token])


def _client(root: Path, edition: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("PRAVRUDHI_EDITION", edition)
    return TestClient(create_app(root), base_url="http://localhost", raise_server_exceptions=False)


def _call(c: TestClient, root: Path, method: str, path: str, body: Any, who: str | None) -> Any:
    headers = {TOKEN_HEADER: app_token(root)}
    if who:
        headers["Authorization"] = f"Bearer {who}"
    return c.request(method, path, headers=headers, json=body)


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


@pytest.mark.parametrize("edition", EDITIONS)
@pytest.mark.parametrize(("method", "path", "body"), GATED)
def test_non_admin_is_403_anonymous_401_and_the_admin_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edition: str, method: str, path: str, body: Any
) -> None:
    c = _client(tmp_path, edition, monkeypatch)
    assert _call(c, tmp_path, method, path, body, None).status_code == 401
    assert _call(c, tmp_path, method, path, body, "plain").status_code == 403
    assert _call(c, tmp_path, method, path, body, "admin").status_code not in (401, 403)


@pytest.mark.parametrize("edition", EDITIONS)
def test_a_refused_update_write_leaves_update_yaml_byte_for_byte_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edition: str
) -> None:
    cfg = tmp_path / ".pravrudhi" / "update.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("channel: dev\nauto_apply: true\ncheck_interval_min: 60\nkeep_previous: 2\n")
    before = _sha(cfg)
    c = _client(tmp_path, edition, monkeypatch)
    evil = {"channel": "release", "auto_apply": False, "check_interval_min": 1, "keep_previous": 9}
    for method, path, body in (("PUT", "/api/update/config", evil), ("POST", "/api/update/config", evil),
                               ("DELETE", "/api/update/config", None), ("PATCH", "/api/update/config", evil),
                               ("POST", "/api/update/apply", {"channel": "release"}), ("POST", "/api/update/rollback", None),
                               ("PUT", "/api/update", evil), ("DELETE", "/api/update/apply", None)):
        for who in (None, "plain"):
            r = _call(c, tmp_path, method, path, body, who)
            assert r.status_code in (401, 403, 404, 405), (method, path, who, r.status_code)
    assert _sha(cfg) == before
    assert _call(c, tmp_path, "PUT", "/api/update/config", evil, "admin").status_code == 200
    assert _sha(cfg) != before  # the operator can still change it


@pytest.mark.parametrize(("edition", "who"), [("product", "plain"), ("product", "admin"), ("studio", "admin")])
def test_reading_the_update_status_stays_user_facing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edition: str, who: str
) -> None:
    # (Studio admits administrators only as a whole, #246, so the non-admin read is the product's.)
    c = _client(tmp_path, edition, monkeypatch)
    for path in ("/api/update", "/api/update/config", "/api/update/last-check"):
        assert _call(c, tmp_path, "GET", path, None, who).status_code == 200, path


@pytest.mark.parametrize("edition", EDITIONS)
def test_a_refused_run_start_spawns_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edition: str) -> None:
    import subprocess

    spawned: list[Any] = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: spawned.append(a) or (_ for _ in ()).throw(AssertionError))
    c = _client(tmp_path, edition, monkeypatch)
    r = _call(c, tmp_path, "POST", "/api/runs", {"target": "model", "k": 32, "budget_gpu_h": 48}, "plain")
    assert r.status_code == 403 and spawned == []


def test_authentication_off_keeps_the_single_operator_install_working(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_AUTH", "disabled")
    c = _client(tmp_path, "product", monkeypatch)
    assert _call(c, tmp_path, "GET", "/api/app-token", None, None).status_code == 200
    assert _call(c, tmp_path, "GET", "/api/runs", None, None).status_code == 200


# -- the run child's environment and the proposer endpoint -----------------------------------------------------------

SECRETS = {
    "OPENAI_API_KEY": "sk-test-" + "a" * 24, "ANTHROPIC_API_KEY": "sk-test-" + "b" * 24,
    "PRAVRUDHI_CHAT_API_KEY": "sk-test-" + "c" * 24, "HF_TOKEN": "hf_" + "d" * 30, "GITHUB_TOKEN": "ghp_" + "e" * 30,
    "SUPABASE_JWT_SECRET": "jwt-secret-value", "PRAVRUDHI_ADMINS": "op-1", "CLOUDFLARE_API_TOKEN": "cf-token-value",
    "NYAYA_HOUSE_JUDGE_API_KEY": "judge-key-value", "CUDA_API_KEY": "x" * 8, "XDG_SESSION_TOKEN": "y" * 8,
    "AWS_SECRET_ACCESS_KEY": "z" * 8, "DATABASE_URL": "postgres://u:p@h/db",
}


def test_child_env_is_an_allowlist_with_no_secret_looking_name(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    env = child_env()
    assert not [k for k in env if k in SECRETS], sorted(k for k in env if k in SECRETS)
    assert not [v for v in env.values() if v in SECRETS.values()]
    assert env["PYTHONUNBUFFERED"] == "1" and env["PATH"] == os.environ["PATH"]
    assert env["CUDA_VISIBLE_DEVICES"] == "0" and env["LC_ALL"] == "C.UTF-8"
    assert set(env) <= set(os.environ) | {"PYTHONUNBUFFERED"}


@pytest.mark.parametrize("name", [
    "UV_INDEX_URL", "UV_EXTRA_INDEX_URL", "UV_DEFAULT_INDEX", "UV_INDEX", "UV_PUBLISH_TOKEN", "UV_HTTP_TIMEOUT_TOKEN_X",
])
def test_a_credentialed_uv_index_url_is_dropped(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, "https://user:pw-" + "x" * 12 + "@pypi.example.org/simple")
    monkeypatch.setenv("UV_CACHE_DIR", "/home/user/.cache/uv")
    env = child_env()
    assert name not in env and not [v for v in env.values() if "pw-" in v]
    assert env["UV_CACHE_DIR"] == "/home/user/.cache/uv"  # the exact uv names a child needs still pass


def test_the_default_passthrough_is_the_judge_urls_and_model_pins_and_hf_home_and_no_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(PASSTHROUGH_ENV, raising=False)
    assert passthrough_names() == list(DEFAULT_PASSTHROUGH)
    assert not [n for n in DEFAULT_PASSTHROUGH if "KEY" in n or "TOKEN" in n or "SECRET" in n]
    for n in DEFAULT_PASSTHROUGH:
        monkeypatch.setenv(n, f"value-of-{n}")
    monkeypatch.setenv("NYAYA_HOUSE_JUDGE_API_KEY", "judge-key-value")
    env = child_env()
    assert all(env[n] == f"value-of-{n}" for n in DEFAULT_PASSTHROUGH)
    assert "NYAYA_HOUSE_JUDGE_API_KEY" not in env


def test_the_passthrough_is_the_deployments_config_not_a_request_and_only_plain_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PASSTHROUGH_ENV, "MY_RUN_FLAG, HF_TOKEN, bad name, A=B, ,1X")
    monkeypatch.setenv("MY_RUN_FLAG", "on")
    monkeypatch.setenv("HF_TOKEN", "hf_" + "t" * 30)
    monkeypatch.setenv("HF_HOME", "/data/hf")
    assert passthrough_names() == ["MY_RUN_FLAG", "HF_TOKEN"]
    env = child_env()
    assert env["MY_RUN_FLAG"] == "on" and env["HF_TOKEN"].startswith("hf_")  # an explicit operator decision
    assert "HF_HOME" not in env  # the list replaces the default; it is the deployment's to widen or narrow
    monkeypatch.setenv(PASSTHROUGH_ENV, "")
    assert passthrough_names() == [] and "MY_RUN_FLAG" not in child_env()


def test_the_run_request_cannot_choose_the_passthrough() -> None:
    assert "env" not in RunRequest.model_fields and "passthrough" not in RunRequest.model_fields


def test_the_spawned_run_child_really_gets_that_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "child-env.json"
    script = tmp_path / "fake_cli.py"
    script.write_text(f"import json, os\nopen({str(out)!r}, 'w').write(json.dumps(dict(os.environ)))\n")
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("PRAVRUDHI_CLI", f"{sys.executable} {script}")
    mgr = RunManager(tmp_path)
    run = mgr.start(RunRequest(target="model", budget_gpu_h=0.01))
    assert run.proc is not None
    run.proc.wait(timeout=30)
    seen = json.loads(out.read_text())
    assert not [k for k in seen if k in SECRETS], sorted(k for k in seen if k in SECRETS)
    assert "PATH" in seen and seen["PYTHONUNBUFFERED"] == "1"


@pytest.mark.parametrize("endpoint", [
    "http://169.254.169.254/latest", "https://attacker.example.com/v1", "http://evil.test:8000", "ftp://127.0.0.1/x",
    "http://user:pw@127.0.0.1/v1", "http://127.0.0.1.evil.test/v1", "file:///etc/passwd", "http://[::1", "javascript:alert(1)",
    "//127.0.0.1/v1", "http://10.0.0.5:8000/v1",
])
def test_a_disallowed_proposer_endpoint_is_refused(endpoint: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRAVRUDHI_PROPOSER_ENDPOINT_HOSTS", raising=False)
    assert not proposer_endpoint_allowed(endpoint)
    with pytest.raises(ValueError):
        RunRequest(target="model", proposer_endpoint=endpoint)


@pytest.mark.parametrize("endpoint", ["http://127.0.0.1:8110/v1", "http://localhost:8000", "http://[::1]:9/v1", ""])
def test_loopback_and_empty_proposer_endpoints_are_allowed(endpoint: str) -> None:
    assert RunRequest(target="model", proposer_endpoint=endpoint).proposer_endpoint == endpoint


def test_a_named_host_is_allowed_only_when_the_deployment_lists_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_PROPOSER_ENDPOINT_HOSTS", "spark-dev, gpu.internal")
    assert proposer_endpoint_allowed("http://spark-dev:8112/v1") and proposer_endpoint_allowed("https://GPU.internal/v1")
    assert not proposer_endpoint_allowed("http://other-host:8112/v1")


@pytest.mark.parametrize("edition", EDITIONS)
def test_the_run_route_answers_422_for_a_disallowed_endpoint_and_spawns_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edition: str
) -> None:
    import subprocess

    spawned: list[Any] = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: spawned.append(a))
    c = _client(tmp_path, edition, monkeypatch)
    body = {"target": "model", "proposer_endpoint": "https://attacker.example.com/v1"}
    r = _call(c, tmp_path, "POST", "/api/runs", body, "admin")
    assert r.status_code == 422 and spawned == []

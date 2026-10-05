"""scripts/partner_smoke.py: exit codes, the origin-only URL rule, no secret in the output, key/anonymous paths."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

spec = importlib.util.spec_from_file_location("partner_smoke", Path(__file__).parent.parent / "scripts" / "partner_smoke.py")
smoke = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["partner_smoke"] = smoke
spec.loader.exec_module(smoke)  # type: ignore[union-attr]

KEY = "e2e-key-secret-value"
GOOD = {
    ("GET", "/api/v1/status"): (200, {}, {"engine_version": "0.5.44"}),
    ("GET", "/api/v1/audit?limit=1"): (200, {}, {"rows": [], "next_offset": None}),
    ("GET", "/api/v1/audit"): (401, {}, None),
    ("GET", "/api/v1/analyse-facts/jobs/smoke-nonexistent-job"): (404, {}, None),
    ("POST", "/api/v1/analyse-facts/jobs"): (401, {}, None),
    ("GET", "/api/v1/orgs/smoke/usage/summary"): (401, {}, None),
}
ANALYSIS = {
    "retention_notice": "kept",
    "standard": {"requested": "proved"},
    "contracts": [{"contract_id": "bns69", "outcome": "PROOF"}],
}


def _send(overrides: dict | None = None) -> Any:
    table = {**GOOD, **(overrides or {})}

    def send(method: str, path: str, headers: dict[str, str], body: dict | None) -> tuple[int, dict, dict | None]:
        has_key = headers.get(smoke.KEY_HEADER) == KEY
        if (method, path) == ("POST", "/api/v1/analyse-facts"):
            if headers.get(smoke.KEY_HEADER) and not has_key:
                return 401, {}, None
            if body and body["facts"] == ["   "]:
                return (422 if has_key else 401), {}, None
            return (200, {"x-ratelimit-limit": "60"}, ANALYSIS) if has_key else (401, {}, None)
        if has_key and path == "/api/v1/orgs/smoke/usage/summary":
            return 403, {}, None
        return table.get((method, path), (500, {}, None))

    return send


def _env(**kw: str) -> dict[str, str]:
    return {"SMOKE_BASE_URL": "https://engine.example.test", **kw}


def test_all_pass_with_a_key_and_prints_no_secret(capsys: pytest.CaptureFixture[str]) -> None:
    assert smoke.main(_env(SMOKE_API_KEY=KEY), _send()) == 0
    out = capsys.readouterr()
    assert KEY not in out.out + out.err and "FAIL" not in out.out


def test_no_key_is_incomplete_not_green(capsys: pytest.CaptureFixture[str]) -> None:
    assert smoke.main(_env(), _send()) == 3
    assert "SKIP" in capsys.readouterr().out


def test_a_failed_check_wins_over_incomplete() -> None:
    assert smoke.main(_env(), _send({("GET", "/api/v1/audit"): (200, {}, {"rows": []})})) == 1


def test_anonymous_audit_open_is_a_failure() -> None:
    assert smoke.main(_env(SMOKE_API_KEY=KEY), _send({("GET", "/api/v1/audit"): (200, {}, {"rows": []})})) == 1


def test_status_without_engine_version_fails() -> None:
    assert smoke.main(_env(SMOKE_API_KEY=KEY), _send({("GET", "/api/v1/status"): (200, {}, {})})) == 1


def test_analysis_is_opt_in_and_checks_the_reply_shape() -> None:
    results = smoke.run(_send(), KEY, run_analysis=False)
    assert [s for c, s, *_ in results if c.opt_in] == [None, None]
    results = smoke.run(_send(), KEY, run_analysis=True)
    assert all(ok for *_x, ok, _n in results)


@pytest.mark.parametrize("url", ["https://u:p@e.test", "https://e.test/api", "https://e.test?x=1", "http://e.test",
                                 "https://e.test#f", "https://"])
def test_the_url_must_be_an_https_origin(url: str) -> None:
    assert smoke.main({"SMOKE_BASE_URL": url}, _send()) == 2


def test_missing_url_is_a_config_error() -> None:
    assert smoke.main({}, _send()) == 2


def test_an_unexpected_error_is_reported_by_type_only(capsys: pytest.CaptureFixture[str]) -> None:
    def boom(*_a: Any) -> Any:
        raise RuntimeError(KEY)

    assert smoke.main(_env(SMOKE_API_KEY=KEY), boom) == 2
    assert KEY not in capsys.readouterr().err

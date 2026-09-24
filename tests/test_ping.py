"""RunPod serverless load-balancer workers are health-checked at GET /ping (it ignored HEALTH_CHECK_PATH in
Lead-2's 2026-09-24 staging test): a worker with no /ping never turns healthy and never receives traffic."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pravrudhi.application.app_serve import build_app
from pravrudhi.application.init import init_project


def test_ping_answers_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_DISABLE_LOCAL_GUARD", "1")  # as in the hosted image
    init_project(tmp_path)
    r = TestClient(build_app(tmp_path)).get("/ping")
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_ping_needs_no_identity_in_required_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_DISABLE_LOCAL_GUARD", "1")  # as in the hosted image
    monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    init_project(tmp_path)
    assert TestClient(build_app(tmp_path)).get("/ping").status_code == 200

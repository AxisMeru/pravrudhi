"""Cold-start overlap for the house judge (2026-09-24, Lead-2's production test): both RunPod endpoints idle
out after 300s, the judge's own cold start is ~200s, and `house_judge.timeout_s` was 60s -- a fully-cold demo
visit hit ABSTAIN/judge_error every time, a real regression from the always-warm 5090. Two independent fixes:

1. `NYAYA_HOUSE_JUDGE_TIMEOUT_S` env override, alongside the existing NYAYA_HOUSE_JUDGE_* overrides.
2. A non-blocking warm-up GET to the judge's `/models` at engine startup, so the engine's own cold start and
   the judge's overlap instead of chaining serially (the engine answering /ping doesn't wait for the judge to
   be ready; the judge just gets a head start on warming up while real traffic is still arriving).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pravrudhi.application.nyaya_agent import load_agent_config
from pravrudhi.application.nyaya_judge_warmup import warm_up_house_judge


class TestTimeoutEnvOverride:
    def test_defaults_to_the_yaml_value_when_unset(self, tmp_path: Path) -> None:
        (tmp_path / "configs").mkdir()
        (tmp_path / "configs" / "nyaya_agent.yaml").write_text(
            "refer_band: [0.5, 0.74]\nmax_retries: 2\naudit_dir: audit\ntau: 0.74\n"
            "house_judge: {base_url: http://x/v1, statute_chars: 600, max_tokens: 30, top_logprobs: 20, timeout_s: 60}\n"
        )
        assert load_agent_config(tmp_path).house_judge["timeout_s"] == 60

    def test_env_override_replaces_the_yaml_value(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "configs").mkdir()
        (tmp_path / "configs" / "nyaya_agent.yaml").write_text(
            "refer_band: [0.5, 0.74]\nmax_retries: 2\naudit_dir: audit\ntau: 0.74\n"
            "house_judge: {base_url: http://x/v1, statute_chars: 600, max_tokens: 30, top_logprobs: 20, timeout_s: 60}\n"
        )
        monkeypatch.setenv("NYAYA_HOUSE_JUDGE_TIMEOUT_S", "240")
        assert load_agent_config(tmp_path).house_judge["timeout_s"] == 240

    def test_env_override_works_even_when_the_yaml_names_no_house_judge_at_all(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "configs").mkdir()
        (tmp_path / "configs" / "nyaya_agent.yaml").write_text(
            "refer_band: [0.5, 0.74]\nmax_retries: 2\naudit_dir: audit\ntau: 0.74\n"
        )
        monkeypatch.setenv("NYAYA_HOUSE_JUDGE_TIMEOUT_S", "240")
        assert load_agent_config(tmp_path).house_judge["timeout_s"] == 240


class TestWarmUpHouseJudge:
    """`warm_up_house_judge` is the synchronous core (one GET, real caller-supplied transport) -- the
    non-blocking property is a separate, thin wrapper (`start_house_judge_warmup`, real daemon thread, not
    unit-tested here beyond "it returns immediately")."""

    def test_requests_the_models_endpoint_of_the_configured_base_url(self) -> None:
        calls = []

        def fake_get(url: str, *, headers: dict[str, str], timeout_s: float) -> None:
            calls.append((url, headers, timeout_s))

        warm_up_house_judge(base_url="http://x/v1", api_key=None, timeout_s=10, get=fake_get)
        assert calls == [("http://x/v1/models", {}, 10)]

    def test_sends_the_bearer_key_when_configured(self) -> None:
        calls = []

        def fake_get(url: str, *, headers: dict[str, str], timeout_s: float) -> None:
            calls.append(headers)

        warm_up_house_judge(base_url="http://x/v1", api_key="topsecret", timeout_s=10, get=fake_get)
        assert calls == [{"Authorization": "Bearer topsecret"}]

    def test_never_raises_on_a_transport_failure_this_is_best_effort_only(self) -> None:
        def failing_get(url: str, *, headers: dict[str, str], timeout_s: float) -> None:
            raise TimeoutError("connect timed out")

        warm_up_house_judge(base_url="http://x/v1", api_key="topsecret", timeout_s=10, get=failing_get)  # must not raise

    def test_the_key_never_appears_in_a_raised_or_logged_message(self, caplog: pytest.LogCaptureFixture) -> None:
        def failing_get(url: str, *, headers: dict[str, str], timeout_s: float) -> None:
            # A real urllib error message often echoes back request details -- simulated here.
            raise RuntimeError(f"connection to {url} failed with headers {headers}")

        with caplog.at_level("DEBUG"):
            warm_up_house_judge(base_url="http://x/v1", api_key="topsecret", timeout_s=10, get=failing_get)
        assert "topsecret" not in caplog.text

    def test_no_base_url_configured_is_a_silent_no_op(self) -> None:
        calls = []
        warm_up_house_judge(base_url=None, api_key=None, timeout_s=10, get=lambda *a, **k: calls.append(1))
        assert calls == []


class TestStartHouseJudgeWarmupNeverBlocks:
    def test_returns_immediately_even_if_the_transport_would_hang(self) -> None:
        import time

        from pravrudhi.application.nyaya_judge_warmup import start_house_judge_warmup

        def slow_get(url: str, *, headers: dict[str, str], timeout_s: float) -> None:
            time.sleep(5)

        t0 = time.monotonic()
        start_house_judge_warmup(base_url="http://x/v1", api_key=None, timeout_s=10, get=slow_get)
        assert time.monotonic() - t0 < 1.0, "starting the warm-up must not block on the request itself"


class TestEngineStartupTriggersTheWarmup:
    """`create_app`'s own wiring (api/server.py): fires the warm-up at engine startup, from the same
    house_judge config `NyayaAgent.house` reads, and never fails startup when no Nyaya config exists at all
    (a deployment that doesn't use Nyaya -- the same shape every other create_app(tmp_path) test already
    relies on not raising)."""

    def test_no_nyaya_config_at_all_is_a_silent_no_op_not_a_startup_failure(self, tmp_path: Path) -> None:
        from pravrudhi.api.server import create_app

        create_app(tmp_path)  # must not raise

    def test_a_real_house_judge_config_triggers_a_warm_up_with_the_right_url_and_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "configs").mkdir()
        (tmp_path / "configs" / "nyaya_agent.yaml").write_text(
            "refer_band: [0.5, 0.74]\nmax_retries: 2\naudit_dir: audit\ntau: 0.74\n"
            "house_judge: {base_url: http://judge.invalid/v1, statute_chars: 600, max_tokens: 30, "
            "top_logprobs: 20, timeout_s: 240, api_key: topsecret}\n"
        )
        calls = []
        monkeypatch.setattr(
            "pravrudhi.application.nyaya_judge_warmup.start_house_judge_warmup",
            lambda **kw: calls.append(kw),
        )
        from pravrudhi.api.server import create_app

        create_app(tmp_path)
        assert calls == [{"base_url": "http://judge.invalid/v1", "api_key": "topsecret", "timeout_s": 240.0}]

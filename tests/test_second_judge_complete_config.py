"""#356: a second judge configured PARTLY refuses to load, naming the missing keys; it no longer starts and then fails with
KeyError('tau') on the first request. Invented values only."""

from __future__ import annotations

from pathlib import Path

import pytest

from pravrudhi.application import nyaya_agent

REPO = Path(nyaya_agent.__file__).parents[3]
SECOND_ENV = (
    "NYAYA_SECOND_JUDGE_BASE_URL", "NYAYA_SECOND_JUDGE_MODEL", "NYAYA_SECOND_JUDGE_TAU", "NYAYA_SECOND_JUDGE_TIMEOUT_S",
    "NYAYA_SECOND_JUDGE_STATUTE_CHARS", "NYAYA_SECOND_JUDGE_TOP_LOGPROBS", "NYAYA_SECOND_JUDGE_MAX_TOKENS",
    "NYAYA_SECOND_JUDGE_LABEL_MASS_FLOOR", "NYAYA_SECOND_JUDGE_REFER_LOGIT_DELTA", "NYAYA_SECOND_JUDGE_FALLBACK_URLS",
    "NYAYA_HOUSE_JUDGE_MODEL", "PRAVRUDHI_EDITION",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in SECOND_ENV:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("PRAVRUDHI_EDITION", "dev")  # model pinning is its own test; this file is about completeness


def test_a_model_only_second_judge_is_refused_and_the_error_names_every_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NYAYA_SECOND_JUDGE_MODEL", "judge-x")
    with pytest.raises(ValueError, match="second_judge is configured partly") as e:
        nyaya_agent.load_agent_config(REPO)
    msg = str(e.value)
    for key, env in (("base_url", "NYAYA_SECOND_JUDGE_BASE_URL"), ("tau", "NYAYA_SECOND_JUDGE_TAU"),
                     ("timeout_s", "NYAYA_SECOND_JUDGE_TIMEOUT_S")):
        assert key in msg and env in msg


@pytest.mark.parametrize("present", [{"NYAYA_SECOND_JUDGE_BASE_URL": "http://127.0.0.1:1/v1"}, {"NYAYA_SECOND_JUDGE_TAU": "0.9"},
                                     {"NYAYA_SECOND_JUDGE_TIMEOUT_S": "30"}])
def test_any_single_signal_key_without_the_rest_is_refused(monkeypatch: pytest.MonkeyPatch, present: dict[str, str]) -> None:
    for k, v in present.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValueError, match="second_judge is configured partly"):
        nyaya_agent.load_agent_config(REPO)


def test_a_complete_second_judge_loads_and_inherits_the_prompt_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NYAYA_SECOND_JUDGE_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("NYAYA_SECOND_JUDGE_MODEL", "judge-x")
    monkeypatch.setenv("NYAYA_SECOND_JUDGE_TAU", "0.9")
    monkeypatch.setenv("NYAYA_SECOND_JUDGE_TIMEOUT_S", "30")
    sj = nyaya_agent.load_agent_config(REPO).second_judge
    assert sj is not None and sj["tau"] == 0.9 and sj["timeout_s"] == 30


def test_tuning_keys_alone_are_the_inert_not_configured_case_and_still_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """refer_logit_delta and fallback URLs only tune a second judge that is configured; with none configured they are
    inert, exactly as before, and must not start refusing."""
    monkeypatch.setenv("NYAYA_SECOND_JUDGE_REFER_LOGIT_DELTA", "0.2")
    sj = nyaya_agent.load_agent_config(REPO).second_judge
    assert sj is not None and sj["refer_logit_delta"] == 0.2
    assert not any(k in sj for k in ("base_url", "model", "tau", "timeout_s"))


def test_no_second_judge_at_all_is_unchanged() -> None:
    assert nyaya_agent.load_agent_config(REPO).second_judge is None

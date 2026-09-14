"""The Lite Plan is a second Alibaba credential and a second Alibaba endpoint, reached only through
`provider_id="alibaba-plan"`. These tests prove the two providers never cross: a plan run reads its key from
`~/.config/llm/dashscope-plan.env` and dispatches against the plan-exclusive endpoint, and a key that only
exists in the free tier's `dashscope.env` is never the one handed to that endpoint.
"""
import json
from pathlib import Path

import pytest

from pravrudhi.agents import alibaba_agent as alibaba
from pravrudhi.application.credentials import PROVIDERS


def event(kind, **part):
    return json.dumps({"type": kind, "sessionID": "session-1", "part": part})


def _write_env(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{alibaba.KEY_NAME}={value}\n")
    path.chmod(0o600)


def test_credential_path_for_the_plan_provider_is_the_plan_env_file(tmp_path, monkeypatch):
    monkeypatch.setattr(alibaba.Path, "home", lambda: tmp_path)
    assert alibaba.credential_path("alibaba-plan") == tmp_path / ".config/llm/dashscope-plan.env"
    assert alibaba.credential_path("alibaba") == tmp_path / ".config/llm/dashscope.env"


def test_plan_credential_comes_from_the_plan_file_not_the_free_tier_file(tmp_path, monkeypatch):
    monkeypatch.setattr(alibaba.Path, "home", lambda: tmp_path)
    _write_env(tmp_path / ".config/llm/dashscope.env", "free-tier-key-not-for-the-plan")
    _write_env(tmp_path / ".config/llm/dashscope-plan.env", "plan-key-for-the-lite-plan")

    plan_secret = alibaba.credential("alibaba-plan")
    free_secret = alibaba.credential("alibaba")

    assert plan_secret.reveal() == "plan-key-for-the-lite-plan"
    assert free_secret.reveal() == "free-tier-key-not-for-the-plan"
    assert plan_secret.reveal() != free_secret.reveal()


def test_plan_credential_is_missing_when_only_the_free_tier_file_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(alibaba.Path, "home", lambda: tmp_path)
    _write_env(tmp_path / ".config/llm/dashscope.env", "free-tier-key-not-for-the-plan")

    with pytest.raises(OSError):
        alibaba.credential("alibaba-plan")


def test_plan_provider_is_registered_against_the_plan_exclusive_endpoint():
    plan = PROVIDERS["alibaba-plan"]
    free = PROVIDERS["alibaba"]
    assert plan.base_url != free.base_url
    assert plan.base_url == "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"


def test_run_sends_the_plan_key_to_the_plan_endpoint_and_never_the_free_tier_key(tmp_path, monkeypatch):
    monkeypatch.setattr(alibaba.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(alibaba.shutil, "which", lambda _: "/fake/opencode")
    _write_env(tmp_path / ".config/llm/dashscope.env", "free-tier-key-never-sent")
    _write_env(tmp_path / ".config/llm/dashscope-plan.env", "plan-key-actually-sent")

    seen: dict[str, object] = {}

    def transport(cmd, cwd, timeout_s, env):
        seen["env"] = env
        seen["config"] = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        return 0, event("step_finish", reason="stop"), "", 0.5

    monkeypatch.setattr(alibaba, "_run", transport)
    agent = alibaba.AlibabaAgent(tmp_path, model="qwen3.8-flash", provider_id="alibaba-plan")
    result = agent.run("go", tmp_path)

    assert result.ok
    assert seen["env"]["DASHSCOPE_API_KEY"] == "plan-key-actually-sent"
    assert "free-tier-key-never-sent" not in json.dumps(seen), seen
    config = seen["config"]
    sent_base_url = config["provider"][alibaba.PROVIDER]["options"]["baseURL"]
    assert sent_base_url == PROVIDERS["alibaba-plan"].base_url
    assert sent_base_url != PROVIDERS["alibaba"].base_url


def test_run_against_the_free_tier_provider_never_touches_the_plan_file(tmp_path, monkeypatch):
    monkeypatch.setattr(alibaba.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(alibaba.shutil, "which", lambda _: "/fake/opencode")
    _write_env(tmp_path / ".config/llm/dashscope.env", "free-tier-key-actually-sent")
    _write_env(tmp_path / ".config/llm/dashscope-plan.env", "plan-key-never-sent")

    seen: dict[str, object] = {}

    def transport(cmd, cwd, timeout_s, env):
        seen["env"] = env
        seen["config"] = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        return 0, event("step_finish", reason="stop"), "", 0.5

    monkeypatch.setattr(alibaba, "_run", transport)
    agent = alibaba.AlibabaAgent(tmp_path)
    result = agent.run("go", tmp_path)

    assert result.ok
    assert seen["env"]["DASHSCOPE_API_KEY"] == "free-tier-key-actually-sent"
    assert "plan-key-never-sent" not in json.dumps(seen), seen
    assert seen["config"]["provider"][alibaba.PROVIDER]["options"]["baseURL"] == PROVIDERS["alibaba"].base_url

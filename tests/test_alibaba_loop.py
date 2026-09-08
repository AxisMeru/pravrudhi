"""Offline adapter tests: fake CLI transport, never a provider request."""
import json
from pathlib import Path

import pytest

from pravrudhi.agents import alibaba_agent as alibaba
from pravrudhi.agents.base import CodingAgent
from pravrudhi.agents.registry import build_agent
from pravrudhi.application.credentials import Secret


@pytest.fixture
def key(monkeypatch):
    secret = Secret(provider="alibaba", value="test-secret-not-a-standard-key")
    # Takes the provider id now: the same agent serves the free tier and the Lite Plan, which have
    # different endpoints, different keys and different models.
    monkeypatch.setattr(alibaba, "credential", lambda provider_id="alibaba": secret)
    monkeypatch.setattr(alibaba.shutil, "which", lambda _: "/fake/opencode")
    return secret


def event(kind, **part):
    return json.dumps({"type": kind, "sessionID": "session-1", "part": part})


def test_dispatch_uses_model_and_environment_only(tmp_path, monkeypatch, key):
    transcript = "\n".join([
        event("step_start"),
        event("tool_use", tool="read", state={"status": "completed", "output": "x = 1"}),
        event("step_finish", reason="tool-calls"),
        event("text", text="Read x = 1"),
        event("step_finish", reason="stop"),
    ])

    def transport(cmd, cwd, timeout_s, env):
        assert cmd == ["opencode", "run", "--format", "json", "--agent", "build", "--dir", str(tmp_path.resolve()),
                       "-m", "pravrudhi-alibaba/qwen-test", "read x"]
        assert cwd == tmp_path and timeout_s == 12
        assert key.reveal() not in str(cmd)
        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        assert key.reveal() not in env["OPENCODE_CONFIG_CONTENT"]
        assert env["DASHSCOPE_API_KEY"] == key.reveal()
        provider = config["provider"][alibaba.PROVIDER]
        assert provider["npm"] == "@ai-sdk/openai-compatible"
        assert provider["options"]["baseURL"] == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
        assert config["small_model"] == config["model"]
        return 0, transcript, "", 0.5

    monkeypatch.setattr(alibaba, "_run", transport)
    agent = build_agent(tmp_path, "opencode:alibaba", "qwen-test")
    assert isinstance(agent, CodingAgent)
    result = agent.run("read x", tmp_path, timeout_s=12)
    assert result.ok and result.session_id == "session-1"
    assert result.text == transcript


@pytest.mark.parametrize("code,output", [
    (0, ""), (0, "not JSON"), (0, event("step_finish", reason="tool-calls")),
    (0, event("error", message="quota exceeded") + "\n" + event("step_finish", reason="stop")),
    (124, event("step_finish", reason="stop")), (1, "CLI failed"),
])
def test_failed_or_incomplete_transport_is_not_success(tmp_path, monkeypatch, key, code, output):
    monkeypatch.setattr(alibaba, "_run", lambda *a, **k: (code, output, "", 1))
    result = alibaba.AlibabaAgent(tmp_path).run("read", tmp_path)
    assert not result.ok and result.exit_code != 0


def test_redacts_exact_secret_from_both_streams(tmp_path, monkeypatch, key):
    monkeypatch.setattr(alibaba, "_run", lambda *a, **k: (1, key.reveal(), key.reveal(), 1))
    result = alibaba.AlibabaAgent(tmp_path).run("read", tmp_path)
    assert key.reveal() not in repr(result)
    assert "[REDACTED]" in result.text


def test_credentials_are_not_shell_code(tmp_path, monkeypatch):
    monkeypatch.setattr(alibaba.Path, "home", lambda: tmp_path)
    path = tmp_path / ".config/llm/dashscope.env"
    path.parent.mkdir(parents=True)
    path.write_text("export DASHSCOPE_API_KEY='dummy-value'\n")
    path.chmod(0o600)
    assert alibaba.credential().reveal() == "dummy-value"
    path.chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        alibaba.credential()
    assert not alibaba.AlibabaAgent(tmp_path).available()
    path.unlink()
    assert build_agent(tmp_path, "opencode:alibaba") is None


def test_spawn_failure_returns_failure_without_exception_contents(tmp_path, monkeypatch, key):
    def fail(*a, **kw):
        raise OSError(key.reveal())
    monkeypatch.setattr(alibaba, "_run", fail)
    result = alibaba.AlibabaAgent(tmp_path).run("read", tmp_path)
    assert not result.ok and key.reveal() not in repr(result)


def test_the_workspace_is_named_absolutely_so_writes_cannot_escape_it(tmp_path, monkeypatch, key):
    """OpenCode resolves a relative path against the project root it detects, not against its cwd.

    An agent worktree lives under `.worktrees/` inside the repository, so that detected root is the main checkout:
    given only `cwd`, a real dispatch wrote its whole deliverable there and left the worktree empty. `--dir` with
    an absolute path moves both the write tool and the bash tool's workdir into the worktree, which was confirmed
    against the live model before this test was written.
    """
    seen: dict[str, list[str]] = {}

    def transport(cmd, cwd, timeout_s, env):
        seen["cmd"] = cmd
        return 0, event("step_finish", reason="stop"), "", 0.5

    monkeypatch.setattr(alibaba, "_run", transport)
    ws = tmp_path / ".worktrees" / "agent-t"
    ws.mkdir(parents=True)
    agent = build_agent(tmp_path, "opencode:alibaba", "qwen-test")
    agent.run("do it", ws, timeout_s=12)

    cmd = seen["cmd"]
    assert "--dir" in cmd, cmd
    given = cmd[cmd.index("--dir") + 1]
    assert Path(given).is_absolute(), given
    assert Path(given) == ws.resolve()


def test_the_transcript_yields_what_the_turn_cost(tmp_path, monkeypatch, key):
    """A dispatch that cannot say what it spent cannot be budgeted, and a quota went in a day for want of this.

    OpenCode reports usage on each `step_finish`. The largest total across the turn is the cumulative figure —
    the counts include the context replayed every step, so summing them would multiply one conversation's cost
    by its number of steps.
    """
    transcript = "\n".join([
        event("step_finish", reason="tool-calls", tokens={"total": 20419, "input": 569, "output": 50}),
        event("step_finish", reason="tool-calls", tokens={"total": 33110, "input": 856, "output": 66}),
        event("step_finish", reason="stop", tokens={"total": 41002, "input": 980, "output": 71}),
    ])
    monkeypatch.setattr(alibaba, "_run", lambda *a, **k: (0, transcript, "", 1.0))
    result = alibaba.AlibabaAgent(tmp_path).run("do it", tmp_path)
    assert result.ok
    assert result.tokens == 41002, result.tokens


def test_a_transcript_without_usage_reports_zero_not_a_guess(tmp_path, monkeypatch, key):
    monkeypatch.setattr(alibaba, "_run", lambda *a, **k: (0, event("step_finish", reason="stop"), "", 1.0))
    assert alibaba.AlibabaAgent(tmp_path).run("x", tmp_path).tokens == 0

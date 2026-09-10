"""The coding-agent layer: protected paths, worktree isolation, adapter shape, honest availability."""
import subprocess
from pathlib import Path

import pytest

from pravrudhi.agents.base import PROTECTED, CodingAgent, Diff, GitWorktreeMixin
from pravrudhi.agents.cli_agents import ClaudeCodeAgent, CodexAgent
from pravrudhi.agents.orca_agent import OrcaAgent, OrcaUnavailable
from pravrudhi.agents.registry import build_registry, survey


def _repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    for c in (["init", "-q", "-b", "main"], ["config", "user.email", "t@e"], ["config", "user.name", "t"]):
        subprocess.run(["git", *c], cwd=r, check=True, capture_output=True)
    (r / "src").mkdir()
    (r / "src" / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=r, check=True, capture_output=True)
    return r


def test_protected_paths_are_flagged_not_silently_accepted():
    d = Diff(files=["src/ok.py", "pravrudhi_kernel/x.py", "research/ledger.jsonl", "research/prereg/lora_night.yaml"])
    assert d.violations == ["pravrudhi_kernel/x.py", "research/ledger.jsonl", "research/prereg/lora_night.yaml"]
    assert Diff(files=["src/ok.py"]).violations == []
    assert all(p in PROTECTED for p in ("pravrudhi_kernel/", "research/ledger.jsonl", "gates/"))


def test_worktree_isolates_changes_and_reports_the_diff(tmp_path):
    r = _repo(tmp_path)
    a = ClaudeCodeAgent(r)
    wt = a.create_workspace("t1")
    assert wt.exists() and wt != r
    (wt / "src" / "a.py").write_text("x = 2\n")
    (wt / "src" / "new.py").write_text("y = 3\n")
    d = a.collect_changes(wt)
    assert "src/a.py" in d.files and "src/new.py" in d.files and d.insertions >= 1
    assert (r / "src" / "a.py").read_text() == "x = 1\n", "main worktree must be untouched"
    assert d.violations == []
    a.stop(wt)
    assert not wt.exists()


def test_kernel_edit_by_an_agent_is_a_violation(tmp_path):
    r = _repo(tmp_path)
    (r / "pravrudhi_kernel").mkdir()
    (r / "pravrudhi_kernel" / "k.py").write_text("k = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "k"], cwd=r, check=True, capture_output=True)
    a = ClaudeCodeAgent(r)
    wt = a.create_workspace("t2")
    (wt / "pravrudhi_kernel" / "k.py").write_text("k = 999\n")
    assert a.collect_changes(wt).violations == ["pravrudhi_kernel/k.py"]
    a.stop(wt)


def test_adapters_satisfy_the_protocol_and_name_themselves(tmp_path):
    r = _repo(tmp_path)
    for a in (ClaudeCodeAgent(r), CodexAgent(r), OrcaAgent(r, agent_id="codex"), OrcaAgent(r, agent_id="local")):
        assert isinstance(a, CodingAgent) and isinstance(a, GitWorktreeMixin)
    assert ClaudeCodeAgent(r).name == "claude-code"
    assert CodexAgent(r).name == "codex"
    assert OrcaAgent(r, agent_id="codex").name == "orca:codex"


def test_orca_refuses_clearly_when_its_runtime_is_absent(tmp_path, monkeypatch):
    a = OrcaAgent(_repo(tmp_path))
    monkeypatch.setattr(a.ws, "ready", lambda: False)
    with pytest.raises(OrcaUnavailable):
        a.create_workspace("t3")


def test_each_agent_kind_has_a_headless_invocation():
    from pravrudhi.agents.orca_agent import LOCAL_PROVIDER, headless_command

    # `claude` is prefixed with `env CLAUDE_CONFIG_DIR=...` so an Orca terminal cannot reach the operator's
    # personal account -- Orca builds its own shell string, so there is no environment dict to pass and the
    # credential has to ride inside the command. The property to hold is that the pin is there and the
    # invocation is unchanged after it, not the exact prefix length.
    claude = headless_command("claude", "do it")
    assert claude[0] == "env"
    assert any(a.startswith("CLAUDE_CONFIG_DIR=") for a in claude), "the personal account must be unreachable"
    at = claude.index("claude")
    assert claude[at : at + 3] == ["claude", "-p", "do it"]
    # And only for claude: codex has its own credential store and its own instruction.
    assert headless_command("codex", "do it")[:2] == ["codex", "exec"]
    local = headless_command("local", "do it", model="qwen3-30b-a3b")
    assert local[:4] == ["opencode", "run", "--format", "json"]
    assert f"{LOCAL_PROVIDER}/qwen3-30b-a3b" in local
    with pytest.raises(OrcaUnavailable):
        headless_command("gemini", "do it")


def test_survey_reports_a_reason_for_every_agent(tmp_path):
    r = _repo(tmp_path)
    rows = survey(r)
    assert {r.name for r in rows} >= {"claude-code", "codex", "orca:claude", "orca:codex", "orca:local"}
    assert all(r.reason for r in rows), "an unavailable agent must say why"
    assert set(build_registry(r, include_orca=False)) == {
        "claude-code", "codex", "opencode:alibaba", "opencode:alibaba-plan",
    }


class TestCodexIsToldWhereToWorkUnambiguously:
    """Every dispatch to this agent failed in under a second, and it read as the agent refusing the work.

    The workspace is handed to `codex exec` twice: as the child's working directory, and again as `--cd`. A
    relative path is resolved a second time against the directory the process has already moved into, so
    `.worktrees/agent-x` becomes `.worktrees/agent-x/.worktrees/agent-x`. That path does not exist and codex
    exits immediately with an ENOENT that names nothing, which is indistinguishable in a swarm log from an agent
    that started and gave up.
    """

    def test_the_working_directory_is_passed_as_an_absolute_path(self, tmp_path: Path) -> None:
        from pravrudhi.agents.cli_agents import CodexAgent

        seen: dict[str, object] = {}

        def fake_run(cmd, cwd, timeout_s, env=None):  # type: ignore[no-untyped-def]
            seen["cmd"] = list(cmd)
            return 0, "done", "", 0.1

        agent = CodexAgent(tmp_path, model="m")
        import pravrudhi.agents.cli_agents as mod

        original = mod._run
        mod._run = fake_run  # type: ignore[assignment]
        try:
            agent.run("do the thing", Path(".worktrees/agent-x"), 60)
        finally:
            mod._run = original  # type: ignore[assignment]

        cmd = seen["cmd"]
        assert isinstance(cmd, list)
        target = cmd[cmd.index("--cd") + 1]
        assert Path(target).is_absolute(), (
            f"--cd was given {target!r}; a relative path is resolved again against the workspace and cannot exist"
        )
        assert not target.count("agent-x") > 1, "the workspace segment must not appear twice"

"""An Orca terminal gets its prompt from a private file on stdin, never from argv.

`headless_command` used to embed the prompt positionally for every agent (claude, codex, local/opencode). Orca
runs the joined string in a terminal, and the shell then execs the CLI with that prompt as one argv string --
which Linux caps at MAX_ARG_STRLEN (128 KiB), so a large benchmark prompt died with E2BIG exactly as it did in
`panel.ask_vendor` before 97606bf. There is no stdin pipe to hand a terminal, so the prompt goes into a 0600
file under the run directory and the shell redirects stdin from it (`< path`). All three CLIs read the prompt
from stdin when none is given positionally: `claude -p`, `codex exec` (documented in `--help`), and
`opencode run` (reads `Bun.stdin.text()` when stdin is not a TTY).
"""

from __future__ import annotations

import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from pravrudhi.agents import orca_agent
from pravrudhi.agents.orca_agent import OrcaAgent, headless_command, terminal_command, write_prompt_file

BIG = 250_000


def _big_prompt() -> str:
    # Non-ASCII, quotes and shell metacharacters, so any leak into the shell string or an encoding slip shows.
    return ("Section 302 IPC -- प्रमाण; $(rm -rf /) `x` 'q' \"dq\" \n" * (BIG // 40))[:BIG]


@pytest.fixture(autouse=True)
def _pin_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "claude-home"
    home.mkdir()
    (home / ".credentials.json").write_text("{}")
    monkeypatch.setenv("PRAVRUDHI_CLAUDE_CONFIG_DIR", str(home))


class TestTheCommandCarriesNoPrompt:
    @pytest.mark.parametrize("agent_id", ["claude", "codex", "local"])
    def test_headless_argv_has_no_prompt_slot(self, agent_id: str) -> None:
        argv = headless_command(agent_id, model="glm-4.7-flash")
        # The shape each CLI needs to read its prompt from stdin.
        if agent_id == "claude":
            at = argv.index("claude")
            assert argv[at : at + 2] == ["claude", "-p"]
            assert argv[at + 2].startswith("--")
        elif agent_id == "codex":
            assert argv[:2] == ["codex", "exec"]
            assert all(a.startswith("--") or a in ("exec", "codex", "workspace-write") for a in argv)
        else:
            assert argv[:2] == ["opencode", "run"]

    def test_passing_a_prompt_positionally_is_refused(self) -> None:
        # The old signature took the prompt second; it must not now be silently read as the model.
        with pytest.raises(TypeError):
            headless_command("claude", "do it")  # type: ignore[call-arg]

    @pytest.mark.parametrize("agent_id", ["claude", "codex", "local"])
    def test_a_250kb_prompt_never_appears_in_the_command_string(self, tmp_path: Path, agent_id: str) -> None:
        prompt = _big_prompt()
        path = write_prompt_file(tmp_path / "prompts", prompt)
        cmd = terminal_command(headless_command(agent_id), path)
        assert len(cmd) < 4096
        assert prompt[:200] not in cmd and "प्रमाण" not in cmd
        assert cmd.endswith(" < " + shlex.quote(str(path)))


class TestThePromptFile:
    def test_holds_the_exact_prompt_bytes(self, tmp_path: Path) -> None:
        prompt = _big_prompt()
        path = write_prompt_file(tmp_path / "prompts", prompt)
        assert path.read_bytes() == prompt.encode("utf-8")

    def test_is_private(self, tmp_path: Path) -> None:
        path = write_prompt_file(tmp_path / "prompts", "secret")
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) & 0o077 == 0

    def test_two_writes_do_not_collide(self, tmp_path: Path) -> None:
        a = write_prompt_file(tmp_path / "p", "one")
        b = write_prompt_file(tmp_path / "p", "two")
        assert a != b and a.read_text() == "one" and b.read_text() == "two"

    def test_the_default_directory_is_the_run_dir_not_tmp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tempfile

        monkeypatch.delenv(orca_agent.PROMPT_DIR_ENV, raising=False)
        agent = OrcaAgent(Path("/srv/repo"))  # constructing creates nothing on disk
        assert agent.prompt_dir == Path("/srv/repo/.pravrudhi/orca-prompts")
        assert not str(agent.prompt_dir).startswith(tempfile.gettempdir())

    def test_the_directory_can_be_configured_by_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(orca_agent.PROMPT_DIR_ENV, str(tmp_path / "elsewhere"))
        assert OrcaAgent(Path("/srv/repo")).prompt_dir == tmp_path / "elsewhere"


def _stand_ins(bin_dir: Path) -> None:
    """Executables named like each vendor CLI that print the byte count of their stdin and their argv length."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in ("claude", "codex", "opencode"):
        exe = bin_dir / name
        exe.write_text(
            f"#!{sys.executable}\n"
            "import sys\n"
            "data = sys.stdin.buffer.read()\n"
            "print('STDIN_BYTES', len(data))\n"
            "print('ARGV_BYTES', sum(len(a.encode()) for a in sys.argv))\n"
        )
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class TestTheShellStringActuallyRuns:
    @pytest.mark.parametrize("agent_id", ["claude", "codex", "local"])
    @pytest.mark.parametrize("dirname", ["plain", "with space", "it's \"quoted\" $HOME `x`"])
    def test_bash_delivers_the_whole_prompt_on_stdin(self, tmp_path: Path, agent_id: str, dirname: str) -> None:
        prompt = _big_prompt()
        bin_dir = tmp_path / "bin"
        _stand_ins(bin_dir)
        path = write_prompt_file(tmp_path / dirname / "prompts", prompt)
        cmd = terminal_command(headless_command(agent_id), path)

        env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
        done = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, env=env, timeout=60, check=False)

        assert done.returncode == 0, done.stderr
        lines = dict(line.split(" ", 1) for line in done.stdout.splitlines() if " " in line)
        assert int(lines["STDIN_BYTES"]) == len(prompt.encode("utf-8"))
        assert int(lines["ARGV_BYTES"]) < 4096


class TestOrcaAgentRun:
    def _fake_orca(self, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []

        def fake(args: list[str], timeout_s: int = 120) -> tuple[int, str, str]:
            rec: dict[str, Any] = {"args": list(args)}
            if args[:2] == ["terminal", "create"]:
                cmd = args[args.index("--command") + 1]
                rec["command"] = cmd
                # Read the redirect target while the terminal would be running, before any cleanup.
                target = shlex.split(cmd)[-1]
                rec["file"] = target
                rec["content"] = Path(target).read_text(encoding="utf-8")
                rec["mode"] = stat.S_IMODE(Path(target).stat().st_mode)
                calls.append(rec)
                return 0, '{"ok": true, "result": {"terminal": {"handle": "t-1"}}}', ""
            if args[:2] == ["terminal", "read"]:
                calls.append(rec)
                return 0, '{"ok": true, "result": {"lines": ["done"]}}', ""
            calls.append(rec)
            return 0, '{"ok": true, "result": {}}', ""

        monkeypatch.setattr(orca_agent, "_orca", fake)
        return calls

    @pytest.mark.parametrize("agent_id", ["claude", "codex", "local"])
    def test_run_sends_the_prompt_by_file_and_removes_it_after(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent_id: str
    ) -> None:
        calls = self._fake_orca(monkeypatch)
        prompt = _big_prompt()
        agent = OrcaAgent(tmp_path / "repo", agent_id=agent_id)
        run = agent.run(prompt, tmp_path / "wt")

        create = next(c for c in calls if "command" in c)
        assert prompt[:200] not in create["command"]
        assert all(prompt[:200] not in a for c in calls for a in c["args"])
        assert create["content"] == prompt
        assert create["mode"] == 0o600
        assert Path(create["file"]).parent == agent.prompt_dir
        assert run.ok is True and run.text == "done"
        assert not Path(create["file"]).exists(), "the prompt file must be removed once the terminal has exited"

    def test_the_file_is_removed_even_when_orca_gives_no_terminal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(args: list[str], timeout_s: int = 120) -> tuple[int, str, str]:
            return 1, '{"ok": false, "error": {"message": "no runtime"}}', ""

        monkeypatch.setattr(orca_agent, "_orca", refuse)
        agent = OrcaAgent(tmp_path / "repo", agent_id="codex")
        run = agent.run("hello", tmp_path / "wt")
        assert run.ok is False
        assert list(agent.prompt_dir.iterdir()) == []

    def test_a_configured_prompt_dir_is_used(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._fake_orca(monkeypatch)
        where = tmp_path / "state dir" / "prompts"
        OrcaAgent(tmp_path / "repo", agent_id="codex", prompt_dir=where).run("hi", tmp_path / "wt")
        create = next(c for c in calls if "command" in c)
        assert Path(create["file"]).parent == where

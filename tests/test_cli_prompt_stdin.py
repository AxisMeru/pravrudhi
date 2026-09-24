"""The prompt reaches a vendor CLI on stdin, never on argv.

Linux caps a single argv string at MAX_ARG_STRLEN (128 KiB). A P1 benchmark prompt carrying IL-TUR's full
100-statute candidate block exceeds it, and `subprocess.Popen` then fails before the CLI ever starts:
`OSError: [Errno 7] Argument list too long: 'claude'` (2026-09-24, row 313 of the claude-cli IL-TUR cell).
Both CLIs document stdin as a prompt source -- `claude -p` with no positional prompt, and `codex exec` with
the prompt omitted -- so the prompt moves there and argv carries only flags.

The end-to-end tests put a stand-in `claude` / `codex` executable on PATH and let the REAL `_run` launch it,
so "reaches the subprocess stdin" is observed from inside the child rather than inferred from a stub.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from pravrudhi.agents import cli_agents
from pravrudhi.agents.base import SCRATCH_DIRNAME

#: Well past MAX_ARG_STRLEN (131072), and past 200 KB as the regression requires.
BIG = 250_000


def _big_prompt() -> str:
    # Non-ASCII included so an encoding slip on the stdin path would change the byte count.
    return ("Section 302 IPC -- प्रमाण. " * (BIG // 20))[:BIG]


def _stand_in(bin_dir: Path, name: str, report: Path) -> None:
    """An executable named like the vendor CLI that records its argv and stdin, then answers."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    exe = bin_dir / name
    exe.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "data = sys.stdin.read()\n"
        f"open({str(report)!r}, 'w', encoding='utf-8').write(json.dumps({{'argv': sys.argv, 'stdin': data}}))\n"
        "print('ANSWER: A')\n"
    )
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _provision_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "claude-home"
    home.mkdir()
    (home / ".credentials.json").write_text("{}")
    monkeypatch.setenv("PRAVRUDHI_CLAUDE_CONFIG_DIR", str(home))
    return home


class TestRunFeedsStdin:
    def test_stdin_text_reaches_the_child_intact(self, tmp_path: Path) -> None:
        prompt = _big_prompt()
        (tmp_path / SCRATCH_DIRNAME).mkdir()
        code, out, err, _ = cli_agents._run(
            [sys.executable, "-c", "import sys; d = sys.stdin.read(); print(len(d)); print(d[-12:])"],
            tmp_path, 60, stdin_text=prompt,
        )
        assert code == 0, err
        n, tail = out.splitlines()[:2]
        assert int(n) == len(prompt)
        assert tail == prompt[-12:]

    def test_without_stdin_text_the_child_still_gets_devnull(self, tmp_path: Path) -> None:
        # The DEVNULL default exists so a CLI never waits on a parent agent's never-delivering pipe.
        code, out, _, _ = cli_agents._run(
            [sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"], tmp_path, 30,
        )
        assert code == 0
        assert out.strip() == "''"


class TestPanelAskVendorEndToEnd:
    def test_claude_prompt_goes_on_stdin_not_argv(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from pravrudhi.application import panel

        home = _provision_claude(tmp_path, monkeypatch)
        report = tmp_path / "report.json"
        _stand_in(tmp_path / "bin", "claude", report)
        monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.chdir(tmp_path)
        prompt = _big_prompt()

        ans = panel.ask_vendor(panel.VENDORS["claude-cli"], prompt)

        seen = json.loads(report.read_text(encoding="utf-8"))
        assert seen["stdin"] == prompt
        assert all(prompt not in a for a in seen["argv"])
        assert seen["argv"][1:] == ["-p", "--output-format", "text"]
        assert ans.text == "ANSWER: A"
        assert home.exists()

    def test_codex_prompt_goes_on_stdin_not_argv(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from pravrudhi.application import panel

        report = tmp_path / "report.json"
        _stand_in(tmp_path / "bin", "codex", report)
        monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.chdir(tmp_path)
        prompt = _big_prompt()

        ans = panel.ask_vendor(panel.VENDORS["codex-cli"], prompt)

        seen = json.loads(report.read_text(encoding="utf-8"))
        assert seen["stdin"] == prompt
        assert all(prompt not in a for a in seen["argv"])
        assert seen["argv"][1:] == ["exec", "--skip-git-repo-check"]
        assert ans.text == "ANSWER: A"


class _Capture:
    def __init__(self, out: str = "ok") -> None:
        self.calls: list[dict[str, object]] = []
        self.out = out

    def __call__(self, cmd, cwd, timeout_s, env=None, *, stdin_text=None):  # type: ignore[no-untyped-def]
        self.calls.append({"cmd": list(cmd), "env": dict(env or {}), "stdin_text": stdin_text})
        return 0, self.out, "", 0.1


class TestPanelKeepsTheAccount:
    def test_claude_env_is_passed_unchanged(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from pravrudhi.agents.account import claude_env
        from pravrudhi.application import panel

        _provision_claude(tmp_path, monkeypatch)
        cap = _Capture()
        monkeypatch.setattr(cli_agents, "_run", cap)
        prompt = _big_prompt()

        panel.ask_vendor(panel.VENDORS["claude-cli"], prompt)

        (call,) = cap.calls
        assert call["env"] == claude_env()
        assert call["stdin_text"] == prompt
        assert all(prompt not in a for a in call["cmd"])  # type: ignore[attr-defined]


class TestCodingAgentsUseStdin:
    def test_claude_code_agent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _provision_claude(tmp_path, monkeypatch)
        cap = _Capture(out=json.dumps({"result": "done", "session_id": "s", "total_cost_usd": 0.0}))
        monkeypatch.setattr(cli_agents, "_run", cap)
        prompt = _big_prompt()

        run = cli_agents.ClaudeCodeAgent(tmp_path, model="m").run(prompt, tmp_path)

        (call,) = cap.calls
        cmd = call["cmd"]
        assert isinstance(cmd, list)
        assert call["stdin_text"] == prompt
        assert all(prompt not in a for a in cmd)
        assert cmd[:2] == ["claude", "-p"] and "--model" in cmd and "--allowed-tools" in cmd
        assert "CLAUDE_CONFIG_DIR" in call["env"]  # type: ignore[operator]
        assert run.ok and run.text == "done"

    def test_codex_agent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cap = _Capture()
        monkeypatch.setattr(cli_agents, "_run", cap)
        prompt = _big_prompt()

        cli_agents.CodexAgent(tmp_path, model="m").run(prompt, tmp_path)

        (call,) = cap.calls
        cmd = call["cmd"]
        assert isinstance(cmd, list)
        assert call["stdin_text"] == prompt
        assert all(prompt not in a for a in cmd)
        assert cmd[:2] == ["codex", "exec"] and "--json" in cmd

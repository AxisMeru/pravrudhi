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


#: A minimal real-shaped `claude -p --output-format json` envelope (Tag review, 2026-09-26): the actual
#: 2026-09-26 seat-0 call this PR's own testing used had a `modelUsage` object keyed by the resolved model
#: (here `claude-sonnet-5`) and a `usage` object with real cache/input/output counts -- see `_usage` in
#: `cli_agents.py` for how those are summed.
CLAUDE_JSON_ENVELOPE = json.dumps({
    "result": "ANSWER: A",
    "is_error": False,
    "modelUsage": {"claude-sonnet-5": {"canonicalModel": "claude-sonnet-5"}},
    "usage": {"input_tokens": 2, "output_tokens": 4, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 20},
})


#: CONSTRUCTED codex `--json` stream (the agent_message/turn.completed shapes match the recorded stream in
#: test_astra_cost.py; the model-bearing `model` key is CONSTRUCTED -- see test_ask_vendor_gaps.py -- because
#: every codex call now needs a pinned AND resolved model).
CODEX_JSONL = "\n".join(json.dumps(e) for e in [
    {"type": "thread.started", "thread_id": "th_1", "model": "gpt-x-1"},
    {"type": "item.completed", "item": {"type": "agent_message", "text": "ANSWER: A"}},
    {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 5}},
])


def _stand_in(bin_dir: Path, name: str, report: Path, *, json_envelope: bool = False) -> None:
    """An executable named like the vendor CLI that records its argv and stdin, then answers.

    `json_envelope`: the claude-cli stand-in must answer with a real `--output-format json` envelope (Tag
    review, 2026-09-26) now that `panel.ask_vendor` parses one -- codex's stand-in keeps answering with plain
    text, since `ask_vendor`'s codex branch returns raw stdout unparsed.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    exe = bin_dir / name
    answer_line = (f"print({CLAUDE_JSON_ENVELOPE!r})" if json_envelope
                   else f"print({CODEX_JSONL!r})" if name == "codex" else "print('ANSWER: A')")
    exe.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "data = sys.stdin.read()\n"
        f"open({str(report)!r}, 'w', encoding='utf-8').write(json.dumps({{'argv': sys.argv, 'stdin': data}}))\n"
        f"{answer_line}\n"
    )
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _provision_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "claude-home"
    home.mkdir()
    (home / ".credentials.json").write_text("{}")
    monkeypatch.setenv("PRAVRUDHI_CLAUDE_CONFIG_DIR", str(home))
    return home


def _provision_claude_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`panel.ask_vendor`'s OWN dedicated credential directory (issue #59 follow-up) -- a separate env var
    from `_provision_claude` above, since `ask_vendor`'s claude-cli path deliberately does not use
    `account.claude_env`'s CLI-agent seat rotation at all."""
    home = tmp_path / "claude-cli-colab"
    home.mkdir()
    (home / ".credentials.json").write_text("{}")
    monkeypatch.setenv("PRAVRUDHI_CLAUDE_CLI_CONFIG_DIR", str(home))
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

        home = _provision_claude_cli(tmp_path, monkeypatch)
        report = tmp_path / "report.json"
        _stand_in(tmp_path / "bin", "claude", report, json_envelope=True)
        monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.chdir(tmp_path)
        prompt = _big_prompt()

        ans = panel.ask_vendor(panel.VENDORS["claude-cli"], prompt)

        seen = json.loads(report.read_text(encoding="utf-8"))
        assert seen["stdin"] == prompt
        assert all(prompt not in a for a in seen["argv"])
        # Issue #59: the slim invocation -- no plugin/MCP/skill/CLAUDE.md load, model capped at sonnet.
        # `--output-format json`, not `text` (Tag review, 2026-09-26): see TestPanelClaudeCliJsonEnvelope.
        assert seen["argv"][1:] == [
            "-p", "--output-format", "json",
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--setting-sources", "", "--disable-slash-commands", "--tools", "",
            "--model", "sonnet",
        ]
        assert ans.text == "ANSWER: A"
        assert ans.model == "sonnet"
        assert ans.resolved_model == "claude-sonnet-5"
        assert ans.tokens == 2 + 4 + 10 + 20
        assert ans.cache_read_tokens == 10
        assert ans.cache_write_tokens == 20
        assert home.exists()

    def test_codex_prompt_goes_on_stdin_not_argv(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from dataclasses import replace

        from pravrudhi.application import panel

        report = tmp_path / "report.json"
        _stand_in(tmp_path / "bin", "codex", report)
        monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.chdir(tmp_path)
        prompt = _big_prompt()

        base = panel.VENDORS["codex-cli"]
        ans = panel.ask_vendor(replace(base, params={**base.params, "codex_model": "gpt-x-1"}), prompt)

        seen = json.loads(report.read_text(encoding="utf-8"))
        assert seen["stdin"] == prompt
        assert all(prompt not in a for a in seen["argv"])
        assert seen["argv"][1:] == ["exec", "--skip-git-repo-check", "--json", "-m", "gpt-x-1"]
        assert ans.text == "ANSWER: A"
        assert ans.tokens == 105 and ans.cache_read_tokens == 60


class _Capture:
    #: Valid enough to satisfy `panel.ask_vendor`'s claude-cli JSON parsing (Tag review, 2026-09-26) so every
    #: existing call site that never cared about the answer text keeps working unchanged; a test that DOES
    #: care passes its own `out=`.
    def __init__(self, out: str = CLAUDE_JSON_ENVELOPE) -> None:
        self.calls: list[dict[str, object]] = []
        self.out = out

    def __call__(self, cmd, cwd, timeout_s, env=None, *, stdin_text=None):  # type: ignore[no-untyped-def]
        self.calls.append({"cmd": list(cmd), "env": dict(env or {}), "stdin_text": stdin_text})
        return 0, self.out, "", 0.1


class TestPanelKeepsTheAccount:
    def test_claude_cli_env_points_at_its_own_dedicated_seat(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Issue #59 follow-up: `ask_vendor`'s claude-cli path uses ITS OWN dedicated credential
        (`_claude_cli_env`), never `account.claude_env`'s CLI-agent seat rotation -- this one-shot vendor
        comparison would otherwise compete with those seats for the same weekly quota."""
        from pravrudhi.application import panel

        home = _provision_claude_cli(tmp_path, monkeypatch)
        cap = _Capture()
        monkeypatch.setattr(cli_agents, "_run", cap)
        prompt = _big_prompt()

        panel.ask_vendor(panel.VENDORS["claude-cli"], prompt)

        (call,) = cap.calls
        assert call["env"] == {"CLAUDE_CONFIG_DIR": str(home)}
        assert call["stdin_text"] == prompt
        assert all(prompt not in a for a in call["cmd"])  # type: ignore[attr-defined]

    def test_refuses_when_the_dedicated_seat_has_no_login(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail closed (Lead-2, 2026-09-26): a missing login at the dedicated colab directory must refuse,
        never silently fall back to whatever `CLAUDE_CONFIG_DIR` happens to be ambient in the environment."""
        from pravrudhi.application import panel

        monkeypatch.setenv("PRAVRUDHI_CLAUDE_CLI_CONFIG_DIR", str(tmp_path / "never-provisioned"))
        cap = _Capture()
        monkeypatch.setattr(cli_agents, "_run", cap)

        with pytest.raises(panel.ClaudeCliNotProvisioned, match="never-provisioned"):
            panel.ask_vendor(panel.VENDORS["claude-cli"], "hello")
        assert cap.calls == []  # refused before ever reaching the subprocess call

    def test_defaults_to_the_colab_directory_when_unset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No env override at all: `_claude_cli_env` still names a real, specific default location (the
        operator's own colab seat) rather than leaving `CLAUDE_CONFIG_DIR` to resolve however the ambient
        environment happens to. Monkeypatches the DEFAULT constant itself to a guaranteed-empty directory --
        never asserts on the real `~/.config/pravrudhi/claude-colab` path's actual state, since that is a
        real, live, machine-specific login this test must not depend on being present OR absent."""
        from pravrudhi.application import panel

        monkeypatch.delenv("PRAVRUDHI_CLAUDE_CLI_CONFIG_DIR", raising=False)
        empty = tmp_path / "not-provisioned-here"
        monkeypatch.setattr(panel, "CLAUDE_CLI_CONFIG_DIR_DEFAULT", empty)
        with pytest.raises(panel.ClaudeCliNotProvisioned, match=r"not-provisioned-here"):
            panel.ask_vendor(panel.VENDORS["claude-cli"], "hello")


class TestPanelSlimClaudeCliFlags:
    """Issue #59: `panel.ask_vendor`'s claude-cli path is a one-shot vendor comparison, never an agentic
    coding turn -- unlike `ClaudeCodeAgent`/`orca_agent.headless_command` below, it never needs a tool, a
    skill, an MCP server, or this repo's own CLAUDE.md, so it should never pay a default `claude -p`'s
    ~105k-token context load (Lead-2's own measurement, 2026-09-26 -- see TEAM-RULES.md) for a plain question.
    These tests capture the actual subprocess command line via a mocked `cli_agents._run` -- never a real
    `claude` binary."""

    #: The 6 slim flags this whole class exists to prove are present (Tag review, 2026-09-26): every test
    #: below that succeeds asserts every one of these appears in the captured argv, not just a same-length
    #: list equality that could pass by coincidence.
    SLIM_FLAGS: tuple[str, ...] = (
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--setting-sources", "", "--disable-slash-commands", "--tools", "",
    )

    def test_default_invocation_is_the_slim_flags_with_sonnet(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pravrudhi.application import panel

        _provision_claude_cli(tmp_path, monkeypatch)
        cap = _Capture()
        monkeypatch.setattr(cli_agents, "_run", cap)

        panel.ask_vendor(panel.VENDORS["claude-cli"], "hello")

        (call,) = cap.calls
        cmd = call["cmd"]
        assert isinstance(cmd, list)
        # `--output-format json`, not `text` (Tag review, 2026-09-26): the JSON envelope is what carries the
        # resolved model and the real usage breakdown -- see TestPanelClaudeCliJsonEnvelope.
        assert cmd == [
            "claude", "-p", "--output-format", "json",
            *self.SLIM_FLAGS,
            "--model", "sonnet",
        ]
        for flag in self.SLIM_FLAGS:
            assert flag in cmd

    def test_vendor_param_can_override_the_pinned_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A vendor may pick its own model within the Sonnet cap (an exact id here); opus without the
        pre-registered `effort: low` is refused -- see test_ask_vendor_gaps.py."""
        from dataclasses import replace

        from pravrudhi.application import panel

        _provision_claude_cli(tmp_path, monkeypatch)
        cap = _Capture()
        monkeypatch.setattr(cli_agents, "_run", cap)
        vendor = replace(panel.VENDORS["claude-cli"], params={**panel.VENDORS["claude-cli"].params, "model": "claude-sonnet-5"})

        panel.ask_vendor(vendor, "hello")

        (call,) = cap.calls
        cmd = call["cmd"]
        assert isinstance(cmd, list)
        assert cmd[-2:] == ["--model", "claude-sonnet-5"]
        for flag in self.SLIM_FLAGS:
            assert flag in cmd

    def test_explicit_none_model_param_raises_rather_than_falling_back_to_the_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tag review, 2026-09-26: an ABSENT `params['model']` is the ordinary case the default exists for,
        but a vendor that explicitly sets `model=None` (e.g. a config override gone wrong) asked for
        something and got nothing -- that must raise, never silently resolve to `sonnet` via `x or DEFAULT`,
        which cannot tell "not set" from "set to a falsy value" apart."""
        from dataclasses import replace

        from pravrudhi.application import panel

        _provision_claude_cli(tmp_path, monkeypatch)
        cap = _Capture()
        monkeypatch.setattr(cli_agents, "_run", cap)
        vendor = replace(panel.VENDORS["claude-cli"], params={**panel.VENDORS["claude-cli"].params, "model": None})

        with pytest.raises(ValueError, match="params\\['model'\\]"):
            panel.ask_vendor(vendor, "hello")
        assert cap.calls == []  # refused before ever reaching the subprocess call

    def test_explicit_empty_string_model_param_also_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dataclasses import replace

        from pravrudhi.application import panel

        _provision_claude_cli(tmp_path, monkeypatch)
        cap = _Capture()
        monkeypatch.setattr(cli_agents, "_run", cap)
        vendor = replace(panel.VENDORS["claude-cli"], params={**panel.VENDORS["claude-cli"].params, "model": ""})

        with pytest.raises(ValueError, match="params\\['model'\\]"):
            panel.ask_vendor(vendor, "hello")
        assert cap.calls == []


class TestPanelClaudeCliJsonEnvelope:
    """Tag review, 2026-09-26: `--output-format json` carries the resolved model and the real usage breakdown
    that `text` threw away, and a quota/limit notice on stdout with exit 0 is a real, previously-seen failure
    mode (the 2026-09-26 incident that contaminated 847 audit rows), not a hypothetical one -- these assert
    that shape is handled honestly rather than scored as an answer."""

    def test_records_the_resolved_model_and_real_token_usage(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pravrudhi.application import panel

        _provision_claude_cli(tmp_path, monkeypatch)
        cap = _Capture(out=CLAUDE_JSON_ENVELOPE)
        monkeypatch.setattr(cli_agents, "_run", cap)

        ans = panel.ask_vendor(panel.VENDORS["claude-cli"], "hello")

        assert ans.model == "sonnet"  # the --model flag PINNED before the call
        assert ans.resolved_model == "claude-sonnet-5"  # what the vendor actually billed it as
        assert ans.tokens == 2 + 4 + 10 + 20
        assert ans.cache_read_tokens == 10
        assert ans.cache_write_tokens == 20

    def test_exit_0_with_an_empty_result_is_a_gap_never_a_scored_answer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The 2026-09-26 incident this guards against: a quota-exhausted `claude -p` exits 0 with a limit
        notice on stdout, which parses as valid JSON with no `result` text worth scoring -- must raise, so
        `run_panel` records it as a gap, never as an answer with an empty string in it."""
        from pravrudhi.application import panel

        _provision_claude_cli(tmp_path, monkeypatch)
        empty = json.dumps({"result": "", "is_error": False})
        cap = _Capture(out=empty)
        monkeypatch.setattr(cli_agents, "_run", cap)

        with pytest.raises(RuntimeError, match="empty result"):
            panel.ask_vendor(panel.VENDORS["claude-cli"], "hello")

    def test_an_is_error_envelope_raises_even_on_exit_0(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pravrudhi.application import panel

        _provision_claude_cli(tmp_path, monkeypatch)
        errored = json.dumps({"result": "something went wrong", "is_error": True})
        cap = _Capture(out=errored)
        monkeypatch.setattr(cli_agents, "_run", cap)

        with pytest.raises(RuntimeError):
            panel.ask_vendor(panel.VENDORS["claude-cli"], "hello")

    def test_unparseable_stdout_on_exit_0_raises_rather_than_returning_raw_text(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A quota/limit notice can print as plain text rather than JSON -- exit 0, unparseable stdout must
        be treated exactly like exit 0 with an empty envelope: a gap, never `Answer(text=<the raw notice>)`."""
        from pravrudhi.application import panel

        _provision_claude_cli(tmp_path, monkeypatch)
        cap = _Capture(out="You've hit your usage limit. Try again later.")
        monkeypatch.setattr(cli_agents, "_run", cap)

        with pytest.raises(RuntimeError):
            panel.ask_vendor(panel.VENDORS["claude-cli"], "hello")

    def test_nonzero_exit_still_raises_as_before(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pravrudhi.application import panel

        _provision_claude_cli(tmp_path, monkeypatch)

        class _Fail:
            def __call__(self, cmd, cwd, timeout_s, env=None, *, stdin_text=None):  # type: ignore[no-untyped-def]
                return 1, "", "boom", 0.1

        monkeypatch.setattr(cli_agents, "_run", _Fail())

        with pytest.raises(RuntimeError, match="boom"):
            panel.ask_vendor(panel.VENDORS["claude-cli"], "hello")

    def test_panel_manifest_records_the_pinned_model_for_claude_cli(self) -> None:
        """Tag review, 2026-09-26: the manifest is written before any call is made, so it cannot know a
        RESOLVED model -- but it can and must record the PINNED one, computed the same way `ask_vendor` will,
        so a reader of the manifest already knows what the run was going to ask for."""
        from pravrudhi.application import panel

        manifest = panel.panel_manifest([], [panel.VENDORS["claude-cli"], panel.VENDORS["codex-cli"]])

        by_id = {v["id"]: v for v in manifest["vendors"]}
        assert by_id["claude-cli"]["pinned_model"] == panel.CLAUDE_CLI_MODEL_DEFAULT
        assert by_id["codex-cli"]["pinned_model"] is None  # not a claude-cli vendor -- nothing to pin


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

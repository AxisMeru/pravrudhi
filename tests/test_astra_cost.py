"""The most expensive route in the table must not be the one route whose cost is invisible.

`astra` is `codex` on `gpt-6-astra`: relative_cost 10.0, admitted at the design and critical tiers — the
dearest seat this engine has. Of its 32 recorded dispatches, 0 carried a token figure, because `CodexAgent.run`
ran plain `codex exec` and extracted nothing from it.

It was reporting usage the whole time. `codex exec --json` emits JSONL events, and the `turn.completed` event
carries usage. Observed on a real trivial call on this machine, 2026-09-09:

    {"type": "turn.completed", "usage": {"input_tokens": 15296, "cached_input_tokens": 12160,
     "cache_write_input_tokens": 0, "output_tokens": 5, "reasoning_output_tokens": 0}}

Note the arithmetic differs from Anthropic's. 15,296 input tokens for a four-word prompt, of which 12,160 were
cached, means `cached_input_tokens` is a SUBSET of `input_tokens` here — where Anthropic reports
`cache_read_input_tokens` alongside a separate, small `input_tokens`. Summing the same way for both would
double-count every cached token on this seat.

Spec: docs/superpowers/specs/2026-09-09-dispatch-cost-instrumentation.md, card M6.3.
"""

from __future__ import annotations

import json
from pathlib import Path

from pravrudhi.agents import cli_agents
from pravrudhi.agents.cli_agents import CodexAgent

# The event stream a real `codex exec --json` produced, trimmed to the events that matter.
REAL_EVENTS = [
    {"type": "thread.started", "thread_id": "th_1"},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"type": "assistant_message", "text": "ok"}},
    {"type": "turn.completed", "usage": {
        "input_tokens": 15296, "cached_input_tokens": 12160,
        "cache_write_input_tokens": 0, "output_tokens": 5, "reasoning_output_tokens": 0}},
]


def _stub(monkeypatch, lines, code: int = 0) -> list[list[str]]:
    seen: list[list[str]] = []

    def fake_run(cmd, cwd, timeout_s, env=None):  # type: ignore[no-untyped-def]
        seen.append(list(cmd))
        body = lines if isinstance(lines, str) else "\n".join(json.dumps(e) for e in lines)
        return code, body, "", 2.0
    monkeypatch.setattr(cli_agents, "_run", fake_run)
    return seen


class TestAstraReportsWhatItSpent:
    def test_the_turn_completed_event_becomes_the_run_cost(self, tmp_path: Path, monkeypatch) -> None:
        _stub(monkeypatch, REAL_EVENTS)
        run = CodexAgent(tmp_path).run("do a thing", tmp_path)

        assert run.ok
        # input + output only: cached_input_tokens is already inside input_tokens on this vendor, so adding it
        # would count 12,160 tokens twice.
        assert run.tokens == 15296 + 5
        assert run.cache_read_tokens == 12160
        assert run.cache_write_tokens == 0

    def test_the_json_flag_is_actually_passed(self, tmp_path: Path, monkeypatch) -> None:
        """Without it codex prints prose and there is no usage to read."""
        seen = _stub(monkeypatch, REAL_EVENTS)
        CodexAgent(tmp_path).run("do a thing", tmp_path)
        assert "--json" in seen[0]

    def test_a_stream_without_usage_is_unmeasured_not_free(self, tmp_path: Path, monkeypatch) -> None:
        _stub(monkeypatch, [e for e in REAL_EVENTS if e["type"] != "turn.completed"])
        run = CodexAgent(tmp_path).run("do a thing", tmp_path)
        assert run.tokens is None
        assert run.cache_read_tokens is None and run.cache_write_tokens is None

    def test_prose_output_is_unmeasured_rather_than_a_crash(self, tmp_path: Path, monkeypatch) -> None:
        """An older codex, or one that ignored --json, must degrade to unmeasured and still return a run."""
        _stub(monkeypatch, "I could not do that, sorry.")
        run = CodexAgent(tmp_path).run("do a thing", tmp_path)
        assert run.tokens is None and run.ok

    def test_the_last_turn_wins_when_a_run_has_several(self, tmp_path: Path, monkeypatch) -> None:
        """`codex exec` can report more than one turn; the cost of the run is the last cumulative report."""
        events = list(REAL_EVENTS) + [{"type": "turn.completed", "usage": {
            "input_tokens": 20000, "cached_input_tokens": 15000,
            "cache_write_input_tokens": 100, "output_tokens": 40, "reasoning_output_tokens": 0}}]
        _stub(monkeypatch, events)
        run = CodexAgent(tmp_path).run("do a thing", tmp_path)
        assert run.tokens == 20000 + 40
        assert run.cache_read_tokens == 15000 and run.cache_write_tokens == 100


class TestTheSentinelStillSeesTheVendorsWords:
    def test_the_whole_output_is_kept_so_a_usage_limit_is_still_detectable(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """`delegate.dispatch` classifies limits over `run.text`. Replacing it with just the final message would
        have hidden the vendor's own limit sentence and silently broken the fallback chain for the dearest seat
        in the table."""
        from pravrudhi.application import availability

        events = list(REAL_EVENTS) + [
            {"type": "item.completed", "item": {"type": "error", "message": "You've hit your usage limit."}},
        ]
        _stub(monkeypatch, events, code=1)
        run = CodexAgent(tmp_path).run("do a thing", tmp_path)

        assert "usage limit" in run.text.lower(), "the vendor's own words must survive into run.text"
        assert availability.classify("codex", run.text, 1) == "limited"

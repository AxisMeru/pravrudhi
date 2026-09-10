"""A dispatch must report what it cost, and an unmeasured dispatch must not read as a free one.

Counted on 2026-09-09 across all 157 outcomes in `.pravrudhi/routing.jsonl`: 155 (98.7%) carried no token
figure. `AgentRun.tokens` is declared `int = 0` and only `agents/alibaba_agent.py` ever assigns it, so every
other seat reports zero, and `Outcome.tokens` carries the docstring "Zero means unknown, never free" — a
semantic the type contradicts. Three call sites then coerce it with `int(... or 0)`, which is what erases the
distinction for good. `routing.spend` sums what it can see, so a weekly allowance is measured against 1.3% of
the dispatches and `over_budget` can essentially never trip.

The measurement was there the whole time. `claude -p --output-format json` returns a `usage` object in the same
envelope the adapter already parses for `total_cost_usd`, carrying input, output and both cache counters. The
shape asserted here is the shape a real call returned on 2026-09-09.

Spec: docs/superpowers/specs/2026-09-09-dispatch-cost-instrumentation.md, card M6.1.
"""

from __future__ import annotations

import json
from pathlib import Path

from pravrudhi.agents import cli_agents
from pravrudhi.agents.cli_agents import ClaudeCodeAgent
from pravrudhi.application import routing
from pravrudhi.application.routing import Outcome

# Field-for-field the envelope a real `claude -p --output-format json` returned on 2026-09-09.
REAL_ENVELOPE = {
    "type": "result", "subtype": "success", "is_error": False, "result": "ok",
    "session_id": "sess-abc", "total_cost_usd": 0.4901805, "num_turns": 1,
    "usage": {
        "input_tokens": 2,
        "output_tokens": 4,
        "cache_creation_input_tokens": 47852,
        "cache_read_input_tokens": 23101,
        "output_tokens_details": {"thinking_tokens": 0},
        "cache_creation": {"ephemeral_1h_input_tokens": 47852, "ephemeral_5m_input_tokens": 0},
        "service_tier": "standard",
    },
}


def _stub_cli(monkeypatch, payload: object, code: int = 0, *, credential: Path | None = None) -> None:
    def fake_run(cmd, cwd, timeout_s, env=None):  # type: ignore[no-untyped-def]
        return code, json.dumps(payload) if not isinstance(payload, str) else payload, "", 1.5
    monkeypatch.setattr(cli_agents, "_run", fake_run)
    # `ClaudeCodeAgent.run` refuses to spawn `claude` without this project's OWN credential, rather than
    # falling back to the operator's personal login (2026-09-10 instruction). That refusal is production
    # behaviour worth keeping, so these tests provision a credential instead of monkeypatching it away --
    # which also documents what "provisioned" means: a config directory holding `.credentials.json`.
    if credential is not None:
        credential.mkdir(parents=True, exist_ok=True)
        (credential / ".credentials.json").write_text("{}")
        monkeypatch.setenv("PRAVRUDHI_CLAUDE_CONFIG_DIR", str(credential))


class TestTheAdapterReportsWhatItSpent:
    def test_usage_in_the_envelope_becomes_the_run_cost(self, tmp_path: Path, monkeypatch) -> None:
        _stub_cli(monkeypatch, REAL_ENVELOPE, credential=tmp_path / 'claude')
        run = ClaudeCodeAgent(tmp_path).run("do a thing", tmp_path)

        assert run.ok
        # Every token the vendor billed for, cache included: cache read is cheap, not free.
        assert run.tokens == 2 + 4 + 47852 + 23101
        assert run.cost_usd == 0.4901805
        # The two counters the prompt-cache target is computed from, kept apart rather than summed away.
        assert run.cache_read_tokens == 23101
        assert run.cache_write_tokens == 47852

    def test_an_envelope_without_usage_is_unmeasured_not_free(self, tmp_path: Path, monkeypatch) -> None:
        """The whole defect in one assertion: absence must not arrive as zero."""
        _stub_cli(
            monkeypatch,
            {k: v for k, v in REAL_ENVELOPE.items() if k != "usage"},
            credential=tmp_path / "claude",
        )
        run = ClaudeCodeAgent(tmp_path).run("do a thing", tmp_path)

        assert run.tokens is None, "an adapter that cannot tell must say so, not report zero"
        assert run.cache_read_tokens is None and run.cache_write_tokens is None

    def test_unparseable_output_is_unmeasured(self, tmp_path: Path, monkeypatch) -> None:
        _stub_cli(monkeypatch, "not json at all", credential=tmp_path / 'claude')
        run = ClaudeCodeAgent(tmp_path).run("do a thing", tmp_path)
        assert run.tokens is None

    def test_a_genuine_zero_survives(self, tmp_path: Path, monkeypatch) -> None:
        """A dispatch that really consumed nothing is a measurement, and must stay distinct from silence."""
        envelope = dict(REAL_ENVELOPE)
        envelope["usage"] = {"input_tokens": 0, "output_tokens": 0,
                             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        _stub_cli(monkeypatch, envelope, credential=tmp_path / 'claude')
        assert ClaudeCodeAgent(tmp_path).run("x", tmp_path).tokens == 0


class TestTheRecordKeepsTheDistinction:
    def test_an_unmeasured_outcome_round_trips_as_unmeasured(self, tmp_path: Path) -> None:
        routing.record_outcome(tmp_path, Outcome(
            tier="standard", route_id="sonnet", task_id="t1", accepted=True, wall_s=1.0,
            at="2026-09-09T12:00:00Z", tokens=None))
        (back,) = routing.outcomes(tmp_path)
        assert back.tokens is None, "reading the log back must not invent a zero"

    def test_a_measured_outcome_round_trips_with_its_cost_and_cache(self, tmp_path: Path) -> None:
        routing.record_outcome(tmp_path, Outcome(
            tier="standard", route_id="sonnet", task_id="t2", accepted=True, wall_s=1.0,
            at="2026-09-09T12:00:00Z", tokens=70959, cost_usd=0.49,
            cache_read_tokens=23101, cache_write_tokens=47852))
        (back,) = routing.outcomes(tmp_path)
        assert back.tokens == 70959 and back.cost_usd == 0.49
        assert back.cache_read_tokens == 23101 and back.cache_write_tokens == 47852

    def test_spend_ignores_unmeasured_but_counts_a_real_zero(self, tmp_path: Path) -> None:
        for i, tok in enumerate((None, 0, 100)):
            routing.record_outcome(tmp_path, Outcome(
                tier="standard", route_id="sonnet", task_id=f"t{i}", accepted=True, wall_s=1.0,
                at="2026-09-09T12:00:00Z", tokens=tok))
        from datetime import UTC, datetime
        spent = routing.spend(tmp_path, now=datetime(2026, 9, 9, 13, 0, tzinfo=UTC))
        assert spent == {"sonnet": 100}


class TestTheHoleIsVisible:
    """`spend` silently summing 1.3% of dispatches is how a budget stops being a control without saying so."""

    def test_coverage_reports_how_much_of_the_record_carries_a_cost(self, tmp_path: Path) -> None:
        for i, tok in enumerate((None, None, None, 500)):
            routing.record_outcome(tmp_path, Outcome(
                tier="standard", route_id="sonnet", task_id=f"t{i}", accepted=True, wall_s=1.0,
                at="2026-09-09T12:00:00Z", tokens=tok))
        cover = routing.cost_coverage(tmp_path)
        assert cover["total"] == 4
        assert cover["measured"] == 1
        assert cover["unmeasured"] == 3
        assert cover["share_measured"] == 0.25
        assert cover["by_route"]["sonnet"] == {"total": 4, "measured": 1, "measured_nonzero": 1}

    def test_coverage_of_an_empty_record_is_not_a_division_by_zero(self, tmp_path: Path) -> None:
        cover = routing.cost_coverage(tmp_path)
        assert cover["total"] == 0 and cover["share_measured"] == 0.0


class TestCoverageDoesNotFlatterItselfOnLegacyRows:
    """Rows written before 2026-09-09 encoded "unknown" as zero, which the new reading counts as a measurement.

    Run against the real record straight after the change, `cost_coverage` reported 5 of 157 measured where a
    hand count that treated zero as unknown found 2. Neither is wrong; they disagree about legacy encoding. An
    instrument built to expose a blind spot must not overstate its own coverage, so both readings are reported.
    """

    def test_a_recorded_zero_is_counted_but_also_reported_separately(self, tmp_path: Path) -> None:
        for i, tok in enumerate((None, 0, 0, 500)):
            routing.record_outcome(tmp_path, Outcome(
                tier="standard", route_id="sonnet", task_id=f"t{i}", accepted=True, wall_s=1.0,
                at="2026-09-09T12:00:00Z", tokens=tok))
        cover = routing.cost_coverage(tmp_path)
        assert cover["measured"] == 3, "a recorded zero is a measurement under the new encoding"
        assert cover["measured_nonzero"] == 1, "and only one row actually carries a cost"
        assert cover["share_measured_nonzero"] == 0.25


class TestOpenCodeUsageIsUnmeasuredWhenAbsent:
    """The same invariant this file already enforces for `claude`, now for the OpenCode seat.

    Measured in `.pravrudhi/routing.jsonl` on 2026-09-10: every `qwen-lite-max` outcome carries `tokens: 0`
    while the two `sonnet` outcomes beside them carry 660,860 and 1,612,380. Zero is this codebase's word for
    "the seat says it was free", and an Alibaba dispatch is not free -- so the seat the operator wants as the
    bulk tier is the one reporting a number that reads as no spend at all.

    The cause is `sum(step_tokens.values())` over an empty dict, which is 0. It is the surviving sibling of the
    `max()`-versus-`sum()` defect recorded in the comment above it, which under-read a measured 8,679,807-token
    session as 170,830 and let a weekly plan empty in a day without the budget ever tripping. That one made the
    meter read 1/500th of the spend; this one makes it read none of it.
    """

    def test_a_stream_with_no_usage_parts_is_unmeasured_not_free(self) -> None:
        from pravrudhi.agents.alibaba_agent import usage_from_events

        stream = "\n".join(
            json.dumps(e)
            for e in ({"sessionID": "s1"}, {"type": "step_finish", "part": {"reason": "stop"}})
        )
        assert usage_from_events(stream) is None

    def test_usage_parts_are_summed_across_steps(self) -> None:
        """Summed, not maxed: OpenCode reports each step's own total, and the bill is their sum."""
        from pravrudhi.agents.alibaba_agent import usage_from_events

        stream = "\n".join(
            json.dumps({"part": {"id": pid, "tokens": {"total": n}}})
            for pid, n in (("p1", 1000), ("p2", 2500), ("p3", 400))
        )
        assert usage_from_events(stream) == 3900

    def test_a_re_emitted_step_is_not_counted_twice(self) -> None:
        from pravrudhi.agents.alibaba_agent import usage_from_events

        stream = "\n".join(
            json.dumps({"part": {"id": pid, "tokens": {"total": n}}})
            for pid, n in (("p1", 1000), ("p1", 1000), ("p2", 500))
        )
        assert usage_from_events(stream) == 1500

    def test_a_genuine_zero_survives(self) -> None:
        """A step that really reported zero is a measured zero and must not become `None`: the distinction
        runs both ways, and collapsing it in either direction is the defect."""
        from pravrudhi.agents.alibaba_agent import usage_from_events

        assert usage_from_events(json.dumps({"part": {"id": "p1", "tokens": {"total": 0}}})) == 0

    def test_unparseable_output_is_unmeasured(self) -> None:
        from pravrudhi.agents.alibaba_agent import usage_from_events

        assert usage_from_events("not json at all\n{broken") is None

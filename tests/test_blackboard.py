"""The blackboard: the shared medium that lets agents in a swarm run affect one another, CPU-only, fake agents."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from pravrudhi.agents.base import AgentRun, Diff
from pravrudhi.application import blackboard, swarm
from pravrudhi.application.delegate import TaskSpec, Verdict


class CapturingAgent:
    """An agent that records every prompt it was run with, so a dispatcher's briefing can be inspected."""

    def __init__(self, name: str, files: list[str], prompts: list[str]) -> None:
        self.name, self.files, self._prompts = name, files, prompts

    def create_workspace(self, task_id, base_ref="HEAD"):
        return Path(tempfile.mkdtemp())

    def run(self, prompt, workspace, timeout_s=60):
        self._prompts.append(prompt)
        return AgentRun(agent=self.name, ok=True, exit_code=0, wall_s=0.1, text="", workspace=workspace)

    def collect_changes(self, workspace):
        return Diff(files=list(self.files))


def test_post_rejects_an_unknown_kind_and_an_empty_body(tmp_path):
    with pytest.raises(blackboard.BlackboardError):
        blackboard.post(tmp_path, "w1", author="a", kind="bogus", subject="x", body="fine text")
    with pytest.raises(blackboard.BlackboardError):
        blackboard.post(tmp_path, "w1", author="a", kind="finding", subject="x", body="   ")


def test_a_blackboard_entry_can_never_carry_a_numeric_result_claim(tmp_path):
    """The same guard `memory.remember` applies to a note: this is operational scaffolding for a swarm run, not
    evidence, and a copy of a ledger number here would go stale the moment the ledger is repaired."""
    with pytest.raises(blackboard.BlackboardError):
        blackboard.post(tmp_path, "w1", author="a", kind="finding", subject="x", body="GSM8K is now 49%")
    with pytest.raises(blackboard.BlackboardError):
        blackboard.post(tmp_path, "w1", author="a", kind="finding", subject="x", body="the run reached 0.4898")
    with pytest.raises(blackboard.BlackboardError):
        blackboard.post(tmp_path, "w1", author="a", kind="finding", subject="49% done", body="fine body")
    # a plain fact with a small integer is not a results claim
    ok = blackboard.post(tmp_path, "w1", author="a", kind="finding", subject="x", body="touched 3 files")
    assert ok.body == "touched 3 files"


def test_post_read_roundtrip_and_kind_filter(tmp_path):
    f = blackboard.post(tmp_path, "w1", author="a1", kind="finding", subject="s1", body="b1", refs=("src/a.py",))
    w = blackboard.post(tmp_path, "w1", author="a2", kind="warning", subject="s2", body="b2")

    everything = blackboard.read(tmp_path, "w1")
    assert [e.id for e in everything] == [f.id, w.id], "read returns entries in append (oldest-first) order"
    assert everything[0].refs == ("src/a.py",)

    only_warnings = blackboard.read(tmp_path, "w1", kinds=("warning",))
    assert [e.id for e in only_warnings] == [w.id]

    since_f = blackboard.read(tmp_path, "w1", since=f.ts)
    assert [e.id for e in since_f] == [w.id] or since_f == [], "same-second timestamps may tie; either is honest"


def test_read_survives_a_corrupt_line(tmp_path):
    good = blackboard.post(tmp_path, "w1", author="a", kind="finding", subject="s", body="b")
    path = blackboard.board_dir(tmp_path) / "w1.jsonl"
    with path.open("a") as fh:
        fh.write("{not json\n")

    entries = blackboard.read(tmp_path, "w1")
    assert [e.id for e in entries] == [good.id]


def test_digest_is_bounded_and_deduplicated(tmp_path):
    blackboard.post(tmp_path, "w1", author="a1", kind="finding", subject="dup", body="same thing found twice")
    blackboard.post(tmp_path, "w1", author="a2", kind="finding", subject="dup", body="same thing found twice")
    blackboard.post(tmp_path, "w1", author="a3", kind="warning", subject="watch out", body="a distinct warning")

    full = blackboard.digest(tmp_path, "w1", 10_000)
    assert full.count("same thing found twice") == 1, "an identical entry posted twice costs nothing extra"
    assert "a distinct warning" in full
    # newest first: the warning, posted last, precedes the older (deduplicated) finding
    assert full.index("a distinct warning") < full.index("same thing found twice")

    tiny = blackboard.digest(tmp_path, "w1", 5)
    assert len(tiny) <= 5, "the budget is respected by dropping whole lines, never by cutting one in half"

    assert blackboard.digest(tmp_path, "empty-wave", 1000) == ""


def test_peer_briefing_is_empty_for_an_empty_digest_and_wraps_a_real_one():
    assert blackboard.peer_briefing("") == ""
    wrapped = blackboard.peer_briefing("- [finding] s: b")
    assert "shared blackboard" in wrapped
    assert wrapped.endswith("\n\n")
    assert "- [finding] s: b" in wrapped


def test_competition_records_the_winner_and_the_reason(tmp_path):
    good = Verdict("shared-task", "agent-a", True, [], ["src/x.py"], "", 1.0)
    bad = Verdict("shared-task", "agent-b", False, ["validation failed"], [], "", 2.0)

    result = blackboard.competition(tmp_path, "w1", "shared-task", [good, bad])
    assert result.winner == "agent-a"
    assert "agent-b" in result.reason and "validation failed" in result.reason

    posted = blackboard.read(tmp_path, "w1", kinds=("finding",))
    assert any(e.subject == "competition on shared-task" for e in posted)

    with pytest.raises(blackboard.BlackboardError):
        blackboard.competition(tmp_path, "w1", "shared-task", [good])


def test_competition_with_no_winner_still_records_why_every_attempt_lost(tmp_path):
    a = Verdict("t2", "agent-a", False, ["no change produced"], [], "", 1.0)
    b = Verdict("t2", "agent-b", False, ["validation failed"], [], "", 1.0)

    result = blackboard.competition(tmp_path, "w1", "t2", [a, b])
    assert result.winner is None
    assert "agent-a" in result.reason and "agent-b" in result.reason
    assert "no change produced" in result.reason and "validation failed" in result.reason


def test_peer_finding_reaches_the_next_waves_brief(tmp_path):
    """`run_wave(..., blackboard=True)`: what one wave posts, the next wave sharing the same `wave_id` inherits."""
    task_a = swarm.SwarmTask(TaskSpec("a", "do A", ("src/a.py",), validate="true"), "standard")
    task_b = swarm.SwarmTask(TaskSpec("b", "do B", ("src/b.py",), validate="true"), "standard")

    verdicts_a = swarm.run_wave(
        lambda name, model: CapturingAgent(name, ["src/a.py"], []),
        [task_a], log=lambda s: None, root=tmp_path, blackboard=True, wave_id="w1",
    )
    assert verdicts_a[0].accepted

    prompts_b: list[str] = []
    swarm.run_wave(
        lambda name, model: CapturingAgent(name, ["src/b.py"], prompts_b),
        [task_b], log=lambda s: None, root=tmp_path, blackboard=True, wave_id="w1",
    )
    assert prompts_b, "the fake agent must have been run"
    assert "shared blackboard" in prompts_b[0]
    assert "a: accepted" in prompts_b[0], "task a's outcome is what task b should be briefed on"


def test_wave_with_blackboard_off_behaves_exactly_as_before(tmp_path):
    # pre-seed a blackboard entry to prove it is ignored entirely when the flag is off
    blackboard.post(tmp_path, "w1", author="x", kind="finding", subject="pre", body="a pre-existing note")

    task = swarm.SwarmTask(TaskSpec("c", "do C", ("src/c.py",), validate="true"), "standard")
    prompts: list[str] = []
    swarm.run_wave(
        lambda name, model: CapturingAgent(name, ["src/c.py"], prompts),
        [task], log=lambda s: None, root=tmp_path, wave_id="w1",
    )

    assert prompts, "the fake agent must have been run"
    assert prompts[0].startswith(swarm.SCOPE_PREAMBLE + task.spec.prompt), "no briefing is added between them"
    assert "shared blackboard" not in prompts[0]

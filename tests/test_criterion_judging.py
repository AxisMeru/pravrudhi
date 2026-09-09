"""An accepted dispatch has to be able to close the criterion it was dispatched for.

Nothing in the engine ever called `requests.meet`. The obligations beat could dispatch work, and the work could
be accepted - in scope, validated, files on disk - and the criterion stayed unmet, because being accepted only
says the diff was well behaved. So the next beat chose the same criterion and dispatched it again, and the beat
after that, until the attempt budget parked it. Both engines did this: the studio spent three accepted
dispatches on r-5795501a criterion 7 and stopped, the product thirteen on r-3981d7e0 criterion 1.

Every criterion was a dead end. The loop could spend and could not progress, which is the shape the operator saw
as "stuck again".

Acceptance is not satisfaction, so the beat does not mark it met on acceptance. It asks, and it fails closed:
only an explicit verdict of met marks it met. A verdict of not-met is kept, so the next attempt starts from the
reason the last one fell short instead of guessing again.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pravrudhi.application import heartbeat, requests


def _owed(root: Path) -> str:
    req = requests.capture(root, "stand the thing up")
    requests.add_criteria(root, req.id, [requests.Criterion(text="the thing stands up", source="operator")])
    return req.id


def _accepting_agent(root: Path) -> object:
    """A build agent whose dispatch is accepted, writing one real file into the criterion's scratch."""
    class Fake:
        name = "fake"

        def create_workspace(self, task_id: str) -> Path:
            return root

        def run(self, prompt: str, workspace: Path, timeout_s: int = 0) -> object:
            index = prompt.count("\x00")  # unused; the file is written where the beat looks
            del index
            return type("R", (), {"ok": True, "text": "", "wall_s": 1.0, "tokens": 0, "stderr_tail": ""})()

        def collect_changes(self, workspace: Path) -> object:
            return type("D", (), {"files": [], "empty": False, "violations": []})()

        def stop(self, workspace: Path) -> None:
            return None

    return Fake()


def test_a_judged_criterion_is_marked_met_with_the_produced_files_as_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = _owed(tmp_path)
    produced = "proposals/requests/x/README.md"
    (tmp_path / "proposals/requests/x").mkdir(parents=True)
    (tmp_path / produced).write_text("it stands up")

    monkeypatch.setattr(heartbeat.swarm, "run_wave", lambda *a, **k: [
        type("V", (), {"accepted": True, "agent": "fake", "files": [produced], "reasons": []})()])

    _chose, reason, result = heartbeat._beat_obligations(
        tmp_path, lambda _t: "", judge=lambda **_kw: "VERDICT: met\nThe README shows the thing standing up.")

    criterion = requests.get(tmp_path, rid).criteria[0]
    assert criterion.met is True, reason
    assert [e.ref for e in criterion.evidence] == [produced]
    assert result["judged"] == "met"
    assert heartbeat.attempts(tmp_path, rid, 0) == 0, "a criterion that moved has not stalled"


def test_a_refused_judgement_leaves_it_unmet_and_keeps_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = _owed(tmp_path)
    monkeypatch.setattr(heartbeat.swarm, "run_wave", lambda *a, **k: [
        type("V", (), {"accepted": True, "agent": "fake", "files": ["proposals/requests/x/a.py"], "reasons": []})()])

    heartbeat._beat_obligations(
        tmp_path, lambda _t: "",
        judge=lambda **_kw: "VERDICT: not met\nIt describes the thing rather than standing it up.")

    request = requests.get(tmp_path, rid)
    assert request.criteria[0].met is False
    assert any("rather than standing it up" in str(n.get("note", "")) for n in request.notes), request.notes


def test_a_judge_that_says_nothing_useful_does_not_mark_it_met(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail closed: the same discipline the completion gate applies to a confident summary with no reasoning."""
    _owed(tmp_path)
    monkeypatch.setattr(heartbeat.swarm, "run_wave", lambda *a, **k: [
        type("V", (), {"accepted": True, "agent": "fake", "files": ["proposals/requests/x/a.py"], "reasons": []})()])

    for answer in ("", "looks good to me", "VERDICT: unclear"):
        _chose, _reason, result = heartbeat._beat_obligations(
            tmp_path, lambda _t: "", judge=lambda *, prompt, a=answer: a)
        assert result["judged"] == "not met", answer


def test_the_next_attempt_is_told_why_the_last_one_fell_short(tmp_path: Path) -> None:
    """Three identical dispatches taught the loop nothing. The prompt carries the last refusal forward."""
    prompt = heartbeat._obligation_prompt(
        "stand it up", "the thing stands up", "scratch", "true", prior="it described rather than built")
    assert "it described rather than built" in prompt

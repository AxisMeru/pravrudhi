"""The request ledger must make drift visible and must refuse to be told work is done.

Both properties were chosen after a session in which the operator had to repeat himself: an ask made once was
addressed partly, the partial state was reported as progress, and nothing in the system held the original words
against what had actually been built.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pravrudhi.application.requests import (
    Criterion,
    Evidence,
    RequestError,
    add_criteria,
    advance,
    backlog,
    capture,
    get,
    load,
    meet,
    next_unmet,
    staleness,
)


def _req(root: Path, text: str = "make the thing work", n: int = 2) -> str:
    r = capture(root, text)
    add_criteria(root, r.id, [Criterion(text=f"criterion {i}", source="operator") for i in range(n)])
    return r.id


class TestCapture:
    def test_the_operators_words_are_stored_unmodified(self, tmp_path: Path) -> None:
        text = "  don't be superficial..i want real things ..deep, sophisticated..complete  "
        r = capture(tmp_path, text)
        assert get(tmp_path, r.id) is not None
        assert load(tmp_path)[0].text == text, "the ask is quoted, never cleaned up"

    def test_capturing_the_same_ask_twice_does_not_duplicate_it(self, tmp_path: Path) -> None:
        a = capture(tmp_path, "same words")
        b = capture(tmp_path, "same words")
        assert a.id == b.id
        assert len(load(tmp_path)) == 1

    def test_a_criterion_records_who_wrote_it(self, tmp_path: Path) -> None:
        rid = _req(tmp_path)
        add_criteria(tmp_path, rid, [Criterion(text="my own reading", source="engine")])
        req = get(tmp_path, rid)
        assert req is not None
        sources = {c.source for c in req.criteria}
        assert sources == {"operator", "engine"}, "an invented criterion must never pass as the operator's words"


class TestTheEvidenceGate:
    def test_delivery_is_refused_while_a_criterion_is_unmet(self, tmp_path: Path) -> None:
        rid = _req(tmp_path)
        advance(tmp_path, rid, "in_progress")
        with pytest.raises(RequestError, match="unmet criterion"):
            advance(tmp_path, rid, "delivered")

    def test_a_criterion_cannot_be_met_by_assertion(self, tmp_path: Path) -> None:
        rid = _req(tmp_path)
        with pytest.raises(RequestError, match="evidence, not by assertion"):
            meet(tmp_path, rid, 0, [])

    def test_evidence_must_name_a_kind_the_ledger_understands(self, tmp_path: Path) -> None:
        rid = _req(tmp_path)
        with pytest.raises(RequestError, match="unknown evidence kind"):
            meet(tmp_path, rid, 0, [Evidence("vibes", "it looked fine")])

    def test_a_request_with_no_criteria_cannot_be_delivered(self, tmp_path: Path) -> None:
        r = capture(tmp_path, "something vague")
        advance(tmp_path, r.id, "in_progress")
        with pytest.raises(RequestError, match="nothing to have delivered"):
            advance(tmp_path, r.id, "delivered")

    def test_delivery_succeeds_once_every_criterion_carries_evidence(self, tmp_path: Path) -> None:
        rid = _req(tmp_path)
        meet(tmp_path, rid, 0, [Evidence("commit", "abc1234")])
        meet(tmp_path, rid, 1, [Evidence("command", "pytest -q", "12 passed")])
        advance(tmp_path, rid, "in_progress")
        req = advance(tmp_path, rid, "delivered")
        assert req.state == "delivered"
        assert advance(tmp_path, rid, "verified").state == "verified"


class TestTheStateMachine:
    def test_captured_cannot_jump_straight_to_verified(self, tmp_path: Path) -> None:
        rid = _req(tmp_path)
        with pytest.raises(RequestError, match="cannot go from captured to verified"):
            advance(tmp_path, rid, "verified")

    def test_an_ask_may_be_declined_with_a_reason_from_anywhere(self, tmp_path: Path) -> None:
        rid = _req(tmp_path)
        req = advance(tmp_path, rid, "declined", note="the kernel forbids it; ADR requested instead")
        assert req.state == "declined"
        assert "kernel forbids" in req.notes[-1]["note"]


class TestDriftIsVisible:
    def test_an_untouched_ask_gets_older_and_a_closed_one_does_not(self, tmp_path: Path) -> None:
        old = (datetime.now(UTC) - timedelta(days=9)).isoformat().replace("+00:00", "Z")
        r = capture(tmp_path, "an ask made and forgotten", asked_at=old)
        assert staleness(r) > 8.5

        add_criteria(tmp_path, r.id, [Criterion(text="one thing", source="operator")])
        meet(tmp_path, r.id, 0, [Evidence("commit", "deadbee")])
        advance(tmp_path, r.id, "in_progress")
        advance(tmp_path, r.id, "delivered")
        done = advance(tmp_path, r.id, "verified")
        assert staleness(done) == 0.0, "closed work is never stale"

    def test_the_heartbeat_is_handed_the_ask_that_has_waited_longest(self, tmp_path: Path) -> None:
        recent = capture(tmp_path, "asked just now")
        add_criteria(tmp_path, recent.id, [Criterion(text="new work", source="operator")])
        old_at = (datetime.now(UTC) - timedelta(days=5)).isoformat().replace("+00:00", "Z")
        old = capture(tmp_path, "asked days ago and never touched", asked_at=old_at)
        add_criteria(tmp_path, old.id, [Criterion(text="neglected work", source="operator")])

        picked = next_unmet(tmp_path)
        assert picked is not None
        req, criterion, index = picked
        assert req.id == old.id, "the oldest neglected ask is the one to work on"
        assert criterion.text == "neglected work" and index == 0

    def test_nothing_is_offered_when_every_ask_is_satisfied(self, tmp_path: Path) -> None:
        rid = _req(tmp_path, n=1)
        meet(tmp_path, rid, 0, [Evidence("file", "x.py")])
        advance(tmp_path, rid, "in_progress")
        advance(tmp_path, rid, "delivered")
        advance(tmp_path, rid, "verified")
        assert next_unmet(tmp_path) is None


class TestBacklog:
    def test_the_backlog_counts_what_is_open_and_how_far_each_has_got(self, tmp_path: Path) -> None:
        rid = _req(tmp_path, n=3)
        meet(tmp_path, rid, 0, [Evidence("commit", "abc")])
        b = backlog(tmp_path)
        assert b["total"] == 1 and b["open"] == 1
        assert b["requests"][0]["progress"] == [1, 3]
        assert b["by_state"]["captured"] == 1

    def test_open_asks_sort_before_closed_ones(self, tmp_path: Path) -> None:
        done = _req(tmp_path, "already handled", n=1)
        meet(tmp_path, done, 0, [Evidence("commit", "abc")])
        advance(tmp_path, done, "in_progress")
        advance(tmp_path, done, "delivered")
        advance(tmp_path, done, "verified")
        _req(tmp_path, "still outstanding", n=1)

        rows = backlog(tmp_path)["requests"]
        assert rows[0]["text"] == "still outstanding"

    def test_a_corrupt_store_is_an_empty_backlog_not_a_crash(self, tmp_path: Path) -> None:
        (tmp_path / ".pravrudhi").mkdir(parents=True)
        (tmp_path / ".pravrudhi" / "requests.json").write_text("{not json")
        assert load(tmp_path) == []
        assert backlog(tmp_path)["total"] == 0


class TestCorrectingABadReference:
    def test_a_wrong_reference_can_be_retracted(self, tmp_path: Path) -> None:
        from pravrudhi.application.requests import retract_evidence

        rid = _req(tmp_path, n=1)
        meet(tmp_path, rid, 0, [Evidence("commit", "wronghash"), Evidence("file", "real.py")])
        req = retract_evidence(tmp_path, rid, 0, "wronghash")
        assert [e.ref for e in req.criteria[0].evidence] == ["real.py"]
        assert req.criteria[0].met, "the criterion still stands on its remaining evidence"

    def test_retracting_the_last_reference_un_meets_the_criterion(self, tmp_path: Path) -> None:
        """Deleting the evidence that failed must not be a way to make a criterion pass."""
        from pravrudhi.application.requests import retract_evidence

        rid = _req(tmp_path, n=1)
        meet(tmp_path, rid, 0, [Evidence("commit", "onlyone")])
        req = retract_evidence(tmp_path, rid, 0, "onlyone")
        assert not req.criteria[0].met
        assert req.criteria[0].evidence == []

    def test_retracting_a_reference_that_is_not_there_is_refused(self, tmp_path: Path) -> None:
        from pravrudhi.application.requests import retract_evidence

        rid = _req(tmp_path, n=1)
        meet(tmp_path, rid, 0, [Evidence("file", "x.py")])
        with pytest.raises(RequestError, match="carries no evidence"):
            retract_evidence(tmp_path, rid, 0, "nothere")

"""The request ledger must make drift visible and must refuse to be told work is done.

Both properties were chosen after a session in which the operator had to repeat himself: an ask made once was
addressed partly, the partial state was reported as progress, and nothing in the system held the original words
against what had actually been built.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pravrudhi.application import requests
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
    next_obligation,
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


class TestWhatIsStillOwed:
    """The appetite said it was working on the oldest unmet criterion while the loop reported there was none.

    Both were reading half of the obligation. Once every criterion carries evidence there is nothing left to
    build, and the request is still owed until it has been through the gate. One answer serves both now.
    """

    def test_an_unmet_criterion_is_what_is_owed(self, tmp_path: Path) -> None:
        from pravrudhi.application.requests import next_obligation

        rid = _req(tmp_path, "build the thing", n=2)
        meet(tmp_path, rid, 0, [Evidence("commit", "abc1234")])
        owed = next_obligation(tmp_path)
        assert owed is not None
        assert owed["kind"] == "meet_criterion" and owed["criterion"] == 1

    def test_a_fully_evidenced_delivered_request_is_owed_the_gate(self, tmp_path: Path) -> None:
        from pravrudhi.application.requests import next_obligation

        rid = _req(tmp_path, "build the thing", n=1)
        meet(tmp_path, rid, 0, [Evidence("commit", "abc1234")])
        advance(tmp_path, rid, "in_progress")
        advance(tmp_path, rid, "delivered")
        owed = next_obligation(tmp_path)
        assert owed is not None
        assert owed["kind"] == "verify_request" and owed["request"] == rid
        assert "completion gate" in owed["description"]

    def test_a_fully_evidenced_request_not_yet_delivered_is_owed_its_next_step(self, tmp_path: Path) -> None:
        from pravrudhi.application.requests import next_obligation

        rid = _req(tmp_path, "build the thing", n=1)
        meet(tmp_path, rid, 0, [Evidence("commit", "abc1234")])
        owed = next_obligation(tmp_path)
        assert owed is not None and owed["kind"] == "advance_request" and owed["request"] == rid

    def test_nothing_is_owed_once_every_request_is_verified(self, tmp_path: Path) -> None:
        from pravrudhi.application.requests import next_obligation

        rid = _req(tmp_path, "build the thing", n=1)
        meet(tmp_path, rid, 0, [Evidence("commit", "abc1234")])
        for state in ("in_progress", "delivered", "verified"):
            advance(tmp_path, rid, state)
        assert next_obligation(tmp_path) is None


def test_a_malformed_criterion_can_be_dropped(tmp_path: Path) -> None:
    """A criterion that demands nothing can never be met, and blocks everything behind it.

    The completion gate turns a review's finding into a criterion. An early version of that extraction took the
    first non-heading line, which on a review that opens "Summary of the strongest reason:" produced a criterion
    with the demand missing. The extraction was fixed; the criterion it had already written stayed, unanswerable,
    and the loop dispatched an agent at it nine times in one day before an attempt budget stopped it.

    Dropping is deliberately not deleting evidence: it removes one criterion by index and leaves the rest, so the
    gate can re-run and write a well-formed one in its place.
    """
    req = requests.capture(tmp_path, "do the thing")
    requests.add_criteria(tmp_path, req.id, [
        requests.Criterion(text="a real, answerable demand about the interface", source="operator"),
        requests.Criterion(text="Answer the review's finding: Summary of the strongest reason:", source="engine"),
    ])
    before = requests.get(tmp_path, req.id)
    assert before is not None and len(before.criteria) == 2

    after = requests.drop_criterion(tmp_path, req.id, 1)

    assert len(after.criteria) == 1
    assert after.criteria[0].text.startswith("a real, answerable demand")


def test_dropping_an_index_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    req = requests.capture(tmp_path, "x")
    requests.add_criteria(tmp_path, req.id, [requests.Criterion(text="only one", source="operator")])
    with pytest.raises(requests.RequestError):
        requests.drop_criterion(tmp_path, req.id, 5)


class TestAParkedRequestDoesNotStarveTheRest:
    """A selector that returns work it cannot act on, every hour, is not selecting.

    Measured on 2026-09-10 from the heartbeat journal: five consecutive beats chose `r-5795501a`, found all its
    remaining criteria attempt-budget-spent, and returned `{"kind": "parked"}`. In the same workspace
    `r-cad91781` had 0 of 13 criteria unmet -- every one carrying evidence, one step from the completion gate --
    and was never looked at, because `next_obligation` takes the single STALEST open row and stops there.
    Staleness 4.2 against 4.1 was the whole difference, and five hours of the loop went into saying so.

    Parking is reported only when NOTHING is actionable. It stays visible either way:
    `watchdog._parked_criteria` reports it to `pravrudhi watch` independently of what the beat chose.
    """

    def test_an_actionable_request_is_preferred_to_a_staler_parked_one(self, tmp_path, monkeypatch) -> None:
        stale = capture(tmp_path, "older ask", asked_at="2026-09-01T00:00:00Z")
        fresh = capture(tmp_path, "newer ask", asked_at="2026-09-02T00:00:00Z")
        add_criteria(tmp_path, stale.id, [Criterion(text="cannot be met", source="operator")])
        add_criteria(tmp_path, fresh.id, [Criterion(text="already met", source="operator")])
        meet(tmp_path, fresh.id, 0, [Evidence("commit", "abc1234")])
        # Parking is a property of dispatch attempts, not of the ledger, so it is simulated here: what is
        # under test is the SELECTION, not how a criterion comes to be parked.
        monkeypatch.setattr(requests, "_parked", lambda root, rid, i: rid == stale.id)

        owed = next_obligation(tmp_path)
        assert owed is not None, "nothing offered while an actionable request waited"
        assert owed["request"] == fresh.id, f"chose {owed['request']} ({owed['kind']}) over an actionable request"
        assert owed["kind"] != "parked_request"

    def test_parked_is_still_reported_when_nothing_else_can_move(self, tmp_path, monkeypatch) -> None:
        """The branch must not vanish: when every open request is parked, saying so IS the work."""
        only = capture(tmp_path, "only ask", asked_at="2026-09-01T00:00:00Z")
        add_criteria(tmp_path, only.id, [Criterion(text="cannot be met", source="operator")])
        monkeypatch.setattr(requests, "_parked", lambda root, rid, i: True)

        owed = next_obligation(tmp_path)
        assert owed is not None and owed["kind"] == "parked_request" and owed["request"] == only.id


class TestSetMode:
    """`pravrudhi requests set-mode`'s underlying function: a hand-flip of dispatch mode, with a reason on
    record. An operator flipping eleven criteria by hand one morning, with no tool and no note, is what this
    replaces."""

    def test_flipping_mode_records_the_reason_and_the_old_value(self, tmp_path: Path) -> None:
        req = requests.capture(tmp_path, "ask")
        requests.add_criteria(tmp_path, req.id, [Criterion(text="update `src/x.py`", source="operator")])

        out = requests.set_mode(tmp_path, req.id, 0, "build", why="drafted before the detector existed")

        assert out.criteria[0].mode == "build"
        assert any("proposal -> build" in n["note"] and "drafted before the detector existed" in n["note"]
                   for n in out.notes)

    def test_a_blank_reason_is_refused(self, tmp_path: Path) -> None:
        req = requests.capture(tmp_path, "ask")
        requests.add_criteria(tmp_path, req.id, [Criterion(text="x", source="operator")])
        with pytest.raises(RequestError):
            requests.set_mode(tmp_path, req.id, 0, "build", why="  ")

    def test_an_unknown_mode_is_refused(self, tmp_path: Path) -> None:
        req = requests.capture(tmp_path, "ask")
        requests.add_criteria(tmp_path, req.id, [Criterion(text="x", source="operator")])
        with pytest.raises(RequestError):
            requests.set_mode(tmp_path, req.id, 0, "sideways", why="because")  # type: ignore[arg-type]

    def test_setting_the_mode_already_in_effect_is_a_silent_no_op(self, tmp_path: Path) -> None:
        req = requests.capture(tmp_path, "ask")
        requests.add_criteria(tmp_path, req.id, [Criterion(text="x", source="operator", mode="build")])
        out = requests.set_mode(tmp_path, req.id, 0, "build", why="already build")
        assert out.notes == [], "nothing changed, so nothing was written"


class TestDeclineCriterion:
    """A criterion-level "no" that keeps the record, unlike `drop_criterion` which erases one wrongly written."""

    def test_a_declined_criterion_is_no_longer_unmet(self, tmp_path: Path) -> None:
        req = requests.capture(tmp_path, "ask")
        requests.add_criteria(tmp_path, req.id, [Criterion(text="out of scope", source="operator")])
        out = requests.decline_criterion(tmp_path, req.id, 0, why="not something this engine can do")
        assert out.criteria[0].declined is True
        assert out.unmet() == []
        assert any("declined" in n["note"] and "not something this engine can do" in n["note"] for n in out.notes)

    def test_a_declined_criterion_does_not_block_the_beat_on_the_rest_of_the_request(self, tmp_path: Path) -> None:
        req = requests.capture(tmp_path, "ask")
        requests.add_criteria(tmp_path, req.id, [
            Criterion(text="out of scope", source="operator"),
            Criterion(text="still to build", source="operator"),
        ])
        requests.decline_criterion(tmp_path, req.id, 0, why="scope cut")
        picked = requests.next_unmet(tmp_path)
        assert picked is not None
        _, criterion, index = picked
        assert index == 1 and criterion.text == "still to build"

    def test_an_already_met_criterion_cannot_be_declined(self, tmp_path: Path) -> None:
        req = requests.capture(tmp_path, "ask")
        requests.add_criteria(tmp_path, req.id, [Criterion(text="done", source="operator")])
        requests.meet(tmp_path, req.id, 0, [Evidence("commit", "abc1234")])
        with pytest.raises(RequestError):
            requests.decline_criterion(tmp_path, req.id, 0, why="changed my mind")

    def test_a_blank_reason_is_refused(self, tmp_path: Path) -> None:
        req = requests.capture(tmp_path, "ask")
        requests.add_criteria(tmp_path, req.id, [Criterion(text="x", source="operator")])
        with pytest.raises(RequestError):
            requests.decline_criterion(tmp_path, req.id, 0, why="   ")

    def test_a_request_whose_only_criterion_is_declined_cannot_be_delivered(self, tmp_path: Path) -> None:
        """Everything unmet() reports is settled, but nothing was actually met - a request delivered on the
        strength of criteria that were only ever declined would be a false "done", not a real one."""
        req = requests.capture(tmp_path, "ask")
        requests.add_criteria(tmp_path, req.id, [Criterion(text="out of scope", source="operator")])
        requests.decline_criterion(tmp_path, req.id, 0, why="scope cut")
        requests.advance(tmp_path, req.id, "in_progress")
        with pytest.raises(RequestError, match="no criterion actually marked met"):
            requests.advance(tmp_path, req.id, "delivered")

    def test_a_request_with_one_met_and_one_declined_criterion_can_still_be_delivered(self, tmp_path: Path) -> None:
        req = requests.capture(tmp_path, "ask")
        requests.add_criteria(tmp_path, req.id, [
            Criterion(text="out of scope", source="operator"),
            Criterion(text="the real ask", source="operator"),
        ])
        requests.decline_criterion(tmp_path, req.id, 0, why="scope cut")
        requests.meet(tmp_path, req.id, 1, [Evidence("commit", "abc1234")])
        requests.advance(tmp_path, req.id, "in_progress")
        delivered = requests.advance(tmp_path, req.id, "delivered")
        assert delivered.state == "delivered"


class TestMarkMetByHand:
    """`meet`'s optional `why`/`actor`: a note only appears when a human decision supplied a reason, so the
    heartbeat's own automatic calls (which pass neither) keep writing exactly as they always have."""

    def test_a_reason_lands_as_a_dated_note_naming_the_actor_and_the_evidence(self, tmp_path: Path) -> None:
        req = requests.capture(tmp_path, "ask")
        requests.add_criteria(tmp_path, req.id, [Criterion(text="the widget spins", source="operator")])
        out = requests.meet(
            tmp_path, req.id, 0, [Evidence("commit", "abc1234")],
            why="watched it spin in the browser", actor="sharath",
        )
        assert out.criteria[0].met is True
        assert len(out.notes) == 1
        assert "sharath" in out.notes[0]["note"]
        assert "commit:abc1234" in out.notes[0]["note"]
        assert "watched it spin in the browser" in out.notes[0]["note"]

    def test_the_heartbeats_own_call_with_no_reason_writes_no_note(self, tmp_path: Path) -> None:
        req = requests.capture(tmp_path, "ask")
        requests.add_criteria(tmp_path, req.id, [Criterion(text="the widget spins", source="operator")])
        out = requests.meet(tmp_path, req.id, 0, [Evidence("commit", "abc1234")])
        assert out.notes == []

    def test_a_declined_criterion_cannot_be_marked_met(self, tmp_path: Path) -> None:
        req = requests.capture(tmp_path, "ask")
        requests.add_criteria(tmp_path, req.id, [Criterion(text="x", source="operator")])
        requests.decline_criterion(tmp_path, req.id, 0, why="scope cut")
        with pytest.raises(RequestError):
            requests.meet(tmp_path, req.id, 0, [Evidence("commit", "abc1234")])


class TestTheStoreIsLocked:
    """The heartbeat and a hand-edit both write `.pravrudhi/requests.json`; the lock is what keeps one from
    landing mid-write of the other. `locked()` is exclusive and reentrant-unsafe by design (see its docstring),
    so this checks the property that actually matters: two writers racing for the same criterion do not produce
    a torn or silently-overwritten file."""

    def test_two_concurrent_writers_do_not_corrupt_the_store(self, tmp_path: Path) -> None:
        import threading

        req = requests.capture(tmp_path, "ask")
        requests.add_criteria(tmp_path, req.id, [Criterion(text=f"c{i}", source="operator") for i in range(20)])
        errors: list[Exception] = []

        def meet_one(index: int) -> None:
            try:
                requests.meet(tmp_path, req.id, index, [Evidence("commit", f"sha{index}")])
            except Exception as e:  # noqa: BLE001 - collected and asserted on below
                errors.append(e)

        threads = [threading.Thread(target=meet_one, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        after = requests.get(tmp_path, req.id)
        assert after is not None
        assert all(c.met for c in after.criteria), "a lost update would leave some criteria un-met"

    def test_locked_is_exclusive_within_one_process(self, tmp_path: Path) -> None:
        import threading
        import time

        holder_ready = threading.Event()
        hold_for = 0.2

        def hold() -> None:
            with requests.locked(tmp_path):
                holder_ready.set()
                time.sleep(hold_for)

        t = threading.Thread(target=hold)
        t.start()
        assert holder_ready.wait(timeout=5)
        start = time.monotonic()
        with requests.locked(tmp_path):
            waited = time.monotonic() - start
        t.join()
        assert waited >= hold_for * 0.5, "the second lock must wait for the first to release, not run past it"

"""A criterion the loop cannot finish must stop being retried at full price.

On 2026-09-08 the studio heartbeat dispatched request r-5795501a criterion 7 nine times in one day — five
accepted, four rejected — and the criterion was still unmet at the end of it. Every hour an agent was paid to
produce a proposal, the adversarial reviewer refused it, and the next beat dispatched the identical task again.
Nothing recorded that the attempt had already been made, so nothing could notice it was being made again.

The Lite Plan seat hit its usage limit the same afternoon.

A retry is right; an unbounded identical retry is a standing order to spend. After a few attempts that do not
move the criterion, the loop records it as stalled, says so where the operator will see it, and spends the beat
on something else instead.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from pravrudhi.application import heartbeat
from pravrudhi.application.requests import Criterion, add_criteria, capture, next_unmet, note


def test_attempts_are_counted_per_criterion(tmp_path: Path) -> None:
    assert heartbeat.attempts(tmp_path, "r-1", 7) == 0
    heartbeat.record_attempt(tmp_path, "r-1", 7)
    heartbeat.record_attempt(tmp_path, "r-1", 7)
    assert heartbeat.attempts(tmp_path, "r-1", 7) == 2
    assert heartbeat.attempts(tmp_path, "r-1", 8) == 0, "a different criterion has its own budget"
    assert heartbeat.attempts(tmp_path, "r-2", 7) == 0, "a different request has its own budget"


def test_progress_clears_the_count(tmp_path: Path) -> None:
    """A criterion that moves has not stalled, whatever it cost to get there."""
    heartbeat.record_attempt(tmp_path, "r-1", 7)
    heartbeat.record_attempt(tmp_path, "r-1", 7)
    heartbeat.clear_attempts(tmp_path, "r-1", 7)
    assert heartbeat.attempts(tmp_path, "r-1", 7) == 0


def test_the_budget_is_exhausted_only_after_repeated_failure(tmp_path: Path) -> None:
    for _ in range(heartbeat.MAX_CRITERION_ATTEMPTS - 1):
        heartbeat.record_attempt(tmp_path, "r-1", 7)
    assert not heartbeat.stalled(tmp_path, "r-1", 7), "one short of the budget is still worth trying"
    heartbeat.record_attempt(tmp_path, "r-1", 7)
    assert heartbeat.stalled(tmp_path, "r-1", 7)


def test_a_stalled_criterion_survives_a_restart(tmp_path: Path) -> None:
    """The count lives on disk: an hourly loop that forgot on restart would never reach any budget."""
    for _ in range(heartbeat.MAX_CRITERION_ATTEMPTS):
        heartbeat.record_attempt(tmp_path, "r-1", 7)
    assert heartbeat.stalled(tmp_path, "r-1", 7)
    assert (tmp_path / heartbeat._ATTEMPTS_FILE).is_file()


class TestTheBudgetMovesTheLoopOn:
    """Spending the budget must also release the choice.

    The budget stopped the paying and left selection pinned to the thing it had just given up on. On 2026-09-09
    both engines reported the same criterion on six consecutive beats — `r-5795501a` criterion 7 in the studio,
    `r-3981d7e0` criterion 1 in the release install — each with the reason "has stalled after 3 attempts;
    leaving it for the operator". Correct about not paying, and still the only thing either loop could see, so
    neither did anything else for days while 28 captured asks waited behind it.
    """

    def test_a_stalled_criterion_is_not_chosen_again(self, tmp_path: Path) -> None:
        old_at = (datetime.now(UTC) - timedelta(days=5)).isoformat().replace("+00:00", "Z")
        parked = capture(tmp_path, "the ask the loop could not finish", asked_at=old_at)
        add_criteria(tmp_path, parked.id, [Criterion(text="the criterion it gave up on", source="operator")])
        fresh = capture(tmp_path, "an ask it has not tried yet")
        add_criteria(tmp_path, fresh.id, [Criterion(text="work worth a beat", source="operator")])

        picked = next_unmet(tmp_path)
        assert picked is not None and picked[0].id == parked.id, "the oldest ask comes first while it is live"

        for _ in range(heartbeat.MAX_CRITERION_ATTEMPTS):
            heartbeat.record_attempt(tmp_path, parked.id, 0)

        picked = next_unmet(tmp_path)
        assert picked is not None, "a parked criterion must not hide the work behind it"
        assert picked[0].id == fresh.id, "the beat goes to what the loop can still move"
        assert picked[1].text == "work worth a beat"

    def test_nothing_is_offered_when_every_criterion_is_parked(self, tmp_path: Path) -> None:
        """`None` is what lets the beat fall through to another drive, rather than re-choosing a dead end."""
        parked = capture(tmp_path, "the only ask, and it is stuck")
        add_criteria(tmp_path, parked.id, [Criterion(text="stuck work", source="operator")])
        for _ in range(heartbeat.MAX_CRITERION_ATTEMPTS):
            heartbeat.record_attempt(tmp_path, parked.id, 0)
        assert next_unmet(tmp_path) is None

    def test_progress_puts_a_parked_criterion_back_in_play(self, tmp_path: Path) -> None:
        """The budget is spent per attempt, not per criterion for ever: clearing it restores the choice."""
        parked = capture(tmp_path, "stuck, then unstuck")
        add_criteria(tmp_path, parked.id, [Criterion(text="stuck work", source="operator")])
        for _ in range(heartbeat.MAX_CRITERION_ATTEMPTS):
            heartbeat.record_attempt(tmp_path, parked.id, 0)
        assert next_unmet(tmp_path) is None
        heartbeat.clear_attempts(tmp_path, parked.id, 0)
        picked = next_unmet(tmp_path)
        assert picked is not None and picked[0].id == parked.id


class TestNetworkCapabilityGapParksEarly:
    """2026-09-13, cli-lead: r-3981d7e0 criterion 3 needed a real IL-TUR INSTALL.md with live-fetched
    HuggingFace metadata. No dispatch policy grants network access, so no number of retries could ever produce
    it, and the judge said so -- in different words -- on every attempt. 'Flag such criteria rather than
    letting the attempt budget drain' (cli-lead): once the judge's own reasoning names the same capability gap
    twice, the criterion parks before the third, full-price attempt, same as an exhausted budget."""

    def test_matches_real_recorded_verdicts_not_only_invented_ones(self, tmp_path: Path) -> None:
        """cli-lead, 2026-09-13 (relayed via cli-studio): a regex keyed on judge prose nobody controls drifts
        every time the judge prompt changes, and a regex tested only against strings invented to make it fire
        will pass and then match nothing real -- a false park (silently removing real work from the loop's
        reach) is worse than a burned attempt (costs money). These two judgements are copied VERBATIM, not
        paraphrased, from `/home/ss/pravrudhi-product-loop/.pravrudhi/heartbeat.jsonl`'s actual
        `result["dispatches"][i]["judgement"]` field for request r-3981d7e0 criterion 3 -- the real beats at
        2026-09-12T11:16:44Z and 2026-09-12T19:58:25Z, both judged "not met". Checked by hand (2026-09-13)
        against all 8 real recorded not-met verdicts for this criterion: 4 of 8 matched, these two among them.
        """
        req = capture(tmp_path, "the real product-loop ask")
        add_criteria(tmp_path, req.id, [Criterion(text="produce the real INSTALL.md", source="operator")])
        note(
            tmp_path, req.id,
            "criterion 0 not yet met: The proposal provides scripts and explicit instructions for how the "
            "INSTALL.md file *should* be generated in a future dispatch with network access, but it does not "
            "produce the file itself. The file `proposals/prabhasa-nyaya/harness/INSTALL.md` does not exist "
            "in the working directory. The README explicitly states \"Neither script is run as part of this "
            "dispatch\" and \"this proposal is not authorised to write there.\" While the proposal is "
            "methodologically sound and CHARTER §6-compliant (it scrupulously avoids inventing numbers), "
            "it explains what would meet the criterion rather than meeting it. The criterion requires the "
            "actual file to exist with exact commands to install lm-eval and fetch IL-TUR, along with the "
            "dataset's stated size and licence from HuggingFace—not a plan for how to produce it.",
        )
        note(
            tmp_path, req.id,
            "criterion 0 not yet met: The criterion requires the finished file "
            "`proposals/prabhasa-nyaya/harness/INSTALL.md` with actual commands to install lm-eval, actual "
            "commands to fetch IL-TUR, and the dataset's actual size and licence transcribed from its "
            "Hugging Face page. What exists is a proposal with tooling (a template and rendering script) "
            "explaining how that file should be created by a future dispatch with network access. The file "
            "`proposals/prabhasa-nyaya/harness/INSTALL.md` does not exist; the deliverables are instead in "
            "`proposals/requests/r-3981d7e0/3/`. The template marks size and licence as `PLACEHOLDER`, and "
            "the README explicitly states this dispatch was not permitted to access the Hugging Face page or "
            "write to `proposals/prabhasa-nyaya/**`. A proposal that explains what would satisfy the "
            "criterion is not the same as the criterion being satisfied.",
        )

        assert heartbeat.network_capability_gap(tmp_path, req.id, 0), (
            "both real verdicts name 'network access', which the regex must still catch outside a lab string"
        )

    def test_a_single_network_flavoured_judgement_is_not_enough(self, tmp_path: Path) -> None:
        req = capture(tmp_path, "an ask needing fetched data")
        add_criteria(tmp_path, req.id, [Criterion(text="produce the real file", source="operator")])
        note(tmp_path, req.id, "criterion 0 not yet met: the sandbox has no network access to fetch the page")

        assert not heartbeat.network_capability_gap(tmp_path, req.id, 0), (
            "one mention could be an agent's own excuse, not a confirmed constraint"
        )
        assert not heartbeat.stalled(tmp_path, req.id, 0)

    def test_two_independent_network_flavoured_judgements_park_the_criterion_early(self, tmp_path: Path) -> None:
        req = capture(tmp_path, "an ask needing fetched data")
        add_criteria(tmp_path, req.id, [Criterion(text="produce the real file", source="operator")])
        note(tmp_path, req.id, "criterion 0 not yet met: the sandbox has no network access to fetch the page")
        heartbeat.record_attempt(tmp_path, req.id, 0)
        note(tmp_path, req.id, "criterion 0 not yet met: there is still no way to fetch live data from Hugging Face")
        heartbeat.record_attempt(tmp_path, req.id, 0)

        assert heartbeat.network_capability_gap(tmp_path, req.id, 0)
        assert heartbeat.stalled(tmp_path, req.id, 0), "parked before MAX_CRITERION_ATTEMPTS, not after"
        assert heartbeat.attempts(tmp_path, req.id, 0) < heartbeat.MAX_CRITERION_ATTEMPTS

    def test_unrelated_repeated_judgements_do_not_trigger_the_network_gap(self, tmp_path: Path) -> None:
        """A criterion parked twice for an ordinary reason (wrong file, missing test) must not be misread as a
        capability gap - only judgements that actually name a network/fetch constraint count."""
        req = capture(tmp_path, "an ordinary ask")
        add_criteria(tmp_path, req.id, [Criterion(text="fix the bug", source="operator")])
        note(tmp_path, req.id, "criterion 0 not yet met: the function still returns the wrong value")
        note(tmp_path, req.id, "criterion 0 not yet met: the test still fails on the same assertion")

        assert not heartbeat.network_capability_gap(tmp_path, req.id, 0)
        assert not heartbeat.stalled(tmp_path, req.id, 0)

    def test_the_gap_is_scoped_to_its_own_criterion(self, tmp_path: Path) -> None:
        req = capture(tmp_path, "two different criteria on one request")
        add_criteria(
            tmp_path, req.id,
            [
                Criterion(text="needs fetched data", source="operator"),
                Criterion(text="an ordinary criterion", source="operator"),
            ],
        )
        note(tmp_path, req.id, "criterion 0 not yet met: no network access to fetch the page")
        note(tmp_path, req.id, "criterion 0 not yet met: still no way to fetch the live values")

        assert heartbeat.network_capability_gap(tmp_path, req.id, 0)
        assert not heartbeat.network_capability_gap(tmp_path, req.id, 1), (
            "a different criterion's own history must not borrow another criterion's gap"
        )

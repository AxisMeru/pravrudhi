"""Kṣudhā must never turn a missing measurement into a guessed number, and its hysteresis must not thrash.

Every test here hand-calculates the expected deficit from injected data — no doctor process, no HTTP, no real
tool catalogue — because the whole point of `application/kshudha.py` is that it is a pure calculator plus a
small deterministic selector, testable without a running engine.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from pravrudhi.agents.registry import AgentStatus
from pravrudhi.application.kshudha import (
    AppetiteConfig,
    AppetiteState,
    Drive,
    DriveState,
    load_config,
    load_state,
    pramana_navyata_drive,
    sadhana_drive,
    samarthya_drive,
    save_state,
    select,
    sentence,
    seva_drive,
    seva_overdue,
    spardha_drive,
    sthiti_drive,
    unnati_avakasha_drive,
)

CFG = AppetiteConfig(
    weights={"sthiti": 1.0, "samarthya": 1.0, "pramana_navyata": 1.0, "unnati_avakasha": 1.0, "sadhana": 1.0, "seva": 1.0},
    targets={"sthiti": 1.0, "samarthya": 0.8, "pramana_navyata": 1.0, "unnati_avakasha": 1.0, "sadhana": 1.0, "seva": 0.9},
    hungry_threshold=0.6,
    sated_threshold=0.3,
    cooldown_beats=2,
    benchmark_headroom_scale=0.05,
    resource_min_routes=2,
    seva_overdue_days=7.0,
    seva_age_scale_days=14.0,
)


def _at(n: int) -> datetime:
    return datetime(2026, 9, 6, 0, n, tzinfo=UTC)


class TestSthiti:
    def test_deficit_is_the_weighted_fraction_failing(self) -> None:
        checks = [{"name": "initialised", "ok": True}, {"name": "ledger", "ok": False}]
        d = sthiti_drive(checks, CFG)
        assert d.unknown is False
        assert d.deficit == 0.5
        assert d.value == 0.5
        assert d.eligible is True

    def test_no_checks_is_unknown_not_a_fabricated_zero(self) -> None:
        d = sthiti_drive([], CFG)
        assert d.unknown is True
        assert d.value is None
        assert d.deficit is None
        assert d.eligible is False
        assert d.blocked_reason

    def test_per_check_weight_changes_the_deficit(self) -> None:
        cfg = AppetiteConfig(weights=CFG.weights, targets=CFG.targets, sthiti_check_weights={"ledger": 3.0})
        checks = [{"name": "initialised", "ok": True}, {"name": "ledger", "ok": False}]
        d = sthiti_drive(checks, cfg)
        # total weight = 1 (initialised) + 3 (ledger) = 4; failing weight = 3 -> deficit 0.75
        assert d.deficit == 0.75


class TestSamarthya:
    def test_deficit_matches_the_design_formula(self) -> None:
        tools = [{"id": "a", "available": True}, {"id": "b", "available": False}]
        recipes = [{"id": "r1", "available": True}]
        agents = [AgentStatus("x", True, "ready"), AgentStatus("y", False, "not installed")]
        d = samarthya_drive(tools, recipes, agents, CFG)
        # C = 3/5 = 0.6; target 0.8 -> D = clip((0.8-0.6)/0.8) = 0.25
        assert d.value == 0.6
        assert round(d.deficit or 0.0, 4) == 0.25
        assert d.eligible is True

    def test_no_catalogue_is_unknown(self) -> None:
        d = samarthya_drive([], [], [], CFG)
        assert d.unknown is True
        assert d.deficit is None

    def test_zero_target_is_unknown_not_a_division_by_zero(self) -> None:
        cfg = AppetiteConfig(weights=CFG.weights, targets={"samarthya": 0.0})
        d = samarthya_drive([{"id": "a", "available": True}], [], [], cfg)
        assert d.unknown is True
        assert "target" in d.blocked_reason


class TestPramanaNavyata:
    def test_is_always_unknown_because_no_source_exists_yet(self) -> None:
        d = pramana_navyata_drive(CFG)
        assert d.unknown is True
        assert d.value is None
        assert d.deficit is None
        assert d.eligible is False
        assert d.blocked_reason


class TestUnnatiAvakasha:
    def test_deficit_for_a_higher_is_better_benchmark(self) -> None:
        d = unnati_avakasha_drive([("t:acc", "up", 0.05, 0.02)], CFG)
        # H = clip(1 * (0.05 - 0.02) / 0.05) = 0.6
        assert d.unknown is False
        assert round(d.deficit or 0.0, 4) == 0.6

    def test_deficit_for_a_lower_is_better_benchmark_clips_at_one(self) -> None:
        d = unnati_avakasha_drive([("t:latency", "down", -0.10, -0.02)], CFG)
        # H = clip(-1 * (-0.10 - (-0.02)) / 0.05) = clip(1.6) = 1.0
        assert round(d.deficit or 0.0, 4) == 1.0

    def test_a_benchmark_with_no_target_or_measurement_is_excluded_not_zero(self) -> None:
        d = unnati_avakasha_drive([("t:acc", "up", 0.05, 0.02), ("t:unmeasured", "up", None, None)], CFG)
        assert d.unknown is False
        assert round(d.deficit or 0.0, 4) == 0.6  # the unmeasured benchmark never pulls the mean toward 0

    def test_no_measurable_benchmark_at_all_is_unknown(self) -> None:
        d = unnati_avakasha_drive([("t:unmeasured", "up", None, None)], CFG)
        assert d.unknown is True
        assert d.deficit is None


class TestSadhana:
    def test_deficit_matches_the_design_formula(self) -> None:
        d = sadhana_drive(["a", "b", "c"], ["a", "b"], {"a"}, CFG)
        # usable = {b}; a_r = clip(1/2) = 0.5; D = 1 - 0.5 = 0.5
        assert d.value == 0.5
        assert d.deficit == 0.5

    def test_zero_required_routes_is_unknown(self) -> None:
        cfg = AppetiteConfig(weights=CFG.weights, targets=CFG.targets, resource_min_routes=0)
        d = sadhana_drive(["a"], ["a"], set(), cfg)
        assert d.unknown is True


class TestSeva:
    def test_deficit_is_the_max_of_verification_debt_and_age_debt(self) -> None:
        backlog = {"total": 10, "by_state": {"verified": 3}, "oldest_open_days": 2.0}
        d = seva_drive(backlog, CFG)
        # R = 0.3; D_R = clip((0.9-0.3)/0.9) = 0.6667; D_age = clip(2/14) = 0.1429 -> max = D_R
        assert d.value == 0.3
        assert round(d.deficit or 0.0, 4) == round(2.0 / 3.0, 4)

    def test_age_debt_can_dominate_verification_debt(self) -> None:
        backlog = {"total": 10, "by_state": {"verified": 9}, "oldest_open_days": 20.0}
        d = seva_drive(backlog, CFG)
        # R = 0.9 = target -> D_R = 0; D_age = clip(20/14) = 1.0 -> max = D_age
        assert round(d.deficit or 0.0, 4) == 1.0

    def test_zero_denominator_is_no_cohort_not_perfect_delivery(self) -> None:
        d = seva_drive({"total": 0}, CFG)
        assert d.unknown is True
        assert d.deficit is None

    def test_overdue_flag(self) -> None:
        assert seva_overdue({"oldest_open_days": 10.0}, CFG) is True
        assert seva_overdue({"oldest_open_days": 5.0}, CFG) is False


def _drive(drive_id: str, wire: str, deficit: float, weight: float = 1.0, eligible: bool = True) -> Drive:
    return Drive(
        id=drive_id, wire_name=wire, value=1.0 - deficit, target=1.0, deficit=deficit, weight=weight,
        eligible=eligible, blocked_reason="", sources=(), unknown=False,
    )


class TestSelectOverdue:
    def test_an_overdue_request_beats_a_larger_benchmark_deficit(self) -> None:
        drives = [
            _drive("seva", "obligations", 0.4),
            _drive("unnati_avakasha", "benchmark_headroom", 0.95),
        ]
        appetite = select(drives, state=AppetiteState(), overdue=True, config=CFG, now=_at(0))
        assert appetite.selected == "seva"
        assert appetite.largest_unmet == "unnati_avakasha"  # still reported, just not chosen


class TestSelectHysteresis:
    def test_does_not_thrash_across_the_band_and_honours_the_cooldown(self) -> None:
        state = AppetiteState()

        beat1 = select([_drive("samarthya", "capability", 0.70)], state=state, config=CFG, now=_at(1))
        assert beat1.selected == "samarthya"

        # deficit dips into the 0.3-0.6 band: an ordinary fluctuation must not release the commitment.
        beat2 = select([_drive("samarthya", "capability", 0.45)], state=state, config=CFG, now=_at(2))
        assert beat2.selected == "samarthya"

        beat3 = select([_drive("samarthya", "capability", 0.55)], state=state, config=CFG, now=_at(3))
        assert beat3.selected == "samarthya"

        # only a measured deficit at or below the sated threshold releases the commitment.
        beat4 = select([_drive("samarthya", "capability", 0.25)], state=state, config=CFG, now=_at(4))
        assert beat4.selected is None
        assert state.drives["samarthya"].phase == "sated"
        assert state.drives["samarthya"].cooldown == 2

        # the cooldown blocks reselection even though the deficit has already spiked back up.
        beat5 = select([_drive("samarthya", "capability", 0.70)], state=state, config=CFG, now=_at(5))
        assert beat5.selected is None
        beat6 = select([_drive("samarthya", "capability", 0.70)], state=state, config=CFG, now=_at(6))
        assert beat6.selected is None
        assert state.drives["samarthya"].cooldown == 0

        # the cooldown has elapsed: the same high deficit may now be selected again.
        beat7 = select([_drive("samarthya", "capability", 0.70)], state=state, config=CFG, now=_at(7))
        assert beat7.selected == "samarthya"


class TestSelectBlocking:
    def test_a_blocked_drive_stays_visible_with_its_exclusion_reason(self) -> None:
        state = AppetiteState(drives={"seva": DriveState(phase="sated", cooldown=1, since="")})
        drives = [_drive("seva", "obligations", 0.7), _drive("samarthya", "capability", 0.2)]
        appetite = select(drives, state=state, config=CFG, now=_at(0))
        seva = next(d for d in appetite.drives if d.id == "seva")
        assert seva.eligible is False
        assert "cooling down" in seva.blocked_reason
        assert seva.deficit == 0.7  # still visible, not hidden


class TestSentence:
    def test_names_the_real_drive_and_blocker(self) -> None:
        appetite = select(
            [
                _drive("seva", "obligations", 0.7),
                Drive(
                    id="unnati_avakasha", wire_name="benchmark_headroom", value=0.1, target=1.0, deficit=0.9,
                    weight=1.0, eligible=False, blocked_reason="a budgeted trial slot", sources=(), unknown=False,
                ),
            ],
            state=AppetiteState(),
            overdue=True,
            config=CFG,
            now=_at(0),
        )
        text = sentence(appetite)
        assert appetite.selected == "seva"
        assert "obligations" in text
        assert "benchmark_headroom" in text
        assert "a budgeted trial slot" in text

    def test_a_diagnostic_fallback_does_not_claim_an_eligible_deficit(self) -> None:
        """With nothing hungry and eligible, `select` falls back to a cheap diagnostic on an unknown drive.

        That fallback is correct — an idle loop should look at something it cannot yet measure rather than
        fabricate work. The sentence was not: it read "freshness has the largest eligible deficit" for a drive
        whose `eligible` is False and whose deficit is None. The product heartbeat printed exactly that once its
        continuity block was cleared, and it is the operator's only human-readable account of what the loop is
        doing, so it must not assert a measurement that does not exist.
        """
        appetite = select(
            [
                Drive(
                    id="pramana_navyata", wire_name="freshness", value=0.0, target=1.0, deficit=None,
                    weight=1.0, eligible=False,
                    blocked_reason="no evidence-freshness source is wired into the engine yet",
                    sources=(), unknown=True,
                ),
            ],
            state=AppetiteState(),
            overdue=False,
            config=CFG,
            now=_at(0),
        )
        assert appetite.selected == "pramana_navyata"
        text = sentence(appetite)
        assert "freshness" in text
        assert "largest eligible deficit" not in text, text


class TestState:
    def test_round_trips_through_disk(self, tmp_path: Path) -> None:
        state = AppetiteState(beat=3, committed="seva", drives={"seva": DriveState(phase="hungry", cooldown=0, since="x")})
        save_state(tmp_path, state)
        back = load_state(tmp_path)
        assert back.beat == 3
        assert back.committed == "seva"
        assert back.drives["seva"].phase == "hungry"

    def test_a_missing_or_corrupt_file_is_a_fresh_state_not_a_crash(self, tmp_path: Path) -> None:
        assert load_state(tmp_path) == AppetiteState()
        (tmp_path / ".pravrudhi").mkdir()
        (tmp_path / ".pravrudhi" / "appetite.json").write_text("{not json")
        assert load_state(tmp_path) == AppetiteState()


def _parity_with(numerator: int, denominator: int):
    """A minimal stand-in for `parity.report`: only coverage and the next gap are read by the drive."""
    fraction = (numerator / denominator) if denominator else None
    # Duck-typed on purpose: the drive reads coverage.fraction and the gap's id/ours through `getattr`, so a
    # test that built real pydantic models would couple this to parity's schema and break on a field it never
    # reads.
    return SimpleNamespace(
        coverage=SimpleNamespace(numerator=numerator, denominator=denominator, fraction=fraction),
        next_gap=SimpleNamespace(id="desktop-update", ours="none") if numerator < denominator else None,
    )


def _full_gap_parity():
    return _parity_with(20, 25)


def _empty_parity():
    return _parity_with(0, 0)


def _rival_figures():
    return load_config().rivals


class TestRivalryReadsTheParityMatrix:
    """`parity.py` is a measured source of rivalry work that no drive ever consulted.

    Its own docstring says it is "a repeatable source of work for the existing rivalry drive" and to "call
    next_gap on each planning pass". Nothing called it: `grep parity` across kshudha.py, night.py and
    heartbeat.py returned nothing, which is the completion review's finding and the criterion parked on
    r-5795501a since the attempt budget ran out.

    Meanwhile the matrix is real -- 25 capabilities, 20 verified, 4 gaps, coverage 0.80 on 2026-09-11 -- and
    `spardha_drive` fell to `unknown` whenever no rival declared a figure on a benchmark this workspace had
    measured, which is almost always. So the rivalry drive was never eligible and the only checked-in source
    of capability work never produced any.

    Capability coverage is MEASURED, not declared: every row's evidence is a path that must exist or a
    read-only command that must pass, run against this repository. So using it keeps the "unknown, never
    fabricated" rule the other six drives follow -- what it must never do is stand in for a benchmark
    comparison when one is actually available.
    """

    def test_a_benchmark_comparison_still_wins_when_one_exists(self) -> None:
        """Parity is the fallback, never the override: a measured rival gap is the better evidence."""
        cfg = load_config()
        rivals = tuple(_rival_figures())
        if not rivals:
            pytest.skip("no rival figure is declared in the shipped appetite config")
        with_parity = spardha_drive({rivals[0].benchmark: 0.1}, rivals, cfg, parity=_full_gap_parity())
        assert not with_parity.unknown
        assert any("rival" in s or rivals[0].benchmark in s for s in with_parity.sources)

    def test_capability_coverage_makes_the_drive_eligible_when_no_rival_figure_applies(self) -> None:
        drive = spardha_drive({}, (), load_config(), parity=_full_gap_parity())
        assert not drive.unknown, "a measured capability gap is evidence, so the drive must not be unknown"
        assert drive.eligible
        assert drive.deficit is not None and drive.deficit > 0
        assert any("parity" in s for s in drive.sources), drive.sources

    def test_without_a_parity_report_the_drive_is_unknown_as_before(self) -> None:
        """The existing contract is unchanged for every caller that passes nothing."""
        drive = spardha_drive({}, (), load_config())
        assert drive.unknown and not drive.eligible

    def test_an_empty_matrix_is_unknown_not_a_coverage_of_zero(self) -> None:
        """An undefined coverage is not a total deficit. Reporting 1.0 would make an unpopulated matrix the
        loudest drive in the engine."""
        drive = spardha_drive({}, (), load_config(), parity=_empty_parity())
        assert drive.unknown

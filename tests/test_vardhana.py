"""Vardhana: a gap that names nothing checkable is refused, the cheapest resolution wins, the pinned suite a
builder is judged against cannot be the builder's own work, and nothing but a real held-out trial can admit.

Every agent here is a fake exactly like `tests/test_swarm.py`'s `OkAgent`: `create_workspace`/`run`/
`collect_changes`, no model, no network. `build`'s own completion check is real — a small standalone script run
by a real `python3` subprocess, because that check running for real (not a mocked "accepted") is the entire
point of design §7's admission rule.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from pravrudhi.agents.base import AgentRun, Diff
from pravrudhi.application import requests, vardhana
from pravrudhi.application.sandbox_policy import ALWAYS_DENIED, _collides


def _agent_status(name: str, available: bool) -> Any:
    return vardhana.agents_registry.AgentStatus(name=name, available=available, reason="")


class FakeAgent:
    """Writes (or refuses to write) `artifact_rel` under whatever content `content` gives, and reports exactly
    that one file as its diff -- or none at all, when `write` is False."""

    name = "fake"

    def __init__(self, artifact_rel: str, content: str, *, write: bool = True) -> None:
        self.artifact_rel = artifact_rel
        self.content = content
        self.write = write

    def create_workspace(self, task_id: str, base_ref: str = "HEAD") -> Path:
        return Path(tempfile.mkdtemp())

    def run(self, prompt: str, workspace: Path, timeout_s: int = 60) -> AgentRun:
        if self.write:
            p = workspace / self.artifact_rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(self.content)
        return AgentRun(agent=self.name, ok=True, exit_code=0, wall_s=0.05, text="", workspace=workspace)

    def collect_changes(self, workspace: Path) -> Diff:
        return Diff(files=[self.artifact_rel] if self.write else [])


def _agent_factory(agent: Any):
    return lambda name, model: agent


# ----------------------------------------------------------------------------------------------------------
# A gap that names nothing checkable is refused, with the reason.
# ----------------------------------------------------------------------------------------------------------


class TestAGapMustNameSomethingCheckable:
    def test_a_criterion_gap_with_no_request_or_index_is_refused(self, tmp_path: Path) -> None:
        gap = vardhana.Gap(id="g1", kind="criterion", reason="")
        with pytest.raises(vardhana.GapError, match="names no request id"):
            vardhana.propose(tmp_path, gap)

    def test_a_capability_gap_with_no_capability_id_is_refused(self, tmp_path: Path) -> None:
        gap = vardhana.Gap(id="g2", kind="capability", reason="something is missing")
        with pytest.raises(vardhana.GapError, match="names no capability id"):
            vardhana.propose(tmp_path, gap)

    def test_a_survival_gap_with_no_reason_is_refused(self, tmp_path: Path) -> None:
        gap = vardhana.Gap(id="g3", kind="survival", reason="")
        with pytest.raises(vardhana.GapError, match="gives no reason"):
            vardhana.propose(tmp_path, gap)

    def test_create_more_skills_with_no_linked_use_is_not_a_gap(self, tmp_path: Path) -> None:
        """The operator's own phrase (design §7.1): a bare wish, with no unmet criterion, no required capability,
        and no survival reason attached, must be refused rather than built."""
        wish = vardhana.Gap(id="wish", kind="capability", reason="the operator would like more skills", capability_id="")
        with pytest.raises(vardhana.GapError):
            vardhana.propose(tmp_path, wish)

    def test_resolve_also_refuses_an_unreal_gap(self, tmp_path: Path) -> None:
        bad = vardhana.Gap(id="g4", kind="criterion", reason="", request_id="", criterion_index=-1)
        with pytest.raises(vardhana.GapError):
            vardhana.resolve(tmp_path, bad)


# ----------------------------------------------------------------------------------------------------------
# find_gaps: only real signals produce a gap.
# ----------------------------------------------------------------------------------------------------------


class TestGapsFromSignals:
    def test_an_unmet_criterion_on_an_open_request_is_a_gap(self) -> None:
        req = requests.Request(
            id="r-1", asked_at="2026-01-01T00:00:00Z", text="do the thing",
            criteria=[requests.Criterion(text="does the thing", source="operator", met=False)],
        )
        gaps = vardhana.gaps_from_signals((), [], [], [], [req])
        assert len(gaps) == 1
        assert gaps[0].kind == "criterion" and gaps[0].request_id == "r-1" and gaps[0].criterion_index == 0

    def test_a_met_criterion_is_not_a_gap(self) -> None:
        req = requests.Request(
            id="r-2", asked_at="2026-01-01T00:00:00Z", text="do the thing",
            criteria=[requests.Criterion(text="does the thing", source="operator", met=True)],
        )
        assert vardhana.gaps_from_signals((), [], [], [], [req]) == []

    def test_a_declined_requests_criteria_are_not_gaps(self) -> None:
        req = requests.Request(
            id="r-3", asked_at="2026-01-01T00:00:00Z", text="do the thing", state="declined",
            criteria=[requests.Criterion(text="does the thing", met=False)],
        )
        assert vardhana.gaps_from_signals((), [], [], [], [req]) == []

    def test_a_required_capability_absent_from_the_catalogue_is_a_gap(self) -> None:
        gaps = vardhana.gaps_from_signals(("tool:widget",), [{"id": "widget", "available": False}], [], [], [])
        assert len(gaps) == 1
        assert gaps[0].kind == "capability" and gaps[0].capability_id == "tool:widget"

    def test_a_required_capability_that_is_available_is_not_a_gap(self) -> None:
        gaps = vardhana.gaps_from_signals(("tool:widget",), [{"id": "widget", "available": True}], [], [], [])
        assert gaps == []

    def test_a_capability_nobody_required_is_not_a_gap_even_if_absent(self) -> None:
        """"Create more skills" generalises: a bigger catalogue is not itself a gap (design §7.1)."""
        gaps = vardhana.gaps_from_signals((), [{"id": "widget", "available": False}], [], [], [])
        assert gaps == []

    def test_no_available_agent_route_is_a_survival_gap(self) -> None:
        gaps = vardhana.gaps_from_signals((), [], [], [_agent_status("codex", False)], [])
        assert any(g.kind == "survival" for g in gaps)

    def test_at_least_one_available_agent_route_means_no_survival_gap(self) -> None:
        gaps = vardhana.gaps_from_signals((), [], [], [_agent_status("codex", True)], [])
        assert not any(g.kind == "survival" for g in gaps)


# ----------------------------------------------------------------------------------------------------------
# resolve: the ordered strategy. Reuse beats configure beats repair beats authoring, in that order.
# ----------------------------------------------------------------------------------------------------------


class TestResolutionOrder:
    GAP = vardhana.Gap(id="capability:tool:widget", kind="capability", reason="widget missing", capability_id="tool:widget")

    def test_an_already_qualified_capability_is_reused_not_authored(self) -> None:
        res = vardhana.resolve_from_signals(self.GAP, [{"id": "widget", "available": True}], [], [], None)
        assert res.strategy == "reuse"

    def test_a_catalogued_but_undetected_capability_is_configured_not_authored(self) -> None:
        res = vardhana.resolve_from_signals(self.GAP, [{"id": "widget", "available": False}], [], [], None)
        assert res.strategy == "configure"

    def test_a_prior_unfinished_attempt_is_repaired_before_authoring_afresh(self) -> None:
        prior = {"gap_id": self.GAP.id, "state": "rejected", "artifact_hash": "deadbeef"}
        res = vardhana.resolve_from_signals(self.GAP, [], [], [], prior)
        assert res.strategy == "repair" and res.candidate == "deadbeef"

    def test_an_admitted_prior_version_is_not_treated_as_something_to_repair(self) -> None:
        """An admitted version is the live capability, not a failed attempt waiting to be adapted."""
        prior = {"gap_id": self.GAP.id, "state": "admitted", "artifact_hash": "deadbeef"}
        res = vardhana.resolve_from_signals(self.GAP, [], [], [], prior)
        assert res.strategy == "author"

    def test_authoring_is_the_last_resort(self) -> None:
        res = vardhana.resolve_from_signals(self.GAP, [], [], [], None)
        assert res.strategy == "author"

    def test_an_agent_route_capability_resolves_through_agents_registry(self) -> None:
        gap = vardhana.Gap(id="capability:agent:codex", kind="capability", reason="x", capability_id="agent:codex")
        res = vardhana.resolve_from_signals(gap, [], [], [_agent_status("codex", True)], None)
        assert res.strategy == "reuse"


# ----------------------------------------------------------------------------------------------------------
# propose: freezes the contract and a pinned acceptance suite before anything is built.
# ----------------------------------------------------------------------------------------------------------


class TestPropose:
    def test_the_pinned_suite_is_written_before_any_build_and_hash_matches_it(self, tmp_path: Path) -> None:
        gap = vardhana.Gap(id="capability:tool:widget", kind="capability", reason="x", capability_id="tool:widget")
        ext = vardhana.propose(tmp_path, gap)
        suite_path = vardhana._suite_path(tmp_path, gap.id)
        assert suite_path.exists()
        assert vardhana._hash(suite_path.read_text()) == ext.test_suite_hash
        assert ext.state == "specified" and ext.gap_id == gap.id

    def test_the_suite_directory_is_structurally_unreachable_by_any_sandbox_policy(self) -> None:
        """Whatever preset build() names, `.pravrudhi/**` is unconditionally denied (sandbox_policy.py's
        ALWAYS_DENIED), so a builder's allowed_paths can never include the pinned suite regardless of policy."""
        suite_rel = f"{vardhana.STORE_DIR}/suites/whatever.py"
        assert any(_collides(suite_rel, pat) for pat in ALWAYS_DENIED)

    def test_proposing_the_same_gap_twice_carries_forward_a_rollback_target_only_once_admitted(
        self, tmp_path: Path
    ) -> None:
        gap = vardhana.Gap(id="capability:tool:widget", kind="capability", reason="x", capability_id="tool:widget")
        first = vardhana.propose(tmp_path, gap)
        assert first.rollback_target == "", "nothing has ever been admitted for this gap yet"
        second = vardhana.propose(tmp_path, gap)
        assert second.rollback_target == "", "a merely proposed (not admitted) prior version is not a rollback target"


# ----------------------------------------------------------------------------------------------------------
# build: dispatches through the existing swarm; the pinned suite, not the agent's word, decides.
# ----------------------------------------------------------------------------------------------------------


class TestBuild:
    def _propose(self, root: Path, gap_id: str = "capability:tool:widget") -> vardhana.Extension:
        gap = vardhana.Gap(id=gap_id, kind="capability", reason="x", capability_id="tool:widget")
        return vardhana.propose(root, gap)

    def test_a_builder_that_produces_the_real_artifact_is_accepted(self, tmp_path: Path) -> None:
        ext = self._propose(tmp_path)
        artifact_rel = vardhana._artifact_rel_path(ext)
        agent = FakeAgent(artifact_rel, json.dumps({"capability": ext.gap_id}))
        built = vardhana.build(tmp_path, ext, _agent_factory(agent), log=lambda s: None)
        assert built.state == "built"
        assert built.artifact_hash
        bundle = tmp_path / vardhana.STORE_DIR / "objects" / built.artifact_hash / "manifest.json"
        assert bundle.exists()

    def test_a_builder_that_writes_nothing_is_rejected_after_the_iteration_budget(self, tmp_path: Path) -> None:
        ext = self._propose(tmp_path, "capability:tool:absent-widget")
        agent = FakeAgent(vardhana._artifact_rel_path(ext), "", write=False)
        built = vardhana.build(tmp_path, ext, _agent_factory(agent), log=lambda s: None, max_iterations=1)
        assert built.state == "rejected"

    def test_a_builder_that_writes_the_wrong_content_is_caught_by_the_pinned_suite_running_for_real(
        self, tmp_path: Path,
    ) -> None:
        """The check is a command's exit status, not a reading of the agent's claim (design §7's ralph
        discipline): wrong JSON content fails even though a file exists at exactly the declared path."""
        ext = self._propose(tmp_path, "capability:tool:wrong-content-widget")
        artifact_rel = vardhana._artifact_rel_path(ext)
        agent = FakeAgent(artifact_rel, json.dumps({"capability": "not-the-right-gap-id"}))
        built = vardhana.build(tmp_path, ext, _agent_factory(agent), log=lambda s: None, max_iterations=1)
        assert built.state == "rejected"

    def test_the_pinned_suite_on_disk_is_unchanged_by_a_successful_build(self, tmp_path: Path) -> None:
        ext = self._propose(tmp_path, "capability:tool:unchanged-widget")
        suite_path = vardhana._suite_path(tmp_path, ext.gap_id)
        before = suite_path.read_text()
        artifact_rel = vardhana._artifact_rel_path(ext)
        agent = FakeAgent(artifact_rel, json.dumps({"capability": ext.gap_id}))
        vardhana.build(tmp_path, ext, _agent_factory(agent), log=lambda s: None)
        assert suite_path.read_text() == before, "nothing about a build may touch the pinned suite"

    def test_building_an_already_built_extension_is_refused(self, tmp_path: Path) -> None:
        ext = self._propose(tmp_path, "capability:tool:double-build-widget")
        artifact_rel = vardhana._artifact_rel_path(ext)
        agent = FakeAgent(artifact_rel, json.dumps({"capability": ext.gap_id}))
        built = vardhana.build(tmp_path, ext, _agent_factory(agent), log=lambda s: None)
        with pytest.raises(vardhana.VardhanaError):
            vardhana.build(tmp_path, built, _agent_factory(agent), log=lambda s: None)

    def test_a_tampered_pinned_suite_refuses_to_build_at_all(self, tmp_path: Path) -> None:
        ext = self._propose(tmp_path, "capability:tool:tampered-widget")
        vardhana._suite_path(tmp_path, ext.gap_id).write_text("print('tampered')\n")
        agent = FakeAgent(vardhana._artifact_rel_path(ext), "{}")
        with pytest.raises(vardhana.VardhanaError, match="no longer matches its frozen hash"):
            vardhana.build(tmp_path, ext, _agent_factory(agent), log=lambda s: None)


# ----------------------------------------------------------------------------------------------------------
# admit: only a completed, checked, useful trial with a held-out pass can admit anything.
# ----------------------------------------------------------------------------------------------------------


class TestAdmit:
    def _built(self, tmp_path: Path, gap_id: str) -> vardhana.Extension:
        gap = vardhana.Gap(id=gap_id, kind="capability", reason="x", capability_id="tool:widget")
        ext = vardhana.propose(tmp_path, gap)
        agent = FakeAgent(vardhana._artifact_rel_path(ext), json.dumps({"capability": gap.id}))
        return vardhana.build(tmp_path, ext, _agent_factory(agent), log=lambda s: None)

    def test_admission_is_refused_when_the_held_out_variants_fail(self, tmp_path: Path) -> None:
        built = self._built(tmp_path, "capability:tool:heldout-widget")
        trial = vardhana.Trial(
            trial_id="t1", outcome="useful", completed=True, checked=True,
            held_out_pass=False, evidence=("ledger:1",),
        )
        result = vardhana.admit(tmp_path, built, trial)
        assert result.admitted is False
        assert "held-out" in result.reason
        assert result.extension.state == "built", "a refused admission must not silently change the extension's state"

    def test_admission_is_refused_when_the_trial_outcome_is_not_useful(self, tmp_path: Path) -> None:
        built = self._built(tmp_path, "capability:tool:not-useful-widget")
        trial = vardhana.Trial(
            trial_id="t2", outcome="not_useful", completed=True, checked=True,
            held_out_pass=True, evidence=("ledger:2",),
        )
        assert vardhana.admit(tmp_path, built, trial).admitted is False

    def test_admission_is_refused_without_evidence_references(self, tmp_path: Path) -> None:
        built = self._built(tmp_path, "capability:tool:no-evidence-widget")
        trial = vardhana.Trial(
            trial_id="t3", outcome="useful", completed=True, checked=True, held_out_pass=True, evidence=(),
        )
        assert vardhana.admit(tmp_path, built, trial).admitted is False

    def test_admission_is_refused_for_an_extension_that_was_never_built(self, tmp_path: Path) -> None:
        gap = vardhana.Gap(id="capability:tool:never-built-widget", kind="capability", reason="x", capability_id="tool:widget")
        specified = vardhana.propose(tmp_path, gap)
        trial = vardhana.Trial(
            trial_id="t4", outcome="useful", completed=True, checked=True, held_out_pass=True, evidence=("e",),
        )
        result = vardhana.admit(tmp_path, specified, trial)
        assert result.admitted is False and "not 'built'" in result.reason

    def test_a_genuinely_useful_checked_held_out_trial_admits(self, tmp_path: Path) -> None:
        built = self._built(tmp_path, "capability:tool:good-widget")
        trial = vardhana.Trial(
            trial_id="t5", outcome="useful", completed=True, checked=True,
            held_out_pass=True, evidence=("ledger:5",),
        )
        result = vardhana.admit(tmp_path, built, trial)
        assert result.admitted is True
        assert result.extension.state == "admitted"

    def test_an_extensions_own_free_text_has_nowhere_to_go_and_cannot_admit_it(self) -> None:
        """Design §7.4: "an agent's summary can never admit anything." There is deliberately no summary field on
        `Trial` for one to be smuggled through."""
        assert "summary" not in vardhana.Trial.__dataclass_fields__
        assert "text" not in vardhana.Trial.__dataclass_fields__

    def test_a_rollback_target_is_recorded_across_a_second_admitted_version(self, tmp_path: Path) -> None:
        gap_id = "capability:tool:rollback-widget"
        first_built = self._built(tmp_path, gap_id)
        first_trial = vardhana.Trial(
            trial_id="t-first", outcome="useful", completed=True, checked=True,
            held_out_pass=True, evidence=("ledger:1",),
        )
        first_admitted = vardhana.admit(tmp_path, first_built, first_trial).extension
        assert first_admitted.rollback_target == "", "the first admitted version has nothing to roll back to"

        gap = vardhana.Gap(id=gap_id, kind="capability", reason="x", capability_id="tool:widget")
        second_proposed = vardhana.propose(tmp_path, gap)
        assert second_proposed.rollback_target == first_admitted.artifact_hash

        agent = FakeAgent(vardhana._artifact_rel_path(second_proposed), json.dumps({"capability": gap_id}))
        second_built = vardhana.build(tmp_path, second_proposed, _agent_factory(agent), log=lambda s: None)
        second_trial = vardhana.Trial(
            trial_id="t-second", outcome="useful", completed=True, checked=True,
            held_out_pass=True, evidence=("ledger:2",),
        )
        second_admitted = vardhana.admit(tmp_path, second_built, second_trial).extension
        assert second_admitted.rollback_target == first_admitted.artifact_hash


# ----------------------------------------------------------------------------------------------------------
# registry: only admitted extensions, each with what it projects into.
# ----------------------------------------------------------------------------------------------------------


class TestRegistry:
    def test_a_specified_but_unbuilt_extension_is_not_listed(self, tmp_path: Path) -> None:
        gap = vardhana.Gap(id="capability:tool:unlisted-widget", kind="capability", reason="x", capability_id="tool:widget")
        vardhana.propose(tmp_path, gap)
        assert vardhana.registry(tmp_path) == []

    def test_an_admitted_extension_is_listed_with_what_it_projects_into(self, tmp_path: Path) -> None:
        gap = vardhana.Gap(id="capability:tool:listed-widget", kind="capability", reason="x", capability_id="tool:widget")
        ext = vardhana.propose(tmp_path, gap)
        agent = FakeAgent(vardhana._artifact_rel_path(ext), json.dumps({"capability": gap.id}))
        built = vardhana.build(tmp_path, ext, _agent_factory(agent), log=lambda s: None)
        trial = vardhana.Trial(
            trial_id="t", outcome="useful", completed=True, checked=True, held_out_pass=True, evidence=("e",),
        )
        vardhana.admit(tmp_path, built, trial)

        rows = vardhana.registry(tmp_path)
        assert len(rows) == 1
        assert rows[0]["gap_id"] == gap.id
        assert rows[0]["projects_into"] == vardhana._artifact_rel_path(built)
        assert rows[0]["state"] == "admitted"

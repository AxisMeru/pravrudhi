"""Issue #44 (Lead-2, 2026-09-26 spec clarification). Unit tests for the pure check logic and, per Lead-2's
explicit instruction, the fail-closed path against a STUBBED endpoint error and timeout -- no real endpoint
call in any test here (the live 32B endpoint has 0 workers, EU-RO-1 capacity, as of this session; real E2E
waits for that)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from pravrudhi.application.second_judge_positive_control import (
    SEALED_PIN_UNSET,
    ControlCheckResult,
    ControlElement,
    EstablishedResult,
    NEResult,
    ParityResult,
    PinnedCounts,
    PrivateControlDataUnavailable,
    SealedControlVerificationError,
    SealedSetPin,
    compute_established_accuracy,
    compute_ne_discrimination,
    compute_parity,
    count_sealed_rows,
    decide_availability,
    load_sealed_manifest,
    load_verified_control_sets,
    parse_sealed_manifest,
    resolve_private_root,
    run_live_check,
    verify_sealed_file,
)

TAU = 0.97

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Obviously-constructed, clearly-fake 64-hex values. They are the right SHAPE for a digest (so the
#: validators accept them and the logic under test is reachable) and could never be the real sha256 of
#: anything -- nobody in this public repo has ever been able to compute the sealed sets' real digests, and
#: no test here pretends to. Where a test needs a digest that actually matches a file, it hashes the
#: temp file it just wrote.
FAKE_ESTABLISHED_SHA = "a" * 64
FAKE_NE_SHA = "b" * 64


def _pinned(established: int = 200, ne: int = 71) -> PinnedCounts:
    """The planned n `decide_availability` now requires. Defaults to the shipped 200/71 so the pre-existing
    tests below keep isolating exactly the branch each one was written for."""
    return PinnedCounts(established=established, ne=ne, established_sha256=FAKE_ESTABLISHED_SHA,
                         ne_sha256=FAKE_NE_SHA)


def _est(p_sealed: float, item_id: str = "i", element_id: str = "el0") -> ControlElement:
    return ControlElement(item_id, element_id, p_sealed, "established")


def _ne(p_sealed: float, item_id: str = "i", element_id: str = "el0") -> ControlElement:
    return ControlElement(item_id, element_id, p_sealed, "not_established")


class TestComputeParity:
    def test_full_agreement_and_zero_drift_against_itself(self) -> None:
        """A live score identical to the sealed reference (the dry-run's own case) is trivially 100%
        agreement, 0 median drift -- confirms the dry-run's own "trivially 100% by construction" claim."""
        elements = [_est(0.99, "a"), _ne(0.01, "b"), _est(0.5, "c")]
        live = {f"{el.item_id}__{el.element_id}": el.sealed_reference_p for el in elements}
        result = compute_parity(elements, live, TAU)
        assert result.n_tau_agree == 3
        assert result.agree_rate == 1.0
        assert result.median_abs_dp == 0.0

    def test_tau_side_flip_counts_as_disagreement(self) -> None:
        elements = [_est(0.98, "a")]  # sealed: >= tau
        live = {"a__el0": 0.5}  # live: < tau -- a real calibration drift across the decision boundary
        result = compute_parity(elements, live, TAU)
        assert result.n_tau_agree == 0
        assert result.median_abs_dp == pytest.approx(0.48)

    def test_same_side_of_tau_agrees_even_with_some_drift(self) -> None:
        elements = [_est(0.99, "a")]
        live = {"a__el0": 0.98}  # both >= tau -- same decision, small drift
        result = compute_parity(elements, live, TAU)
        assert result.n_tau_agree == 1
        assert result.median_abs_dp == pytest.approx(0.01)

    def test_empty_elements_gives_none_not_a_fabricated_zero(self) -> None:
        """Fail-open-defaults guard fix: an agreement rate / median drift over ZERO elements is undefined,
        not a measured 0.0 -- a fabricated 0.0 median_abs_dp would read as "perfect match" and pass the
        parity_median_abs_dp gate by coincidence, exactly the unsafe-looks-safe direction the guard flags."""
        result = compute_parity([], {}, TAU)
        assert result.n_total == 0
        assert result.agree_rate is None
        assert result.median_abs_dp is None


class TestComputeNEDiscrimination:
    def test_all_correct(self) -> None:
        elements = [_ne(0.1, "a"), _ne(0.2, "b")]
        live = {"a__el0": 0.05, "b__el0": 0.1}
        result = compute_ne_discrimination(elements, live, TAU)
        assert result.n_correct == 2

    def test_one_fooled_element_reduces_count(self) -> None:
        elements = [_ne(0.1, "a"), _ne(0.99, "b")]
        live = {"a__el0": 0.05, "b__el0": 0.99}  # b: live also >= tau, fooled
        result = compute_ne_discrimination(elements, live, TAU)
        assert result.n_correct == 1


class TestComputeEstablishedAccuracy:
    def test_counts_both_thresholds(self) -> None:
        elements = [_est(0.99, "a"), _est(0.6, "b"), _est(0.3, "c")]
        live = {"a__el0": 0.99, "b__el0": 0.6, "c__el0": 0.3}
        result = compute_established_accuracy(elements, live, TAU)
        assert result.n_p_ge_half == 2  # a, b
        assert result.n_p_ge_tau == 1  # a only

    def test_empty_elements_gives_none_not_a_fabricated_zero(self) -> None:
        """Fail-open-defaults guard fix: accuracy over ZERO elements is undefined, not a measured 0.0."""
        result = compute_established_accuracy([], {}, TAU)
        assert result.n_total == 0
        assert result.accuracy is None


class TestDecideAvailability:
    def _passing_result(self) -> ControlCheckResult:
        elements_est = [_est(0.99, f"e{i}") for i in range(200)]
        elements_ne = [_ne(0.01, f"n{i}") for i in range(70)] + [_ne(0.99, "n70")]  # 70/71 correct
        live = {f"{el.item_id}__{el.element_id}": el.sealed_reference_p for el in elements_est + elements_ne}
        parity = compute_parity(elements_est + elements_ne, live, TAU)
        ne = compute_ne_discrimination(elements_ne, live, TAU)
        est = compute_established_accuracy(elements_est, live, TAU)
        return ControlCheckResult(parity=parity, ne=ne, established=est)

    def test_passes_when_both_gating_floors_clear(self) -> None:
        verdict = decide_availability(self._passing_result(), parity_floor=0.98, parity_median_abs_dp=0.02,
                                       ne_discrimination_min=70, pinned=_pinned())
        assert verdict.available is True
        assert verdict.reasons == []

    def test_fails_when_ne_discrimination_drops_below_floor(self) -> None:
        elements_ne = [_ne(0.01, f"n{i}") for i in range(69)] + [_ne(0.99, "n69"), _ne(0.98, "n70")]
        live = {f"{el.item_id}__{el.element_id}": el.sealed_reference_p for el in elements_ne}
        ne = compute_ne_discrimination(elements_ne, live, TAU)  # 69/71, below the 70 floor
        result = ControlCheckResult(parity=compute_parity(elements_ne, live, TAU), ne=ne,
                                     established=EstablishedResultStub())
        verdict = decide_availability(result, parity_floor=0.98, parity_median_abs_dp=0.02,
                                       ne_discrimination_min=70, pinned=_pinned())
        assert verdict.available is False
        assert any("ne_discrimination" in r for r in verdict.reasons)

    def test_fails_when_parity_drops_below_floor_even_if_ne_passes(self) -> None:
        """Serving integrity gates independently of the safety check -- a parity failure alone (e.g. wrong
        adapter, template drift) fails closed even when NE discrimination happens to still pass."""
        elements_ne = [_ne(0.01, f"n{i}") for i in range(71)]
        live_ne = {f"{el.item_id}__{el.element_id}": el.sealed_reference_p for el in elements_ne}  # NE: exact
        elements_est = [_est(0.99, f"e{i}") for i in range(10)]
        live_est = {f"{el.item_id}__{el.element_id}": 0.01 for el in elements_est}  # drifted hard off parity
        live = {**live_ne, **live_est}
        parity = compute_parity(elements_est + elements_ne, live, TAU)
        ne = compute_ne_discrimination(elements_ne, live, TAU)
        result = ControlCheckResult(parity=parity, ne=ne, established=EstablishedResultStub())
        verdict = decide_availability(result, parity_floor=0.98, parity_median_abs_dp=0.02,
                                       ne_discrimination_min=70, pinned=_pinned())
        assert verdict.available is False
        assert any("parity" in r for r in verdict.reasons)
        assert ne.n_correct == 71  # confirms NE alone would have passed -- parity is what fails this

    def test_fails_when_median_drift_exceeds_floor_even_at_full_tau_agreement(self) -> None:
        """Every element stays on the SAME side of tau (so tau-agreement is 100%) but drifts uniformly by
        more than the allowed median -- a real miscalibration parity alone would catch."""
        elements = [_est(0.99, f"e{i}") for i in range(20)]
        live = {f"{el.item_id}__{el.element_id}": 0.94 for el in elements}  # still >= tau=0.97? no: 0.94<0.97
        # Use a lower tau region instead so both sides stay >=0.5 (established-ish) but drift > floor:
        elements = [_est(0.6, f"e{i}") for i in range(20)]
        live = {f"{el.item_id}__{el.element_id}": 0.4 for el in elements}  # both < tau=0.97: same side
        parity = compute_parity(elements, live, TAU)
        assert parity.agree_rate == 1.0  # same side of tau every time
        result = ControlCheckResult(parity=parity, ne=NEResultStub(), established=EstablishedResultStub())
        verdict = decide_availability(result, parity_floor=0.98, parity_median_abs_dp=0.02,
                                       ne_discrimination_min=0, pinned=_pinned())
        assert verdict.available is False
        assert any("median" in r for r in verdict.reasons)

    def test_empty_parity_alone_fails_closed_without_crashing(self) -> None:
        """Fail-open-defaults guard fix: agree_rate/median_abs_dp are None on an empty parity set, so
        decide_availability must not compare None to a float (TypeError) or treat it as passing. NE is
        healthy/passing here so this isolates the PARITY branch specifically -- if that branch were
        removed, this test alone would fail (unlike a test that also has NE empty, which the ne branch's
        own message would mask)."""
        empty_parity = compute_parity([], {}, TAU)
        elements_ne = [_ne(0.01, f"n{i}") for i in range(71)]
        live_ne = {f"{el.item_id}__{el.element_id}": el.sealed_reference_p for el in elements_ne}
        ne = compute_ne_discrimination(elements_ne, live_ne, TAU)  # 71/71, would pass on its own
        result = ControlCheckResult(parity=empty_parity, ne=ne, established=EstablishedResultStub())
        verdict = decide_availability(result, parity_floor=0.98, parity_median_abs_dp=0.02,
                                       ne_discrimination_min=70, pinned=_pinned())
        assert verdict.available is False
        assert any("parity" in r and "empty control set" in r for r in verdict.reasons)

    def test_empty_ne_alone_fails_closed_without_crashing(self) -> None:
        """Isolates the NE branch specifically -- parity here is healthy/passing (against itself), so this
        test alone would fail if the NE empty-set branch were removed."""
        elements_est = [_est(0.99, f"e{i}") for i in range(10)]
        live_est = {f"{el.item_id}__{el.element_id}": el.sealed_reference_p for el in elements_est}
        parity = compute_parity(elements_est, live_est, TAU)  # scored against itself: 100%/0 drift
        empty_ne = compute_ne_discrimination([], {}, TAU)
        result = ControlCheckResult(parity=parity, ne=empty_ne, established=EstablishedResultStub())
        verdict = decide_availability(result, parity_floor=0.98, parity_median_abs_dp=0.02,
                                       ne_discrimination_min=70, pinned=_pinned())
        assert verdict.available is False
        assert any("ne_discrimination" in r and "empty control set" in r for r in verdict.reasons)


class TestFailClosedOnEndpointFailure:
    """Lead-2's explicit instruction: unit-test the fail-closed path with a stubbed endpoint error and
    timeout -- never a partial/silent pass when the live endpoint cannot be reached at all."""

    def test_stubbed_connection_error_fails_closed(self) -> None:
        def _raising_score_fn(element: ControlElement) -> float:
            raise ConnectionError("stubbed: endpoint refused connection")

        result = run_live_check([_est(0.99, "a")], [_ne(0.01, "b")], score_fn=_raising_score_fn, parity_tau=TAU)
        assert result.endpoint_unavailable is True
        assert "ConnectionError" in result.endpoint_error
        verdict = decide_availability(result, parity_floor=0.98, parity_median_abs_dp=0.02,
                                       ne_discrimination_min=70, pinned=_pinned())
        assert verdict.available is False
        assert any("endpoint_unavailable" in r for r in verdict.reasons)

    def test_stubbed_timeout_fails_closed(self) -> None:
        def _timing_out_score_fn(element: ControlElement) -> float:
            raise TimeoutError("stubbed: endpoint did not respond in time")

        result = run_live_check([_est(0.99, "a")], [_ne(0.01, "b")], score_fn=_timing_out_score_fn,
                                 parity_tau=TAU)
        assert result.endpoint_unavailable is True
        verdict = decide_availability(result, parity_floor=0.98, parity_median_abs_dp=0.02,
                                       ne_discrimination_min=70, pinned=_pinned())
        assert verdict.available is False

    def test_partial_failure_partway_through_is_still_whole_endpoint_unavailable(self) -> None:
        """A score_fn that succeeds on the first N elements then fails must NOT produce a partial result
        scored only on what succeeded -- the whole check fails closed, not a silently-smaller sample."""
        calls = {"n": 0}

        def _flaky_score_fn(element: ControlElement) -> float:
            calls["n"] += 1
            if calls["n"] > 3:
                raise TimeoutError("stubbed: endpoint stopped responding after a few calls")
            return 0.5

        elements_est = [_est(0.99, f"e{i}") for i in range(10)]
        result = run_live_check(elements_est, [_ne(0.01, "n0")], score_fn=_flaky_score_fn, parity_tau=TAU)
        assert result.endpoint_unavailable is True
        assert result.parity is None
        assert result.ne is None
        assert result.established is None

    def test_real_score_fn_succeeding_never_marks_endpoint_unavailable(self) -> None:
        def _working_score_fn(element: ControlElement) -> float:
            return element.sealed_reference_p

        elements_est = [_est(0.99, "a")]
        elements_ne = [_ne(0.01, "b")]
        result = run_live_check(elements_est, elements_ne, score_fn=_working_score_fn, parity_tau=TAU)
        assert result.endpoint_unavailable is False
        assert result.parity is not None
        assert result.parity.agree_rate == 1.0


class TestResolvePrivateRoot:
    """Lead-2, 2026-09-26: pravrudhi is PUBLIC. The sealed control sets (item ids, sealed reference p
    values) and eval_items.jsonl (scenario/statute text) must never be committed here -- the CLI reads them
    from a configured private path (prabhasa-nyaya) and refuses to run if that path is absent or
    incomplete. No real file I/O against the actual private repo in these tests -- tmp_path fixtures only."""

    def test_refuses_when_env_var_unset(self) -> None:
        with pytest.raises(PrivateControlDataUnavailable, match="PRABHASA_NYAYA_ROOT is not set"):
            resolve_private_root(env={})

    def test_refuses_when_env_var_empty_string(self) -> None:
        with pytest.raises(PrivateControlDataUnavailable, match="PRABHASA_NYAYA_ROOT is not set"):
            resolve_private_root(env={"PRABHASA_NYAYA_ROOT": ""})

    def test_refuses_when_path_set_but_files_missing(self, tmp_path: Path) -> None:
        empty_dir = tmp_path / "not_actually_prabhasa_nyaya"
        empty_dir.mkdir()
        with pytest.raises(PrivateControlDataUnavailable, match="missing required file"):
            resolve_private_root(env={"PRABHASA_NYAYA_ROOT": str(empty_dir)})

    def test_refuses_when_only_some_files_present(self, tmp_path: Path) -> None:
        configc = tmp_path / "research" / "gates" / "P2b" / "configC" / "second_judge_positive_control"
        configc.mkdir(parents=True)
        (configc / "established_200.json").write_text("{}")
        # ne_discrimination_71.json and eval_items.jsonl both still missing
        with pytest.raises(PrivateControlDataUnavailable, match="missing required file"):
            resolve_private_root(env={"PRABHASA_NYAYA_ROOT": str(tmp_path)})

    def test_succeeds_when_all_required_files_present(self, tmp_path: Path) -> None:
        configc = tmp_path / "research" / "gates" / "P2b" / "configC" / "second_judge_positive_control"
        configc.mkdir(parents=True)
        (configc / "established_200.json").write_text("{}")
        (configc / "ne_discrimination_71.json").write_text("{}")
        eval_dir = tmp_path / "research" / "gates" / "P2b" / "element_judgment_v1"
        eval_dir.mkdir(parents=True)
        (eval_dir / "eval_items.jsonl").write_text("")
        result = resolve_private_root(env={"PRABHASA_NYAYA_ROOT": str(tmp_path)})
        assert result == tmp_path


# -- lightweight stubs for tests that only need SOME of ControlCheckResult's fields non-None -------------
def NEResultStub() -> NEResult:
    return NEResult(n_total=71, n_correct=71)


def EstablishedResultStub() -> EstablishedResult:
    return EstablishedResult(n_total=200, n_p_ge_half=191, n_p_ge_tau=100)


# =======================================================================================================
# Issue #100. The two blocking fail-opens, and the three behaviours the operator requires: an established
# half short of the pinned planned n, any empty or truncated sealed set, and a run that evaluated zero
# items must EACH produce available=False and a non-zero exit code.
# =======================================================================================================

MANIFEST_PATH = REPO_ROOT / "configs" / "sealed_control_manifest.yaml"

PREFLIGHT = REPO_ROOT / "scripts" / "second_judge_positive_control_preflight.py"


def _ids_json(n: int, gold: str = "established", prefix: str = "e") -> str:
    """A sealed-set-shaped JSON document with n rows. Synthetic ids and reference scores, constructed here
    in the test -- the real sealed sets are private and are never reproduced, even in a fixture."""
    rows = [
        {"item_id": f"{prefix}{i}", "element_id": "el0", "sealed_reference_p": 0.99, "gold_status": gold}
        for i in range(n)
    ]
    return json.dumps({"ids": rows})


def _write_private_root(tmp_path: Path, n_established: int = 200, n_ne: int = 71,
                         n_eval: int = 5) -> tuple[Path, dict[str, str]]:
    """Writes a fake PRABHASA_NYAYA_ROOT tree and returns it with the real sha256 of each file it wrote."""
    configc = tmp_path / "research" / "gates" / "P2b" / "configC" / "second_judge_positive_control"
    configc.mkdir(parents=True)
    est_text = _ids_json(n_established, "established", "e")
    ne_text = _ids_json(n_ne, "not_established", "n")
    (configc / "established_200.json").write_text(est_text)
    (configc / "ne_discrimination_71.json").write_text(ne_text)
    eval_dir = tmp_path / "research" / "gates" / "P2b" / "element_judgment_v1"
    eval_dir.mkdir(parents=True)
    eval_text = "".join(json.dumps({"item_id": f"e{i}", "element_id": "el0"}) + "\n" for i in range(n_eval))
    (eval_dir / "eval_items.jsonl").write_text(eval_text)
    digests = {
        "established_200": hashlib.sha256(est_text.encode()).hexdigest(),
        "ne_discrimination_71": hashlib.sha256(ne_text.encode()).hexdigest(),
        "eval_items_v1": hashlib.sha256(eval_text.encode()).hexdigest(),
    }
    return tmp_path, digests


def _manifest_text(digests: dict[str, str], n_established: int = 200, n_ne: int = 71,
                    n_eval: int = 5) -> str:
    return json.dumps({
        "sealed_sets": [
            {"name": "established_200", "kind": "json_ids", "n_rows": n_established,
             "sha256": digests["established_200"],
             "relative_path":
                 "research/gates/P2b/configC/second_judge_positive_control/established_200.json"},
            {"name": "ne_discrimination_71", "kind": "json_ids", "n_rows": n_ne,
             "sha256": digests["ne_discrimination_71"],
             "relative_path":
                 "research/gates/P2b/configC/second_judge_positive_control/ne_discrimination_71.json"},
            {"name": "eval_items_v1", "kind": "jsonl", "n_rows": n_eval,
             "sha256": digests["eval_items_v1"],
             "relative_path": "research/gates/P2b/element_judgment_v1/eval_items.jsonl"},
        ]
    })


class TestTheUnpinnedSentinel:
    """The sentinel must be impossible to mistake for a digest AND impossible to satisfy accidentally. If
    someone later "fixes" the manifest by making the sentinel parse as a digest, these fail."""

    def test_the_sentinel_is_not_a_well_formed_sha256(self) -> None:
        assert SEALED_PIN_UNSET == "UNPINNED"
        assert len(SEALED_PIN_UNSET) != 64
        assert re.match(r"^[0-9a-f]{64}$", SEALED_PIN_UNSET) is None
        # Not hex at all, so it cannot be turned into a digest-shaped value by padding either.
        assert not all(c in "0123456789abcdef" for c in SEALED_PIN_UNSET)

    def test_the_digest_validator_rejects_the_sentinel(self) -> None:
        """The same validator every pinned digest goes through refuses the sentinel -- the sentinel is not
        special-cased into acceptance anywhere."""
        with pytest.raises(SealedControlVerificationError, match="not a lowercase"):
            SealedSetPin(name="x", relative_path="p", kind="json_ids", n_rows=1,
                          sha256=SEALED_PIN_UNSET)

    @pytest.mark.parametrize("bad", ["", "abc123", "A" * 64, "g" * 64, "0" * 63, "0" * 65, "f" * 64 + "0"])
    def test_a_malformed_digest_is_refused_rather_than_treated_as_absent(self, bad: str) -> None:
        """A wrong-shaped digest must not fall through to the unpinned path (which would be a refusal too,
        but for the wrong reason) or to acceptance. `f`*64 and `0`*64 ARE well-formed sha256 strings by
        shape, so they are deliberately NOT in this list -- they would be caught by the file not hashing
        to them, not by shape."""
        with pytest.raises(SealedControlVerificationError):
            SealedSetPin(name="x", relative_path="p", kind="json_ids", n_rows=1, sha256=bad)

    @pytest.mark.parametrize("bad", [0, -1, "200", 200.0, True, None])
    def test_a_pinned_row_count_that_is_not_a_positive_int_is_refused(self, bad: object) -> None:
        """A pin of 0 would make an empty file satisfy its own expectation; a bool would make True == 1."""
        with pytest.raises(SealedControlVerificationError):
            SealedSetPin(name="x", relative_path="p", kind="json_ids", n_rows=bad,  # type: ignore[arg-type]
                          sha256=FAKE_ESTABLISHED_SHA)


class TestTheCommittedManifest:
    """`configs/sealed_control_manifest.yaml` as it is actually committed. Asserts the pin is WELL-FORMED
    and its unset state is ACTUALLY ENFORCED BY A REFUSAL -- the same standard
    `tests/test_sealed_set_digests.py::TestEnforcedDigestPins` holds every other pin in this repo to."""

    def _raw(self) -> dict[str, object]:
        loaded = yaml.safe_load(MANIFEST_PATH.read_text())
        assert isinstance(loaded, dict)
        return loaded

    def test_the_manifest_is_committed(self) -> None:
        assert MANIFEST_PATH.is_file()

    def test_it_covers_every_sealed_file_the_control_reads(self) -> None:
        entries = self._raw()["sealed_sets"]
        assert isinstance(entries, list)
        by_name = {e["name"]: e for e in entries}
        assert set(by_name) == {"established_200", "ne_discrimination_71", "eval_items_v1"}
        assert (by_name["established_200"]["relative_path"]
                == "research/gates/P2b/configC/second_judge_positive_control/established_200.json")
        assert (by_name["ne_discrimination_71"]["relative_path"]
                == "research/gates/P2b/configC/second_judge_positive_control/ne_discrimination_71.json")
        assert (by_name["eval_items_v1"]["relative_path"]
                == "research/gates/P2b/element_judgment_v1/eval_items.jsonl")

    def test_the_two_public_row_counts_are_pinned_as_real_numbers(self) -> None:
        """200 and 71 are public knowledge in this repo (configs/nyaya_agent.yaml's commentary and
        tests/test_sealed_set_digests.py's EXPECTED_PRIVATE_COUNTS), so they are pinned, not sentinelled."""
        entries = self._raw()["sealed_sets"]
        assert isinstance(entries, list)
        by_name = {e["name"]: e for e in entries}
        assert by_name["established_200"]["n_rows"] == 200
        assert by_name["ne_discrimination_71"]["n_rows"] == 71

    def test_every_digest_is_now_pinned_and_digest_shaped(self) -> None:
        """Pinned 2026-09-27 (Lead-2, computed from prabhasa-nyaya commit add40e6d8418b16b726ec54fa8444969
        d78403b0, git objects, off-repo) -- this test's premise flipped from "nobody here can compute these"
        to "they are computed", so it now asserts every entry is real-digest-shaped, never the sentinel."""
        entries = self._raw()["sealed_sets"]
        assert isinstance(entries, list)
        for entry in entries:
            assert entry["sha256"] != SEALED_PIN_UNSET, f"{entry['name']}'s sha256 is still the sentinel"
            assert re.match(r"^[0-9a-f]{64}$", str(entry["sha256"])), (
                f"{entry['name']}'s sha256 is not a lowercase 64-hex digest"
            )

    def test_loading_it_succeeds_now_that_the_pins_are_set(self) -> None:
        """Fail-closed on an unset digest still RAISES (covered directly in TestParseSealedManifest below);
        against THIS committed manifest, now pinned, loading succeeds and returns all three pins."""
        pins = load_sealed_manifest(REPO_ROOT)
        assert set(pins) == {"established_200", "ne_discrimination_71", "eval_items_v1"}


class TestParseSealedManifest:
    def test_a_fully_pinned_manifest_parses(self) -> None:
        digests = {"established_200": FAKE_ESTABLISHED_SHA, "ne_discrimination_71": FAKE_NE_SHA,
                    "eval_items_v1": "c" * 64}
        pins = parse_sealed_manifest(_manifest_text(digests))
        assert set(pins) == {"established_200", "ne_discrimination_71", "eval_items_v1"}
        assert pins["established_200"].n_rows == 200

    @pytest.mark.parametrize("text", ["", "null", "[]", "sealed_sets:", "sealed_sets: []",
                                       "sealed_sets: {}", "other_key: 1"])
    def test_an_empty_or_shapeless_manifest_is_refused_not_treated_as_nothing_to_verify(
        self, text: str
    ) -> None:
        """An empty manifest would verify nothing while looking like a manifest -- "checked nothing" must
        not read as "checked and clean"."""
        with pytest.raises(SealedControlVerificationError):
            parse_sealed_manifest(text)

    @pytest.mark.parametrize("missing", ["name", "relative_path", "kind", "n_rows", "sha256"])
    def test_a_missing_field_is_refused_rather_than_defaulted(self, missing: str) -> None:
        entry = {"name": "established_200", "kind": "json_ids", "n_rows": 200,
                 "sha256": FAKE_ESTABLISHED_SHA, "relative_path": "a/b.json"}
        del entry[missing]
        with pytest.raises(SealedControlVerificationError, match=missing):
            parse_sealed_manifest(json.dumps({"sealed_sets": [entry]}))

    def test_a_duplicate_entry_is_refused(self) -> None:
        entry = {"name": "established_200", "kind": "json_ids", "n_rows": 200,
                 "sha256": FAKE_ESTABLISHED_SHA, "relative_path": "a/b.json"}
        with pytest.raises(SealedControlVerificationError, match="twice"):
            parse_sealed_manifest(json.dumps({"sealed_sets": [entry, dict(entry)]}))

    def test_an_unknown_kind_is_refused(self) -> None:
        with pytest.raises(SealedControlVerificationError, match="kind"):
            parse_sealed_manifest(json.dumps({"sealed_sets": [
                {"name": "x", "kind": "csv", "n_rows": 1, "sha256": FAKE_NE_SHA, "relative_path": "a"}]}))


class TestCountSealedRows:
    def test_json_ids_counts_the_ids_list(self) -> None:
        assert count_sealed_rows("json_ids", _ids_json(7).encode()) == 7

    def test_jsonl_counts_non_blank_lines(self) -> None:
        assert count_sealed_rows("jsonl", b'{"a":1}\n\n{"a":2}\n') == 2

    @pytest.mark.parametrize("raw", [b"not json", b"[1,2,3]", b'{"rows": []}', b"\xff\xfe"])
    def test_an_unparseable_or_unrecognised_file_raises_instead_of_counting_zero(self, raw: bytes) -> None:
        """The fail-open shape being removed: an unparseable sealed file must become an exception, never a
        row count of 0 that then gets compared against a pin."""
        with pytest.raises(SealedControlVerificationError):
            count_sealed_rows("json_ids", raw)

    def test_a_bad_jsonl_line_raises_and_names_the_line(self) -> None:
        with pytest.raises(SealedControlVerificationError, match="line 2"):
            count_sealed_rows("jsonl", b'{"a":1}\nnot json\n')


class TestVerifySealedFile:
    def _pin(self, path: Path, n_rows: int, kind: str = "json_ids") -> SealedSetPin:
        return SealedSetPin(name="established_200", relative_path=path.name, kind=kind, n_rows=n_rows,
                             sha256=hashlib.sha256(path.read_bytes()).hexdigest())

    def test_a_matching_file_verifies_and_returns_its_row_count(self, tmp_path: Path) -> None:
        f = tmp_path / "established_200.json"
        f.write_text(_ids_json(200))
        assert verify_sealed_file(f, self._pin(f, 200)) == 200

    def test_a_truncated_file_fails_the_digest(self, tmp_path: Path) -> None:
        """Requirement 2, at load time: a truncated sealed set cannot rehash to the pinned value."""
        f = tmp_path / "established_200.json"
        f.write_text(_ids_json(200))
        pin = self._pin(f, 200)
        f.write_text(_ids_json(199))
        with pytest.raises(SealedControlVerificationError, match="pinned"):
            verify_sealed_file(f, pin)

    def test_a_same_length_content_change_fails_the_digest_where_the_count_cannot(
        self, tmp_path: Path
    ) -> None:
        """The digest comparison's OWN load-bearing case, and the reason it is not redundant with the row
        count. Found by mutation: disabling the sha256 comparison entirely left the whole suite green,
        because every other mismatch fixture here TRUNCATES the file and so is caught by the count check
        instead. Here the count is identical -- 200 rows in, 200 rows out -- and only the digest differs.

        This is also the realistic tamper rather than a synthetic one: `sealed_reference_p` swapped for a
        different run's scores is precisely the "wrong adapter / re-sealed against a broken run" case
        parity exists to detect, and it changes no row count at all."""
        f = tmp_path / "established_200.json"
        f.write_text(_ids_json(200))
        pin = self._pin(f, 200)
        rows = json.loads(f.read_text())["ids"]
        for row in rows:
            row["sealed_reference_p"] = 0.10
        f.write_text(json.dumps({"ids": rows}))
        assert count_sealed_rows("json_ids", f.read_bytes()) == 200, (
            "the row count must be UNCHANGED here, or this test is just another truncation test and "
            "proves nothing about the digest"
        )
        with pytest.raises(SealedControlVerificationError, match="pinned"):
            verify_sealed_file(f, pin)

    def test_an_emptied_file_fails_closed(self, tmp_path: Path) -> None:
        f = tmp_path / "established_200.json"
        f.write_text(_ids_json(200))
        pin = self._pin(f, 200)
        f.write_text(json.dumps({"ids": []}))
        with pytest.raises(SealedControlVerificationError):
            verify_sealed_file(f, pin)

    def test_a_file_with_zero_rows_fails_even_when_its_digest_matches(self, tmp_path: Path) -> None:
        """The digest and the count are checked independently on purpose. Here the pin is honestly computed
        over an EMPTY file, so the digest matches -- and the run is still refused, because a verdict over
        zero elements is not a verdict. Without this, an emptied set that someone re-pinned would pass."""
        f = tmp_path / "established_200.json"
        f.write_text(json.dumps({"ids": []}))
        pin = SealedSetPin(name="established_200", relative_path=f.name, kind="json_ids", n_rows=1,
                            sha256=hashlib.sha256(f.read_bytes()).hexdigest())
        with pytest.raises(SealedControlVerificationError, match="zero rows"):
            verify_sealed_file(f, pin)

    def test_a_row_count_mismatch_fails_even_when_the_digest_matches(self, tmp_path: Path) -> None:
        """The count is asserted in its own right, not inferred from the digest -- it is the number the
        verdict's count identity is measured against."""
        f = tmp_path / "established_200.json"
        f.write_text(_ids_json(199))
        pin = SealedSetPin(name="established_200", relative_path=f.name, kind="json_ids", n_rows=200,
                            sha256=hashlib.sha256(f.read_bytes()).hexdigest())
        with pytest.raises(SealedControlVerificationError, match="199 rows, pinned 200"):
            verify_sealed_file(f, pin)

    def test_a_missing_file_raises(self, tmp_path: Path) -> None:
        pin = SealedSetPin(name="established_200", relative_path="gone.json", kind="json_ids", n_rows=1,
                            sha256=FAKE_ESTABLISHED_SHA)
        with pytest.raises(SealedControlVerificationError, match="could not be read"):
            verify_sealed_file(tmp_path / "gone.json", pin)


class TestLoadVerifiedControlSets:
    def test_a_fully_pinned_and_matching_tree_loads_with_its_pinned_counts(self, tmp_path: Path) -> None:
        root, digests = _write_private_root(tmp_path)
        sets = load_verified_control_sets(root, parse_sealed_manifest(_manifest_text(digests)))
        assert len(sets.established) == 200
        assert len(sets.ne) == 71
        assert sets.pinned.established == 200
        assert sets.pinned.ne == 71
        assert sets.pinned.total == 271

    def test_a_truncated_established_half_refuses_at_load_time(self, tmp_path: Path) -> None:
        """Requirement 1, at load time. The manifest still pins 200; the file now holds 199."""
        root, digests = _write_private_root(tmp_path)
        configc = root / "research" / "gates" / "P2b" / "configC" / "second_judge_positive_control"
        (configc / "established_200.json").write_text(_ids_json(199))
        with pytest.raises(SealedControlVerificationError):
            load_verified_control_sets(root, parse_sealed_manifest(_manifest_text(digests)))

    def test_an_emptied_ne_half_refuses_at_load_time(self, tmp_path: Path) -> None:
        root, digests = _write_private_root(tmp_path)
        configc = root / "research" / "gates" / "P2b" / "configC" / "second_judge_positive_control"
        (configc / "ne_discrimination_71.json").write_text(json.dumps({"ids": []}))
        with pytest.raises(SealedControlVerificationError):
            load_verified_control_sets(root, parse_sealed_manifest(_manifest_text(digests)))

    def test_a_truncated_eval_items_refuses_at_load_time(self, tmp_path: Path) -> None:
        """eval_items.jsonl is verified too: it is where each element's request content is looked up, so a
        truncated copy makes elements unscoreable -- which #100's item 4 showed surfaces as a spurious
        endpoint outage rather than as the data-integrity fault it is."""
        root, digests = _write_private_root(tmp_path)
        (root / "research" / "gates" / "P2b" / "element_judgment_v1" / "eval_items.jsonl").write_text(
            '{"item_id": "e0", "element_id": "el0"}\n')
        with pytest.raises(SealedControlVerificationError):
            load_verified_control_sets(root, parse_sealed_manifest(_manifest_text(digests)))

    def test_a_manifest_that_does_not_cover_a_file_the_run_reads_refuses(self, tmp_path: Path) -> None:
        """"Checked nothing" must not read as "checked and clean": an absent manifest entry is a refusal,
        not an unverified pass-through."""
        root, digests = _write_private_root(tmp_path)
        pins = parse_sealed_manifest(_manifest_text(digests))
        del pins["ne_discrimination_71"]
        with pytest.raises(SealedControlVerificationError, match="does not cover"):
            load_verified_control_sets(root, pins)

    def test_a_row_missing_its_sealed_reference_is_refused_not_defaulted_to_zero(
        self, tmp_path: Path
    ) -> None:
        root, _ = _write_private_root(tmp_path)
        configc = root / "research" / "gates" / "P2b" / "configC" / "second_judge_positive_control"
        text = json.dumps({"ids": [{"item_id": "e0", "element_id": "el0", "gold_status": "established"}]})
        (configc / "established_200.json").write_text(text)
        digests = {
            "established_200": hashlib.sha256(text.encode()).hexdigest(),
            "ne_discrimination_71": hashlib.sha256(
                (configc / "ne_discrimination_71.json").read_bytes()).hexdigest(),
            "eval_items_v1": hashlib.sha256(
                (root / "research" / "gates" / "P2b" / "element_judgment_v1"
                 / "eval_items.jsonl").read_bytes()).hexdigest(),
        }
        with pytest.raises(SealedControlVerificationError, match="sealed_reference_p"):
            load_verified_control_sets(root, parse_sealed_manifest(
                _manifest_text(digests, n_established=1)))


class TestPinnedCountsCannotExpressAnEmptyExpectation:
    @pytest.mark.parametrize("established,ne", [(0, 71), (200, 0), (0, 0), (-1, 71)])
    def test_a_non_positive_planned_n_is_refused(self, established: int, ne: int) -> None:
        """`n_planned` coming out as 0 is the fail-open this whole change removes: a truncated input that
        computes its own expectation reports itself complete. A planned n of 0 is not expressible."""
        with pytest.raises(SealedControlVerificationError, match="positive int"):
            PinnedCounts(established=established, ne=ne, established_sha256=FAKE_ESTABLISHED_SHA,
                          ne_sha256=FAKE_NE_SHA)

    def test_a_count_without_a_digest_to_key_it_to_is_refused(self) -> None:
        """A self-declared expectation is not a check. The count is only meaningful keyed by the sha256 of
        the artefact it was read from, so a malformed digest refuses the whole object."""
        with pytest.raises(SealedControlVerificationError, match="only meaningful keyed by"):
            PinnedCounts(established=200, ne=71, established_sha256="", ne_sha256=FAKE_NE_SHA)


class TestDecideAvailabilityRequiresThePinnedCounts:
    def test_there_is_no_default_for_pinned(self) -> None:
        """A default would silently reintroduce the fail-open the first time a new caller omitted it."""
        with pytest.raises(TypeError, match="pinned"):
            decide_availability(  # type: ignore[call-arg]
                ControlCheckResult(parity=ParityResult(271, 271, 0.0), ne=NEResultStub(),
                                    established=EstablishedResultStub()),
                parity_floor=0.98, parity_median_abs_dp=0.02, ne_discrimination_min=70)

    def test_a_result_with_no_established_half_at_all_fails_closed(self) -> None:
        """`decide_availability` never read `result.established` before this change; a None established
        half now fails closed instead of being ignored."""
        verdict = decide_availability(
            ControlCheckResult(parity=ParityResult(271, 271, 0.0), ne=NEResultStub(), established=None),
            parity_floor=0.98, parity_median_abs_dp=0.02, ne_discrimination_min=70, pinned=_pinned())
        assert verdict.available is False


class TestIssue100RequiredBehaviourOne:
    """REQUIRED BEHAVIOUR 1: an ESTABLISHED half with fewer rows than the pinned planned n produces
    available=False. #100 reproduced the opposite against the landed code: 200 -> 1 -> 0 established
    elements, all three `available=True, reasons=[]`. These drive the same path the issue drove -- the real
    `run_live_check` with a HEALTHY stub scorer (each element's own `sealed_reference_p`, so parity is 100%
    and median|dp| is 0 by construction, and the NE half is fully correct) -- so nothing but the
    established count differs between a pass and a fail here."""

    def _healthy(self, n_established: int) -> ControlCheckResult:
        est = [_est(0.99, f"e{i}") for i in range(n_established)]
        ne = [_ne(0.01, f"n{i}") for i in range(71)]
        return run_live_check(est, ne, score_fn=lambda el: el.sealed_reference_p, parity_tau=TAU)

    def test_the_full_two_hundred_still_passes(self) -> None:
        """The control must still say AVAILABLE on a complete, healthy run -- otherwise the guard below is
        proving nothing except that everything fails."""
        verdict = decide_availability(self._healthy(200), parity_floor=0.98, parity_median_abs_dp=0.02,
                                       ne_discrimination_min=70, pinned=_pinned())
        assert verdict.available is True, verdict.reasons
        assert verdict.reasons == []

    @pytest.mark.parametrize("n_established", [199, 100, 1, 0])
    def test_a_short_established_half_fails_closed(self, n_established: int) -> None:
        result = self._healthy(n_established)
        verdict = decide_availability(result, parity_floor=0.98, parity_median_abs_dp=0.02,
                                       ne_discrimination_min=70, pinned=_pinned())
        assert verdict.available is False
        assert any("count_identity(established)" in r for r in verdict.reasons), verdict.reasons
        assert any(str(n_established) in r and "200 planned" in r for r in verdict.reasons)

    def test_the_reason_names_the_digest_the_planned_n_was_keyed_to(self) -> None:
        """The planned n is only a check if it came from an artefact this run did not produce. The reason
        string carries the sha256 it was keyed to, so a reader can tell it was not `len(loaded)`."""
        verdict = decide_availability(self._healthy(1), parity_floor=0.98, parity_median_abs_dp=0.02,
                                       ne_discrimination_min=70, pinned=_pinned())
        assert any(FAKE_ESTABLISHED_SHA in r for r in verdict.reasons)

    def test_an_extended_established_half_also_fails_closed(self) -> None:
        """A count IDENTITY, not a minimum. 201 elements is the wrong file just as surely as 199 is."""
        verdict = decide_availability(self._healthy(201), parity_floor=0.98, parity_median_abs_dp=0.02,
                                       ne_discrimination_min=70, pinned=_pinned())
        assert verdict.available is False
        assert any("count_identity(established)" in r for r in verdict.reasons)


class TestIssue100RequiredBehaviourTwo:
    """REQUIRED BEHAVIOUR 2: ANY empty or truncated sealed set, established or NE, fails closed. The
    load-time half of this is `TestVerifySealedFile` / `TestLoadVerifiedControlSets` above; these are the
    VERDICT-level half, which is what protects a caller that assembled a result some other way."""

    def test_a_truncated_ne_half_fails_closed_on_the_count_identity_alone(self) -> None:
        """Isolates the NE count identity from `ne_discrimination_min`: 70 of 70 NE elements are correct,
        so the discrimination floor of 70 is MET and would have passed on its own. Only the count identity
        against the pinned 71 fails this."""
        est = [_est(0.99, f"e{i}") for i in range(200)]
        ne = [_ne(0.01, f"n{i}") for i in range(70)]
        result = run_live_check(est, ne, score_fn=lambda el: el.sealed_reference_p, parity_tau=TAU)
        assert result.ne is not None and result.ne.n_correct == 70  # the floor itself is satisfied
        verdict = decide_availability(result, parity_floor=0.98, parity_median_abs_dp=0.02,
                                       ne_discrimination_min=70, pinned=_pinned())
        assert verdict.available is False
        assert any("count_identity(not_established)" in r for r in verdict.reasons), verdict.reasons

    def test_an_emptied_ne_half_fails_closed(self) -> None:
        est = [_est(0.99, f"e{i}") for i in range(200)]
        result = run_live_check(est, [], score_fn=lambda el: el.sealed_reference_p, parity_tau=TAU)
        verdict = decide_availability(result, parity_floor=0.98, parity_median_abs_dp=0.02,
                                       ne_discrimination_min=70, pinned=_pinned())
        assert verdict.available is False
        assert any("count_identity(not_established)" in r for r in verdict.reasons)

    def test_a_parity_set_that_does_not_cover_both_halves_fails_closed(self) -> None:
        """Defence in depth against a future caller that scores parity over something other than
        established + ne: the total is an identity against the pinned sum, not a rate over what arrived."""
        verdict = decide_availability(
            ControlCheckResult(parity=ParityResult(n_total=271, n_tau_agree=271, median_abs_dp=0.0),
                                ne=NEResult(n_total=71, n_correct=71),
                                established=EstablishedResult(n_total=200, n_p_ge_half=191, n_p_ge_tau=100)),
            parity_floor=0.98, parity_median_abs_dp=0.02, ne_discrimination_min=70,
            pinned=PinnedCounts(established=200, ne=72, established_sha256=FAKE_ESTABLISHED_SHA,
                                 ne_sha256=FAKE_NE_SHA))
        assert verdict.available is False
        assert any("count_identity(total_scored)" in r for r in verdict.reasons)


class TestIssue100RequiredBehaviourThree:
    """REQUIRED BEHAVIOUR 3: a run where ZERO items are evaluated produces available=False and a non-zero
    exit code. "Checked nothing" gets its own named reason, distinct from "checked and clean"."""

    def test_zero_items_evaluated_is_named_as_such_in_the_verdict(self) -> None:
        result = run_live_check([], [], score_fn=lambda el: el.sealed_reference_p, parity_tau=TAU)
        verdict = decide_availability(result, parity_floor=0.98, parity_median_abs_dp=0.02,
                                       ne_discrimination_min=70, pinned=_pinned())
        assert verdict.available is False
        assert any("zero_items_evaluated" in r for r in verdict.reasons), verdict.reasons
        assert any("checked nothing" in r for r in verdict.reasons)

    def test_the_cli_exits_non_zero_when_no_second_judge_is_configured(self, tmp_path: Path) -> None:
        """Issue #100 finding 2, end to end against the REAL script and the REAL shipped config, where
        `second_judge:` is commented out. This used to print "exiting 0" and return 0, so a scheduler
        reading only the exit code could not tell "the control ran and passed" from "it never ran".

        Run as a subprocess because that is the only thing a deploy pipeline actually observes."""
        root, _ = _write_private_root(tmp_path)
        proc = subprocess.run(
            [sys.executable, str(PREFLIGHT)],
            env={**os.environ, "PRABHASA_NYAYA_ROOT": str(root), "PRAVRUDHI_ROOT": str(REPO_ROOT)},
            capture_output=True, text=True, timeout=180,
        )
        assert proc.returncode != 0, f"exit 0 on a run that evaluated nothing\n{proc.stdout}{proc.stderr}"
        assert proc.returncode == 2, f"expected EXIT_NOT_EVALUATED=2, got {proc.returncode}"
        assert "NOT_EVALUATED" in proc.stdout, proc.stdout
        assert "VERDICT: UNAVAILABLE (fail closed)" in proc.stdout, proc.stdout
        assert "exiting 0" not in proc.stdout

    def test_the_cli_exits_non_zero_when_the_private_control_data_is_absent(self, tmp_path: Path) -> None:
        proc = subprocess.run(
            [sys.executable, str(PREFLIGHT)],
            env={**{k: v for k, v in os.environ.items() if k != "PRABHASA_NYAYA_ROOT"},
                 "PRAVRUDHI_ROOT": str(REPO_ROOT)},
            capture_output=True, text=True, timeout=180,
        )
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "NOT_EVALUATED" in proc.stdout
        assert "VERDICT: UNAVAILABLE (fail closed)" in proc.stdout

    def test_the_cli_declares_all_three_exit_codes_and_never_returns_zero_for_not_evaluated(self) -> None:
        """A named constant, so the not-evaluated state cannot drift back to 0 unnoticed."""
        source = PREFLIGHT.read_text()
        assert "EXIT_NOT_EVALUATED = 2" in source
        assert "EXIT_UNAVAILABLE = 1" in source
        assert "EXIT_AVAILABLE = 0" in source
        assert "return EXIT_NOT_EVALUATED" in source

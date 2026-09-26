"""Issue #44 (Lead-2, 2026-09-26 spec clarification). Unit tests for the pure check logic and, per Lead-2's
explicit instruction, the fail-closed path against a STUBBED endpoint error and timeout -- no real endpoint
call in any test here (the live 32B endpoint has 0 workers, EU-RO-1 capacity, as of this session; real E2E
waits for that)."""
from __future__ import annotations

from pathlib import Path

import pytest

from pravrudhi.application.second_judge_positive_control import (
    ControlCheckResult,
    ControlElement,
    PrivateControlDataUnavailable,
    RecordCheckFailed,
    RecordGatedJudge,
    check_record,
    compute_established_accuracy,
    compute_ne_discrimination,
    compute_parity,
    decide_availability,
    read_record,
    resolve_private_root,
    run_live_check,
    write_record,
)

TAU = 0.97


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
                                       ne_discrimination_min=70)
        assert verdict.available is True
        assert verdict.reasons == []

    def test_fails_when_ne_discrimination_drops_below_floor(self) -> None:
        elements_ne = [_ne(0.01, f"n{i}") for i in range(69)] + [_ne(0.99, "n69"), _ne(0.98, "n70")]
        live = {f"{el.item_id}__{el.element_id}": el.sealed_reference_p for el in elements_ne}
        ne = compute_ne_discrimination(elements_ne, live, TAU)  # 69/71, below the 70 floor
        result = ControlCheckResult(parity=compute_parity(elements_ne, live, TAU), ne=ne,
                                     established=EstablishedResultStub())
        verdict = decide_availability(result, parity_floor=0.98, parity_median_abs_dp=0.02,
                                       ne_discrimination_min=70)
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
                                       ne_discrimination_min=70)
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
                                       ne_discrimination_min=0)
        assert verdict.available is False
        assert any("median" in r for r in verdict.reasons)

    def test_empty_control_set_fails_closed_without_crashing(self) -> None:
        """Fail-open-defaults guard fix: agree_rate/median_abs_dp are None on an empty set, so
        decide_availability must not compare None to a float (TypeError) or, worse, treat None as passing.
        An empty control set is a config error, not a clean 100%/0-drift result."""
        empty_parity = compute_parity([], {}, TAU)
        empty_ne = compute_ne_discrimination([], {}, TAU)
        result = ControlCheckResult(parity=empty_parity, ne=empty_ne, established=EstablishedResultStub())
        verdict = decide_availability(result, parity_floor=0.98, parity_median_abs_dp=0.02,
                                       ne_discrimination_min=70)
        assert verdict.available is False
        assert any("empty control set" in r for r in verdict.reasons)


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
                                       ne_discrimination_min=70)
        assert verdict.available is False
        assert any("endpoint_unavailable" in r for r in verdict.reasons)

    def test_stubbed_timeout_fails_closed(self) -> None:
        def _timing_out_score_fn(element: ControlElement) -> float:
            raise TimeoutError("stubbed: endpoint did not respond in time")

        result = run_live_check([_est(0.99, "a")], [_ne(0.01, "b")], score_fn=_timing_out_score_fn,
                                 parity_tau=TAU)
        assert result.endpoint_unavailable is True
        verdict = decide_availability(result, parity_floor=0.98, parity_median_abs_dp=0.02,
                                       ne_discrimination_min=70)
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


class TestRecordRoundTrip:
    """Lead-2, 2026-09-26, trigger wiring: write_record/read_record/check_record are the pure functions
    the engine gates on -- no record, stale record, or mismatched record all fail closed the same way."""

    def test_write_then_read_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / "record.json"
        write_record(path, available=True, endpoint_id="ep1", adapter_sha="abc", reasons=[], timestamp=1000.0)
        record = read_record(path)
        assert record == {"timestamp": 1000.0, "available": True, "endpoint_id": "ep1",
                           "adapter_sha": "abc", "reasons": []}

    def test_read_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert read_record(tmp_path / "does_not_exist.json") is None

    def test_read_malformed_json_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "record.json"
        path.write_text("not json {{{")
        assert read_record(path) is None

    def test_write_is_atomic_no_tmp_file_left_behind(self, tmp_path: Path) -> None:
        path = tmp_path / "record.json"
        write_record(path, available=True, endpoint_id="ep1", adapter_sha="abc", reasons=[])
        assert path.exists()
        assert not path.with_suffix(".json.tmp").exists()

    def test_check_record_none_fails(self) -> None:
        ok, reason = check_record(None, max_age_hours=24, expected_endpoint_id="ep1",
                                   expected_adapter_sha="abc")
        assert ok is False
        assert reason == "no record"

    def test_check_record_last_check_failed_fails(self) -> None:
        record = {"timestamp": 1000.0, "available": False, "endpoint_id": "ep1", "adapter_sha": "abc",
                  "reasons": ["ne_discrimination 65/71 < floor 70/71"]}
        ok, reason = check_record(record, max_age_hours=24, expected_endpoint_id="ep1",
                                   expected_adapter_sha="abc", now=1000.0)
        assert ok is False
        assert "last check failed" in reason

    def test_check_record_too_old_fails(self) -> None:
        record = {"timestamp": 1000.0, "available": True, "endpoint_id": "ep1", "adapter_sha": "abc"}
        now = 1000.0 + 25 * 3600  # 25 hours later, past a 24h max_age
        ok, reason = check_record(record, max_age_hours=24, expected_endpoint_id="ep1",
                                   expected_adapter_sha="abc", now=now)
        assert ok is False
        assert "old" in reason

    def test_check_record_within_max_age_passes(self) -> None:
        record = {"timestamp": 1000.0, "available": True, "endpoint_id": "ep1", "adapter_sha": "abc"}
        now = 1000.0 + 23 * 3600  # 23 hours later, within a 24h max_age
        ok, reason = check_record(record, max_age_hours=24, expected_endpoint_id="ep1",
                                   expected_adapter_sha="abc", now=now)
        assert ok is True

    def test_check_record_wrong_endpoint_id_fails(self) -> None:
        record = {"timestamp": 1000.0, "available": True, "endpoint_id": "ep1", "adapter_sha": "abc"}
        ok, reason = check_record(record, max_age_hours=24, expected_endpoint_id="ep2",
                                   expected_adapter_sha="abc", now=1000.0)
        assert ok is False
        assert "endpoint_id" in reason

    def test_check_record_wrong_adapter_sha_fails(self) -> None:
        record = {"timestamp": 1000.0, "available": True, "endpoint_id": "ep1", "adapter_sha": "abc"}
        ok, reason = check_record(record, max_age_hours=24, expected_endpoint_id="ep1",
                                   expected_adapter_sha="different", now=1000.0)
        assert ok is False
        assert "adapter_sha" in reason

    def test_check_record_missing_available_key_fails_closed(self) -> None:
        """Fail-open-defaults guard fix: a malformed record with NO "available" key must fail exactly like
        one whose available was measured False, never silently pass via a `.get(..., False)` default that
        can't tell "no verdict" from "verdict was False" apart from a real measured value."""
        record = {"timestamp": 1000.0, "endpoint_id": "ep1", "adapter_sha": "abc"}
        ok, reason = check_record(record, max_age_hours=24, expected_endpoint_id="ep1",
                                   expected_adapter_sha="abc", now=1000.0)
        assert ok is False

    def test_check_record_none_expected_endpoint_id_fails_closed_even_if_record_also_unset(self) -> None:
        """The dangerous direction the guard flagged: if a deployment never configures second_judge.endpoint_
        id, the OLD code defaulted both the written record's endpoint_id and the expected value to "" --
        two unset configs would silently "match" as equal empty strings, letting an unverifiable identity
        read as verified. None must never match None (or "") here -- an unset expected identity always
        fails closed, regardless of what the record itself contains."""
        record = {"timestamp": 1000.0, "available": True, "endpoint_id": None, "adapter_sha": "abc"}
        ok, reason = check_record(record, max_age_hours=24, expected_endpoint_id=None,
                                   expected_adapter_sha="abc", now=1000.0)
        assert ok is False
        assert "not configured" in reason

    def test_check_record_none_expected_adapter_sha_fails_closed_even_if_record_also_unset(self) -> None:
        record = {"timestamp": 1000.0, "available": True, "endpoint_id": "ep1", "adapter_sha": None}
        ok, reason = check_record(record, max_age_hours=24, expected_endpoint_id="ep1",
                                   expected_adapter_sha=None, now=1000.0)
        assert ok is False
        assert "not configured" in reason


class TestRecordGatedJudge:
    def _write_passing_record(self, path: Path, now: float) -> None:
        write_record(path, available=True, endpoint_id="ep1", adapter_sha="abc", reasons=[], timestamp=now)

    def test_delegates_to_inner_when_record_is_fresh_and_matching(self, tmp_path: Path) -> None:
        import time as time_mod

        path = tmp_path / "record.json"
        self._write_passing_record(path, time_mod.time())

        class _StubInner:
            name = "inner"

            def judge(self, request):
                return "real judgment"

        gated = RecordGatedJudge(_StubInner(), record_path=path, max_age_hours=24,
                                  expected_endpoint_id="ep1", expected_adapter_sha="abc")
        assert gated.judge(object()) == "real judgment"

    def test_raises_without_calling_inner_when_no_record(self, tmp_path: Path) -> None:
        calls = []

        class _StubInner:
            name = "inner"

            def judge(self, request):
                calls.append(request)
                return "should never be reached"

        gated = RecordGatedJudge(_StubInner(), record_path=tmp_path / "no_such_record.json",
                                  max_age_hours=24, expected_endpoint_id="ep1", expected_adapter_sha="abc")
        with pytest.raises(RecordCheckFailed, match="no record"):
            gated.judge(object())
        assert calls == []  # the real endpoint must never be reached when the record check fails

    def test_raises_without_calling_inner_when_record_is_stale(self, tmp_path: Path) -> None:
        import time as time_mod

        path = tmp_path / "record.json"
        self._write_passing_record(path, time_mod.time() - 25 * 3600)  # 25h old, past a 24h max_age

        class _StubInner:
            name = "inner"

            def judge(self, request):
                raise AssertionError("must not be called")

        gated = RecordGatedJudge(_StubInner(), record_path=path, max_age_hours=24,
                                  expected_endpoint_id="ep1", expected_adapter_sha="abc")
        with pytest.raises(RecordCheckFailed, match="old"):
            gated.judge(object())

    def test_raises_without_calling_inner_when_adapter_sha_mismatches(self, tmp_path: Path) -> None:
        import time as time_mod

        path = tmp_path / "record.json"
        write_record(path, available=True, endpoint_id="ep1", adapter_sha="OLD_ADAPTER", reasons=[],
                     timestamp=time_mod.time())

        class _StubInner:
            name = "inner"

            def judge(self, request):
                raise AssertionError("must not be called")

        gated = RecordGatedJudge(_StubInner(), record_path=path, max_age_hours=24,
                                  expected_endpoint_id="ep1", expected_adapter_sha="NEW_ADAPTER")
        with pytest.raises(RecordCheckFailed, match="adapter_sha"):
            gated.judge(object())


# -- lightweight stubs for tests that only need SOME of ControlCheckResult's fields non-None -------------
def NEResultStub():
    from pravrudhi.application.second_judge_positive_control import NEResult
    return NEResult(n_total=71, n_correct=71)


def EstablishedResultStub():
    from pravrudhi.application.second_judge_positive_control import EstablishedResult
    return EstablishedResult(n_total=200, n_p_ge_half=191, n_p_ge_tau=100)

"""Unit tests for `pravrudhi.application.nyaya_pin_regression.compare_pins`'s own comparison logic --
monkeypatches the binary-facing functions so this never needs a real `score` executable (the tool was
already verified end-to-end against the real 29f6eaed/700de3aa binaries during the 2026-09-26 pin bump;
these tests are for the harness's own logic, not a second copy of that verification)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pravrudhi.application import nyaya_lean_registry as reg
from pravrudhi.application.nyaya_pin_regression import compare_pins

OLD = Path("old-binary")
NEW = Path("new-binary")


def _describe(contract_id: str, *, score_bin: Path) -> reg.DescribedContract:
    return reg.DescribedContract(contract_id=contract_id, elements=["el0", "el1"], denials=["deny0"])


def _check(assertions: dict[str, bool], contract_id: str, *, score_bin: Path) -> dict:
    met = {k for k, v in assertions.items() if v}
    elements, denials = {"el0", "el1"}, {"deny0"}
    denied = sorted(elements.union(denials) & met & denials)
    omitted = sorted(elements - met)
    unlicensed = sorted(met - elements - denials)
    verdict = "flagged" if (denied or omitted or unlicensed) else "grounded"
    return {"verdict": verdict, "denied_claims": denied, "unlicensed_claims": unlicensed, "omitted_claims": omitted}


class TestCleanCase:
    def test_identical_binaries_report_no_diffs(self) -> None:
        with (
            patch("pravrudhi.application.nyaya_pin_regression.list_contract_ids", return_value={"bns69"}),
            patch("pravrudhi.application.nyaya_pin_regression.describe_source", return_value="same text"),
            patch.object(reg, "describe_contract_detail", side_effect=_describe),
            patch.object(reg, "check_registry", side_effect=_check),
        ):
            assert compare_pins(OLD, NEW) == []


class TestMissingContract:
    def test_old_id_missing_from_new_is_critical_and_skips_everything_else(self) -> None:
        def list_ids(binpath: Path) -> set[str]:
            return {"bns69", "bns47"} if binpath == OLD else {"bns69"}

        with patch("pravrudhi.application.nyaya_pin_regression.list_contract_ids", side_effect=list_ids):
            diffs = compare_pins(OLD, NEW)
        assert len(diffs) == 1
        assert "bns47" in diffs[0]
        assert "CRITICAL" in diffs[0]


class TestSourceTextDiff:
    def test_differing_source_text_is_reported_but_scenarios_still_run(self) -> None:
        def describe_source(binpath: Path, contract_id: str) -> str:
            return "old text" if binpath == OLD else "new text"

        with (
            patch("pravrudhi.application.nyaya_pin_regression.list_contract_ids", return_value={"bns69"}),
            patch("pravrudhi.application.nyaya_pin_regression.describe_source", side_effect=describe_source),
            patch.object(reg, "describe_contract_detail", side_effect=_describe),
            patch.object(reg, "check_registry", side_effect=_check),
        ):
            diffs = compare_pins(OLD, NEW)
        assert len(diffs) == 1
        assert "describe-source DIFFERS" in diffs[0]
        assert "bns69" in diffs[0]


class TestElementShapeDiff:
    def test_differing_elements_is_reported_and_scenarios_are_skipped_for_that_id(self) -> None:
        def describe(contract_id: str, *, score_bin: Path) -> reg.DescribedContract:
            elements = ["el0", "el1"] if score_bin == OLD else ["el0", "el1", "el2"]
            return reg.DescribedContract(contract_id=contract_id, elements=elements, denials=["deny0"])

        with (
            patch("pravrudhi.application.nyaya_pin_regression.list_contract_ids", return_value={"bns69"}),
            patch("pravrudhi.application.nyaya_pin_regression.describe_source", return_value="same"),
            patch.object(reg, "describe_contract_detail", side_effect=describe),
            patch.object(reg, "check_registry", side_effect=_check) as mocked_check,
        ):
            diffs = compare_pins(OLD, NEW)
        assert len(diffs) == 1
        assert "describe-contract DIFFERS" in diffs[0]
        mocked_check.assert_not_called()


class TestCheckRegistryDiff:
    def test_a_different_proof_verdict_is_reported(self) -> None:
        def check(assertions: dict[str, bool], contract_id: str, *, score_bin: Path) -> dict:
            result = _check(assertions, contract_id, score_bin=score_bin)
            if score_bin == NEW and set(assertions) == {"el0", "el1"} and all(assertions.values()):
                result = {**result, "verdict": "flagged"}
            return result

        with (
            patch("pravrudhi.application.nyaya_pin_regression.list_contract_ids", return_value={"bns69"}),
            patch("pravrudhi.application.nyaya_pin_regression.describe_source", return_value="same"),
            patch.object(reg, "describe_contract_detail", side_effect=_describe),
            patch.object(reg, "check_registry", side_effect=check),
        ):
            diffs = compare_pins(OLD, NEW)
        assert any("PROOF path DIFFERS" in d for d in diffs)

    def test_a_different_missing_element_omission_is_reported(self) -> None:
        def check(assertions: dict[str, bool], contract_id: str, *, score_bin: Path) -> dict:
            result = _check(assertions, contract_id, score_bin=score_bin)
            if score_bin == NEW and set(assertions) == {"el1"}:
                result = {**result, "omitted_claims": []}  # NEW wrongly fails to flag the dropped element
            return result

        with (
            patch("pravrudhi.application.nyaya_pin_regression.list_contract_ids", return_value={"bns69"}),
            patch("pravrudhi.application.nyaya_pin_regression.describe_source", return_value="same"),
            patch.object(reg, "describe_contract_detail", side_effect=_describe),
            patch.object(reg, "check_registry", side_effect=check),
        ):
            diffs = compare_pins(OLD, NEW)
        assert any("missing-element path (dropped 'el0')" in d for d in diffs)

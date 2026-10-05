"""Row count and sha256 for every fixed-size sealed set this repo can actually see (operator, 2026-09-26).

Why this exists. The fail-open-default sweep found that the #44 second-judge positive control loads its two
sealed control sets with an EXISTENCE check only -- `resolve_private_root` calls `p.is_file()` and nothing
else, `load_control_elements` accepts `{"ids": []}`, the parity floor is a RATE with no minimum n, and the
established-set result is recorded but never gated. So a truncated or emptied `established_200.json` yields
a PASSING verdict, and (with PR #54's trigger wiring) a passing record that unlocks the real second judge.
A count-and-digest assertion at load time makes that unreachable. This file is that assertion for everything
sealed that lives HERE.

THAT PARAGRAPH IS NOW AN ASSERTING TEST, NOT PROSE (issue #100). It was written as a warning before #46
landed, the warning was accurate, and #46 shipped anyway -- so the same words now live in
`TestTheFailOpenThisFileWarnedAboutIsNowClosed` below, as executing assertions that fail if any of the four
holes it named reopens. Prose above a guard does not hold a guard shut; only a test does. #100 verified all
four on `main` at `93c3e02` before the fix: 200 -> 1 -> 0 established elements, every one `available=True`
with `reasons=[]`.

**The limit, stated rather than papered over** (see `TestTheLimitOfWhatCanBeAssertedHere`): the two #44
control sets are private -- they live only in prabhasa-nyaya and are never committed to this public repo --
so their count and digest CANNOT be asserted from here. That assertion has to land in the loader itself or
in prabhasa-nyaya's own tests. The expected shape is written down in this file so whoever implements it does
not have to rediscover it, and a test proves the files really are absent, which is *why* the check cannot
live here. Nothing in this file pretends to cover them.

A note on count-vs-digest: an ENFORCED sha256 pin already subsumes a row count, because a truncated file
cannot rehash to the pinned value. A count matters where there is no digest. Both are asserted below for the
in-repo sets, which carry neither today.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from pravrudhi.application import nyaya_lean_registry as reg
from pravrudhi.application.nyaya_validity import GOLD

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_CONFIG = REPO_ROOT / "configs" / "nyaya_agent.yaml"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _digest(obj: Any) -> str:
    """The sha256 of a canonical JSON rendering -- stable across dict ordering, so the pin below tracks the
    CONTENT of a sealed set and nothing else."""
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _config() -> dict[str, Any]:
    return yaml.safe_load(AGENT_CONFIG.read_text()) or {}


class TestValidatedContractsAllowlist:
    """The 14-id fail-closed safety allowlist (issue #36): the only contracts that may reach a user as a
    real PROOF or DENIAL. A silent addition here widens what the engine will assert about the law, so its
    size and content are pinned, not just its members' existence."""

    EXPECTED_N = 14
    EXPECTED_DIGEST = "a77bfa91d03a43b3957fcf269f6fbb7ca6b96869ccfa040e3241210b5721aa61"

    def test_row_count(self) -> None:
        assert len(_config()["validated_contracts"]) == self.EXPECTED_N

    def test_digest(self) -> None:
        ids = sorted(_config()["validated_contracts"])
        assert _digest(ids) == self.EXPECTED_DIGEST, (
            "configs/nyaya_agent.yaml's validated_contracts changed. A contract joins this set only by "
            "Lead-2's explicit decision after a signed eval covering it -- if that is what happened, update "
            f"EXPECTED_N and EXPECTED_DIGEST here in the same commit. New digest: {_digest(ids)}"
        )

    def test_no_duplicates(self) -> None:
        ids = _config()["validated_contracts"]
        assert len(set(ids)) == len(ids)

    def test_every_id_is_a_real_registry_contract(self) -> None:
        """`load_agent_config` already refuses an unknown id at load time; this pins that the shipped config
        actually satisfies it, so a typo can never validate nothing while looking like 14 ids."""
        assert set(_config()["validated_contracts"]) <= reg.KNOWN_CONTRACT_IDS


class TestKnownContractIds:
    """The pinned registry's own id set. Everything not in here is REFER by default, so its size is a
    safety-relevant number: a pin bump that quietly adds ids changes what the allowlist is measured against.
    """

    EXPECTED_N = 37
    EXPECTED_DIGEST = "3bd625515977c044b7da7abff9c73e7832de6c37d05e4532368310b0325fc763"

    def test_row_count(self) -> None:
        assert len(reg.KNOWN_CONTRACT_IDS) == self.EXPECTED_N

    def test_digest(self) -> None:
        ids = sorted(reg.KNOWN_CONTRACT_IDS)
        assert _digest(ids) == self.EXPECTED_DIGEST, f"new digest: {_digest(ids)}"


class TestHandLabelledGoldSet:
    """`nyaya_validity.GOLD`: the 5-item hand-labelled set `derive_verdict` is scored against. Asserted by
    hand precisely so the checker is not graded with itself, which makes a silent edit here a silent change
    to the only independent check on that function."""

    EXPECTED_N = 5
    EXPECTED_DIGEST = "755601c670637a0ab73daa1bcf506354ae35ba657046e46d0beedf1a384413ba"

    def test_row_count(self) -> None:
        assert len(GOLD) == self.EXPECTED_N

    def test_digest(self) -> None:
        rows = [dict(item) for item in GOLD]
        assert _digest(rows) == self.EXPECTED_DIGEST, f"new digest: {_digest(rows)}"

    def test_every_item_carries_an_expected_label(self) -> None:
        """An item with no `expected` would be scored against nothing and counted as a pass or a fail
        depending on the comparison -- the fail-open shape this whole exercise is about."""
        for item in GOLD:
            assert item.get("expected"), f"gold item {item.get('id')!r} has no expected label"


class TestEnforcedDigestPins:
    """The sealed EVAL artifacts this repo pins by digest. Each assertion is that the pin is well-formed AND
    actually enforced by a refusal -- a pinned constant nobody compares against is decoration."""

    def test_the_lean_score_binary_pin_is_well_formed(self) -> None:
        pinned = _config()["pinned_score_sha256"]
        assert _HEX64.match(str(pinned)), f"pinned_score_sha256 is not a sha256: {pinned!r}"

    def test_the_lean_score_binary_pin_is_enforced_before_any_call(self) -> None:
        """`BinaryRegistry.__init__` refuses a mismatched binary at construction, before any subprocess."""
        source = (REPO_ROOT / "src" / "pravrudhi" / "application" / "nyaya_agent.py").read_text()
        assert "raise BinaryShaMismatch" in source

    def test_the_t2_c3_raw_outputs_pin_is_well_formed_and_enforced(self) -> None:
        source = (REPO_ROOT / "scripts" / "t2_c3_score.py").read_text()
        match = re.search(r'^RAW_SHA256 = "([0-9a-f]+)"', source, re.M)
        assert match, "scripts/t2_c3_score.py no longer pins RAW_SHA256"
        assert _HEX64.match(match.group(1))
        assert "if digest != RAW_SHA256:" in source, "the pin is no longer compared against"
        assert "REFUSING" in source, "a mismatched digest no longer refuses"

    def test_the_t1_parity_prompt_set_pin_is_well_formed_and_enforced(self) -> None:
        source = (REPO_ROOT / "scripts" / "typed_layer_c3_baseline.py").read_text()
        match = re.search(r'^EXPECTED_SHA = "([0-9a-f]+)"', source, re.M)
        assert match, "scripts/typed_layer_c3_baseline.py no longer pins EXPECTED_SHA"
        assert _HEX64.match(match.group(1))
        assert "digest != EXPECTED_SHA" in source, "the pin is no longer compared against"


class TestTheLimitOfWhatCanBeAssertedHere:
    """The #44 control sets. This class asserts the LIMIT, not the sets: it proves they are absent from this
    public repo, which is exactly why their count-and-digest check cannot live here.

    The expected shape, for whoever implements it in the loader or in prabhasa-nyaya's own tests:

        research/gates/P2b/configC/second_judge_positive_control/established_200.json   -> len(ids) == 200
        research/gates/P2b/configC/second_judge_positive_control/ne_discrimination_71.json -> len(ids) == 71
        research/gates/P2b/element_judgment_v1/eval_items.jsonl                          -> digest pinned

    plus, on each row, a `gold_status` in {"established", "not_established"} -- today that field is loaded
    into `ControlElement` and never read by anything, so nothing checks that the NE set is actually gold
    not-established. `resolve_private_root` checks `is_file()` only; `ne_discrimination_min` being an
    ABSOLUTE COUNT (70) is the sole thing protecting the NE half from truncation, and the established half
    has no equivalent.
    """

    PRIVATE_CONTROL_FILES = (
        "research/gates/P2b/configC/second_judge_positive_control/established_200.json",
        "research/gates/P2b/configC/second_judge_positive_control/ne_discrimination_71.json",
        "research/gates/P2b/element_judgment_v1/eval_items.jsonl",
    )

    EXPECTED_PRIVATE_COUNTS = {"established_200.json": 200, "ne_discrimination_71.json": 71}

    def test_the_control_sets_are_not_in_this_public_repo(self) -> None:
        """pravrudhi is PUBLIC: the sealed item ids and reference p values must never be committed here. If
        this ever fails, the digest assertion above becomes both possible AND mandatory -- write it, do not
        delete this test."""
        for rel in self.PRIVATE_CONTROL_FILES:
            assert not (REPO_ROOT / rel).exists(), (
                f"{rel} is present in the public repo. Two things to do, in order: get it out of the "
                f"history, then replace this test with a real count+digest assertion over it."
            )

    def test_the_expected_counts_are_recorded_somewhere_a_reader_will_find_them(self) -> None:
        """The counts are load-bearing (a truncated established set still passes the control), so they are
        written down here rather than living only in a filename."""
        assert self.EXPECTED_PRIVATE_COUNTS == {"established_200.json": 200, "ne_discrimination_71.json": 71}
        assert sum(self.EXPECTED_PRIVATE_COUNTS.values()) == 271, "parity is scored over established + ne"


class TestTheFailOpenThisFileWarnedAboutIsNowClosed:
    """Issue #100: this file's own module docstring (above) named four holes in the #44 control as PROSE,
    before #46 landed, and #46 landed with all four. Each is now an executing assertion.

    Deliberately BEHAVIOURAL wherever the behaviour is reachable from this public repo -- driving the real
    functions rather than grepping for a string, because "a pinned constant nobody compares against is
    decoration" (`TestEnforcedDigestPins`' own words) applies just as much to a test that only reads
    source. The two source-level assertions below are the two facts that are about ABSENCE -- a loader that
    no longer exists, and a manifest field that must stay unpinned -- which nothing behavioural can show.
    """

    def test_hole_one_the_existence_only_check_now_has_a_count_and_digest_check_beside_it(self) -> None:
        """`resolve_private_root` still checks existence, by design -- it is the first gate. What was
        missing was anything after it. `verify_sealed_file` is that: it hashes the file and counts its rows
        against a pin, and refuses a file that is present but truncated."""
        import hashlib as _h
        import json as _j
        import tempfile

        from pravrudhi.application.second_judge_positive_control import (
            SealedControlVerificationError,
            SealedSetPin,
            verify_sealed_file,
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "established_200.json"
            full = _j.dumps({"ids": [{"item_id": f"e{i}", "element_id": "el0",
                                       "sealed_reference_p": 0.99, "gold_status": "established"}
                                      for i in range(200)]})
            path.write_text(full)
            pin = SealedSetPin(name="established_200", relative_path=path.name, kind="json_ids",
                                n_rows=200, sha256=_h.sha256(full.encode()).hexdigest())
            assert verify_sealed_file(path, pin) == 200  # the intact file still verifies
            path.write_text(full[: len(full) // 2])  # now truncated mid-document
            with pytest.raises(SealedControlVerificationError):
                verify_sealed_file(path, pin)

    def test_hole_two_an_empty_ids_list_is_no_longer_accepted(self) -> None:
        """`load_control_elements` accepted `{"ids": []}`. Nothing does now: an empty sealed set refuses."""
        import hashlib as _h
        import json as _j
        import tempfile

        from pravrudhi.application.second_judge_positive_control import (
            SealedControlVerificationError,
            SealedSetPin,
            verify_sealed_file,
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "established_200.json"
            empty = _j.dumps({"ids": []})
            path.write_text(empty)
            # Pinned HONESTLY over the empty file, so the digest matches -- and it is still refused.
            pin = SealedSetPin(name="established_200", relative_path=path.name, kind="json_ids", n_rows=1,
                                sha256=_h.sha256(empty.encode()).hexdigest())
            with pytest.raises(SealedControlVerificationError, match="zero rows"):
                verify_sealed_file(path, pin)

    def test_hole_two_b_the_unverified_loader_is_gone_rather_than_patched(self) -> None:
        """The function that accepted `{"ids": []}` was `load_control_elements` in the preflight script. It
        is removed, not fixed in place, so no caller can reach an unverified load path at all."""
        preflight = (REPO_ROOT / "scripts" / "second_judge_positive_control_preflight.py").read_text()
        assert "def load_control_elements" not in preflight
        assert "load_verified_control_sets" in preflight, (
            "the verified loader is what replaced it; if this name changed, update this assertion to the "
            "new one -- do not delete it"
        )

    def test_hole_three_the_parity_rate_now_has_a_minimum_n_as_a_count_identity(self) -> None:
        """"the parity floor is a RATE with no minimum n". It now has one, and it is an IDENTITY against a
        number read off the sealed file's own pin -- not `len(whatever_loaded)`, which a truncated input
        satisfies by construction."""
        from pravrudhi.application.second_judge_positive_control import (
            ControlElement,
            PinnedCounts,
            decide_availability,
            run_live_check,
        )

        def _healthy(n_est: int) -> object:
            est = [ControlElement(f"e{i}", "el0", 0.99, "established") for i in range(n_est)]
            ne = [ControlElement(f"n{i}", "el0", 0.01, "not_established") for i in range(71)]
            return run_live_check(est, ne, score_fn=lambda el: el.sealed_reference_p, parity_tau=0.97)

        pinned = PinnedCounts(established=200, ne=71, established_sha256="a" * 64, ne_sha256="b" * 64)
        kwargs = {"parity_floor": 0.98, "parity_median_abs_dp": 0.02, "ne_discrimination_min": 70,
                   "pinned": pinned}
        assert decide_availability(_healthy(200), **kwargs).available is True  # type: ignore[arg-type]
        for truncated_to in (199, 1, 0):
            verdict = decide_availability(_healthy(truncated_to), **kwargs)  # type: ignore[arg-type]
            assert verdict.available is False, (
                f"established half truncated to {truncated_to} still reads AVAILABLE -- this is exactly "
                "what issue #100 reproduced on main at 93c3e02"
            )

    def test_hole_four_the_established_set_result_is_now_gated_not_merely_recorded(self) -> None:
        """"the established-set result is recorded but never gated". `decide_availability` did not mention
        `result.established` anywhere in its body. It does now, and an absent established half fails
        closed instead of being ignored."""
        from pravrudhi.application.second_judge_positive_control import (
            ControlCheckResult,
            NEResult,
            ParityResult,
            PinnedCounts,
            decide_availability,
        )

        pinned = PinnedCounts(established=200, ne=71, established_sha256="a" * 64, ne_sha256="b" * 64)
        verdict = decide_availability(
            ControlCheckResult(parity=ParityResult(n_total=271, n_tau_agree=271, median_abs_dp=0.0),
                                ne=NEResult(n_total=71, n_correct=71), established=None),
            parity_floor=0.98, parity_median_abs_dp=0.02, ne_discrimination_min=70, pinned=pinned)
        assert verdict.available is False

    def test_the_private_sets_pin_exists_is_enforced_and_is_now_honestly_pinned(self) -> None:
        """The count-and-digest check the docstring said "has to land in the loader" has landed there, and
        its pins live in `configs/sealed_control_manifest.yaml`. Pinned 2026-09-27 (Lead-2, from
        prabhasa-nyaya add40e6, git objects) -- this test's own premise flipped from "nobody here can
        compute the real ones" to "they are now filled in", so it now asserts the OTHER direction: every
        digest is real digest-shaped (not the sentinel), and loading the manifest SUCCEEDS rather than
        refusing. `test_sealed_pin_unset_is_still_enforced` right below keeps the refusal path covered
        directly, so this file still proves the unset-pin guard works, just not against its own manifest."""
        from pravrudhi.application.second_judge_positive_control import (
            SEALED_PIN_UNSET,
            load_sealed_manifest,
        )

        manifest_path = REPO_ROOT / "configs" / "sealed_control_manifest.yaml"
        assert manifest_path.is_file()
        raw = yaml.safe_load(manifest_path.read_text())
        entries = {e["name"]: e for e in raw["sealed_sets"]}
        assert set(entries) == {"established_200", "ne_discrimination_71", "eval_items_v1"}
        assert entries["established_200"]["n_rows"] == 200
        assert entries["ne_discrimination_71"]["n_rows"] == 71
        assert entries["eval_items_v1"]["n_rows"] == 1519
        for name, entry in entries.items():
            assert entry["sha256"] != SEALED_PIN_UNSET, f"{name} still carries the unset sentinel"
            assert _HEX64.match(str(entry["sha256"])), f"{name}'s sha256 is not a lowercase 64-hex digest"
        pins = load_sealed_manifest(REPO_ROOT)
        assert set(pins) == {"established_200", "ne_discrimination_71", "eval_items_v1"}

    def test_sealed_pin_unset_is_still_enforced(self) -> None:
        """The refusal-on-unset-pin path this file used to prove against its own (then-unpinned) manifest
        still needs direct coverage now that manifest is pinned -- a fresh manifest with one field left as
        the sentinel must still raise, so the guard itself, not just this file's fixture data, is real."""
        from pravrudhi.application.second_judge_positive_control import (
            SealedPinUnset,
            parse_sealed_manifest,
        )

        unset_manifest = """
sealed_sets:
  - name: established_200
    relative_path: research/gates/P2b/configC/second_judge_positive_control/established_200.json
    kind: json_ids
    n_rows: 200
    sha256: UNPINNED
"""
        with pytest.raises(SealedPinUnset):
            parse_sealed_manifest(unset_manifest)

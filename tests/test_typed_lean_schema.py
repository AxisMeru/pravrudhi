"""T3a (docs/decisions/TYPED-LAYER-PLAN-2026-09-24.md design principle 5): generate a typed Schema from a
Lean registry Contract's own `--describe-contract` output, instead of a per-element judge prompt loop
implicitly deciding the same shape. Most of this file is pure mapping over
`nyaya_lean_registry.DescribedContract` fixtures, no live model and no live Lean binary, so it has no
measurement dependency. `TestContractSchemaAgainstTheRealBinary` at the bottom is the one exception: fixtures
alone can't catch drift from the real binary (a constructed `DescribedContract` is exactly what a bug in
`describe_contract_detail`'s own parsing would still satisfy), so it runs `contract_schema` over the pinned
binary's own live output, skipped unless `PRABHASA_NYAYA_SCORE_BIN` is set AND matches the pinned
sha256 -- same no-host-path-default pattern as 4ac21c3, plus the sha pin so a stale or wrong binary can't
produce a silently misleading pass.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from pravrudhi.application import nyaya_lean_registry as reg
from pravrudhi.application.nyaya_judges import _EST_TOKENS, _NOT_TOKENS
from pravrudhi.application.nyaya_lean_registry import DescribedContract
from pravrudhi.application.typed.lean_schema import contract_schema, element_field_name
from pravrudhi.application.typed.schema import FieldKind

#: The pinned build's own sha256 (`configs/nyaya_agent.yaml`'s `pinned_score_sha256`). No host path is
#: committed here -- PRABHASA_NYAYA_SCORE_BIN has no default, and the integration test below skips unless
#: it's set AND the binary it points to matches this exact hash.
#: 2026-09-26: re-pinned to the 37-contract build, assistant/trackA/wave23-integration @636c540.
_PINNED_SCORE_SHA256 = "700de3aadf50b482f4cf23499a03fd518cf72734bf8f51b950b5983555dea229"
_SCORE_BIN = Path(os.environ.get("PRABHASA_NYAYA_SCORE_BIN", "prabhasa-nyaya-score-not-configured"))


def _score_bin_matches_pinned_sha() -> bool:
    if not _SCORE_BIN.exists():
        return False
    return hashlib.sha256(_SCORE_BIN.read_bytes()).hexdigest() == _PINNED_SCORE_SHA256


requires_pinned_registry_scorer = pytest.mark.skipif(
    not _score_bin_matches_pinned_sha(),
    reason=(
        "PRABHASA_NYAYA_SCORE_BIN is not set, missing, or does not match the pinned sha256 "
        f"{_PINNED_SCORE_SHA256} (set it to the pinned 37-contract build to run this integration test)"
    ),
)


def test_one_bool_field_per_required_element_in_order() -> None:
    described = DescribedContract(
        contract_id="bns46_conspiracy",
        elements=["engages in a conspiracy for the doing of the thing", "the thing is itself an offence"],
    )
    cs = contract_schema(described)
    assert [f.name for f in cs.schema.fields] == ["element_0", "element_1"]
    assert all(f.kind == FieldKind.BOOL for f in cs.schema.fields)


def test_fields_use_the_same_token_variants_house_judge_scores() -> None:
    described = DescribedContract(contract_id="bns85", elements=["one element"])
    cs = contract_schema(described)
    field = cs.schema.fields[0]
    assert field.options == {"true": _EST_TOKENS, "false": _NOT_TOKENS}


def test_elements_and_denials_carried_through_unchanged() -> None:
    described = DescribedContract(
        contract_id="ipc405_misappropriation",
        elements=["element A", "element B", "element C"],
        denials=["a valid entrustment defence", "consent of the owner"],
    )
    cs = contract_schema(described)
    assert cs.contract_id == "ipc405_misappropriation"
    assert cs.elements == ("element A", "element B", "element C")
    assert cs.denials == ("a valid entrustment defence", "consent of the owner")


def test_no_denial_becomes_a_decidable_field() -> None:
    described = DescribedContract(contract_id="bns47", elements=["e1"], denials=["d1", "d2"])
    cs = contract_schema(described)
    assert len(cs.schema.fields) == 1  # denials are metadata only, never Schema fields (T3a scope, module doc)


def test_element_text_returns_the_element_a_field_decides() -> None:
    described = DescribedContract(contract_id="bns69", elements=["first element", "second element"])
    cs = contract_schema(described)
    assert cs.element_text("element_0") == "first element"
    assert cs.element_text("element_1") == "second element"


def test_element_text_raises_for_an_unknown_field_name() -> None:
    described = DescribedContract(contract_id="bns69", elements=["only element"])
    cs = contract_schema(described)
    with pytest.raises(KeyError):
        cs.element_text("element_5")


def test_element_field_name_is_deterministic_and_zero_indexed() -> None:
    assert element_field_name(0) == "element_0"
    assert element_field_name(7) == "element_7"


def test_a_contract_with_many_elements_names_every_field() -> None:
    described = DescribedContract(contract_id="bnss187_extended_serious", elements=[f"e{i}" for i in range(8)])
    cs = contract_schema(described)
    assert [f.name for f in cs.schema.fields] == [f"element_{i}" for i in range(8)]
    assert cs.schema.field("element_7") is cs.schema.fields[7]


@pytest.mark.requires_score_bin
@requires_pinned_registry_scorer
class TestContractSchemaAgainstTheRealBinary:
    """Fixtures alone can't catch drift from the real binary -- this reads every known contract's OWN
    `--describe-contract` output live and checks `contract_schema` against it, not against a second
    hand-copied element count."""

    def test_field_count_and_order_match_the_binary_for_every_known_contract(self) -> None:
        mismatches = []
        for contract_id in sorted(reg.KNOWN_CONTRACT_IDS):
            described = reg.describe_contract_detail(contract_id, score_bin=_SCORE_BIN)
            cs = contract_schema(described)
            if len(cs.schema.fields) != len(described.elements):
                mismatches.append(f"{contract_id}: {len(cs.schema.fields)} fields != {len(described.elements)} elements")
                continue
            if [f.name for f in cs.schema.fields] != [element_field_name(i) for i in range(len(described.elements))]:
                mismatches.append(f"{contract_id}: field names out of order")
                continue
            if cs.elements != tuple(described.elements):
                mismatches.append(f"{contract_id}: element text/order does not match the binary's own order")
        assert not mismatches, "\n".join(mismatches)

"""Mechanical validation for id-ref and span fields (T1, docs/decisions/TYPED-LAYER-PLAN-2026-09-24.md
design principles 2-3): id-refs are checked against a fixed candidate set, never fuzzy-matched; spans reuse
`nyaya_quote`'s existing verbatim check verbatim -- never reimplemented.
"""

from __future__ import annotations

import pytest

from pravrudhi.application import nyaya_quote
from pravrudhi.application.typed.schema import Field, FieldKind
from pravrudhi.application.typed.validators import IdRefNotKnown, validate_id_ref, validate_span

FACT_ID = Field(name="fact_id", kind=FieldKind.ID_REF, candidates=("F1", "F2"))


class TestValidateIdRef:
    def test_a_known_candidate_is_accepted(self) -> None:
        assert validate_id_ref(FACT_ID, "F1") == "F1"

    def test_none_means_no_reference_made_and_is_accepted(self) -> None:
        assert validate_id_ref(FACT_ID, None) is None

    def test_a_value_outside_the_candidates_is_refused_never_a_near_match(self) -> None:
        # "F1 " (trailing space) or "f1" (case) are real near-misses a model might actually emit; neither is
        # silently corrected -- the whole point of an id-ref over free text is exactness.
        with pytest.raises(IdRefNotKnown, match="F1 "):
            validate_id_ref(FACT_ID, "F1 ")
        with pytest.raises(IdRefNotKnown, match="f1"):
            validate_id_ref(FACT_ID, "f1")

    def test_refuses_a_non_id_ref_field(self) -> None:
        with pytest.raises(ValueError, match="id_ref"):
            validate_id_ref(Field(name="n", kind=FieldKind.INT), "F1")


class TestValidateSpanReusesNyayaQuote:
    FACTS = {"F1": "The cheque was dishonoured on presentment for insufficient funds."}

    def test_a_real_verbatim_substring_is_valid(self) -> None:
        loc = validate_span(self.FACTS, fact_id="F1", quote="dishonoured on presentment")
        assert loc.valid is True
        assert (loc.start, loc.end) == (15, 41)

    def test_a_non_substring_is_invalid_with_the_real_reason(self) -> None:
        loc = validate_span(self.FACTS, fact_id="F1", quote="never in the text")
        assert loc.valid is False
        assert loc.reason == "quote_not_found"

    def test_unknown_fact_id_is_invalid(self) -> None:
        loc = validate_span(self.FACTS, fact_id="F9", quote="anything")
        assert loc.valid is False
        assert loc.reason == "unknown_fact"

    def test_is_the_exact_same_function_nyaya_quote_exports_not_a_reimplementation(self) -> None:
        # The strongest guarantee this module never drifts from the mechanical check: it IS
        # nyaya_quote.locate_quote, not a lookalike that happens to agree today.
        from pravrudhi.application.typed import validators

        assert validators.validate_span is nyaya_quote.locate_quote

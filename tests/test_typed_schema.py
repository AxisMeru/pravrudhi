"""Schema field kinds for the typed layer (T1, docs/decisions/TYPED-LAYER-PLAN-2026-09-24.md): enum, bool,
int, number, id-ref, span, free-text. A `Field` is a plain, validated description of what one output slot
means -- no decoding logic lives here (that's decoder.py); this module only says what a valid Field looks
like for each kind.
"""

from __future__ import annotations

import pytest

from pravrudhi.application.typed.schema import Field, FieldKind, Schema, bool_field


class TestFieldKinds:
    def test_every_plan_kind_is_a_real_field_kind_value(self) -> None:
        # docs/decisions/TYPED-LAYER-PLAN-2026-09-24.md: "Schema field kinds: enum, bool, int, number,
        # id-ref, span, free-text" -- verbatim, not a paraphrase this test could drift from.
        assert {k.value for k in FieldKind} == {"enum", "bool", "int", "number", "id_ref", "span", "free_text"}


class TestDecisionFields:
    def test_enum_field_needs_options(self) -> None:
        with pytest.raises(ValueError, match="options"):
            Field(name="outcome", kind=FieldKind.ENUM)

    def test_enum_field_with_options_is_valid(self) -> None:
        f = Field(name="outcome", kind=FieldKind.ENUM, options={"a": ("A",), "b": ("B",)})
        assert f.options == {"a": ("A",), "b": ("B",)}

    def test_bool_field_needs_options(self) -> None:
        with pytest.raises(ValueError, match="options"):
            Field(name="claimed", kind=FieldKind.BOOL)

    def test_bool_field_helper_builds_a_two_option_decision_field(self) -> None:
        f = bool_field("status", true_tokens=(" established", "established"), false_tokens=(" not", "not"))
        assert f.kind == FieldKind.BOOL
        assert f.options == {"true": (" established", "established"), "false": (" not", "not")}

    def test_a_decision_field_needs_at_least_two_options(self) -> None:
        with pytest.raises(ValueError, match="at least two"):
            Field(name="outcome", kind=FieldKind.ENUM, options={"only": ("X",)})


class TestIdRefField:
    def test_id_ref_field_needs_candidates(self) -> None:
        with pytest.raises(ValueError, match="candidates"):
            Field(name="fact_id", kind=FieldKind.ID_REF)

    def test_id_ref_field_with_candidates_is_valid(self) -> None:
        f = Field(name="fact_id", kind=FieldKind.ID_REF, candidates=("F1", "F2"))
        assert f.candidates == ("F1", "F2")

    def test_id_ref_candidates_may_not_repeat(self) -> None:
        # A duplicate candidate is a real gap in the field's own construction, not something to silently
        # de-duplicate -- the caller built the candidate list wrong.
        with pytest.raises(ValueError, match="duplicate"):
            Field(name="fact_id", kind=FieldKind.ID_REF, candidates=("F1", "F1"))


class TestSpanField:
    def test_span_field_needs_a_source_id_ref_field_name(self) -> None:
        with pytest.raises(ValueError, match="span_source"):
            Field(name="quote", kind=FieldKind.SPAN)

    def test_span_field_with_source_is_valid(self) -> None:
        f = Field(name="quote", kind=FieldKind.SPAN, span_source="fact_id")
        assert f.span_source == "fact_id"


class TestNumericFields:
    def test_int_field_is_valid_with_no_bounds(self) -> None:
        assert Field(name="count", kind=FieldKind.INT).kind == FieldKind.INT

    def test_number_field_rejects_minimum_above_maximum(self) -> None:
        with pytest.raises(ValueError, match="minimum"):
            Field(name="p", kind=FieldKind.NUMBER, minimum=1.0, maximum=0.0)

    def test_number_field_with_valid_bounds(self) -> None:
        f = Field(name="p", kind=FieldKind.NUMBER, minimum=0.0, maximum=1.0)
        assert (f.minimum, f.maximum) == (0.0, 1.0)


class TestFreeTextField:
    def test_free_text_field_has_a_default_generation_budget(self) -> None:
        f = Field(name="notes", kind=FieldKind.FREE_TEXT)
        assert f.max_tokens > 0

    def test_free_text_field_rejects_a_non_positive_budget(self) -> None:
        with pytest.raises(ValueError, match="max_tokens"):
            Field(name="notes", kind=FieldKind.FREE_TEXT, max_tokens=0)


class TestSchema:
    def test_schema_holds_its_fields_by_name(self) -> None:
        schema = Schema(fields=(Field(name="fact_id", kind=FieldKind.ID_REF, candidates=("F1",)),))
        assert schema.field("fact_id").kind == FieldKind.ID_REF

    def test_schema_rejects_duplicate_field_names(self) -> None:
        with pytest.raises(ValueError, match="duplicate"):
            Schema(
                fields=(
                    Field(name="x", kind=FieldKind.INT),
                    Field(name="x", kind=FieldKind.NUMBER),
                )
            )

    def test_schema_rejects_a_span_field_whose_source_is_not_in_the_schema(self) -> None:
        with pytest.raises(ValueError, match="span_source"):
            Schema(fields=(Field(name="quote", kind=FieldKind.SPAN, span_source="fact_id"),))

    def test_schema_accepts_a_span_field_whose_source_is_present(self) -> None:
        schema = Schema(
            fields=(
                Field(name="fact_id", kind=FieldKind.ID_REF, candidates=("F1",)),
                Field(name="quote", kind=FieldKind.SPAN, span_source="fact_id"),
            )
        )
        assert schema.field("quote").span_source == "fact_id"

    def test_schema_rejects_a_span_source_that_is_not_an_id_ref_field(self) -> None:
        with pytest.raises(ValueError, match="id_ref"):
            Schema(
                fields=(
                    Field(name="notes", kind=FieldKind.FREE_TEXT),
                    Field(name="quote", kind=FieldKind.SPAN, span_source="notes"),
                )
            )

    def test_unknown_field_raises(self) -> None:
        schema = Schema(fields=(Field(name="x", kind=FieldKind.INT),))
        with pytest.raises(KeyError):
            schema.field("y")

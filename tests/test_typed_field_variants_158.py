"""#158: Field refuses option token variants that make `score_decision` meaningless. Constructed inputs only."""

from __future__ import annotations

import pytest

from pravrudhi.application.typed.schema import Field, FieldKind, bool_field


def test_empty_variant_tuple_is_refused() -> None:
    with pytest.raises(ValueError, match="non-empty token variants"):
        bool_field("s", true_tokens=(), false_tokens=(" not",))


def test_a_token_listed_under_two_options_is_refused() -> None:
    with pytest.raises(ValueError, match="both"):
        Field(name="s", kind=FieldKind.ENUM, options={"a": (" x", " y"), "b": (" y", " z")})


def test_blank_token_string_is_refused() -> None:
    with pytest.raises(ValueError, match="non-empty token variants"):
        Field(name="s", kind=FieldKind.ENUM, options={"a": ("",), "b": (" z",)})


def test_non_string_token_is_refused() -> None:
    with pytest.raises(ValueError, match="non-empty token variants"):
        Field(name="s", kind=FieldKind.ENUM, options={"a": (None,), "b": (" z",)})  # type: ignore[arg-type]


def test_a_repeated_token_within_one_option_is_allowed() -> None:
    assert Field(name="s", kind=FieldKind.ENUM, options={"a": (" x", " x"), "b": (" z",)}).options


def test_the_shared_established_not_field_still_constructs() -> None:
    assert bool_field("s", true_tokens=(" established", "established"), false_tokens=(" not", "not")).options

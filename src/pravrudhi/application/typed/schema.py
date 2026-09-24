"""Field kinds for the typed layer's schemas (T1, docs/decisions/TYPED-LAYER-PLAN-2026-09-24.md): enum,
bool, int, number, id-ref, span, free-text -- verbatim from the plan. A `Field` only describes what a valid
output slot looks like for its kind; decoding a value into that slot is decoder.py's job, not this module's.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class FieldKind(StrEnum):
    ENUM = "enum"
    BOOL = "bool"
    INT = "int"
    NUMBER = "number"
    ID_REF = "id_ref"
    SPAN = "span"
    FREE_TEXT = "free_text"


#: enum/bool fields (design principle 1): decided by scoring, never sampled.
_DECISION_KINDS = (FieldKind.ENUM, FieldKind.BOOL)


@dataclass(frozen=True)
class Field:
    """One output slot. Only the attributes its `kind` actually uses are required; the rest stay `None` (or
    the numeric-field default) and are never read for a different kind.
    """

    name: str
    kind: FieldKind
    #: ENUM/BOOL only: value -> the token surface-form variants a tokenizer might realize it as (HouseJudge's
    #: own " established"/"established" pattern), scored at the first generated position.
    options: Mapping[str, tuple[str, ...]] | None = None
    #: ID_REF only: the fixed candidate ids known at request time (fact ids, sentence ids, statute/citation
    #: ids) -- never a value the model is free to invent.
    candidates: tuple[str, ...] | None = None
    #: SPAN only: the name of the ID_REF field in the same Schema naming which candidate's text this span
    #: must be a verbatim substring of.
    span_source: str | None = None
    #: INT/NUMBER only, both optional: inclusive bounds checked after generation.
    minimum: float | None = None
    maximum: float | None = None
    #: FREE_TEXT (and the generation budget for INT/NUMBER when they're read from generated text rather than
    #: scored): how many tokens the decoder may spend on this field.
    max_tokens: int = 256

    def __post_init__(self) -> None:
        if self.kind in _DECISION_KINDS:
            if not self.options:
                raise ValueError(f"{self.kind.value} field {self.name!r} needs options")
            if len(self.options) < 2:
                raise ValueError(f"{self.kind.value} field {self.name!r} needs at least two options, got {list(self.options)}")
        if self.kind == FieldKind.ID_REF:
            if not self.candidates:
                raise ValueError(f"id_ref field {self.name!r} needs candidates")
            if len(set(self.candidates)) != len(self.candidates):
                raise ValueError(f"id_ref field {self.name!r} has duplicate candidates: {self.candidates}")
        if self.kind == FieldKind.SPAN and not self.span_source:
            raise ValueError(f"span field {self.name!r} needs span_source (the id_ref field it quotes from)")
        bounded = self.kind == FieldKind.NUMBER and self.minimum is not None and self.maximum is not None
        if bounded and self.minimum > self.maximum:  # type: ignore[operator]
            raise ValueError(f"number field {self.name!r}: minimum {self.minimum} > maximum {self.maximum}")
        if self.max_tokens <= 0:
            raise ValueError(f"field {self.name!r}: max_tokens must be positive, got {self.max_tokens}")


def bool_field(name: str, *, true_tokens: tuple[str, ...], false_tokens: tuple[str, ...]) -> Field:
    """A two-option decision field with the canonical `"true"`/`"false"` value names -- HouseJudge's own
    established/not_established call is one of these (see house_judge.py)."""
    return Field(name=name, kind=FieldKind.BOOL, options={"true": true_tokens, "false": false_tokens})


@dataclass(frozen=True)
class Schema:
    """A named, ordered set of Fields with no duplicate names, and every SPAN field's `span_source` naming a
    real ID_REF field in the same schema (never a dangling reference, never a source of the wrong kind)."""

    fields: tuple[Field, ...]
    _by_name: dict[str, Field] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        by_name: dict[str, Field] = {}
        for f in self.fields:
            if f.name in by_name:
                raise ValueError(f"duplicate field name {f.name!r}")
            by_name[f.name] = f
        for f in self.fields:
            if f.kind != FieldKind.SPAN:
                continue
            source = by_name.get(f.span_source or "")
            if source is None:
                raise ValueError(f"span field {f.name!r}: span_source {f.span_source!r} is not a field in this schema")
            if source.kind != FieldKind.ID_REF:
                raise ValueError(
                    f"span field {f.name!r}: span_source {f.span_source!r} is a {source.kind.value} field, not id_ref"
                )
        object.__setattr__(self, "_by_name", by_name)

    def field(self, name: str) -> Field:
        return self._by_name[name]

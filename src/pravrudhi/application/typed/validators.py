"""Mechanical validation for id-ref and span fields (T1, docs/decisions/TYPED-LAYER-PLAN-2026-09-24.md
design principles 2-3): schema-valid is necessary, never sufficient, so these checks stay separate from
decoding and always run.
"""

from __future__ import annotations

from pravrudhi.application import nyaya_quote
from pravrudhi.application.typed.schema import Field, FieldKind


class IdRefNotKnown(ValueError):
    """A decoded id-ref value is not among the field's fixed candidates. Never a nearest/fuzzy match -- an
    id the model wrote that isn't a real candidate is a wrong answer, not a typo to silently repair."""


def validate_id_ref(field: Field, value: str | None) -> str | None:
    """`value` must be exactly one of `field.candidates`, or `None` (no reference made)."""
    if field.kind != FieldKind.ID_REF:
        raise ValueError(f"validate_id_ref is only for id_ref fields, got {field.kind.value} ({field.name!r})")
    if value is None:
        return None
    assert field.candidates is not None  # Field.__post_init__ guarantees this for ID_REF
    if value not in field.candidates:
        raise IdRefNotKnown(f"{field.name}: {value!r} is not among the known candidates {field.candidates}")
    return value


#: The span check itself: `nyaya_quote.locate_quote`, reused directly rather than reimplemented (design
#: principle 3) -- `facts` maps an id-ref candidate to its text, exactly as `locate_quote` already expects.
validate_span = nyaya_quote.locate_quote

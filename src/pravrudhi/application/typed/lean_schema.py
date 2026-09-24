"""Typed schema generation from a Lean registry Contract (T3a, docs/decisions/TYPED-LAYER-PLAN-2026-09-24.md
design principle 5): "A contract's element list is a typed schema. Generate the judge schema from the Lean
contract... so the judge, the assembler and the Lean checker share one source of truth."

No live model, no live Lean binary here -- this is a pure mapping from
`nyaya_lean_registry.DescribedContract` (itself already read live from the binary's own
`--describe-contract`, never hand-copied elsewhere) to a `typed.Schema`: one `bool` field per required
element, decided with the SAME established/not_established token variants HouseJudge already scores
(imported from `nyaya_judges`, never re-typed here -- a third hand-copied literal would be exactly the drift
principle 5 exists to remove; `house_judge.py`'s own `_STATUS_FIELD` is the second copy already, this module
does not add a third). This does not change what gets decided: HouseJudge already makes this exact
per-element established/not_established call today; this module names the field set explicitly instead of
leaving it implicit in a per-element prompt loop.

Denials are carried as metadata (`ContractSchema.denials`), never as decidable Schema fields: T3a does not
yet decide how a defeater enters a typed decision (a `DENY: ` line is a refutation, not itself something a
judge is asked established/not_established for), and inventing that shape here ahead of an actual design
would be design-in-code, not schema generation.
"""

from __future__ import annotations

from dataclasses import dataclass

from pravrudhi.application.nyaya_judges import _EST_TOKENS, _NOT_TOKENS
from pravrudhi.application.nyaya_lean_registry import DescribedContract
from pravrudhi.application.typed.schema import Schema, bool_field

#: `contract_schema`'s field-naming convention: `element_<i>`, 0-indexed in the Contract's own element order
#: -- the same order `describe_contract_detail` reports and the per-element judging loop already uses.
ELEMENT_FIELD_PREFIX = "element_"


def element_field_name(index: int) -> str:
    return f"{ELEMENT_FIELD_PREFIX}{index}"


@dataclass(frozen=True)
class ContractSchema:
    """One Lean registry Contract's typed schema: `schema.field(element_field_name(i))` decides
    `elements[i]`'s established/not_established status. `elements`/`denials` are the Contract's own
    natural-language text, carried alongside the Schema so a caller can build each field's judge prompt
    without a second lookup back to the binary."""

    contract_id: str
    schema: Schema
    elements: tuple[str, ...]
    denials: tuple[str, ...]

    def element_text(self, field_name: str) -> str:
        """The natural-language element text the field named `field_name` decides. Raises `KeyError` for a
        name that isn't one of this contract's own fields -- never falls back to a nearest match."""
        field = self.schema.field(field_name)  # raises KeyError for an unknown name
        return self.elements[self.schema.fields.index(field)]


def contract_schema(described: DescribedContract) -> ContractSchema:
    """One `bool` field per required element, in the Contract's own order, decided with the SAME
    established/not_established token variants HouseJudge already scores (design principle 1: scored, never
    sampled)."""
    fields = tuple(
        bool_field(element_field_name(i), true_tokens=_EST_TOKENS, false_tokens=_NOT_TOKENS)
        for i in range(len(described.elements))
    )
    return ContractSchema(
        contract_id=described.contract_id,
        schema=Schema(fields),
        elements=tuple(described.elements),
        denials=tuple(described.denials),
    )

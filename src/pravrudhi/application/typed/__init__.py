"""The typed layer (T1, docs/decisions/TYPED-LAYER-PLAN-2026-09-24.md): a backend-agnostic interface for
structured LLM output, absorbing the TypeLLM idea rather than depending on it (v0.1.4, SGLang-only).

Design principles this package holds itself to (see the plan doc for the full reasoning):

1. Decisions are scored, not sampled. An enum or bool field is decided by the log-probability of each
   allowed option given the prefix -- the same thing HouseJudge already does for its established/
   not_established call. No grammar-renormalised sampling over a decision token, so a calibrated tau keeps
   measuring the same quantity.
2. References are ids, never text. fact_id, sentence_id, statute id and citation id are id-ref fields: enums
   built from the input or the corpus at request time, never free text.
3. Schema-valid is necessary, never sufficient. The mechanical checks (nyaya_quote's span check, reused
   here, never reimplemented) and Lean stay; a well-formed answer can still be wrong.
4. Backend-agnostic: one TypedDecoder protocol, with vLLM and SGLang adapters -- both serve an
   OpenAI-compatible /completions endpoint with first-token top-k logprobs, so the same scoring primitive
   works against either.

This package does not change HouseJudge's default runtime path. `nyaya_judges.HouseJudge` is untouched;
`house_judge.TypedHouseJudge` here is a separate, additional re-expression of the same judge through this
layer, reachable only when a caller explicitly constructs it (see nyaya_agent's `typed_layer` config flag).
"""

from pravrudhi.application.typed.schema import Field, FieldKind, Schema, bool_field

__all__ = ["Field", "FieldKind", "Schema", "bool_field"]

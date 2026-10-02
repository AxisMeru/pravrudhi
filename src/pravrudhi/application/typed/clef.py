"""Clef adapter (E1, research/clef-decoder): a Lean `ContractSchema` as Clef `noul` questions, and a
fail-closed reading of Clef's per-option logits back into one probability per element.

Clef (Cloudflare, Apache-2.0) answers every question of a record in one forward pass and returns logits per
option; `noul` is its yes/no type. This module only builds records and decodes replies -- the transport (the
local `joint_schema_model` or the Workers AI API) is an injected callable, so nothing here touches a network
or a GPU. Nothing is guessed: a missing, extra, non-numeric or non-finite value raises `ClefDecodeError`,
never a default (same rule as `typed.decoder.DecodeError`).

Not wired into `nyaya_agent`; research prototype only.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any

from pravrudhi.application.typed.lean_schema import ContractSchema

#: Clef's documented per-request question limit (Workers AI model page).
MAX_QUESTIONS = 64

#: A `noul` question's two option ids. ASSUMED names until the model code is read (see E2); override per call.
TRUE_OPTION = "true"
FALSE_OPTION = "false"

Record = dict[str, Any]
Transport = Callable[[Record], Mapping[str, Mapping[str, float]]]


class ClefDecodeError(ValueError):
    """A Clef reply could not be read as exactly the answers asked for -- never guessed."""


def build_records(cs: ContractSchema, *, state: str, max_questions: int = MAX_QUESTIONS) -> list[Record]:
    """One `noul` question per required element (denials are never questions), in the Contract's order,
    split into records of at most `max_questions`. Question ids are the typed layer's own field names."""
    if not state.strip():
        raise ValueError("state must not be empty")
    if max_questions < 1:
        raise ValueError(f"max_questions must be >= 1, got {max_questions}")
    items = [(f.name, cs.element_text(f.name)) for f in cs.schema.fields]
    return [
        {"state": state, "questions": {name: {"type": "noul", "instructions": text} for name, text in chunk}}
        for chunk in (items[i : i + max_questions] for i in range(0, len(items), max_questions))
    ]


def noul_probability(options: Mapping[str, float], *, true_option: str = TRUE_OPTION, false_option: str = FALSE_OPTION) -> float:
    """P(true) = softmax over exactly the two option logits."""
    if not isinstance(options, Mapping):
        raise ClefDecodeError(f"noul reply must be a mapping of option -> logit, got {type(options).__name__}")
    if set(options) != {true_option, false_option}:
        raise ClefDecodeError(f"noul options must be exactly {{{true_option!r}, {false_option!r}}}, got {sorted(options)}")
    for k, v in options.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ClefDecodeError(f"noul logit for {k!r} is not a finite number: {v!r}")
    t, f = float(options[true_option]), float(options[false_option])
    m = max(t, f)
    et, ef = math.exp(t - m), math.exp(f - m)
    return et / (et + ef)


def judge_contract(
    cs: ContractSchema, *, state: str, transport: Transport, max_questions: int = MAX_QUESTIONS,
    true_option: str = TRUE_OPTION, false_option: str = FALSE_OPTION,
) -> dict[str, float]:
    """P(established) per element, keyed by field name, in contract order. Every question must be answered
    and nothing else may come back; one bad record fails the whole contract (no partial result). Transport
    errors propagate unchanged."""
    scores: dict[str, float] = {}
    for record in build_records(cs, state=state, max_questions=max_questions):
        reply = transport(record)
        if not isinstance(reply, Mapping):
            raise ClefDecodeError(f"reply must be a mapping of question id -> options, got {type(reply).__name__}")
        asked = list(record["questions"])
        extra = [q for q in reply if q not in record["questions"]]
        if extra:
            raise ClefDecodeError(f"reply answers questions that were not asked: {extra}")
        for q in asked:
            if q not in reply:
                raise ClefDecodeError(f"question {q!r} was not answered")
            scores[q] = noul_probability(reply[q], true_option=true_option, false_option=false_option)
    return scores


def _main(argv: list[str] | None = None) -> int:
    """`python -m pravrudhi.application.typed.clef --elements FILE --state-file FILE [--max-questions N]`:
    print the Clef records (JSON list) for a contract. FILE for --elements is a JSON list of element texts,
    or an object `{"contract_id": ..., "elements": [...], "denials": [...]}`. Offline; scores nothing."""
    import argparse
    import json
    from pathlib import Path

    from pravrudhi.application.nyaya_lean_registry import DescribedContract
    from pravrudhi.application.typed.lean_schema import contract_schema

    ap = argparse.ArgumentParser(prog="clef", description=_main.__doc__)
    ap.add_argument("--elements", required=True, type=Path)
    ap.add_argument("--state-file", required=True, type=Path)
    ap.add_argument("--max-questions", type=int, default=MAX_QUESTIONS)
    args = ap.parse_args(argv)
    raw = json.loads(args.elements.read_text())
    obj = {"contract_id": "cli", "elements": raw, "denials": []} if isinstance(raw, list) else raw
    cs = contract_schema(DescribedContract(str(obj.get("contract_id", "cli")), list(obj["elements"]), list(obj.get("denials", []))))
    records = build_records(cs, state=args.state_file.read_text(), max_questions=args.max_questions)
    print(json.dumps(records, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

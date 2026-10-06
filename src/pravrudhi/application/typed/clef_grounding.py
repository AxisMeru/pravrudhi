"""Clef as a GROUNDING / span-selection backend (#428 step 2, #427 memo): which of the request's facts support a claim,
and the reusable "does a quoted fact tie this accused to this act" check (#618).

Additive and OFF by default. Nothing in `nyaya_agent`, `HouseJudge` or `TypedHouseJudge` imports this module (a test
pins that), and the only switch is `build_clef_grounding(cfg, transport)`, which returns `None` unless
`cfg["clef_grounding"] is True` (the key is read nowhere else; also tested). It does NOT decide established /
not_established (that stays the house judge's scored call). It reuses `clef.py`'s `noul` question type and its fail-closed
reading: a missing, extra, wrong-option-set, bool or non-finite answer raises `ClefDecodeError`, never a default.

WHAT THE SCORE IS. Each fact gets its OWN yes-probability from a separate noul question, so this is a multi-label score,
not a choice: facts do not compete (unlike the #316 ID_REF softmax) and several facts can legitimately support a claim.
The number is an UNCALIBRATED yes-probability per fact, not a probability of grounding. The fact text is IN each
question's instructions (the shared `state` is the caller's context); the question id is the fact id, so two facts with
identical text still get distinct questions.

FAIL-CLOSED = RAISE (Lead-2, as in #316). `select_grounding` raises `UngroundedError` when no fact clears the floor, and
`AmbiguousGrounding` (a subclass) when a SINGLE id is needed (`need_single=True`, the default, because a quote needs one
fact) and two or more facts clear the floor with best-minus-second below the margin; with `need_single=False` all facts
above the floor are returned (multiple support is normal, not ambiguity) and the first in fact order is `best` only on a
tie. Neither class is a `ClefDecodeError` (that means a bad reply). WHEN THIS IS EVER WIRED IN (a later PR, not this
one) the caller must catch these and record "ungrounded" as its own reason: it must NOT fall into the judge_error path,
which the item outcome counts as an ERROR (and ERROR = PROOF in the M4/M4b worst-case bounds). The quote itself stays the
whole fact text, checked by the existing `nyaya_quote` span check; this module never writes one.

THRESHOLDS. `floor` and `margin` are REQUIRED, finite, in [0, 1], with no defaults. They must come from a DEV calibration
on real element-level labels, never from Obj-1b or the seen Obj-1 items, and those labels do not exist yet (the #358
audit). The factory refuses to build an object unless the caller supplies both.

KNOWN GAPS. (1) Only `noul` is used (as `clef.py` does); Clef's reply shape for other question types, and its option names
('true'/'false' are ASSUMED), are unread; a live reply with other option names fails closed. (2) Clef answers every
question of a record in ONE forward pass, so answers can influence each other (order and position effects); the question
order is fixed (fact order given) and the calibration run that sets floor and margin MUST include a reversed-order
permutation check; canned-transport tests cannot show this. (3) `accused_link` is a documented OPTION: the #618 rule is a
frozen regex, so it must not be used in that test without a prereg amendment.

Not wired into the agent; research prototype only.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pravrudhi.application.nyaya_judges import _NARRATIVE_FACT_ID
from pravrudhi.application.typed.clef import (
    FALSE_OPTION,
    MAX_QUESTIONS,
    TRUE_OPTION,
    ClefDecodeError,
    Record,
    Transport,
    noul_probability,
)

MAX_CLAIM_CHARS = 2000
#: the config key that turns the backend on; read ONLY inside `build_clef_grounding`.
FLAG_KEY = "clef_grounding"


class UngroundedError(ValueError):
    """No fact is grounded well enough to quote. Deliberately NOT a ClefDecodeError (that means a bad reply)."""


class AmbiguousGrounding(UngroundedError):
    """Two or more facts clear the floor but the best does not beat the runner-up by the margin, and one id is needed."""


class AccusedNotInFacts(UngroundedError):
    """The accused's name appears in none of the facts, so no fact can tie them to the act (raised before any call)."""


@dataclass(frozen=True)
class GroundingChoice:
    ids_above_floor: tuple[str, ...]  # in the order the facts were given (documented rule: first in id order)
    best: str  # highest probability; ties broken by the order the facts were given
    margin: float  # best minus the second-highest probability of ALL facts (the best itself when there is one fact)
    probs: Mapping[str, float]  # the uncalibrated per-fact yes-probabilities


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


def _check_unit(name: str, v: object) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not (0.0 <= v <= 1.0):  # NaN fails both comparisons
        raise ValueError(f"{name} must be a finite number in [0, 1], got {v!r}")
    return float(v)


def _askable(facts: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for fid, text in facts:
        if fid == _NARRATIVE_FACT_ID:
            continue  # the narrative is never a citable fact
        if not isinstance(fid, str) or not fid.strip():
            raise ValueError(f"fact id must be a non-empty string, got {fid!r}")
        if fid in seen:
            raise ValueError(f"duplicate fact id {fid!r}")
        if not _norm(text):
            raise ValueError(f"fact {fid!r} has no text")
        seen.add(fid)
        out.append((fid, str(text)))
    return out


def _check_claim(claim: str) -> str:
    c = _norm(claim)
    if not c:
        raise ValueError("claim must not be empty")
    if len(c) > MAX_CLAIM_CHARS:
        raise ValueError(f"claim is {len(c)} characters, over the cap {MAX_CLAIM_CHARS}")
    return c


def build_grounding_records(
    facts: Sequence[tuple[str, str]], claim: str, *, state: str, max_questions: int = MAX_QUESTIONS
) -> list[Record]:
    """One `noul` question per fact (F_narrative never), question id = the fact id, fact text in the instructions, in the
    order given, split into records of at most `max_questions` (Clef's documented limit); `state` repeats per record."""
    claim = _check_claim(claim)
    if not _norm(state):
        raise ValueError("state must not be empty")
    if max_questions < 1:
        raise ValueError(f"max_questions must be >= 1, got {max_questions}")
    askable = _askable(facts)
    if not askable:
        raise ValueError("no askable facts (empty, or only F_narrative)")
    items = [(fid, f"Does this fact support the claim: {claim}\nFact: {_norm(text)}") for fid, text in askable]
    return [
        {"state": state, "questions": {fid: {"type": "noul", "instructions": instr} for fid, instr in chunk}}
        for chunk in (items[i : i + max_questions] for i in range(0, len(items), max_questions))
    ]


def decode_grounding_reply(
    record: Record, reply: object, *, true_option: str = TRUE_OPTION, false_option: str = FALSE_OPTION
) -> dict[str, float]:
    """P(yes) per fact for ONE record. Every question must be answered and nothing else may come back."""
    if not isinstance(reply, Mapping):
        raise ClefDecodeError(f"reply must be a mapping of question id -> options, got {type(reply).__name__}")
    extra = [q for q in reply if q not in record["questions"]]
    if extra:
        raise ClefDecodeError(f"reply answers questions that were not asked: {extra}")
    out: dict[str, float] = {}
    for q in record["questions"]:
        if q not in reply:
            raise ClefDecodeError(f"question {q!r} was not answered")
        out[q] = noul_probability(reply[q], true_option=true_option, false_option=false_option)
    return out


def ground_facts(
    facts: Sequence[tuple[str, str]], claim: str, *, state: str, transport: Transport, max_questions: int = MAX_QUESTIONS
) -> dict[str, float]:
    """P(yes) per fact, in the order given. One bad record fails the whole call (no partial result); transport errors
    propagate unchanged."""
    probs: dict[str, float] = {}
    for record in build_grounding_records(facts, claim, state=state, max_questions=max_questions):
        probs.update(decode_grounding_reply(record, transport(record)))
    return probs


def select_grounding(
    probs: Mapping[str, float], *, floor: float, margin: float, need_single: bool = True, order: Sequence[str] | None = None
) -> GroundingChoice:
    """Select or RAISE. `floor` and `margin` are required (no defaults), finite, in [0, 1]. `order` is the fact order
    (default: the mapping's own order) for the id-order and tie-breaking rules. `margin` = best minus the second-highest
    probability of ALL facts; it is enforced only when `need_single` and two or more facts clear the floor."""
    floor, margin = _check_unit("floor", floor), _check_unit("margin", margin)
    ids = list(order) if order is not None else list(probs)
    if set(ids) != set(probs):
        raise ClefDecodeError("order and probabilities name different facts")
    for fid in ids:
        p = probs[fid]
        if isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0.0 <= p <= 1.0:
            raise ClefDecodeError(f"probability for fact {fid!r} is not a finite number in [0, 1]: {p!r}")
    if not ids:
        raise UngroundedError("nothing citable (no facts, or only F_narrative)")
    ranked = sorted(ids, key=lambda f: (-float(probs[f]), ids.index(f)))
    best = ranked[0]
    second = float(probs[ranked[1]]) if len(ranked) > 1 else 0.0
    gap = float(probs[best]) - second
    above = tuple(f for f in ids if float(probs[f]) >= floor)
    if not above:
        raise UngroundedError(f"no fact clears the floor {floor} (max {float(probs[best]):.4g}): ungrounded")
    if need_single and len(above) > 1 and gap < margin:
        raise AmbiguousGrounding(
            f"{len(above)} facts clear the floor but best-minus-second {gap:.4g} is below the margin {margin}"
        )
    return GroundingChoice(above, best, gap, {f: float(probs[f]) for f in ids})


def accused_link(
    facts: Sequence[tuple[str, str]],
    accused: str,
    act: str,
    *,
    state: str,
    transport: Transport,
    floor: float,
    margin: float,
    max_questions: int = MAX_QUESTIONS,
) -> GroundingChoice:
    """Does a fact tie `accused` to `act` (#618; a documented OPTION, see KNOWN GAPS 3)? Raises `AccusedNotInFacts` BEFORE
    any call when the accused's name appears in NO fact (rather than scoring every fact low); otherwise grounds the claim
    "<accused> committed the act: <act>" and selects with `select_grounding` (single id needed)."""
    if not _norm(accused) or not _norm(act):
        raise ValueError("accused and act must not be empty")
    _check_unit("floor", floor), _check_unit("margin", margin)
    askable = _askable(facts)
    needle = _norm(accused).casefold()
    if not any(needle in _norm(text).casefold() for _, text in askable):
        raise AccusedNotInFacts(f"the accused {accused!r} is named in none of the facts: no fact can tie them to the act")
    probs = ground_facts(
        facts, f"{_norm(accused)} committed the act: {_norm(act)}", state=state, transport=transport, max_questions=max_questions
    )
    return select_grounding(probs, floor=floor, margin=margin, order=[f for f, _ in askable])


class ClefGrounding:
    """The enabled backend: the operations bound to one transport and one (floor, margin)."""

    def __init__(self, transport: Transport, *, floor: float, margin: float) -> None:
        self._transport, self.floor, self.margin = transport, _check_unit("floor", floor), _check_unit("margin", margin)

    def ground(self, facts: Sequence[tuple[str, str]], claim: str, *, state: str, need_single: bool = True) -> GroundingChoice:
        probs = ground_facts(facts, claim, state=state, transport=self._transport)
        return select_grounding(
            probs, floor=self.floor, margin=self.margin, need_single=need_single, order=[f for f, _ in _askable(facts)]
        )

    def accused_link(self, facts: Sequence[tuple[str, str]], accused: str, act: str, *, state: str) -> GroundingChoice:
        return accused_link(facts, accused, act, state=state, transport=self._transport, floor=self.floor, margin=self.margin)


def build_clef_grounding(cfg: Mapping[str, object] | None, transport: Transport | None) -> ClefGrounding | None:
    """The one switch. Returns `None` (the default) unless `cfg["clef_grounding"] is True`; when enabled it needs a
    transport and explicit `floor` and `margin` in `cfg` (no defaults), else it raises."""
    if not isinstance(cfg, Mapping) or cfg.get(FLAG_KEY) is not True:
        return None
    if transport is None:
        raise ValueError("clef_grounding is enabled but no transport was given")
    if "floor" not in cfg or "margin" not in cfg:
        raise ValueError(
            "clef_grounding needs explicit `floor` and `margin` (no defaults: they need a DEV calibration on real labels)"
        )
    return ClefGrounding(transport, floor=cfg["floor"], margin=cfg["margin"])  # type: ignore[arg-type]

"""Accused-attribution check, variant M1 (hybrid): a MODEL picks the actor from deterministic candidates; deterministic rules
decide.

D0 found the actor with a closed verb lexicon, which does not generalise (held-out v1: 61% of true proofs refused). M1 keeps what
is
deterministic and moves the one judgement that needs language understanding to a model, as a typed choice:

  1. Hard vetoes first (`nyaya_attribution._veto`: an unlisted co-actor after together with / along with / and; a short-alias
  range).
     A veto refuses with no model call.
  2. Candidates: every actor-like expression in the quote with offsets (the accused's aliases, the other parties the caller names,
     collective forms, kin phrases, and unlisted personal names). Pronouns are NOT offered: the model names the PARTY, so a same-
     sentence
     "he" is resolved by the model, never by a rule.
  3. Selection: `ActorSelector.select` returns the index of the party that performs the act, or None. The shipped selector asks a
  base
     language model (Qwen2.5-32B on the dev Spark; no adapter) for ONE letter and reads the first-token top-k logprobs, so the
     answer is
     always one of the offered options, never free text. Below a probability floor, or with little mass on the options, the answer
     is
     "unresolved".
  4. Decision on the SELECTED candidate with the same deterministic rules as D0: the accused's own alias passes (unless a listed
  party
     is joined to it by a connector); another party refuses (`..._not_matched`); a collective or generic refuses
     (`..._collective`);
     none, a low-confidence pick or an error refuses (`..._unresolved`). Never a pass on an error.

A refusal never changes the element's status; the agent turns it into REFER_TO_LAWYER exactly as for D0. Everything is default
OFF.
No model is called when the answer is already determined (no accused, a veto, no candidate).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from typing import Any, Protocol

from pravrudhi.application.nyaya_attribution import (
    _CONNECTOR,
    MAX_QUOTE_CHARS,
    REASON_COLLECTIVE,
    REASON_NOT_MATCHED,
    REASON_NOT_SPECIFIED,
    REASON_UNRESOLVED,
    AccusedRef,
    AttributionResult,
    Candidate,
    _veto,
    extract_candidates,
)

LETTERS = "ABCDEFGHIJKL"
NONE_LETTER = "N"
MAX_OPTIONS = len(LETTERS)

_TITLE_NAME = re.compile(r"\b(?:Mr|Mrs|Ms|Smt|Shri|Sri|Dr|Kum)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*")
_FULL_NAME = re.compile(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})+\b")
_NOT_NAMES = {
    "accused",
    "petitioner",
    "petitioners",
    "respondent",
    "complainant",
    "court",
    "section",
    "sections",
    "act",
    "code",
    "police",
    "station",
    "india",
    "indian",
    "penal",
    "high",
    "supreme",
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    "hon",
    "ble",
    "judge",
    "magistrate",
    "sessions",
    "criminal",
    "procedure",
    "dowry",
    "prohibition",
}

PROMPT_HEADER = (
    "Task: each item is a sentence from a court record. Choose the party that PERFORMS the harmful act it describes "
    "(the harassment, cruelty, beating, threat or demand). Answer with the letter of that party. "
    f"If the sentence does not say who performs it, answer {NONE_LETTER}.\n\n"
)
FEW_SHOT = (
    ("Dev kicked Nila and abused her in front of the neighbours.", ["Dev", "the neighbours"], "A"),
    ("Nila was slapped by her brother-in-law on several evenings.", ["Nila", "her brother-in-law"], "B"),
    ("Her father-in-law, Mohan, demanded two lakh rupees from her parents.", ["her father-in-law", "Mohan", "her parents"], "B"),
    ("The family members taunted her daily and Rani joined them.", ["The family members", "Rani"], "A"),
    ("It is alleged that she was harassed for dowry.", ["she"], NONE_LETTER),
    ("When Anil and Kavita argued, Anil threw a plate at her.", ["Anil", "Kavita"], "A"),
)


def _options_text(options: list[str]) -> str:
    return "  ".join(f"{LETTERS[i]}. {o}" for i, o in enumerate(options))


def build_prompt(quote: str, options: list[str]) -> str:
    shots = "".join(
        f"Sentence: {s}\nParties: {_options_text(o)}  {NONE_LETTER}. none of these\nAnswer: {a}\n\n" for s, o, a in FEW_SHOT
    )
    tail = f"Sentence: {quote.strip()}\nParties: {_options_text(options)}  {NONE_LETTER}. none of these\nAnswer:"
    return f"{PROMPT_HEADER}{shots}{tail}"


@dataclass(frozen=True)
class Selection:
    index: int | None  # index into the offered options, None = "none of these" or unresolved
    p: float | None
    reason: str | None = None  # why unresolved, when index is None


class ActorSelector(Protocol):
    name: str

    def select(self, quote: str, options: list[str]) -> Selection: ...


class LlmActorSelector:
    """One first-token letter from a completions backend (`TypedDecoder.complete`), read as a distribution over the offered
    letters."""

    def __init__(self, decoder: Any, *, threshold: float = 0.6, mass_floor: float = 0.5, top_logprobs: int = 20) -> None:
        self.decoder = decoder
        self.name = f"llm:{getattr(decoder, 'name', 'decoder')}"
        self.threshold, self.mass_floor, self.top_logprobs = threshold, mass_floor, top_logprobs

    def select(self, quote: str, options: list[str]) -> Selection:
        if not options or len(options) > MAX_OPTIONS:
            return Selection(None, None, "no_options" if not options else "too_many_options")
        res = self.decoder.complete(build_prompt(quote, options), max_tokens=1, temperature=0.0, logprobs=self.top_logprobs)
        if not res.top_logprobs:
            return Selection(None, None, "no_logprobs")
        top = res.top_logprobs[0]
        letters = [*LETTERS[: len(options)], NONE_LETTER]
        mass: dict[str, float] = {}
        for tok, lp in top.items():
            t = tok.strip()
            if t in letters and math.isfinite(lp):
                mass[t] = mass.get(t, 0.0) + math.exp(lp)
        total_all = sum(math.exp(lp) for lp in top.values() if math.isfinite(lp))
        total = sum(mass.values())
        if total <= 0 or total_all <= 0 or total / total_all < self.mass_floor:
            return Selection(None, None, "low_mass_on_options")
        best = max(mass, key=lambda k: mass[k])
        p = mass[best] / total
        if p < self.threshold:
            return Selection(None, p, "low_confidence")
        if best == NONE_LETTER:
            return Selection(None, p, "none_of_these")
        return Selection(letters.index(best), p)


def extract_options(quote: str, accused: AccusedRef) -> list[Candidate]:
    """D0's candidates minus pronouns and bare "they", plus unlisted personal names. Sorted by position."""
    cands = [c for c in extract_candidates(quote, accused) if c.role != "pronoun" and c.text.lower() not in ("they", "them")]
    taken = [(c.start, c.end) for c in cands]

    def free(m: re.Match[str]) -> bool:
        return not any(m.start() < e and s < m.end() for s, e in taken)

    for pat in (_TITLE_NAME, _FULL_NAME):
        for m in pat.finditer(quote):
            words = [w.lower().strip(".") for w in m.group(0).split()]
            if free(m) and not any(w in _NOT_NAMES for w in words):
                cands.append(Candidate(m.start(), m.end(), m.group(0), "other", f"NAME:{' '.join(words)}"))
                taken.append((m.start(), m.end()))
    return sorted(cands, key=lambda c: c.start)


def _joined_to_other(quote: str, cands: list[Candidate], chosen: Candidate) -> bool:
    """A listed other party, collective or generic joined to the chosen actor by a bare connector (", and, along with, ...")."""
    for c in cands:
        if c is chosen or c.role not in ("other", "collective", "generic"):
            continue
        gap = (
            quote[c.end : chosen.start]
            if c.end <= chosen.start
            else quote[chosen.end : c.start]
            if chosen.end <= c.start
            else None
        )
        if gap is not None and _CONNECTOR.match(gap):
            return True
    return False


def check_attribution_m1(quote: str | None, accused: AccusedRef | None, selector: ActorSelector) -> AttributionResult:
    """The M1 decision. Never raises: any error is a refusal (R5)."""
    v = "M1"
    if accused is None:
        return AttributionResult(False, REASON_NOT_SPECIFIED, variant=v, rule="R0")
    try:
        if not isinstance(quote, str) or not quote.strip() or len(quote) > MAX_QUOTE_CHARS:
            return AttributionResult(False, REASON_UNRESOLVED, variant=v, rule="R1", error="empty or oversized quote")
        veto = _veto(quote, accused)
        if veto is not None:
            return AttributionResult(False, REASON_COLLECTIVE, variant=v, rule=veto)
        cands = extract_options(quote, accused)
        base: dict[str, Any] = {
            "variant": v,
            "n_candidates": len(cands),
            "candidates": tuple(
                {"text": c.text, "start": c.start, "end": c.end, "role": c.role, "identity": c.identity} for c in cands
            ),
        }
        if not cands:
            return AttributionResult(False, REASON_UNRESOLVED, rule="M1-no-candidate", **base)
        sel = selector.select(quote, [c.text for c in cands])
        if sel.index is None:
            return AttributionResult(False, REASON_UNRESOLVED, rule=f"M1-{sel.reason or 'unresolved'}", **base)
        chosen = cands[sel.index]
        common = {"actor_span": chosen.text, "actor_start": chosen.start, "actor_end": chosen.end, **base}
        res: AttributionResult
        if chosen.role in ("collective", "generic"):
            res = AttributionResult(False, REASON_COLLECTIVE, rule="M1-R3", **common)
        elif chosen.role == "self":
            if _joined_to_other(quote, cands, chosen):
                res = AttributionResult(False, REASON_COLLECTIVE, rule="M1-R3", **common)
            else:
                res = AttributionResult(True, None, rule=None, **common)
        else:
            res = AttributionResult(False, REASON_NOT_MATCHED, rule="M1-R2", **common)
        return replace(res, actor_p=sel.p)
    except Exception as e:  # noqa: BLE001 -- fail closed: an error is a refusal, never a pass
        return AttributionResult(False, REASON_UNRESOLVED, variant=v, rule="R5", error=f"{type(e).__name__}: {e}"[:300])

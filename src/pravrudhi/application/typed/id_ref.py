"""ID_REF scoring for the judge's fact id (B4, research/clef-decoder; R-2 gap 1).

`TypedHouseJudge` decides established/not by SCORING, but reads the fact id by parsing the greedy completion
(`nyaya_judges.parse_house_fact_id`), so a hallucinated or near-miss id is never constrained. Here the id is an
ID_REF field decided the same way the decision is: each candidate id (the request's fact ids, never
`F_narrative`) is scored as the continuation `" <id>:"` of `prompt + " established"`, and the choice is the softmax over
candidates. Nothing is repaired: non-finite or impossible logprobs, an exact tie, or too little probability mass
on the candidate set (the model wanted some other string) raise `IdRefDecodeError`.

ASSUMPTIONS to verify before any live use: (1) the training format writes `established <fact_id>` followed by
`:` or the end, so the terminator `:` separates F1 from F10; (2) a `scorer` returning the total logprob of a
continuation is available -- `continuation_logprob_from_echo` parses vLLM's `echo` + `logprobs` shape, checked
here only against a canned response, NOT a live server.

Not wired into `nyaya_agent` or `TypedHouseJudge`; research prototype only.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pravrudhi.application.nyaya_judges import _NARRATIVE_FACT_ID, ElementJudgment, JudgeRequest
from pravrudhi.application.typed.schema import Field, FieldKind

#: `(prompt, continuation) -> total log-probability of `continuation` given `prompt``.
Scorer = Callable[[str, str], float]

TERMINATOR = ":"
#: The model writes `established F1:` with the space belonging to the id's first token (" F"), so the space goes in
#: the continuation, not the prefix (live check against the dev 4B, 2026-10-02: a prefix ending in a space makes the
#: first token straddle the boundary).
LEAD = " "


class IdRefDecodeError(ValueError):
    """The fact id could not be decided from the scores -- never guessed or repaired."""


@dataclass(frozen=True)
class IdRefChoice:
    best: str
    probs: Mapping[str, float]  # softmax over the candidates only
    mass: float  # sum of exp(logprob) over candidates: the model's own probability on the candidate set
    margin: float  # best minus runner-up probability (1.0 when there is a single candidate)


def score_id_ref(
    field: Field, *, prefix: str, scorer: Scorer, mass_floor: float, terminator: str = TERMINATOR, lead: str = LEAD
) -> IdRefChoice:
    """Decide an ID_REF field by scoring each candidate (+ terminator) after `prefix`."""
    if field.kind != FieldKind.ID_REF or not field.candidates:
        raise ValueError(f"score_id_ref is only for id_ref fields with candidates, got {field.kind.value} ({field.name!r})")
    if not (isinstance(mass_floor, (int, float)) and 0.0 <= mass_floor <= 1.0):  # NaN fails both comparisons
        raise ValueError(f"mass_floor must be in [0, 1], got {mass_floor!r}")
    lps: dict[str, float] = {}
    for cand in field.candidates:
        lp = scorer(prefix, lead + cand + terminator)
        if not isinstance(lp, (int, float)) or isinstance(lp, bool) or not math.isfinite(lp) or lp > 0:
            raise IdRefDecodeError(f"{field.name}: logprob for {cand!r} is not a finite value <= 0: {lp!r}")
        lps[cand] = float(lp)
    m = max(lps.values())
    exps = {c: math.exp(v - m) for c, v in lps.items()}
    z = sum(exps.values())
    probs = {c: e / z for c, e in exps.items()}
    mass = sum(math.exp(v) for v in lps.values())
    if mass < mass_floor:
        raise IdRefDecodeError(
            f"{field.name}: candidate mass {mass:.4g} is below the floor {mass_floor}; the model did not choose a listed id"
        )
    ranked = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        raise IdRefDecodeError(f"{field.name}: exact tie between {ranked[0][0]!r} and {ranked[1][0]!r}")
    margin = ranked[0][1] - ranked[1][1] if len(ranked) > 1 else 1.0
    return IdRefChoice(ranked[0][0], probs, mass, margin)


def rescore_fact_id(
    judgment: ElementJudgment, request: JudgeRequest, *, prompt: str, scorer: Scorer, mass_floor: float
) -> tuple[ElementJudgment, IdRefChoice | None]:
    """For an `established` judgment, replace the greedy-parsed fact id by the enum choice over the request's
    fact ids (granularity `whole_fact`, as the typed house judge already uses). A non-`established` judgment is returned
    as is with no choice. An `established` judgment with NO candidate fact ids (empty, or `F_narrative` only) has nothing
    it may cite, so its greedy-parsed id and quote are a hallucination the module exists to stop: it raises
    `IdRefDecodeError`. Errors propagate; the parsed id is never used as a fallback."""
    if judgment.status != "established":
        return judgment, None
    candidates = tuple(fid for fid, _ in request.facts if fid != _NARRATIVE_FACT_ID)
    if not candidates:
        raise IdRefDecodeError(
            f"established judgment (parsed id {judgment.fact_id!r}) but the request has no candidate fact ids: "
            "nothing it may cite"
        )
    field = Field("fact_id", FieldKind.ID_REF, candidates=candidates)
    choice = score_id_ref(field, prefix=prompt + " established", scorer=scorer, mass_floor=mass_floor)
    text = dict(request.facts)[choice.best]
    return dataclasses.replace(judgment, fact_id=choice.best, quote=text, quote_source="whole_fact"), choice


def continuation_logprob_from_echo(resp: Mapping[str, Any], *, prefix_len: int, end_len: int) -> float:
    """Total logprob of the text in `[prefix_len, end_len)` characters, from an OpenAI/vLLM `/completions` reply made
    with `echo=true, logprobs=0`: sums `token_logprobs` of tokens whose `text_offset` lies in that range. The server
    also appends the token(s) it GENERATES (max_tokens >= 1) after the echoed prompt (seen live); `end_len`
    excludes them. A token straddling either boundary, a null logprob inside the continuation, or an empty
    continuation raises, as does a non-integer offset, a non-string token, or any continuation logprob that is not a
    finite number <= 0 (a positive logprob is impossible)."""
    try:
        lg = resp["choices"][0]["logprobs"]
        tokens, lps, offs = lg["tokens"], lg["token_logprobs"], lg["text_offset"]
    except (KeyError, IndexError, TypeError) as e:
        raise IdRefDecodeError(f"echo reply is not in the expected shape: {e!r}") from e
    if not (len(tokens) == len(lps) == len(offs)):
        raise IdRefDecodeError("echo reply arrays differ in length")
    total, n = 0.0, 0
    for tok, lp, off in zip(tokens, lps, offs, strict=True):
        if not isinstance(tok, str) or not isinstance(off, int) or isinstance(off, bool):
            raise IdRefDecodeError(f"token {tok!r} has a non-string text or a non-integer offset {off!r}")
        if off < prefix_len < off + len(tok):
            raise IdRefDecodeError(f"token {tok!r} straddles the prefix boundary at {prefix_len}")
        if off < end_len < off + len(tok):
            raise IdRefDecodeError(f"token {tok!r} straddles the continuation end at {end_len}")
        if prefix_len <= off < end_len:
            if lp is None:
                raise IdRefDecodeError(f"null logprob for continuation token {tok!r}")
            if not isinstance(lp, (int, float)) or isinstance(lp, bool) or not math.isfinite(lp) or lp > 0:
                raise IdRefDecodeError(f"logprob for continuation token {tok!r} is not a finite value <= 0: {lp!r}")
            total += lp
            n += 1
    if n == 0:
        raise IdRefDecodeError("echo reply has no continuation tokens")
    return total

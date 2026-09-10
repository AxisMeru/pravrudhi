"""Scoring for the legal-hallucination tasks: precision separated from abstention, on purpose.

The product objective's intent is "answers a question of law with the statute or precedent it relied on, and
says it does not know rather than inventing a citation". Accuracy alone cannot express that: a model that
invents a citation every time and a model that declines every time can score identically badly, and the
second is the one a lawyer can use. So each item reports three things and the aggregation keeps them apart:

* `citation_precision` -- correct out of ANSWERED. What the answers it actually gave are worth.
* `citation_abstention` -- how often it said, in words, that it did not know. The behaviour the objective wants.
* `citation_unparsed`  -- how often it produced neither an answer nor a refusal. A harness failure, not a virtue.
* `exact_match`        -- correct out of ALL. Kept so the four are reconcilable.

The split between the last two is the point, and the first version of this file got it wrong. Both an
explicit "I do not know" and a model rambling past its token budget yield no citation, and collapsing them
credits the rambling model with the honesty of the careful one. Measured on eight items with a 16-token
budget, `affirm_reverse` reported abstention 0.5 while the logged outputs showed no refusals at all -- the
model was simply cut off mid-sentence. Four outcomes, and `citation_unparsed` must be near zero before any
of the others is worth reading.

Normalisation strips spaces, periods and commas and lowercases, so "470 F.2d 798", "470 F. 2d 798" and
"470 f.2d 798," are one answer. It does not attempt parallel-citation equivalence: a case reported in two
reporters has two correct citations and only one is in the gold, which is a known undercount and is recorded
as such rather than papered over with a fuzzy match.
"""

from __future__ import annotations

import re
from typing import Any

# A citation shaped like <volume> <reporter> <page>: 470 F.2d 798, 122 U.S. 326, 39 Fla. 536,
# 627 F. Supp. 44, 148 L. Ed. 2d 1355.
#
# The reporter must admit DIGITS. The first version of this pattern was `[A-Z][A-Za-z.]*`, which cannot match
# `F.2d` or `S.W.3d` -- so every circuit citation, which is what this task consists of, was read as no answer
# at all and the precision figure meant nothing. A trailing series designator (`2d`, `3d`) is allowed its own
# token so `L. Ed. 2d 1355` parses as reporter `L. Ed. 2d` and page `1355` rather than failing outright.
CITATION = re.compile(
    r"\b(\d{1,4})\s+"
    r"([A-Z][A-Za-z0-9.]*(?:\s+[A-Za-z][A-Za-z0-9.]*)*(?:\s+\d(?:d|th|st|nd)\.?)?)"
    r"\s+(\d{1,4})\b"
)
YEAR = re.compile(r"\b(1[6-9]\d{2}|20\d{2})\b")
AFFIRM_REVERSE = re.compile(r"(?i)\b(affirm|reverse)")

# Said in place of an answer. An abstention is a correct behaviour, not a failure to parse.
DECLINED = re.compile(
    r"(?i)\b(i (?:do not|don't) know|cannot determine|can't determine|unable to (?:determine|find|locate)"
    r"|no (?:information|record)|not (?:aware|able to)|insufficient information|unknown)\b"
)


def _norm(text: str) -> str:
    return re.sub(r"[\s.,]", "", text).lower()


def _first_line(completion: str) -> str:
    for line in completion.splitlines():
        if line.strip():
            return line.strip()
    return ""


def extract_citation(completion: str) -> str | None:
    if DECLINED.search(completion):
        return None
    m = CITATION.search(completion)
    return f"{m.group(1)} {m.group(2)} {m.group(3)}" if m else None


def extract_year(completion: str) -> str | None:
    if DECLINED.search(completion):
        return None
    m = YEAR.search(completion)
    return m.group(1) if m else None


def extract_affirm_reverse(completion: str) -> str | None:
    if DECLINED.search(completion):
        return None
    m = AFFIRM_REVERSE.search(_first_line(completion) or completion)
    return m.group(1).lower() if m else None


def _result(pred: str | None, gold: str, *, declined: bool) -> dict[str, Any]:
    answered = pred is not None
    correct = bool(answered and pred is not None and _norm(pred) == _norm(gold))
    return {
        "citation_precision": (float(correct), float(answered)),
        "citation_abstention": float(declined and not answered),
        "citation_unparsed": float(not answered and not declined),
        "exact_match": float(correct),
    }


def process_citation(doc: dict[str, Any], results: list[str]) -> dict[str, Any]:
    gold = str(doc["example_correct_answer"])
    return _result(extract_citation(results[0]), gold, declined=bool(DECLINED.search(results[0])))


def process_year(doc: dict[str, Any], results: list[str]) -> dict[str, Any]:
    gold = str(doc["example_correct_answer"])
    return _result(extract_year(results[0]), gold, declined=bool(DECLINED.search(results[0])))


def process_affirm_reverse(doc: dict[str, Any], results: list[str]) -> dict[str, Any]:
    gold = str(doc["example_correct_answer"])
    return _result(extract_affirm_reverse(results[0]), gold, declined=bool(DECLINED.search(results[0])))


def precision(items: list[tuple[float, float]]) -> float:
    """Correct over answered, as a ratio of sums rather than a mean of ratios.

    A per-item mean would weight an item the model did not answer the same as one it did, which is the
    arithmetic that makes precision and abstention indistinguishable.
    """
    answered = sum(a for _, a in items)
    return (sum(c for c, _ in items) / answered) if answered else 0.0

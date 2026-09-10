"""Multiple-choice scorer: deterministic option-letter match. Gold is one option letter; prediction is the
letter the completion commits to.

ADR-REF: ADR-0035. Sibling of `gsm8k.py`, exposing the same four functions over letter answers so a pool whose
manifest declares `answer_kind: choice` can be scored at all. Before this module the engine had exactly one
scorer, numeric by construction, and `gold_answer("A")` raised — so no multiple-choice objective could run a
single night however much GPU was available.

Two deliberate strictnesses, both learned from this repository's own failures:

* `gold_answer` raises on anything that is not one option letter. A numeric gold reaching this scorer means a
  mis-sealed pool, and scoring every item 0 would be indistinguishable from a model that knows nothing.
* `extract_prediction` returns `None` rather than guessing. A case-insensitive letter class reads the article
  in "the answer is a matter of settled precedent" as a confident vote for option A, which would credit a
  refusal as a one-in-four chance. The bare `answer is X` form therefore accepts only an upper-case letter;
  lower case is accepted only after an explicit `:` or `=`, or inside `\\boxed{}`.

Format misses and wrong answers are the confound that makes a generated multiple-choice metric untrustworthy:
both score 0, and only one of them is about the model's knowledge. `metrics.unparsed` names the misses for
any scorer, so the two can be told apart afterwards.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

# The widest option set in use: MMLU has four, MMLU-Pro has ten.
LETTERS = "ABCDEFGHIJ"

_GOLD = re.compile(r"^\(?([A-Ja-j])\)?$")

# Tried in order; within a pattern the LAST match wins, so a closing commitment beats an opening aside.
_EXPLICIT = re.compile(r"(?i:\banswers?\b)[*_\s]*(?i:is\b)?[*_\s]*[:=][*_\s]*\(?([A-Ja-j])\)?(?![A-Za-z])")
_BOXED = re.compile(r"\\boxed\{[*_\s]*\(?([A-Ja-j])\)?[*_\s]*\}")
_BARE = re.compile(r"(?i:\banswers?\b)[*_\s]*(?i:is\b)?[*_\s]*\(?([A-J])\)?(?![A-Za-z])")
_LABELLED = re.compile(r"(?i:\b(?:option|choice)\b)[*_\s]*(?i:is\b)?[*_\s]*\(?([A-J])\)?(?![A-Za-z])")
_ALONE = re.compile(r"^[*_\s]*\(?([A-Ja-j])\)?[).:,]?[*_\s]*$")

_PATTERNS = (_EXPLICIT, _BOXED, _BARE, _LABELLED)


def gold_answer(answer_text: str) -> str:
    m = _GOLD.match(answer_text.strip())
    if not m:
        raise ValueError(f"gold answer is not one option letter ({LETTERS}): {answer_text!r}")
    return m.group(1).upper()


def extract_prediction(completion: str) -> str | None:
    for pattern in _PATTERNS:
        found = pattern.findall(completion)
        if found:
            return str(found[-1]).upper()
    lines = [ln for ln in completion.splitlines() if ln.strip()]
    if lines:
        m = _ALONE.match(lines[-1])
        if m:
            return m.group(1).upper()
    return None


def score_item(completion: str, gold: str) -> int:
    pred = extract_prediction(completion)
    return int(pred is not None and pred == gold_answer(gold))


def score_completions(completions: Mapping[str, str], golds: Mapping[str, str]) -> dict[str, int]:
    """Per-item 0/1 scores keyed by item id. Missing completions score 0 (a crash is a miss, not an
    exclusion)."""
    return {i: (score_item(completions[i], g) if i in completions else 0) for i, g in golds.items()}

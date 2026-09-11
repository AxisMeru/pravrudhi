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

#: "I" is both an option letter and the first-person pronoun, and the pronoun is followed by a verb.
#:
#: ADR-0042. `Answer: I do not know` parsed as a confident vote for option I, because `_EXPLICIT` takes the
#: letter after `answer:` and the `I` is followed by a space. Found on the casehold-val bench, where a
#: candidate prompted to abstain produced five of them: the letters came back
#: `{A: 21, B: 11, C: 8, D: 17, E: 13, I: 5}` on a pool with five options.
#:
#: It corrupts no score -- an out-of-range letter is wrong exactly as an unparsed answer is -- but it corrupts
#: the DIAGNOSIS, counting refusals as wrong answers. Refusal-versus-wrong is the distinction this project's
#: `citation_abstention` metric exists to measure (0.0000 at n=2444), and a scorer that reads "I do not know"
#: as a letter cannot measure abstention at all.
#:
#: Deliberately narrow. `Answer: I`, `ANSWER: I.` and `The answer is I` remain votes for option I, because on
#: a ten-option pool they are exactly that. Only a following lowercase word -- the verb that makes it a
#: pronoun -- disqualifies the match.
_PRONOUN_I = re.compile(r"^\s+[a-z]{2,}")

#: A labelled option: a line that opens with the letter, punctuated as a label, and then the option's text --
#: `D. holding that absent an actionable injury ...`, `D) the claim fails`. One of the two commonest ways a
#: model answers a multiple-choice question, and on CaseHOLD the way this project's model actually answers.
#:
#: ADR-0046. Every pattern above needs the word "answer", "option" or "choice", and `_ALONE` needs the letter
#: to stand by itself, so this shape scored 0 from the day the scorer was written (ADR-0035). The external
#: casehold tier found it: the SAME 500 completions scored 0.5040 with an independent parser and 0.0040 here.
#: The punctuation is what makes it a label rather than a word: `A holding` is prose, `A. holding` is option A.
_OPTION_LABEL = re.compile(r"^[*_\s]*\(?([A-J])[.)]\s+\S")


def gold_answer(answer_text: str) -> str:
    m = _GOLD.match(answer_text.strip())
    if not m:
        raise ValueError(f"gold answer is not one option letter ({LETTERS}): {answer_text!r}")
    return m.group(1).upper()


def _disqualified(completion: str, match: re.Match[str]) -> bool:
    """Whether this match is the pronoun "I" rather than option I. See `_PRONOUN_I`."""
    return match.group(1).upper() == "I" and bool(_PRONOUN_I.match(completion[match.end() :]))


def extract_prediction(completion: str) -> str | None:
    for pattern in _PATTERNS:
        # `finditer`, not `findall`: the guard needs to see what FOLLOWS a match, and the last surviving
        # match still wins so a closing commitment beats an opening aside.
        found = [m for m in pattern.finditer(completion) if not _disqualified(completion, m)]
        if found:
            return str(found[-1].group(1)).upper()
    lines = [ln for ln in completion.splitlines() if ln.strip()]
    if lines:
        m = _ALONE.match(lines[-1])
        if m:
            return m.group(1).upper()
        # A labelled option; the last such line wins, as with the patterns above.
        labelled = [m for m in (_OPTION_LABEL.match(ln) for ln in lines) if m]
        if labelled:
            return str(labelled[-1].group(1)).upper()
    return None


def score_item(completion: str, gold: str) -> int:
    pred = extract_prediction(completion)
    return int(pred is not None and pred == gold_answer(gold))


def score_completions(completions: Mapping[str, str], golds: Mapping[str, str]) -> dict[str, int]:
    """Per-item 0/1 scores keyed by item id. Missing completions score 0 (a crash is a miss, not an
    exclusion)."""
    return {i: (score_item(completions[i], g) if i in completions else 0) for i, g in golds.items()}

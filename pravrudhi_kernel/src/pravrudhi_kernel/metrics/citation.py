"""Free-text scorer: normalised exact match over a legal citation.

ADR-REF: ADR-0036. Third sibling of `gsm8k.py` (numbers) and `mmlu.py` (option letters), reusing the dispatch
point ADR-0035 added rather than introducing a second mechanism.

Why it exists. The product objective's real metric is citation recall, measured at `citation_precision` 0.0000
over n=1000. Two corpora were built and neither can move it: CaseHOLD asks which of five given holdings is
correct, and recall is not discrimination. A recall corpus does exist -- 13,531 distinct case-to-citation pairs
in `reglab/legal_hallucinations` -- but its gold is a citation, which `gsm8k.gold_answer` rejects for lacking
'####' and `mmlu.gold_answer` rejects for not being one letter. So rejection sampling could not verify a single
row of it, and the corpus was unusable however good.

Normalisation drops spaces, periods and commas and lowercases, so `470 F.2d 798`, `470 F. 2d 798` and
`470 f.2d 798,` are one answer. It does NOT attempt parallel-citation equivalence: a case reported in two
reporters has two correct citations and the gold carries one, so this undercounts. That limit is real, it is
the same one the external lm-eval metric has, and a fuzzy matcher would be a judgement call wearing the
clothes of a measurement.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

# <volume> <reporter> <page>: 470 F.2d 798, 122 U.S. 326, 39 Fla. 536, 627 F. Supp. 44, 148 L. Ed. 2d 1355.
#
# The reporter admits DIGITS, because `F.2d` and `S.W.3d` contain them and those are most of what a circuit
# citation is; a pattern without them reads every such citation as no answer at all. Its first character must
# be upper case, which is the guard that keeps ordinary prose out: "decided in 2015 by 3 judges" would
# otherwise parse as volume 2015, reporter "by", page 3. A trailing series designator gets its own token so
# `L. Ed. 2d 1355` parses rather than failing.
CITATION = re.compile(
    r"\b(\d{1,4})\s+"
    r"([A-Z][A-Za-z0-9.]*(?:\s+[A-Za-z][A-Za-z0-9.]*)*(?:\s+\d(?:d|th|st|nd)\.?)?)"
    r"\s+(\d{1,4})\b"
)

# Said in place of an answer. A refusal is not a parse failure, and it is not a correct answer either.
DECLINED = re.compile(
    r"(?i)\b(i (?:do not|don't) know|cannot determine|can't determine|unable to (?:determine|find|locate)"
    r"|no (?:information|record)|not (?:aware|able to)|insufficient information|unknown)\b"
)


def _norm(text: str) -> str:
    return re.sub(r"[\s.,]", "", text).lower()


def gold_answer(answer_text: str) -> str:
    """The gold as written, refusing an empty one.

    An empty gold would make every completion correct by vacuity, which is the worst direction in which to
    fail: a mis-sealed pool would report a perfect score.
    """
    stripped = answer_text.strip()
    if not stripped:
        raise ValueError("gold answer is empty, so nothing could be wrong against it")
    return stripped


def extract_prediction(completion: str) -> str | None:
    """The FIRST citation in the completion, or None.

    First rather than last: a model that answers and then echoes the prompt's few-shot examples ends on one of
    the examples, so the last citation is usually not its answer.
    """
    if DECLINED.search(completion):
        return None
    m = CITATION.search(completion)
    return f"{m.group(1)} {m.group(2)} {m.group(3)}" if m else None


def score_item(completion: str, gold: str) -> int:
    pred = extract_prediction(completion)
    return int(pred is not None and _norm(pred) == _norm(gold_answer(gold)))


def score_completions(completions: Mapping[str, str], golds: Mapping[str, str]) -> dict[str, int]:
    """Per-item 0/1 scores keyed by item id. Missing completions score 0 (a crash is a miss, not an
    exclusion)."""
    return {i: (score_item(completions[i], g) if i in completions else 0) for i, g in golds.items()}

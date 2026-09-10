"""Multi-label scorer for statute identification: per-item Jaccard over a set of section labels.

ADR-REF: ADR-0038. The fourth answer kind, `set`, and the first whose per-item score is not 0 or 1.

The task is IL-TUR's LSI (legal statute identification): given the facts of a case, name every IPC section
that applies. The label space is the 100 sections the dataset declares -- `Section 2` through `Section 511`,
including the alphanumeric ones (`Section 120B`, `Section 498A`) -- and a case carries several.

**Why not exact-set match, which would have kept every score binary.** A case with four applicable sections
scored 0 for naming three of them and 0 for naming none is not a measurement a night can select on: the whole
pool sits at zero and every candidate ties with the incumbent, which is how a bench looks when it is broken
rather than hard. Jaccard gives the same 0 for a disjoint answer and the same 1 for an exact one, and a
gradient in between.

**Why Jaccard rather than micro-F1**, which is what the LSI literature reports. Micro-F1 pools true and false
positives across the whole set of items before dividing, so it is not the mean of anything per item -- and
this engine's boundary is built on paired PER-ITEM deltas and a sigma over items. A metric that only exists in
aggregate cannot be paired, so adopting it would mean the selection instrument and the reported number
disagree about what an item is. Per-item Jaccard is decomposable, its mean is well defined, and micro-F1 stays
available for the external proof tier, where a third-party scorer computes it over a whole run.

**The consequence to know about.** A fractional score is not a Bernoulli trial, so a Wilson interval over it
is invalid -- `wilson_ci` takes an integer count for that reason, and a caller that reaches for `int(sum(...))`
on these scores is fabricating one. `confirm_eval` reports a bootstrap mean interval instead and says which it
used. This is the one place where adding an answer kind changed something outside `metrics/`.

Two strictnesses, both for the same reason as `mmlu.py`'s:

* `gold_answer` raises when the sealed answer contains no label at all. A numeric or free-text gold arriving
  here means a mis-sealed pool, and scoring every item 0 would be indistinguishable from a model that knows no
  law.
* `extract_prediction` returns `None` when the completion commits to nothing, and that is kept distinct from
  the empty set. A model that says "no section applies" HAS answered, and being able to tell that from silence
  is the whole of the abstention measurement this project's citation work exists to move.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

#: Separator in the canonical form. Chosen because no label contains it, so the canonical string round-trips.
SEP = "|"

#: An explicit empty answer: the model says nothing applies, which is a commitment and not a refusal.
EMPTY = ""

#: A section marker, singular or plural. Plural matters more than it looks: the natural way to write several
#: is "Sections 34, 302 and 120B", where only the FIRST number carries a marker.
_MARKER = re.compile(r"(?i)\bs(?:ection|ec)?s?\.?\s*")

#: One section number with an optional alphanumeric suffix (`120B`, `498A`) and an optional parenthesised
#: sub-clause (`294(b)`, `376(2)`).
#:
#: The sub-clause is not cosmetic. Two of IL-TUR's hundred labels carry one, and without it `Section 294(b)`
#: normalised to `Section 294` -- so a prediction of the bare section would have been scored CORRECT against
#: a gold of the sub-clause. That is false credit, not a rounding error, and it touched 22 of 400 dev rows.
#: Found by checking the real corpus against the published label list, which is the only way it could be.
#:
#: `(?![\w(])` in place of `\b`: a trailing `)` is not a word character, so `\b` would not anchor after
#: `294(b)`, and it also stops `294` matching when `(b)` follows and should have been taken with it.
_NUM = re.compile(r"(\d{1,3}[A-Za-z]{0,2}(?:\([0-9A-Za-z]{1,4}\))?)(?![\w(])")

#: What joins numbers in a list under one marker. `r/w` and "read with" are how Indian judgments cite
#: sections together and are common in this corpus's own text.
_JOIN = re.compile(r"(?i)\s*(?:,|&|/|and|or|r/w|read with)\s*")

#: An explicit denial. Matched before the labels, because "no section other than 302 applies" mentions one.
_NOTHING = re.compile(r"(?i)\bno(?:ne|t any)?\s+(?:section|statute|provision)s?\b(?![^.]*\bother than\b)")


#: Splits a matched number into its three parts so each can be normalised on its own terms.
_PARTS = re.compile(r"(?i)^(\d{1,3})([A-Z]{0,2})(?:\(([0-9A-Z]{1,4})\))?$")


def normalise_number(num: str) -> str:
    """`302` / `498a` / `294(B)` as the corpus writes them: `302`, `498A`, `294(b)`.

    Uppercasing the whole match was wrong, and only the real data showed it: IL-TUR writes the letter suffix
    upper (`Section 498A`) and the sub-clause lower (`Section 294(b)`), so a blanket `.upper()` produced
    `Section 294(B)` and failed to match its own gold. Two different conventions in one label, and both have
    to be honoured or the canonical form is not canonical.
    """
    m = _PARTS.match(num)
    if not m:
        return num.upper()
    number, suffix, clause = m.group(1), (m.group(2) or "").upper(), m.group(3)
    return f"{number}{suffix}" + (f"({clause.lower()})" if clause else "")


def _sort_key(label: str) -> tuple[int, str, str]:
    """Numeric order, then letter suffix, then sub-clause.

    Lexicographic would put `Section 107` before `Section 34`, which makes the canonical form depend on how a
    number happens to be spelled. The sub-clause is part of the key because `Section 376` and `Section 376(2)`
    are different labels and a canonical form that ordered them arbitrarily would not be canonical.
    """
    m = re.match(r"Section (\d+)([A-Za-z]*)(\([0-9A-Za-z]+\))?$", label)
    return (int(m.group(1)), m.group(2) or "", m.group(3) or "") if m else (10**6, label, "")


def canonical(labels: set[str]) -> str:
    """The one written form of a set of labels: sorted, de-duplicated, `|`-joined."""
    return SEP.join(sorted(labels, key=_sort_key))


def parse_labels(text: str) -> set[str]:
    """Every section label in `text`, normalised to `Section <n><suffix>`.

    A marker is required to admit a number, because a bare `302` in prose is as likely to be a page, a year or
    a paragraph number, and admitting those would credit a wandering completion with a label it never claimed.
    But one marker licenses a whole LIST -- "Sections 34, 302 and 120B" -- since that is how several sections
    are actually written, and demanding a marker per number would systematically under-credit a correct answer.
    """
    found: set[str] = set()
    for marker in _MARKER.finditer(text):
        at = marker.end()
        while True:
            num = _NUM.match(text, at)
            if not num:
                break
            found.add(f"Section {normalise_number(num.group(1))}")
            at = num.end()
            join = _JOIN.match(text, at)
            if not join:
                break
            at = join.end()
    return found


def gold_answer(answer_text: str) -> str:
    """The canonical label set a sealed answer names.

    Raises when it names none. That is a mis-sealed pool rather than an item with no answer: LSI's items all
    carry at least one section, and silently returning the empty set would score a correct prediction 0 and a
    refusal 1.
    """
    labels = parse_labels(answer_text)
    if not labels:
        raise ValueError(
            f"gold {answer_text[:60]!r} names no statute section; a `set` pool's answers must, so this is a "
            f"mis-sealed pool rather than an unanswerable item"
        )
    return canonical(labels)


def extract_prediction(completion: str) -> str | None:
    """The label set the completion commits to, or `None` if it commits to nothing.

    `None` and `EMPTY` are different answers and stay different. "No section applies" is a claim that can be
    right or wrong; a completion that trailed off having named nothing is not an answer at all, and scoring
    the two the same is the confound that made a 512-token truncation look like a model that knew no law.
    """
    if _NOTHING.search(completion):
        return EMPTY
    labels = parse_labels(completion)
    return canonical(labels) if labels else None


def score_item(completion: str, gold: str) -> float:
    """Jaccard overlap in [0, 1]: |predicted ∩ gold| / |predicted ∪ gold|.

    A completion that commits to nothing scores 0 -- it has not answered, and `metrics.unparsed` is where that
    is told apart from a wrong answer. Two empty sets score 1, which cannot arise from a `set` pool's gold
    (`gold_answer` refuses an empty gold) and is defined so the function is total.
    """
    pred = extract_prediction(completion)
    if pred is None:
        return 0.0
    predicted, wanted = set(filter(None, pred.split(SEP))), set(filter(None, gold_answer(gold).split(SEP)))
    union = predicted | wanted
    return 1.0 if not union else len(predicted & wanted) / len(union)


def score_completions(completions: Mapping[str, str], golds: Mapping[str, str]) -> dict[str, float]:
    """Per-item Jaccard keyed by item id. A missing completion scores 0 (a crash is a miss, not an
    exclusion)."""
    return {i: (score_item(completions[i], g) if i in completions else 0.0) for i, g in golds.items()}

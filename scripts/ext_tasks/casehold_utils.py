"""External scoring for CaseHOLD's held-out test split, under a harness recipe.

This is the proof tier's scorer and it is deliberately a SEPARATE IMPLEMENTATION from both of the internal
ones. `docker/jobs/agent_choice._letter` is harness policy -- it decides retries and votes, and its own
docstring says so -- and `pravrudhi_kernel.metrics.mmlu` is the internal authority that the ledger records.
Neither is imported here. An external tier that reused the internal parser would certify the parser along
with the harness, and a shared bug would be invisible in exactly the comparison the tier exists to make.

So this implements the same SPECIFICATION independently: find the option letter the completion commits to,
and score it against the gold. Where this and the kernel disagree on the same completions, that disagreement
is evidence about the parsers and should be read rather than averaged away.

`unparsed` is reported beside `exact_match` and must be read first. A completion that never states a letter
scores zero here exactly as a wrong letter does, so a harness that reasons well and runs out of generation
budget is indistinguishable from one that reasons badly -- unless the unparsed rate is on the page. That is
the same failure that made the law baseline read 0.1042: at 512 tokens, 61 to 66 of 96 completions contained
no letter at all.
"""

from __future__ import annotations

import re
from typing import Any

LETTERS = "ABCDEFGHIJ"

# Ordered most explicit first: a completion that says "Answer: C" after musing about A is committing to C.
_PATTERNS = (
    re.compile(r"(?:answer|option|choice)\s*(?:is)?\s*[:\-]?\s*\(?([A-J])\)?\b", re.I),
    re.compile(r"\\boxed\{\s*([A-J])\s*\}"),
    re.compile(r"^\s*\(?([A-J])\)?\s*[.):]", re.M),
)
_ALONE = re.compile(r"^\s*\(?([A-J])\)?\s*$")


def option_letter(completion: str) -> str | None:
    """The option letter a completion commits to, or None if it never states one.

    The LAST match wins for every pattern: a model that eliminates options in order and then answers has
    named several letters, and the one it ends on is its answer.
    """
    text = completion or ""
    for pattern in _PATTERNS:
        found = pattern.findall(text)
        if found:
            return str(found[-1]).upper()
    for line in reversed([ln for ln in text.splitlines() if ln.strip()]):
        match = _ALONE.match(line)
        if match:
            return match.group(1).upper()
    return None


def process_results(doc: dict[str, Any], results: list[str]) -> dict[str, float]:
    """Score one item: did the completion commit to the gold letter?"""
    said = option_letter(results[0] if results else "")
    gold = str(doc.get("answer") or "").strip().upper()
    return {
        "exact_match": 1.0 if (said is not None and said == gold) else 0.0,
        "unparsed": 0.0 if said is not None else 1.0,
    }

"""The kernel citation scorer against the external lm-eval one (ADR-0036 §Consequences).

Two implementations of one normalisation rule, in two places for a reason: the external scorer's results enter
the ledger by sha256 as external proof, and the kernel's are the signal a night selects on. Keeping both is
duplication only if they can drift; asserting they agree on every case either is tested against makes it a
differential check, and a divergence is a finding about which one is wrong rather than a merge conflict.

The cases are the ones that caught real defects: `F.2d` (a digit inside the reporter, which the first pattern
could not match, so every circuit citation read as no answer), `L. Ed. 2d` (a series designator as its own
token), prose that looks like a citation, and a refusal.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "ext_tasks"))

from nyaya_utils import extract_citation as external_extract  # noqa: E402

from pravrudhi_kernel.metrics.citation import extract_prediction as kernel_extract  # noqa: E402
from pravrudhi_kernel.metrics.citation import score_item as kernel_score  # noqa: E402

CASES = [
    " 655 F.2d 1233",
    "470 F.3d 798",
    "The citation is 132 U.S. 161.",
    "117 F. 137",
    "627 F. Supp. 44 (1985)",
    "113 S.W.3d 101 (2004)",
    "148 L. Ed. 2d 1355",
    "39 Fla. 536",
    "",
    "The case is about a defective product. The case was decided in 2015.",
    "I do not know the citation for this case.",
    "Unable to determine the citation.",
    "decided in 2015 by 3 judges",
    "there were 12 counts and 4 defendants",
    "the 1990 act at 5 sections",
    "470 f.2d 798",
    " 655 F.2d 1233\nCase: United States v. One Book Called Ulysses\nAnswer: 72 F.2d 705",
]


@pytest.mark.parametrize("completion", CASES)
def test_the_two_extractors_agree(completion: str) -> None:
    assert kernel_extract(completion) == external_extract(completion)


@pytest.mark.parametrize(
    "variant", ["470 F.2d 798", "470 F. 2d 798", " 470  F.2d  798. ", "The answer is 470 F.2d 798,"]
)
def test_the_two_normalisations_agree(variant: str) -> None:
    """Both must call these the same citation, or a night would select on a number the proof disagrees with."""
    from nyaya_utils import process_citation

    gold = "470 F.2d 798"
    assert kernel_score(variant, gold) == int(process_citation({"example_correct_answer": gold}, [variant])["exact_match"])


def test_a_wrong_citation_is_wrong_in_both() -> None:
    gold = "470 F.2d 798"
    from nyaya_utils import process_citation

    assert kernel_score("655 F.2d 1233", gold) == 0
    assert process_citation({"example_correct_answer": gold}, ["655 F.2d 1233"])["exact_match"] == 0.0

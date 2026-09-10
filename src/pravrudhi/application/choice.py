"""Rendering a multiple-choice item the one way this engine renders them.

Two callers need it and they must agree: `pool_admin` seals evaluation pools and `corpus` builds training
rows. If training prompts were shaped differently from evaluation prompts, an adapter would be learning to
answer a question it never sees at eval time, and the resulting gain -- or absence of one -- would be about
the formatting rather than about the law.

The instruction to answer with a letter is deliberately NOT here: that belongs to the harness template
(`harness/prompts/eval/mmlu_v1.md`), which is hashed separately, so how the data is asked can be a candidate
for improvement without the data changing underneath it.
"""

from __future__ import annotations

from collections.abc import Sequence

# The widest option set in use: MMLU has four, CaseHOLD five, MMLU-Pro ten.
LETTERS = "ABCDEFGHIJ"


def render_choice_question(question: str, options: Sequence[str]) -> str:
    """The item as the model sees it: the stem, then the options under their letters."""
    if not 1 < len(options) <= len(LETTERS):
        raise ValueError(f"an item needs 2 to {len(LETTERS)} options, got {len(options)}")
    lines = [f"{LETTERS[i]}. {str(opt).strip()}" for i, opt in enumerate(options)]
    return question.strip() + "\n\n" + "\n".join(lines) + "\n"


def letter(index: int, n_options: int) -> str:
    """The option letter for a zero-based gold index, refusing an index the item does not have."""
    if not 0 <= index < n_options:
        raise ValueError(f"gold index {index} is outside the {n_options} options")
    return LETTERS[index]

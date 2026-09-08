"""The proposer context must hold the prompt as well as the answer.

Night 17 closed in seconds having spent 0.00 of 3.0 GPU-hours, reporting `status: closed` and no outcomes. The
context was computed as `max_tokens * 2 + 8192` = 16384 while the prompt was 16268 tokens, leaving 116 for the
reply. The formula's additive term is an assumption about prompt length, and the proposer prompt is built from
`ledger_summary`, so it grows with the history: the engine tightened its own proposer as it accumulated evidence,
which is the opposite of what a self-improving loop should do to itself.
"""

from __future__ import annotations

from pravrudhi.application.night import proposer_ctx
from pravrudhi.models.llama_server import LlamaServer


def test_the_context_is_never_smaller_than_the_servers_own_default() -> None:
    default = LlamaServer(__file__).ctx
    assert proposer_ctx(4096) >= default


def test_the_night_17_configuration_leaves_room_for_its_real_prompt() -> None:
    """The exact numbers the ledger recorded, which the old formula could not fit."""
    prompt_tokens, max_tokens = 16268, 4096
    assert proposer_ctx(max_tokens) >= prompt_tokens + max_tokens


def test_a_large_max_tokens_still_wins() -> None:
    """Sizing up for a big answer must keep working; the floor is a floor, not a cap."""
    assert proposer_ctx(65536) > proposer_ctx(4096)

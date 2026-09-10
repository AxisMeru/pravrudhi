"""Benchmark definitions, sealed pools and scorers (T0). The only place a number about a model is computed.

ADR-REF: ADR-0035, extended by ADR-0036 (`text`). This module used to re-export `gsm8k`'s four functions as
*the* scorer, so the engine had a single scoring path, numeric by construction, on every model-track night.
A pool is now scored by the scorer its own manifest declares. There is no unconditional re-export: a caller
names the kind it means, and a pool answers for itself.

Two consequences worth stating plainly, because both are provenance rather than convenience:

* `scorer_source` exists so an observation records the file that actually scored it. The engine's expected
  hashes previously named `gsm8k.py` unconditionally, which was true while there was one scorer and would
  have become a lie the first night a choice pool ran.
* A pool sealed before this ADR declares no kind and resolves to `numeric` (`pool.answer_kind`), so the 499
  gsm8k `observe` rows already in the ledger stay reproducible against the same scorer file.
"""

import inspect
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from pravrudhi_kernel.metrics import citation, gsm8k, labels, mmlu
from pravrudhi_kernel.metrics.pool import (
    ANSWER_KINDS,
    DEFAULT_ANSWER_KIND,
    PoolExhausted,
    Rotation,
    answer_kind,
    draw_rotation,
    record_exposure,
    seal_pool,
)


class Scorer(Protocol):
    """What a scorer module must expose. `gsm8k`, `mmlu`, `citation` and `labels` are the implementations.

    The scores are typed `float` and returned as a `Mapping`, both so that `labels` fits without editing the
    three scorers that came before it. A per-item Jaccard is fractional (ADR-0038), so an `int` return type
    would have excluded it; and `Mapping` is covariant in its value type where `dict` is not, so
    `dict[str, int]` satisfies `Mapping[str, float]` and `gsm8k.py` keeps the bytes that 499 `observe` rows in
    this project's ledger name as the file that scored them.
    """

    def gold_answer(self, answer_text: str) -> str: ...

    def extract_prediction(self, completion: str) -> str | None: ...

    def score_item(self, completion: str, gold: str) -> float: ...

    def score_completions(
        self, completions: Mapping[str, str], golds: Mapping[str, str]
    ) -> Mapping[str, float]: ...


def is_binary(kind: str) -> bool:
    """Whether this kind's per-item score is 0 or 1.

    Asked before computing anything that assumes a Bernoulli trial. A Wilson interval over a mean of Jaccard
    values is not a confidence interval for anything, and the way that mistake gets made is `int(sum(scores))`
    passed to `wilson_ci` -- which type-checks, stays inside `[0, n]`, and produces a number nobody can
    interpret. See ADR-0038.
    """
    return kind != "set"


SCORERS: dict[str, Scorer] = {"choice": mmlu, "numeric": gsm8k, "set": labels, "text": citation}

# A kind with no scorer seals a pool nothing can score; a scorer with no kind is unreachable. Either drift is
# a defect, so it fails at import rather than on the night that needed it.
if tuple(sorted(SCORERS)) != ANSWER_KINDS:
    raise ImportError(f"scorer table {sorted(SCORERS)} does not match declared answer kinds {list(ANSWER_KINDS)}")


def scorer_for(kind: str) -> Scorer:
    try:
        return SCORERS[kind]
    except KeyError:
        raise ValueError(f"unknown answer kind {kind!r}; known kinds are {', '.join(ANSWER_KINDS)}") from None


def scorer_for_pool(pool_dir: Path) -> Scorer:
    """The scorer this pool's manifest declares. The one dispatch point; there is no other."""
    return scorer_for(answer_kind(pool_dir))


def unparsed(scorer: Scorer, completions: Mapping[str, str]) -> list[str]:
    """Ids whose completion commits to no answer at all, in the order a caller should report them.

    A format miss and a wrong answer both score 0, and only one of them is about the model. This lives here
    rather than in each scorer so it works for every kind without editing `gsm8k.py`, whose bytes 499
    `observe` rows in this project's ledger name as the file that scored them.
    """
    return sorted(i for i, c in completions.items() if scorer.extract_prediction(c) is None)


def scorer_source(kind: str) -> Path:
    """The file that does the scoring, for the observation's `hashes.scorer`."""
    return Path(inspect.getfile(scorer_for(kind))).resolve()  # type: ignore[arg-type]


def scorer_source_for_pool(pool_dir: Path) -> Path:
    return scorer_source(answer_kind(pool_dir))


__all__ = [
    "ANSWER_KINDS",
    "DEFAULT_ANSWER_KIND",
    "SCORERS",
    "PoolExhausted",
    "Rotation",
    "Scorer",
    "answer_kind",
    "draw_rotation",
    "citation",
    "gsm8k",
    "is_binary",
    "labels",
    "mmlu",
    "record_exposure",
    "scorer_for",
    "scorer_for_pool",
    "scorer_source",
    "scorer_source_for_pool",
    "seal_pool",
    "unparsed",
]

"""The header-cue scan of the intake text tolerates long runs of separator characters in a paragraph. The earlier pattern is
kept verbatim as the oracle: the anchored pattern must give the same answer on every string of a random token mix."""

import contextlib
import itertools
import random
import re
import time

from pravrudhi.application.a0_assist import intake

_OLD_PARTIES = re.compile(
    r"\bbetween\s*:|^\s*and\s*:|-versus-|\bversus\b|(?:\.{2,}|…|-{2,})\s*(?:appellants?|respondents?|petitioners?|applicants?)\b",
    re.I,
)
_TOKENS = [
    "between", ":", "and", "versus", "-versus-", "..", ".", "...", "…", "--", "-", "---", " ", "  ", "\n", "appellant",
    "Respondents", "petitioner", "applicant", "x", "1",
]  # fmt: skip


def _corpus(seed: int, n: int, longest: int):
    rng = random.Random(seed)
    for _ in range(n):
        yield "".join(rng.choice(_TOKENS) for _ in range(rng.randint(0, longest)))


def test_the_anchored_cue_pattern_gives_the_same_answer_as_the_earlier_one():
    new = intake._CUES["parties"]
    matches = 0
    for s in itertools.chain((a + b + c for a in _TOKENS for b in _TOKENS for c in _TOKENS), _corpus(945, 200_000, 12)):
        old_hit = bool(_OLD_PARTIES.search(s))
        assert old_hit == bool(new.search(s)), repr(s)
        matches += old_hit
    assert matches > 1_000  # not vacuous


def test_a_long_run_of_separator_characters_does_not_stall_the_cue_scan():
    n = 200_000
    for sep in (".", "-", "…"):
        for tail in ("x", " appellant", ""):
            t0 = time.perf_counter()
            intake._CUES["parties"].search("and" + sep * n + tail)
            assert time.perf_counter() - t0 < 1.0


def test_every_intake_path_stays_fast_on_long_separator_runs():
    """The whole deterministic intake (cleaning, plan, bundle) on a long paragraph of one separator character."""
    n = 200_000
    for sep in (".", "-", " ", ":", "\n"):
        text = "The first fact is stated here.\n\n" + "and" + sep * (n // 2) + "x" + "\n\nThe second fact follows."
        t0 = time.perf_counter()
        with contextlib.suppress(intake.IntakeRefused):
            intake.build_bundle(text)
        assert time.perf_counter() - t0 < 5.0

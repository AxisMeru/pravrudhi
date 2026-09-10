"""ADR-0035: a pool is scored by the scorer its manifest declares, and every pool sealed before this ADR
stays numeric. The last test is the regression replay the charter requires of a T0 change."""

import hashlib
import json
from pathlib import Path

import pytest

from pravrudhi_kernel.metrics import (
    ANSWER_KINDS,
    SCORERS,
    gsm8k,
    mmlu,
    scorer_for,
    scorer_for_pool,
    scorer_source,
    seal_pool,
    unparsed,
)
from pravrudhi_kernel.metrics.pool import answer_kind, load_manifest

NUMERIC_ROWS = [{"question": f"q{i}", "answer": f"steps\n#### {i}"} for i in range(4)]
CHOICE_ROWS = [{"question": f"q{i}\nA. no\nB. yes", "answer": "B"} for i in range(4)]

# The scorer sha256 carried by 499 `observe` rows in this project's ledger, every one of them a gsm8k night
# (task families gsm8k-test, gsm8k-trainB, gsm8k-trainC). ADR-0035 §8 requires a regression replay: if
# dispatch ever resolves those pools to a different file, or gsm8k.py is edited, this hash moves and every one
# of those rows becomes unreproducible. The other 59 observe rows carry the code scorer's hash and reach it
# through the harness track, which this dispatch does not touch.
LEDGER_GSM8K_SCORER_SHA = "0c9327a794fbb0862792be38d2c7f5e50f8caa36a31e8c9615ef8c8267580524"


def test_scorer_for_selects_by_kind() -> None:
    assert scorer_for("numeric") is gsm8k
    assert scorer_for("choice") is mmlu


def test_scorer_for_refuses_an_unknown_kind() -> None:
    with pytest.raises(ValueError, match="answer kind"):
        scorer_for("vibes")


def test_every_declared_kind_has_a_scorer_and_every_scorer_a_kind() -> None:
    # A scorer added without a manifest kind is unreachable; a kind without a scorer seals pools nothing can
    # score. Either drift is a defect, so they are asserted equal rather than merely overlapping.
    assert tuple(sorted(SCORERS)) == ANSWER_KINDS


def test_a_pool_sealed_before_this_adr_has_no_answer_kind_and_stays_numeric(tmp_path: Path) -> None:
    pool = tmp_path / "legacy"
    seal_pool(pool, "gsm8k-test", NUMERIC_ROWS, {})
    manifest = pool / "manifest.json"
    body = json.loads(manifest.read_text())
    del body["answer_kind"]  # exactly the shape of every manifest sealed before ADR-0035
    manifest.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
    assert answer_kind(pool) == "numeric"
    assert scorer_for_pool(pool) is gsm8k


def test_a_choice_pool_dispatches_to_the_choice_scorer(tmp_path: Path) -> None:
    pool = tmp_path / "law"
    seal_pool(pool, "mmlu-law", CHOICE_ROWS, {}, answer_kind="choice")
    assert load_manifest(pool)["answer_kind"] == "choice"
    assert scorer_for_pool(pool) is mmlu
    scorer = scorer_for_pool(pool)
    assert scorer.score_completions({"i": "Answer: B"}, {"i": "B"}) == {"i": 1}


def test_seal_refuses_a_kind_no_scorer_serves(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="answer kind"):
        seal_pool(tmp_path / "bad", "x", NUMERIC_ROWS, {}, answer_kind="freeform")


def test_pool_version_commits_to_the_answer_kind(tmp_path: Path) -> None:
    # Same rows, different scorer, must not be the same sealed pool: the version has to cover how the items
    # are read, not only what they say.
    a = seal_pool(tmp_path / "a", "x", CHOICE_ROWS, {}, answer_kind="numeric")
    b = seal_pool(tmp_path / "b", "x", CHOICE_ROWS, {}, answer_kind="choice")
    assert a["item_hashes"] == b["item_hashes"]
    assert a["pool_version"] != b["pool_version"]


def test_numeric_scorer_source_still_hashes_to_what_the_ledger_recorded() -> None:
    src = scorer_source("numeric")
    assert src.name == "gsm8k.py"
    assert hashlib.sha256(src.read_bytes()).hexdigest() == LEDGER_GSM8K_SCORER_SHA


def test_the_choice_scorer_has_its_own_source_so_provenance_names_the_file_that_scored(tmp_path: Path) -> None:
    assert scorer_source("choice").name == "mmlu.py"
    assert scorer_source("choice") != scorer_source("numeric")


def test_numeric_scoring_is_unchanged_by_dispatch() -> None:
    # The replay half: dispatch must be a lookup, not a reinterpretation.
    comps = {"a": "Final answer: 18", "b": "so 3 * 4 = 12", "c": "The result is \\boxed{1,200}."}
    golds = {"a": "steps\n#### 18", "b": "steps\n#### 11", "c": "steps\n#### 1200"}
    scorer = scorer_for("numeric")
    direct = gsm8k.score_completions(comps, {i: gsm8k.gold_answer(g) for i, g in golds.items()})
    through = scorer.score_completions(comps, {i: scorer.gold_answer(g) for i, g in golds.items()})
    assert direct == through == {"a": 1, "b": 0, "c": 1}


def test_unparsed_names_format_misses_for_either_scorer() -> None:
    # Both a refusal and a wrong answer score 0; only one of them is about the model. This is the only way
    # the difference is visible after the night, and it must work for the numeric scorer too, whose file
    # cannot be edited without invalidating the observations that name its hash.
    choice = {"a": "Answer: A", "b": "I decline to answer.", "c": ""}
    assert unparsed(mmlu, choice) == ["b", "c"]
    numeric = {"a": "Final answer: 18", "b": "I cannot compute this."}
    assert unparsed(gsm8k, numeric) == ["b"]
    assert unparsed(mmlu, {}) == []

"""The harness track on a choice bench.

`harness_track.SCORERS` maps a pool's bench to a CONTAINER job that executes hidden tests. That is right for
code, where scoring means running the candidate's solution, and meaningless for a multiple-choice pool whose
answer is a letter: there is nothing to execute, and `score_agent` crashed before it got that far, on
`json.loads("B")` while reading a task id out of the pool's answer.

The fix is not a new in-container scorer. ADR-0035 put a choice scorer in the kernel precisely so the engine
would not compute the numbers; adding `score_choice.py` beside `score_code.py` would put it back. A bench with
no container scorer is scored in process by the scorer its own manifest declares, exactly as the model track's
`spine.score_job` does.

The dispatch rule has to survive a code pool that nobody remembered to register. `mbppplus` and `apps` were
sealed before `answer_kind` existed, so their manifests declare none and default to numeric -- routing on
`answer_kind` alone would hand a JSON answer blob to the numeric scorer and score every item zero, which looks
exactly like a harness that got worse. So: a bench in SCORERS keeps its container job; otherwise the pool MUST
declare an answer kind, and a pool that does neither is refused rather than guessed at.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pravrudhi.application.harness_track import SCORERS, _scorer, kernel_scored
from pravrudhi_kernel.metrics import seal_pool

CHOICE = [{"question": f"q{i}\nA. no\nB. yes", "answer": "B"} for i in range(6)]
NUMERIC = [{"question": f"q{i}", "answer": f"steps\n#### {i}"} for i in range(6)]


def test_a_code_bench_keeps_its_container_scorer(tmp_path: Path) -> None:
    pool = tmp_path / "mbppplus"
    seal_pool(pool, "mbppplus", NUMERIC, {})
    assert kernel_scored(pool) is False
    job, _ = _scorer(pool)
    assert job.name == "score_code.py"


def test_a_choice_bench_is_scored_in_process_by_the_kernel(tmp_path: Path) -> None:
    pool = tmp_path / "law"
    seal_pool(pool, "mmlu-law-val", CHOICE, {}, answer_kind="choice")
    assert "mmlu-law-val" not in SCORERS
    assert kernel_scored(pool) is True


def test_a_pool_that_is_neither_registered_nor_declared_is_refused(tmp_path: Path) -> None:
    # The dangerous case: a future code pool left out of SCORERS. Its manifest declares no kind, so routing on
    # answer_kind would hand a JSON answer to the numeric scorer and record a harness that got worse.
    pool = tmp_path / "mystery"
    seal_pool(pool, "mystery-bench", NUMERIC, {})
    manifest = json.loads((pool / "manifest.json").read_text())
    del manifest["answer_kind"]
    (pool / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    with pytest.raises(KeyError, match="mystery-bench"):
        kernel_scored(pool)


def test_the_choice_scorer_reads_the_pool_the_harness_track_would_use(tmp_path: Path) -> None:
    from pravrudhi_kernel.metrics import scorer_for_pool, scorer_source_for_pool
    from pravrudhi_kernel.metrics.pool import read_item

    pool = tmp_path / "law"
    seal_pool(pool, "mmlu-law-val", CHOICE, {}, answer_kind="choice")
    scorer = scorer_for_pool(pool)
    item_id = sorted(json.loads((pool / "manifest.json").read_text())["item_hashes"])[0]
    gold = scorer.gold_answer(read_item(pool, item_id)["answer"])
    assert scorer.score_completions({item_id: "Answer: B"}, {item_id: gold}) == {item_id: 1}
    # And the observation must name the file that scored it, not a container job that never ran.
    assert scorer_source_for_pool(pool).name == "mmlu.py"


def test_the_proposer_prompt_follows_the_config_not_a_hardcoded_v1() -> None:
    """Night 19 ran green and explored the wrong space, which is this project's characteristic failure.

    `harness_proposer/v1.md` describes MBPP+ to the proposer: docstrings, one visible assert, a ```python
    block. Called unconditionally on a choice bench it produced seven candidates whose rationales were
    "Best-of-3 sampling improves chances of passing visible tests" and "Template with explicit code block
    requirement" -- on a multiple-choice question of law. All seven left `max_new_tokens` at 512, the single
    knob measured to be worth 0.2951 on this pool.

    The scoring was right, the job was right, and the night was useless. So the prompt file is read from the
    config's `prompt_version`, and the choice prompt states the token-budget evidence and says plainly that
    `use_visible_tests` does nothing here.
    """
    import re

    source = (Path(__file__).resolve().parents[1] / "src/pravrudhi/application/harness_track.py").read_text()
    assert 'prompt_file="harness_proposer/v1.md"' not in source
    assert "prompt_version" in source

    prompts = Path(__file__).resolve().parents[1] / "harness" / "prompts" / "harness_proposer"
    choice = (prompts / "choice_v1.md").read_text()
    # It must tell the proposer the thing the measurement knows, or it will propose 512 again.
    assert "0.2951" in choice and "1024" in choice
    assert "use_visible_tests" in choice and "NOTHING" in choice
    # And it must not ask for code on a bench that has none.
    assert "```python" not in choice.replace("Do not ask for a ```python", "")
    # Every placeholder the renderer substitutes must be present, or the prompt renders with holes.
    for token in ("{model}", "{k}", "{grammar}", "{state_summary}", "{incumbent_strategy}", "{rethink_note}"):
        assert token in choice, token
    # And no placeholder the renderer does not know about.
    unknown = set(re.findall(r"\{[a-z_]+\}", choice)) - {
        "{model}", "{k}", "{grammar}", "{state_summary}", "{incumbent_strategy}", "{rethink_note}",
        "{question}", "{feedback}",
    }
    assert not unknown, unknown


def test_the_proposer_records_the_prompt_it_actually_rendered() -> None:
    """`propose.py` hardcoded `"prompt_version": "v1"` while `prompt_file` was already a parameter.

    Harness night 20 rendered `harness_proposer/choice_v1.md` and wrote `prompt_version: "v1"` into its
    `proposer_call` audit row, so the ledger named a prompt that was not the one used. Same family as
    `spine.SCORER_SOURCE` naming gsm8k.py whatever had scored: a row is only evidence if it says what happened.
    """
    source = (Path(__file__).resolve().parents[1] / "src/pravrudhi/application/propose.py").read_text()
    assert '"prompt_version": "v1"' not in source
    assert '"prompt_version": Path(prompt_file).stem' in source
    assert '"prompt_file": prompt_file' in source


def test_the_choice_prompt_demands_the_field_that_threw_every_candidate_away() -> None:
    """All eight of night 20's candidates were rejected on `feedback_template` alone.

    Seven for "String should have at least 10 characters" and one for not containing `{feedback}`. The grammar
    doc does state the requirement, and the model dropped the field anyway on the `prompts_only` candidates --
    so the prompt now says it is required even at `retries: 0`, and gives an example.
    """
    choice = (
        Path(__file__).resolve().parents[1] / "harness/prompts/harness_proposer/choice_v1.md"
    ).read_text()
    assert "REQUIRED on every candidate" in choice
    assert "retries: 0" in choice
    assert "at least 10 characters" in choice


def test_the_floor_writer_refuses_to_overwrite_another_benchs_floor(tmp_path: Path) -> None:
    """The read side refuses a floor measured on another bench. Without the same rule on the write side, the
    way to satisfy that check is to destroy the other bench's floor -- which is how the mbppplus floor
    (ADR-0029) would have gone when `apps` was measured against the default path."""
    from pravrudhi.application.harness_track import floor_dest, floor_path

    prereg = tmp_path / "research" / "prereg"
    prereg.mkdir(parents=True)
    (prereg / "variance_harness.json").write_text(json.dumps({"bench": "mbppplus", "sigma_seed": 0.01}))

    with pytest.raises(ValueError, match="holds the floor measured on bench 'mbppplus'"):
        floor_dest(tmp_path, {"bench": "apps"})

    # Naming its own file is the fix, and it is the same rule the night reads by.
    cfg = {"bench": "apps", "noise_floor": "research/prereg/variance_harness_apps.json"}
    assert floor_dest(tmp_path, cfg) == prereg / "variance_harness_apps.json"
    assert floor_path(tmp_path, cfg) == floor_dest(tmp_path, cfg)
    # Re-measuring the same bench into its own floor is not an overwrite worth refusing.
    (prereg / "variance_harness_apps.json").write_text(json.dumps({"bench": "apps", "sigma_seed": 0.02}))
    assert floor_dest(tmp_path, cfg).exists()


def test_the_unparsed_count_travels_into_the_observation(tmp_path: Path) -> None:
    """On a choice bench this is the term that DOMINATES the measurement, and it was recoverable from a job
    directory and absent from the ledger. The casehold-val floor is the case: rotations scored 0.441, 0.368
    and 0.188, and that spread tracks unparsed rates of 35%, 40% and 52% rather than anything about law. A
    number that explains a result has to be in the row that reports it -- CHARTER §6."""
    from pravrudhi.application.harness_track import with_unparsed

    out = tmp_path / "out"
    out.mkdir()
    ref = out / "per_item_scores.jsonl"
    ref.write_text("")

    # No file written by the scorer means none were unparsed, which is a real zero.
    assert with_unparsed({"model": "m"}, ref) == {"model": "m", "n_unparsed": 0}

    (out / "unparsed.json").write_text(json.dumps({"n": 50, "ids": ["a"] * 50}))
    got = with_unparsed({"model": "m"}, ref)
    assert got["n_unparsed"] == 50
    assert got["model"] == "m", "the existing meta must survive"

    # An unreadable file is unknown, not a comforting zero -- the same distinction the scorers keep between a
    # format miss and a wrong answer.
    (out / "unparsed.json").write_text("{ this is not json")
    assert with_unparsed({}, ref)["n_unparsed"] is None


def test_the_bucket_no_longer_claims_a_code_corpus_on_a_law_bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`corpus` was the literal string "mbppplus" for every bench this track ran, so the ledger's
    casehold-val and mmlu-law-val observations all named a code corpus that was never involved."""
    from pravrudhi.application.harness_track import HarnessContext

    # CI has no model cache; an empty snapshot directory satisfies the resolver, and nothing reads weights.
    (tmp_path / "hf" / "hub" / "models--Qwen--Qwen3-1.7B" / "snapshots" / "ci").mkdir(parents=True)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    prereg = tmp_path / "research" / "prereg"
    prereg.mkdir(parents=True)
    (prereg / "v.json").write_text(json.dumps({"bench": "casehold-val", "sigma_seed": 0.01}))
    cfg = {
        "model": "Qwen/Qwen3-1.7B", "bench": "casehold-val",
        "noise_floor": "research/prereg/v.json",
        "boundary": {"alpha_eff": 0.05, "alpha_fut": 0.2, "k_max": 4, "sigma_mode": "adaptive",
                     "n0": 3, "delta_min_floor": 0.034, "min_n_confirm": 2},
    }
    ctx = HarnessContext(tmp_path, cfg, 1, lambda _: None, measuring=True)
    assert ctx.bucket["task_family"] == "casehold-val"
    assert "mbppplus" not in ctx.bucket["corpus"]
    # This track trains nothing, and the honest value says so rather than naming a corpus or repeating the
    # bench (which `task_family` already carries).
    assert "trains-nothing" in ctx.bucket["corpus"]

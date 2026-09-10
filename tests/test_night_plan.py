"""Choosing which night to run. `night.py` read `lora_night.yaml` and `variance.json` unconditionally, so a
second track could not run at all -- and the pairing of a config with a floor measured on a *different* pool
was left to whoever typed the command, which is the mistake ADR-0025 records the consequences of."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from pravrudhi.application.night_plan import NightPlanError, resolve

LORA = {
    "model": "Qwen/Qwen3-0.6B",
    "bench": "gsm8k-trainD",
    "evaluation": {"k_items": 200},
}
NYAYA = {
    "objective": "prabhasa-nyaya",
    "track": "nyaya",
    "model": "Qwen/Qwen3-1.7B",
    "bench": "mmlu-law-val",
    "answer_kind": "choice",
    "noise_floor": "research/prereg/variance_nyaya.json",
    "evaluation": {"k_items": 96},
}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    prereg = tmp_path / "research" / "prereg"
    prereg.mkdir(parents=True)
    (prereg / "lora_night.yaml").write_text(yaml.safe_dump(LORA))
    (prereg / "nyaya_night.yaml").write_text(yaml.safe_dump(NYAYA))
    (prereg / "variance.json").write_text(json.dumps({"bench": "gsm8k-trainD", "sigma_seed": 0.0249}))
    (prereg / "variance_nyaya.json").write_text(json.dumps({"bench": "mmlu-law-val", "sigma_seed": 0.0170}))
    return tmp_path


def test_no_selection_is_the_model_track_exactly_as_before(root: Path) -> None:
    # Every existing command, systemd unit and script calls `night` with no selector. They must keep working
    # and must keep reading the same two files, or this change breaks the loop it was meant to widen.
    plan = resolve(root)
    assert plan.config.name == "lora_night.yaml"
    assert plan.variance.name == "variance.json"
    assert plan.objective is None
    assert plan.bench == "gsm8k-trainD"


def test_an_objective_resolves_to_the_prereg_that_claims_it(root: Path) -> None:
    plan = resolve(root, objective="prabhasa-nyaya")
    assert plan.config.name == "nyaya_night.yaml"
    assert plan.variance.name == "variance_nyaya.json"
    assert plan.objective == "prabhasa-nyaya"
    assert plan.bench == "mmlu-law-val"
    assert plan.track == "nyaya"


def test_an_explicit_config_is_taken_as_given(root: Path) -> None:
    plan = resolve(root, config=root / "research" / "prereg" / "nyaya_night.yaml")
    assert plan.config.name == "nyaya_night.yaml"
    assert plan.variance.name == "variance_nyaya.json"


def test_an_objective_no_prereg_claims_says_what_to_add(root: Path) -> None:
    with pytest.raises(NightPlanError, match="no pre-registration"):
        resolve(root, objective="something-else")


def test_two_preregs_claiming_one_objective_is_refused_rather_than_ordered(root: Path) -> None:
    # Which one wins would depend on directory order, which is not a decision anyone made.
    other = root / "research" / "prereg" / "duplicate_night.yaml"
    other.write_text(yaml.safe_dump({**NYAYA, "bench": "mmlu-law-val"}))
    with pytest.raises(NightPlanError, match="claim objective"):
        resolve(root, objective="prabhasa-nyaya")


def test_objective_and_config_together_are_refused(root: Path) -> None:
    with pytest.raises(NightPlanError, match="not both"):
        resolve(root, objective="prabhasa-nyaya", config=root / "research" / "prereg" / "lora_night.yaml")


def test_a_floor_measured_on_another_pool_is_refused(root: Path) -> None:
    # The whole reason this resolution exists. A config for one bench paired with a floor measured on another
    # sets the boundary from the wrong sigma, and every promotion that night is unreproducible -- silently,
    # because both files are valid on their own.
    (root / "research" / "prereg" / "variance_nyaya.json").write_text(
        json.dumps({"bench": "gsm8k-trainC", "sigma_seed": 0.0249})
    )
    with pytest.raises(NightPlanError, match="measured on bench"):
        resolve(root, objective="prabhasa-nyaya")


def test_a_missing_floor_names_the_study_that_writes_it(root: Path) -> None:
    (root / "research" / "prereg" / "variance_nyaya.json").unlink()
    with pytest.raises(NightPlanError, match="study noise-floor"):
        resolve(root, objective="prabhasa-nyaya")


def test_a_config_that_names_no_floor_falls_back_to_the_model_track_file(root: Path) -> None:
    path = root / "research" / "prereg" / "bare_night.yaml"
    path.write_text(yaml.safe_dump({"model": "m", "bench": "gsm8k-trainD"}))
    plan = resolve(root, config=path)
    assert plan.variance.name == "variance.json"


def test_the_plan_carries_the_loaded_config_so_callers_do_not_re_read_it(root: Path) -> None:
    plan = resolve(root, objective="prabhasa-nyaya")
    assert plan.cfg["evaluation"]["k_items"] == 96
    assert plan.floor["sigma_seed"] == 0.0170


def test_the_default_pair_in_this_repository_is_checked_not_assumed(tmp_path: Path) -> None:
    """The check found a live mismatch the first time it ran, which is the argument for having it.

    `lora_night.yaml` runs bench `gsm8k-trainD` (ADR-0033 moved it to a fresh pool) while `variance.json` was
    measured on `gsm8k-trainC`. Both files are valid on their own, so nothing had gone red: every night since
    that ADR set its boundary from a sigma measured on a different pool. The floor has to be re-measured on
    trainD; until then `pravrudhi night` refuses with the reason rather than running on the wrong number.
    """
    prereg = tmp_path / "research" / "prereg"
    prereg.mkdir(parents=True)
    (prereg / "lora_night.yaml").write_text(yaml.safe_dump({**LORA, "bench": "gsm8k-trainD"}))
    (prereg / "variance.json").write_text(json.dumps({"bench": "gsm8k-trainC", "sigma_seed": 0.0249}))
    with pytest.raises(NightPlanError) as caught:
        resolve(tmp_path)
    message = str(caught.value)
    assert "gsm8k-trainD" in message and "gsm8k-trainC" in message
    assert "study noise-floor" not in message, "the floor exists; what is wrong is which pool it describes"


def test_the_plan_names_the_training_corpus_the_config_declares(root: Path) -> None:
    """Caught before the first nyaya night, not by it.

    `run_night` took `train_parquet` from the CLI, whose default is
    `.pravrudhi/data/gsm8k-train.parquet`. With the choice scorer now selected from the pool, a nyaya night
    would have rejection-sampled GSM8K rows and handed `steps\\n#### 18` to `mmlu.gold_answer`, which raises
    on anything that is not one option letter -- so the night would have died in `ensure_samples`, or worse,
    kept nothing and trained on an empty set.
    """
    prereg = root / "research" / "prereg"
    (prereg / "nyaya_night.yaml").write_text(
        yaml.safe_dump({**NYAYA, "training": {"corpus": ".pravrudhi/data/casehold-train.parquet"}})
    )
    plan = resolve(root, objective="prabhasa-nyaya")
    assert plan.train_corpus == root / ".pravrudhi" / "data" / "casehold-train.parquet"


def test_a_config_with_no_training_corpus_leaves_the_caller_its_default(root: Path) -> None:
    # The model track passes `--train-parquet` and must keep working unchanged.
    assert resolve(root).train_corpus is None

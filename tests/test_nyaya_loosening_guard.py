import math
from pathlib import Path

import pytest
import yaml

from pravrudhi.application.nyaya_loosening_guard import (
    LooseningRefused,
    guarded_candidate_config,
    reject_loosening,
    reject_loosening_env,
)

REAL = yaml.safe_load(
    (Path(__file__).resolve().parents[1] / "configs" / "nyaya_agent.yaml").read_text()
)  # R2 ran the guard against the real baseline
BASE = {
    "tau": 0.74,
    "refer_band": [0.5, 0.74],
    "house_judge": {"tau": 0.74, "label_mass_floor": 0.5},
    "second_judge": {"tau": 0.97, "refer_logit_delta": 0.125},
    "validated_contracts": ["a", "b"],
}
NAN, INF = float("nan"), float("inf")


@pytest.mark.parametrize(
    "cand",
    [
        {"tau": 0.7},
        {"house_judge": {"tau": 0.7}},
        {"second_judge": {"tau": 0.9}},
        {"second_judge": {"refer_logit_delta": 0.1}},
        {"second_judge": None},
        {"second_judge": {}},
        {"refer_band": [0.6, 0.74]},
        {"refer_band": [0.5, 0.7]},
        {"refer_band": None},
        {"house_judge": {"label_mass_floor": 0.4}},
        {"validated_contracts": ["a", "b", "c"]},
        {"tau": "0.9"},
        {"tau": True},
        {"house_judge": {"quote_min_chars": 1}},
        {"quote_mode": "loose"},
    ],
)
def test_loosening_candidate_is_rejected(cand):
    assert reject_loosening(cand, BASE), cand


@pytest.mark.parametrize(
    "cand",
    [
        {},
        {"tau": 0.8},
        {"second_judge": {"tau": 0.98, "refer_logit_delta": 0.2}},
        {"refer_band": [0.4, 0.8]},
        {"house_judge": {"label_mass_floor": 0.6}},
        {"validated_contracts": ["a"]},
        {"house_judge": {"prompt_variant": "v2"}},
        {"house_judge": {"max_concurrency": 4}},
    ],
)
def test_stricter_prompt_and_wiring_candidates_pass(cand):
    assert reject_loosening(cand, BASE) == []


# ---- R2's holes (6 Oct) ----
@pytest.mark.parametrize("cand", [None, [], [1, 2], "x", 0.9, ()])
def test_a_non_dict_candidate_is_rejected_not_accepted_as_empty(cand):
    assert reject_loosening(cand, BASE)


@pytest.mark.parametrize(
    "cand",
    [
        {"tau": NAN},
        {"tau": INF},
        {"tau": -INF},
        {"second_judge": {"tau": NAN}},
        {"second_judge": {"refer_logit_delta": NAN}},
        {"refer_band": [NAN, NAN]},
        {"refer_band": [0.5, NAN]},
        {"refer_band": [NAN, 0.74]},
        {"refer_band": [0.5, INF]},
        {"house_judge": {"label_mass_floor": NAN}},
    ],
)
def test_non_finite_numbers_are_rejected(cand):
    assert reject_loosening(cand, BASE), cand


@pytest.mark.parametrize("vc", [[["x"]], [{"x": 1}], [None], [1], "ab", {"a": 1}, None])
def test_unhashable_or_malformed_validated_contracts_do_not_raise_and_are_rejected(vc):
    assert reject_loosening({"validated_contracts": vc}, BASE)  # no TypeError


@pytest.mark.parametrize(
    "cand",
    [
        {"gate1": {"threshold": 0.9}},
        {"gate1": {"tau_c": 0.1}},
        {"gate1": {"mode": "contradiction_veto"}},
        {"second_judge_positive_control": {"ne_discrimination_tau": 0.5}},
        {"second_judge_positive_control": {"parity_median_abs_dp": 0.5}},
        {"second_judge_positive_control": {"est_accuracy_alert_drop": 99}},
        {"house_judge": {"model": "base"}},
        {"house_judge": {"base_url": "http://evil/v1"}},
        {"house_judge": {"statute_chars": 10}},
        {"house_judge": {"top_logprobs": 1}},
        {"house_judge": {"max_tokens": 1}},
        {"house_judge": {"timeout_s": 1}},
        {"max_retries": 0},
        {"retention_days": 0},
        {"audit_dir": "/tmp/x"},
        {"score_bin": "/bin/true"},
        {"pinned_score_sha256": "0" * 64},
        {"judge_statute_text": {"ni138": "x"}},
        {"second_judge": {"model": "base"}},
        {"second_judge": {"base_url": "http://evil/v1"}},
        {"x": [{"quote": "any"}]},
        {"unknown_new_knob": 1},
    ],
)
def test_unnamed_knobs_are_denied_by_default(cand):
    assert reject_loosening(cand, REAL), cand


def test_the_real_baseline_accepts_a_stricter_and_a_wiring_candidate_and_refuses_a_loosening_one():
    assert reject_loosening({"tau": 0.8, "house_judge": {"prompt_variant": "v2"}, "refer_band": [0.45, 0.8]}, REAL) == []
    assert reject_loosening({"tau": REAL["tau"] - 0.01}, REAL)
    assert reject_loosening({"second_judge_positive_control": {"parity_floor": 0.99, "ne_discrimination_min": 71}}, REAL) == []
    assert reject_loosening({"second_judge_positive_control": {"parity_floor": 0.9}}, REAL)


def test_tightening_is_relative_to_the_baseline_value_not_only_the_floor():
    assert reject_loosening(
        {"second_judge_positive_control": {"ne_discrimination_min": 60}}, REAL
    )  # below the baseline 70, above the zero floor
    assert reject_loosening({"tau": 0.75}, {"tau": 0.8}) and not reject_loosening(
        {"tau": 0.8}, {"tau": 0.8}
    )  # a baseline above the floor binds


# ---- environment overrides ----
@pytest.mark.parametrize(
    "env",
    [
        {"NYAYA_HOUSE_JUDGE_TAU": "0.5"},
        {"NYAYA_SECOND_JUDGE_TAU": "0.9"},
        {"NYAYA_SECOND_JUDGE_REFER_LOGIT_DELTA": "0.01"},
        {"NYAYA_SECOND_JUDGE_LABEL_MASS_FLOOR": "0.1"},
        {"NYAYA_HOUSE_JUDGE_TAU": "nan"},
        {"NYAYA_HOUSE_JUDGE_TAU": "abc"},
        {"NYAYA_HOUSE_JUDGE_TAU_ALLOW_LOWER": "1"},
        {"NYAYA_GATE1_ENABLED": "0"},
        {"NYAYA_GATE1_THRESHOLD": "0.9"},
        {"NYAYA_HOUSE_JUDGE_MODEL": "base"},
        {"NYAYA_HOUSE_JUDGE_BASE_URL": "http://evil"},
        {"NYAYA_SECOND_JUDGE_MODEL": "base"},
        {"NYAYA_HOUSE_JUDGE_API_KEY": "k"},
        {"NYAYA_SPAN_RELEVANCE_ENABLED": "0"},
        {"NYAYA_SECOND_JUDGE_POSITIVE_CONTROL_RECORD_PATH": "/x"},
        {"NYAYA_ANYTHING_NEW": "1"},
    ],
)
def test_loosening_or_unlisted_env_overrides_are_rejected(env):
    assert reject_loosening_env(env, REAL), env


def test_stricter_listed_env_overrides_pass(monkeypatch):
    assert reject_loosening_env({"NYAYA_SECOND_JUDGE_TAU": "0.98", "NYAYA_JUDGE_MAX_CONCURRENCY": "4"}, REAL) == []
    assert reject_loosening_env({}, REAL) == []
    for k in list(__import__("os").environ):
        if k.startswith(("NYAYA_", "PRAVRUDHI_", "PRABHASA_")):
            monkeypatch.delenv(k)
    assert reject_loosening_env(None, REAL) == []  # a clean ambient environment


# ---- the sanctioned caller ----
def test_guarded_candidate_config_merges_a_clean_candidate_and_does_not_mutate_the_baseline():
    before = yaml.safe_dump(REAL)
    out = guarded_candidate_config(
        REAL, {"tau": 0.8, "house_judge": {"prompt_variant": "v2"}}, {"NYAYA_JUDGE_MAX_CONCURRENCY": "2"}
    )
    assert (
        out["tau"] == 0.8
        and out["house_judge"]["prompt_variant"] == "v2"
        and out["house_judge"]["label_mass_floor"] == REAL["house_judge"]["label_mass_floor"]
    )
    assert yaml.safe_dump(REAL) == before


def test_guarded_candidate_config_refuses_before_merging_and_lists_every_violation():
    with pytest.raises(LooseningRefused) as e:
        guarded_candidate_config(REAL, {"tau": 0.5, "max_retries": 0}, {"NYAYA_GATE1_ENABLED": "0"})
    assert len(e.value.violations) == 3 and math.isfinite(len(e.value.violations))


def test_the_phantom_house_judge_tau_name_is_not_allowlisted():
    from pravrudhi.application.nyaya_loosening_guard import ENV_PATHS

    assert "NYAYA_HOUSE_JUDGE_TAU" not in ENV_PATHS  # the name does not exist in the engine: an allowlist entry for it is a trap


@pytest.mark.parametrize(
    "cand",
    [
        {"judge_prompt": {"standard_line": True}},
        {"judge_prompt": {"standard_line": 1}},
        {"judge_prompt": {"standard_line": None}},
    ],
)
def test_a_change_to_standard_line_is_refused_it_moves_the_operating_point(cand):
    v = reject_loosening(cand, REAL)
    assert v and any("standard_line" in m for m in v)


def test_standard_line_equal_to_the_baseline_is_not_a_change_and_an_absent_baseline_value_is_refused():
    assert reject_loosening({"judge_prompt": {"standard_line": REAL["judge_prompt"]["standard_line"]}}, REAL) == []
    assert reject_loosening({"judge_prompt": {"standard_line": False}}, {"tau": 0.74})  # no baseline value to compare: refused


def test_env_none_means_the_ambient_environment_not_no_environment(monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith(("NYAYA_", "PRAVRUDHI_", "PRABHASA_")):
            monkeypatch.delenv(k)
    assert guarded_candidate_config(REAL, {"tau": 0.8})["tau"] == 0.8  # clean ambient
    monkeypatch.setenv("NYAYA_SECOND_JUDGE_TAU", "0.5")
    with pytest.raises(LooseningRefused, match="NYAYA_SECOND_JUDGE_TAU"):
        guarded_candidate_config(REAL, {"tau": 0.8})  # env=None: the ambient loosening override is NOT silently ignored
    monkeypatch.delenv("NYAYA_SECOND_JUDGE_TAU")
    monkeypatch.setenv("PRAVRUDHI_AUTH", "off")
    with pytest.raises(LooseningRefused, match="PRAVRUDHI_AUTH"):
        guarded_candidate_config(REAL, {"tau": 0.8})  # default-deny whatever the prefix


def test_unrelated_ambient_variables_are_not_part_of_a_candidate(monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith(("NYAYA_", "PRAVRUDHI_", "PRABHASA_")):
            monkeypatch.delenv(k)
    monkeypatch.setenv("PATH", "/usr/bin")
    assert guarded_candidate_config(REAL, {})  # PATH/HOME and friends do not trip the guard when the env is the ambient one


def test_an_explicit_empty_env_means_no_environment_overrides(monkeypatch):
    monkeypatch.setenv("NYAYA_SECOND_JUDGE_TAU", "0.5")
    assert (
        guarded_candidate_config(REAL, {"tau": 0.8}, {})["tau"] == 0.8
    )  # the caller states the environment explicitly; the ambient one is not read

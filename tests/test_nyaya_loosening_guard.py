import pytest

from pravrudhi.application.nyaya_loosening_guard import reject_loosening

BASE = {"tau": 0.74, "refer_band": [0.5, 0.74], "house_judge": {"tau": 0.74, "label_mass_floor": 0.5},
        "second_judge": {"tau": 0.97, "refer_logit_delta": 0.125}, "validated_contracts": ["a", "b"]}


@pytest.mark.parametrize("cand", [
    {"tau": 0.7}, {"house_judge": {"tau": 0.7}}, {"second_judge": {"tau": 0.9}}, {"second_judge": {"refer_logit_delta": 0.1}},
    {"second_judge": None}, {"second_judge": {}}, {"refer_band": [0.6, 0.74]}, {"refer_band": [0.5, 0.7]}, {"refer_band": None},
    {"house_judge": {"label_mass_floor": 0.4}}, {"validated_contracts": ["a", "b", "c"]}, {"tau": "0.9"}, {"tau": True},
    {"house_judge": {"quote_min_chars": 1}}, {"quote_mode": "loose"},
])
def test_loosening_candidate_is_rejected(cand):
    assert reject_loosening(cand, BASE), cand


@pytest.mark.parametrize("cand", [
    {}, {"tau": 0.8}, {"second_judge": {"tau": 0.98, "refer_logit_delta": 0.2}}, {"refer_band": [0.4, 0.8]},
    {"house_judge": {"label_mass_floor": 0.6}}, {"validated_contracts": ["a"]}, {"max_retries": 1},
    {"house_judge": {"prompt_variant": "v2"}}, {"max_concurrency": 4},
])
def test_stricter_prompt_and_wiring_candidates_pass(cand):
    assert reject_loosening(cand, BASE) == []

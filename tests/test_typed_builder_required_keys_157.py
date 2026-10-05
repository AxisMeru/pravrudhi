"""#157: the typed branch of `_build_house_judge` requires the keys `HouseJudge.from_config` requires. Constructed."""

from __future__ import annotations

import pytest

from pravrudhi.application import nyaya_agent
from pravrudhi.application.nyaya_judges import HouseJudge

_CFG = {
    "base_url": "http://127.0.0.1:1/v1",
    "model": "m",
    "statute_chars": 600,
    "max_tokens": 30,
    "top_logprobs": 20,
    "timeout_s": 5,
    "label_mass_floor": 0.5,
}


@pytest.mark.parametrize("missing", ["timeout_s", "max_tokens", "top_logprobs"])
def test_typed_builder_requires_the_same_keys_as_house_judge_from_config(missing: str) -> None:
    cfg = {k: v for k, v in _CFG.items() if k != missing}
    with pytest.raises(KeyError, match=missing):
        HouseJudge.from_config(cfg, tau=0.74, api_key_env="X_UNSET")
    with pytest.raises(KeyError, match=missing):
        nyaya_agent._build_house_judge(cfg, tau=0.74, typed=True, api_key_env="X_UNSET")


def test_a_complete_config_still_builds_the_typed_judge() -> None:
    judge = nyaya_agent._build_house_judge(_CFG, tau=0.74, typed=True, api_key_env="X_UNSET")
    assert (judge.max_tokens, judge.top_logprobs) == (30, 20)  # type: ignore[attr-defined]

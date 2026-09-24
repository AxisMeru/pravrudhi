"""`NyayaAgent.house`'s `typed_layer` config flag (T1, docs/decisions/TYPED-LAYER-PLAN-2026-09-24.md): the
typed path sits behind this flag, default off. No network call happens during construction here -- every
`house_judge` config below names its model explicitly, so `ChatClient`/`VLLMDecoder.__init__` never round-
trips to `/models` (only `.judge()` would, and nothing here calls it).
"""

from __future__ import annotations

from pathlib import Path

from pravrudhi.application.nyaya_agent import AgentConfig, NyayaAgent, load_agent_config
from pravrudhi.application.nyaya_judges import HouseJudge
from pravrudhi.application.typed.house_judge import TypedHouseJudge


def _config(tmp_path: Path, **over: object) -> AgentConfig:
    score_bin = tmp_path / "score"
    if not score_bin.exists():
        score_bin.write_bytes(b"")  # BinaryRegistry only checks existence at construction; never invoked here
    base: dict[str, object] = {
        "tau": 0.74,
        "refer_band": (0.5, 0.74),
        "max_retries": 2,
        "audit_dir": tmp_path / "audit",
        "score_bin": score_bin,
        "house_judge": {
            "base_url": "http://fake.invalid/v1", "model": "m", "statute_chars": 600, "max_tokens": 30,
            "top_logprobs": 20, "timeout_s": 60,
        },
    }
    base.update(over)
    return AgentConfig(**base)  # type: ignore[arg-type]


class TestTypedLayerFlagDefaultsOff:
    def test_agent_config_default_is_not_typed(self, tmp_path: Path) -> None:
        assert _config(tmp_path).typed_layer is False

    def test_house_with_no_flag_builds_the_original_house_judge(self, tmp_path: Path) -> None:
        agent = NyayaAgent.house(tmp_path, config=_config(tmp_path))
        assert type(agent.judge) is HouseJudge
        assert agent.judge.name == "house"

    def test_house_with_the_flag_set_builds_the_typed_house_judge_instead(self, tmp_path: Path) -> None:
        agent = NyayaAgent.house(tmp_path, config=_config(tmp_path, typed_layer=True))
        assert type(agent.judge) is TypedHouseJudge
        assert agent.judge.name == "house-typed"

    def test_both_paths_read_the_same_tau_and_statute_chars_from_house_judge_config(self, tmp_path: Path) -> None:
        cfg = _config(tmp_path)
        original = NyayaAgent.house(tmp_path, config=cfg).judge
        typed = NyayaAgent.house(tmp_path, config=_config(tmp_path, typed_layer=True)).judge
        assert (original.tau, original.statute_chars) == (typed.tau, typed.statute_chars) == (0.74, 600)


class TestLoadAgentConfigReadsTheFlag:
    def test_defaults_to_false_when_the_yaml_names_nothing(self, tmp_path: Path) -> None:
        (tmp_path / "configs").mkdir()
        (tmp_path / "configs" / "nyaya_agent.yaml").write_text(
            "refer_band: [0.5, 0.74]\nmax_retries: 2\naudit_dir: audit\ntau: 0.74\n"
        )
        assert load_agent_config(tmp_path).typed_layer is False

    def test_reads_true_when_the_yaml_sets_it(self, tmp_path: Path) -> None:
        (tmp_path / "configs").mkdir()
        (tmp_path / "configs" / "nyaya_agent.yaml").write_text(
            "refer_band: [0.5, 0.74]\nmax_retries: 2\naudit_dir: audit\ntau: 0.74\ntyped_layer: true\n"
        )
        assert load_agent_config(tmp_path).typed_layer is True

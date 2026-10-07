"""#192: the house prompt may state the legal standard (from the proceeding posture), behind `prompt_template` (default legacy).

Toy text only. The legacy prompt is the training prompt and must stay byte-identical.
"""

from __future__ import annotations

import pytest

from pravrudhi.application.nyaya_judges import (
    STANDARD_LINES,
    HouseJudge,
    JudgeRequest,
    build_house_prompt,
    standard_for_posture,
)


def _req(posture: str | None = None) -> JudgeRequest:
    facts = (("F1", "TOY fact one."),)
    return JudgeRequest("c1", "el one", False, "TOY statute text.", "TOY narrative.", facts, proceeding_posture=posture)


LEGACY = (
    "Statute: TOY statute text.\nScenario: TOY narrative.\nElement to judge: el one\n"
    "Available facts:\n[F1] TOY fact one.\nAnswer:"
)


def test_legacy_prompt_is_byte_identical_whatever_the_standard() -> None:
    assert build_house_prompt(_req(), statute_chars=600) == LEGACY
    assert build_house_prompt(_req("proved"), statute_chars=600) == LEGACY
    assert build_house_prompt(_req("proved"), statute_chars=600, prompt_template="legacy") == LEGACY


def test_the_standard_strings_are_pinned() -> None:
    assert STANDARD_LINES == {
        "prima_facie_disclosed": "Take the allegations and material as true and complete, without weighing defences or evidence. "
        "Does the record, on its face, disclose this element?",
        "proved": "Judge whether the evidence in the record establishes this element beyond reasonable doubt. "
        "Allegations alone, or suspicion, do not establish it.",
    }


@pytest.mark.parametrize(
    ("posture", "standard"),
    [("quash", "prima_facie_disclosed"), ("discharge", "prima_facie_disclosed"), ("trial", "proved"), ("appeal", "proved")],
)
def test_each_posture_maps_to_its_standard_line(posture: str, standard: str) -> None:
    assert standard_for_posture(posture) == (standard, "request")
    out = build_house_prompt(_req(posture), statute_chars=600, prompt_template="standard_line_v1")
    assert out == LEGACY.replace("el one\n", f"el one\nStandard: {STANDARD_LINES[standard]}\n")


def test_an_absent_posture_is_the_stricter_proved_and_says_so() -> None:
    assert standard_for_posture(None) == ("proved", "default_proved")
    out = build_house_prompt(_req(None), statute_chars=600, prompt_template="standard_line_v1")
    assert out == LEGACY.replace("el one\n", f"el one\nStandard: {STANDARD_LINES['proved']}\n")


@pytest.mark.parametrize("posture", ["", "bail", "TRIAL"])
def test_an_invalid_posture_raises(posture: str) -> None:
    with pytest.raises(ValueError, match="proceeding_posture"):
        standard_for_posture(posture)
    with pytest.raises(ValueError, match="proceeding_posture"):
        build_house_prompt(_req(posture), statute_chars=600, prompt_template="standard_line_v1")


def test_an_unknown_template_raises() -> None:
    with pytest.raises(ValueError, match="prompt_template"):
        build_house_prompt(_req(), statute_chars=600, prompt_template="v2")


_CFG = {"statute_chars": 600, "base_url": "http://localhost:1/v1", "max_tokens": 30, "top_logprobs": 20, "timeout_s": 5,
        "label_mass_floor": 0.5}


def test_the_judge_defaults_to_legacy_and_reads_the_config_flag() -> None:
    assert HouseJudge.from_config(_CFG, tau=0.5).prompt_template == "legacy"
    assert HouseJudge.from_config({**_CFG, "prompt_template": "standard_line_v1"}, tau=0.5).prompt_template == "standard_line_v1"
    with pytest.raises(ValueError):
        HouseJudge.from_config({**_CFG, "prompt_template": "nope"}, tau=0.5)


def test_the_shipped_config_default_is_legacy() -> None:
    from pathlib import Path

    import yaml

    from pravrudhi.application import nyaya_agent

    path = Path(nyaya_agent.__file__).parents[3] / "configs" / "nyaya_agent.yaml"
    cfg = yaml.safe_load(path.read_text())
    assert "prompt_template" not in cfg["house_judge"]


def _body_cfg(tmp_path, extra: str, house_extra: str = ""):
    from pathlib import Path

    import yaml

    from pravrudhi.application import nyaya_agent

    path = Path(nyaya_agent.__file__).parents[3] / "configs" / "nyaya_agent.yaml"
    body = yaml.safe_load(path.read_text())
    body.update(yaml.safe_load(extra) or {})
    body["house_judge"].update(yaml.safe_load(house_extra) or {})
    root = tmp_path / "root"
    (root / "configs").mkdir(parents=True)
    (root / "configs" / "nyaya_agent.yaml").write_text(yaml.safe_dump(body))
    return nyaya_agent, root


def test_shipped_judge_prompt_flag_is_false_and_prompt_is_the_legacy_bytes(tmp_path) -> None:
    from pathlib import Path

    import yaml

    from pravrudhi.application import nyaya_agent

    path = Path(nyaya_agent.__file__).parents[3] / "configs" / "nyaya_agent.yaml"
    assert yaml.safe_load(path.read_text())["judge_prompt"] == {"standard_line": False, "canonical_layout": False}
    mod, root = _body_cfg(tmp_path, "")
    hj = mod.load_agent_config(root).house_judge
    assert hj["prompt_template"] == "legacy"
    assert build_house_prompt(_req("quash"), statute_chars=600, prompt_template=hj["prompt_template"]) == build_house_prompt(
        _req("quash"), statute_chars=600
    )


def test_the_flag_alone_selects_the_standard_line_template(tmp_path) -> None:
    mod, root = _body_cfg(tmp_path, "judge_prompt: {standard_line: true}")
    assert mod.load_agent_config(root).house_judge["prompt_template"] == "standard_line_v1"


def test_a_conflicting_explicit_prompt_template_is_refused(tmp_path) -> None:
    mod, root = _body_cfg(tmp_path, "", "prompt_template: standard_line_v1")
    with pytest.raises(ValueError, match="judge_prompt.standard_line"):
        mod.load_agent_config(root)

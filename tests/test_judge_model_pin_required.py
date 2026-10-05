"""#237: a null judge model fails closed at config load in every edition but an explicit development one; it never
resolves to the first id the server lists (the dev 32B lists the BASE snapshot first)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pravrudhi.application import nyaya_agent
from pravrudhi.models.openai_compat import ChatClient

REPO = Path(nyaya_agent.__file__).parents[3]
ENV_VARS = (
    "PRAVRUDHI_EDITION", "NYAYA_HOUSE_JUDGE_MODEL", "NYAYA_SECOND_JUDGE_MODEL", "NYAYA_SECOND_JUDGE_BASE_URL",
    "NYAYA_SECOND_JUDGE_TAU",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in ENV_VARS:
        monkeypatch.delenv(v, raising=False)


def _root(tmp_path: Path, house: dict | None = None, second: dict | None = None) -> Path:
    body = yaml.safe_load((REPO / "configs" / "nyaya_agent.yaml").read_text())
    body["house_judge"].update(house or {})
    if second is not None:
        body["second_judge"] = second
    root = tmp_path / "root"
    (root / "configs").mkdir(parents=True)
    (root / "configs" / "nyaya_agent.yaml").write_text(yaml.safe_dump(body))
    return root


def test_the_packaged_yaml_no_longer_ships_a_null_model() -> None:
    body = yaml.safe_load((REPO / "configs" / "nyaya_agent.yaml").read_text())
    assert "model" not in body["house_judge"]
    assert "model: null" not in (REPO / "configs" / "nyaya_agent.yaml").read_text()


@pytest.mark.parametrize("edition", ["product", "studio", "Product", " STUDIO ", "prod", "null", "stage", ""])
def test_a_deployed_edition_refuses_an_unset_house_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edition: str
) -> None:
    monkeypatch.setenv("PRAVRUDHI_EDITION", edition)
    with pytest.raises(ValueError, match="house_judge.model is not set"):
        nyaya_agent.load_agent_config(_root(tmp_path))


def test_an_explicit_null_or_blank_model_is_refused_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_EDITION", "product")
    for i, bad in enumerate((None, "", "   ")):
        with pytest.raises(ValueError, match="house_judge.model is not set"):
            nyaya_agent.load_agent_config(_root(tmp_path / f"r{i}", {"model": bad}))


def test_the_env_var_or_the_yaml_can_name_the_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_EDITION", "product")
    monkeypatch.setenv("NYAYA_HOUSE_JUDGE_MODEL", "nyaya-judge-4b")
    assert nyaya_agent.load_agent_config(_root(tmp_path / "a")).house_judge["model"] == "nyaya-judge-4b"
    monkeypatch.delenv("NYAYA_HOUSE_JUDGE_MODEL")
    cfg = nyaya_agent.load_agent_config(_root(tmp_path / "b", {"model": "nyaya-judge-4b"}))
    assert cfg.house_judge["model"] == "nyaya-judge-4b"


def test_a_second_judge_block_must_name_its_model_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_EDITION", "studio")
    monkeypatch.setenv("NYAYA_HOUSE_JUDGE_MODEL", "nyaya-judge-4b")
    second = {"base_url": "http://127.0.0.1:8111/v1", "tau": 0.97}
    with pytest.raises(ValueError, match="second_judge.model is not set"):
        nyaya_agent.load_agent_config(_root(tmp_path / "a", second=second))
    monkeypatch.setenv("NYAYA_SECOND_JUDGE_MODEL", "judge32b")
    assert nyaya_agent.load_agent_config(_root(tmp_path / "b", second=second)).second_judge["model"] == "judge32b"


def test_an_env_built_second_judge_without_a_model_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_EDITION", "product")
    monkeypatch.setenv("NYAYA_HOUSE_JUDGE_MODEL", "nyaya-judge-4b")
    monkeypatch.setenv("NYAYA_SECOND_JUDGE_BASE_URL", "http://127.0.0.1:8111/v1")
    monkeypatch.setenv("NYAYA_SECOND_JUDGE_TAU", "0.97")
    with pytest.raises(ValueError, match="second_judge.model is not set"):
        nyaya_agent.load_agent_config(_root(tmp_path))


def test_only_an_explicit_dev_edition_on_a_development_checkout_may_leave_it_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRAVRUDHI_EDITION", "dev")
    assert nyaya_agent.load_agent_config(_root(tmp_path / "a")).house_judge.get("model") is None
    monkeypatch.delenv("PRAVRUDHI_EDITION")  # unlabelled is not "dev": enforced
    with pytest.raises(ValueError, match="house_judge.model is not set"):
        nyaya_agent.load_agent_config(_root(tmp_path / "b"))


def test_a_release_install_counts_as_product_with_the_env_unset_or_dev(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pravrudhi.api import edition

    monkeypatch.setattr(edition, "is_release_install", lambda: True)
    with pytest.raises(ValueError, match="in the pravrudhi edition"):
        nyaya_agent.load_agent_config(_root(tmp_path / "a"))
    monkeypatch.setenv("PRAVRUDHI_EDITION", "dev")  # a release install is not a development checkout
    with pytest.raises(ValueError, match="house_judge.model is not set"):
        nyaya_agent.load_agent_config(_root(tmp_path / "b"))


@pytest.mark.parametrize("bad", ["null", "NULL", "None", "none", "~", " null "])
def test_the_literal_string_null_is_refused_like_a_real_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv("PRAVRUDHI_EDITION", "product")
    with pytest.raises(ValueError, match="house_judge.model is not set"):
        nyaya_agent.load_agent_config(_root(tmp_path, {"model": bad}))
    monkeypatch.setenv("NYAYA_HOUSE_JUDGE_MODEL", bad)
    with pytest.raises(ValueError, match="house_judge.model is not set"):
        nyaya_agent.load_agent_config(_root(tmp_path / "env"))


def test_the_dev_32b_listing_order_never_decides_a_deployed_judge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The dev 32B lists the base snapshot first. With a model named, that listing is never consulted; without
    one, a deployed edition does not even load the config, so the base snapshot can never be picked."""
    base = "/models/Qwen2.5-32B-Instruct"
    monkeypatch.setattr(ChatClient, "list_models", lambda self: [base, "judge32b"])
    monkeypatch.setenv("PRAVRUDHI_EDITION", "product")
    with pytest.raises(ValueError, match="house_judge.model is not set"):
        nyaya_agent.load_agent_config(_root(tmp_path / "a"))
    cfg = nyaya_agent.load_agent_config(_root(tmp_path / "b", {"model": "judge32b"}))
    assert cfg.house_judge["model"] == "judge32b" and cfg.house_judge["model"] != base


def test_the_server_warmup_logs_a_refused_judge_config_instead_of_swallowing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from pravrudhi.api.server import _warm_up_house_judge_in_background

    monkeypatch.setenv("PRAVRUDHI_EDITION", "product")
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "nyaya_agent.yaml").write_text((REPO / "configs" / "nyaya_agent.yaml").read_text())
    with caplog.at_level("ERROR"):
        _warm_up_house_judge_in_background(tmp_path)  # must not raise: it never blocks startup
    assert "house judge warm-up not started" in caplog.text and "house_judge.model is not set" in caplog.text
    caplog.clear()
    with caplog.at_level("ERROR"):
        _warm_up_house_judge_in_background(tmp_path / "nowhere")  # no config at all: quiet, as before
    assert "warm-up not started" not in caplog.text

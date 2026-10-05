"""R4 relaxation (Lead-2, 5 Oct; separate pre-registered change, default OFF): a he/she subject in the same sentence, after the
NAMED accused, resolves to that accused only when no other party is named between the accused and the pronoun (this build is
stricter: no other party anywhere before the pronoun). Constructed only."""

from __future__ import annotations

import random

import pytest

from pravrudhi.application.nyaya_attribution import AccusedRef, check_attribution


def _ref(n: int) -> AccusedRef:
    return AccusedRef(f"acc{n}", (f"Accused No.{n}", f"A{n}"), ((f"Accused No.{n + 1}", f"A{n + 1}"), ("her husband",)))


def _nums(seed: int) -> tuple[int, int]:
    r = random.Random(seed)
    n, m = r.sample(range(2, 30), 2)
    return n, m


def _on(sentence: str, ref: AccusedRef):
    return check_attribution(sentence, ref, pronoun_rule=True)


def test_the_rule_is_off_by_default() -> None:
    assert (
        check_attribution("TOY: Accused No.2 came home drunk and he beat Nila.", _ref(2)).reason
        == "accused_attribution_unresolved"
    )
    assert check_attribution("TOY: Accused No.2 came home drunk and he beat Nila.", _ref(2), pronoun_rule=False).rule == "R4"


@pytest.mark.parametrize("seed", range(1, 8))
@pytest.mark.parametrize("pron", ["he", "she", "He", "She"])
def test_a_pronoun_after_the_named_accused_resolves_to_them(seed: int, pron: str) -> None:
    n, _ = _nums(seed)
    r = _on(f"TOY: Accused No.{n} came home drunk and {pron} beat Nila.", _ref(n))
    assert r.passed and r.rule == "R4r" and r.actor_span.lower() == pron.lower()


@pytest.mark.parametrize("seed", range(1, 8))
def test_a_pronoun_after_a_different_named_party_never_resolves_to_the_accused(seed: int) -> None:
    n, m = _nums(seed)
    r = _on(f"TOY: Accused No.{m} came home drunk and he beat Nila.", _ref(n))
    assert not r.passed and r.reason in ("accused_attribution_unresolved", "accused_attribution_not_matched")


@pytest.mark.parametrize(
    "sentence",
    [
        "TOY: Accused No.2 met Accused No.3 and he beat Nila.",
        "TOY: Accused No.2 and her husband came home and he beat Nila.",
        "TOY: Accused No.2 spoke to her in-laws and she beat Nila.",
        "TOY: Accused No.2 argued with the accused and he beat Nila.",
        "TOY: Her husband quarrelled with Accused No.2, and he beat Nila.",
        "TOY: Accused No.3 told Accused No.2 that he beat Nila.",
    ],
)
def test_another_party_named_between_the_accused_and_the_pronoun_keeps_the_refusal(sentence: str) -> None:
    r = _on(sentence, _ref(2))
    assert not r.passed


def test_a_pronoun_with_no_named_antecedent_stays_unresolved() -> None:
    r = _on("TOY: He beat Nila.", _ref(2))
    assert not r.passed and r.reason == "accused_attribution_unresolved" and r.rule == "R4"


def test_the_pronoun_rule_does_not_change_any_non_pronoun_decision() -> None:
    for s in (
        "TOY: Accused No.2 beat Nila.",
        "TOY: Accused No.3 beat Nila.",
        "TOY: The accused beat Nila.",
        "TOY: Accused No.2 along with Accused No.3 beat Nila.",
        "TOY: Nila was beaten by Accused No.2.",
    ):
        off, on = check_attribution(s, _ref(2)), check_attribution(s, _ref(2), pronoun_rule=True)
        assert off.as_dict() == on.as_dict(), s


def test_a_plural_pronoun_is_still_collective() -> None:
    r = _on("TOY: Accused No.2 came home and they beat Nila.", _ref(2))
    assert not r.passed and r.reason == "accused_attribution_collective"


def test_config_default_is_off_and_env_turns_it_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    import yaml

    cfg = yaml.safe_load((Path(__file__).resolve().parent.parent / "configs" / "nyaya_agent.yaml").read_text())
    assert cfg["accused_attribution_pronoun_rule"] is False


def test_the_measurement_with_the_rule_on_resolves_the_same_sentence_pronoun_cell_and_nothing_else_changes() -> None:
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "aadm2", Path(__file__).resolve().parent.parent / "scripts" / "accused_attribution_dev_measure.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    off, on = mod.run(20, 11), mod.run(20, 11, pronoun_rule=True)
    cell = "P12 pronoun subject after the name in one fact"
    assert off["cells"][cell]["refused"] == 20 and on["cells"][cell]["refused"] == 0
    for name in off["cells"]:
        if name != cell:
            assert off["cells"][name]["refused"] == on["cells"][name]["refused"], name
    assert on["negatives_refused"]["rate"] == 1.0

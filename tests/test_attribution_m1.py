"""M1 (hybrid actor selection): a scripted selector or a scripted completions backend stands in for the model. Constructed
only."""

from __future__ import annotations

import math

import pytest

from pravrudhi.application import nyaya_attribution as A
from pravrudhi.application import nyaya_attribution_m1 as M
from pravrudhi.models.openai_compat import CompletionResult


class Pick:
    """A selector that always returns a fixed option text (or None) and counts its calls."""

    name = "pick"

    def __init__(self, text: str | None, p: float = 0.9) -> None:
        self.text, self.p, self.calls = text, p, []

    def select(self, quote: str, options: list[str]) -> M.Selection:
        self.calls.append(options)
        if self.text is None:
            return M.Selection(None, self.p, "none_of_these")
        return M.Selection(options.index(self.text), self.p)


def _ref(n: int = 2) -> A.AccusedRef:
    return A.AccusedRef(f"a{n}", (f"Accused No.{n}", f"A{n}", "Rekha Menon"), (("Accused No.1", "A1"), ("her husband",)))


def test_the_model_is_not_called_when_the_answer_is_already_determined() -> None:
    sel = Pick("Accused No.2")
    assert M.check_attribution_m1("TOY: Accused No.2 beat Nila.", None, sel).reason == "accused_not_specified"
    assert M.check_attribution_m1("TOY: Accused No.2 and Gopal Rao beat Nila.", _ref(), sel).rule == "V1"
    assert M.check_attribution_m1("TOY: A-3 to A-9 beat Nila.", _ref(), sel).rule == "V2"
    assert M.check_attribution_m1("TOY: Nila was hurt.", _ref(), sel).rule == "M1-no-candidate"
    assert sel.calls == []


def test_pronouns_are_never_offered_but_unlisted_names_are() -> None:
    sel = Pick("Accused No.2")
    r = M.check_attribution_m1("TOY: Accused No.2 came home drunk and he beat Nila Rao.", _ref(), sel)
    assert r.passed and r.variant == "M1" and r.actor_p == 0.9
    assert sel.calls == [["Accused No.2", "Nila Rao"]]


@pytest.mark.parametrize(
    "chosen,sentence,passed,reason",
    [
        ("Accused No.2", "TOY: Accused No.2 beat Nila.", True, None),
        ("Accused No.1", "TOY: Accused No.1 beat Nila.", False, "accused_attribution_not_matched"),
        ("Her husband", "TOY: Her husband beat Nila, Accused No.2 watched.", False, "accused_attribution_not_matched"),
        ("Gopal Rao", "TOY: Gopal Rao beat Nila, said Accused No.2.", False, "accused_attribution_not_matched"),
        ("in-laws", "TOY: Her in-laws taunted Nila and Accused No.2 laughed.", False, "accused_attribution_collective"),
        (None, "TOY: Accused No.2 is named in the complaint.", False, "accused_attribution_unresolved"),
    ],
)
def test_decision_follows_the_selected_party(chosen: str | None, sentence: str, passed: bool, reason: str | None) -> None:
    r = M.check_attribution_m1(sentence, _ref(), Pick(chosen))
    assert (r.passed, r.reason) == (passed, reason)


def test_a_listed_party_joined_to_the_chosen_actor_is_collective_even_if_the_model_picks_the_accused() -> None:
    r = M.check_attribution_m1("TOY: Accused No.2 and Accused No.1 beat Nila.", _ref(), Pick("Accused No.2"))
    assert not r.passed and r.reason == "accused_attribution_collective" and r.rule in ("V3", "M1-R3")
    ok = M.check_attribution_m1("TOY: Accused No.2 beat Nila in front of Accused No.1.", _ref(), Pick("Accused No.2"))
    assert ok.passed


def test_a_selector_error_is_a_refusal_never_a_pass() -> None:
    class Boom:
        name = "boom"

        def select(self, quote: str, options: list[str]) -> M.Selection:
            raise RuntimeError("backend down")

    r = M.check_attribution_m1("TOY: Accused No.2 beat Nila.", _ref(), Boom())
    assert not r.passed and r.rule == "R5" and r.reason == "accused_attribution_unresolved"


# -- the completions-backed selector --------------------------------------------------------------------------------------------


class Backend:
    name = "fake"

    def __init__(self, top: dict[str, float]) -> None:
        self.top, self.prompts = top, []

    def complete(self, prompt: str, *, max_tokens: int, temperature: float, logprobs: int | None) -> CompletionResult:
        self.prompts.append((prompt, max_tokens, temperature, logprobs))
        return CompletionResult(text="A", model="m", top_logprobs=[self.top], wall_s=0.0)


def _lp(**p: float) -> dict[str, float]:
    return {k.replace("_", " "): math.log(v) for k, v in p.items()}


def test_the_selector_reads_one_letter_and_ends_the_prompt_at_answer() -> None:
    be = Backend({" B": math.log(0.9), " A": math.log(0.08), " N": math.log(0.01), "the": math.log(0.01)})
    sel = M.LlmActorSelector(be).select("TOY sentence", ["x", "y"])
    assert sel.index == 1 and sel.p == pytest.approx(0.9 / 0.99)
    prompt, max_tokens, temp, lps = be.prompts[0]
    assert prompt.endswith("Sentence: TOY sentence\nParties: A. x  B. y  N. none of these\nAnswer:")
    assert (max_tokens, temp, lps) == (1, 0.0, 20)


@pytest.mark.parametrize(
    "top,reason",
    [
        ({" A": math.log(0.5), " B": math.log(0.45)}, "low_confidence"),
        ({" A": math.log(0.1), "The": math.log(0.8)}, "low_mass_on_options"),
        ({" N": math.log(0.95), " A": math.log(0.02)}, "none_of_these"),
        ({}, "low_mass_on_options"),
    ],
)
def test_the_selector_refuses_unconfident_or_off_option_answers(top: dict[str, float], reason: str) -> None:
    sel = M.LlmActorSelector(Backend(top)).select("TOY", ["x", "y"])
    assert sel.index is None and sel.reason == reason


def test_the_selector_handles_no_logprobs_and_too_many_options_without_calling_twice() -> None:
    class Empty(Backend):
        def complete(self, *a, **k):
            return CompletionResult(text="", model="m", top_logprobs=[], wall_s=0.0)

    assert M.LlmActorSelector(Empty({})).select("TOY", ["x"]).reason == "no_logprobs"
    be = Backend({" A": 0.0})
    assert M.LlmActorSelector(be).select("TOY", [str(i) for i in range(13)]).reason == "too_many_options"
    assert be.prompts == []


def test_unlisted_two_word_names_become_candidates_and_dates_and_courts_do_not() -> None:
    opts = M.extract_options("On 12 March, Gopal Rao told the High Court that Accused No.2 beat Nila.", _ref())
    assert [c.text for c in opts] == ["Gopal Rao", "Accused No.2"]
    assert all(c.role != "pronoun" for c in M.extract_options("He beat her; they watched.", _ref()))


def test_the_default_result_dict_is_unchanged_for_d0() -> None:
    d = A.check_attribution("TOY: Accused No.2 beat Nila.", _ref()).as_dict()
    assert "actor_p" not in d and d["variant"] == "D0"


# -- wiring: judge and agent config ----------------------------------------------------------------------------------------------


def test_the_judge_uses_m1_only_when_a_selector_is_given() -> None:
    from pravrudhi.application.nyaya_attribution import AccusedAttributionJudge, AttributedJudgeRequest
    from pravrudhi.application.nyaya_judges import ElementJudgment

    class Inner:
        name = "inner"

        def judge(self, request):
            return ElementJudgment("established", 0.97, "F1", "Accused No.2 beat Nila.", "model")

    req = AttributedJudgeRequest(
        "bns85", "e", False, "stat", "n", (("F1", "Accused No.2 beat Nila."),), requires_actor=True, accused=_ref()
    )
    d0 = AccusedAttributionJudge(Inner()).judge(req).attribution
    m1 = AccusedAttributionJudge(Inner(), selector=Pick("Accused No.2")).judge(req).attribution
    assert d0["variant"] == "D0" and m1["variant"] == "M1" and m1["passed"] is True and m1["actor_p"] == 0.9


def test_config_selector_defaults_to_d0_validates_and_m1_needs_a_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    from pravrudhi.application.nyaya_agent import AgentConfig, load_agent_config

    assert AgentConfig.__dataclass_fields__["accused_attribution_selector"].default == "d0"
    root = Path(__file__).resolve().parents[1]
    for var in (
        "NYAYA_ACCUSED_ATTRIBUTION_SELECTOR",
        "NYAYA_ACCUSED_ATTRIBUTION_ENABLED",
        "NYAYA_ACCUSED_ATTRIBUTION_M1_BASE_URL",
        "NYAYA_ACCUSED_ATTRIBUTION_M1_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)
    cfg = load_agent_config(root)
    assert cfg.accused_attribution_selector == "d0" and cfg.accused_attribution_enabled is False
    monkeypatch.setenv("NYAYA_ACCUSED_ATTRIBUTION_SELECTOR", "bogus")
    with pytest.raises(ValueError, match="must be 'd0' or 'm1'"):
        load_agent_config(root)
    monkeypatch.setenv("NYAYA_ACCUSED_ATTRIBUTION_SELECTOR", "m1")
    assert load_agent_config(root).accused_attribution_selector == "m1"  # enabled is off: no backend needed yet
    monkeypatch.setenv("NYAYA_ACCUSED_ATTRIBUTION_ENABLED", "1")
    with pytest.raises(ValueError, match="needs accused_attribution_m1"):
        load_agent_config(root)
    monkeypatch.setenv("NYAYA_ACCUSED_ATTRIBUTION_M1_BASE_URL", "http://x/v1")
    monkeypatch.setenv("NYAYA_ACCUSED_ATTRIBUTION_M1_MODEL", "base-model")
    got = load_agent_config(root)
    assert got.accused_attribution_m1 == {"base_url": "http://x/v1", "model": "base-model"}


def test_kin_apposition_before_the_chosen_party_is_not_a_second_actor_in_m1() -> None:
    ok = M.check_attribution_m1("TOY: Her husband, Accused No.2, demanded money from Nila.", _ref(), Pick("Accused No.2"))
    assert ok.passed
    bad = M.check_attribution_m1("TOY: Accused No.1 and her husband, Accused No.2, demanded money.", _ref(), Pick("Accused No.2"))
    assert not bad.passed and bad.reason == "accused_attribution_collective"


def test_a_chosen_kin_phrase_followed_by_the_named_party_stands_for_that_party() -> None:
    sent = "TOY: Her husband, Accused No.2, demanded money from Nila."
    assert M.check_attribution_m1(sent, _ref(), Pick("Her husband")).passed  # the model picked the kin phrase: same person
    other = M.check_attribution_m1("TOY: Her husband, Accused No.1, demanded money.", _ref(), Pick("Her husband"))
    assert not other.passed and other.reason == "accused_attribution_not_matched"

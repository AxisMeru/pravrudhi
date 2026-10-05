"""Accused-attribution check, v1 variant D0 (prabhasa-nyaya #418 design, Lead-2 decisions 5 Oct 2026): a deterministic
check that the actor of a quoted act IS the accused under review, for elements that require a specific act by the accused.

Every fact, name and accused number below is a CONSTRUCTED toy (accused numbering is randomised per pair so a number never
encodes the label); no real or sealed text appears anywhere in this file, and no model is called (judges are scripted doubles).

Part 1 (this block) is the FLAG-OFF GOLDEN: the agent's results and audit records with the flag off must stay byte-identical
to what the code produced before the feature existed. The golden file was generated from the unmodified tree."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application import nyaya_lean_registry as reg
from pravrudhi.application.nyaya_agent import NyayaAgent
from pravrudhi.application.nyaya_judges import ElementJudgment, SpanRelevanceJudge
from tests.test_nyaya_agent import ScriptedJudge, ScriptedRegistry, _config

GOLDEN = Path(__file__).parent / "fixtures" / "nyaya_attribution_flag_off_golden.json"

EL1 = "the accused is the husband, or a relative of the husband, of the woman"
EL2 = "subjects the woman to cruelty (s.86(a) or (b))"
EL3 = "the facts attribute specific acts to this accused, not only a general or omnibus allegation"
#: The registry's own wording of bns85 element 3, as `score --describe-contract bns85` prints it (statute-derived, public).
EL3_REAL = (
    "the facts attribute specific acts of cruelty to this accused (a particular act, role or occasion), "
    "not only a general or omnibus allegation against the husband's family"
)
SHIPPED_KEY = "specific acts of cruelty to this accused"
TOY_KEY = "specific acts to this accused"
ELS = [EL1, EL2, EL3]
FACTS = [
    "TOY: Dev is the husband of Nila.",
    "TOY: Dev beat Nila on several evenings in March.",
    "TOY: Dev demanded money from Nila's parents.",
]


def _est(fid: str, quote: str, p: float = 0.97) -> ElementJudgment:
    return ElementJudgment("established", p, fid, quote, "model")


def _registry() -> ScriptedRegistry:
    return ScriptedRegistry(
        contracts={"bns85": reg.DescribedContract("bns85", list(ELS), [])},
        sources={"bns85": ["Bharatiya Nyaya Sanhita (toy source)"]},
    )


def _cfg(tmp_path: Path, **over: Any):
    return _config(
        tmp_path,
        judge_statute_text={"bns85": "TRAINING statute text for bns85"},
        validated_contracts=frozenset({"bns85"}),
        **over,
    )


def _script(facts: list[str] = FACTS) -> dict[str, list[ElementJudgment | Exception]]:
    return {
        EL1: [_est("F1", "the husband of Nila")],
        EL2: [_est("F2", "beat Nila on several evenings")],
        EL3: [_est("F2", "Dev beat Nila")],
    }


def _strip(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: _strip(v) for k, v in o.items() if not (k in ("ts", "run_id", "audit_path") or k.endswith("_ms"))}
    if isinstance(o, list):
        return [_strip(v) for v in o]
    return o


def _snapshot(run: Any) -> dict[str, Any]:
    lines = [json.loads(x) for x in run.audit_path.read_text().splitlines()]
    for ln in lines:  # these two steps hash wall-clock timings in their inputs, so the hash differs run to run
        if ln["step"] in ("judge_accounting", "run_end"):
            ln.pop("inputs_sha256", None)
    return json.loads(json.dumps(_strip({"run": run.to_dict(), "audit": lines}), sort_keys=True, default=str))


def _golden_runs(tmp_path: Path, **cfg_over: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    # 1: plain single judge, PROOF
    out["proof"] = _snapshot(
        NyayaAgent(ScriptedJudge(_script()), _registry(), _cfg(tmp_path / "a", **cfg_over)).run(
            FACTS, narrative="TOY.", contract_ids=["bns85"]
        )
    )
    # 2: an element not established (ABSTAIN)
    s = _script()
    s[EL3] = [ElementJudgment("not_established", 0.03)]
    out["abstain"] = _snapshot(
        NyayaAgent(ScriptedJudge(s), _registry(), _cfg(tmp_path / "b", **cfg_over)).run(
            FACTS, narrative="TOY.", contract_ids=["bns85"]
        )
    )
    # 3: span-relevance stack demotes element 3
    check = ScriptedJudge({EL1: [_est("F1", "x")], EL2: [_est("F1", "x")], EL3: [ElementJudgment("not_established", 0.1)]})
    out["span_demoted"] = _snapshot(
        NyayaAgent(SpanRelevanceJudge(ScriptedJudge(_script()), check), _registry(), _cfg(tmp_path / "c", **cfg_over)).run(
            FACTS, narrative="TOY.", contract_ids=["bns85"]
        )
    )
    # 4: a quote that is not in the named fact (quote check rejects)
    s = _script()
    s[EL3] = [_est("F2", "this text is not in the fact")]
    out["bad_quote"] = _snapshot(
        NyayaAgent(ScriptedJudge(s), _registry(), _cfg(tmp_path / "d", **cfg_over)).run(
            FACTS, narrative="TOY.", contract_ids=["bns85"]
        )
    )
    return out


def test_generate_golden_only_when_asked(tmp_path: Path) -> None:
    if os.environ.get("NYAYA_ATTRIBUTION_WRITE_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(_golden_runs(tmp_path), indent=1, sort_keys=True) + "\n")
    assert GOLDEN.exists()


def test_flag_off_results_and_audit_are_byte_identical_to_the_pre_feature_golden(tmp_path: Path) -> None:
    assert json.dumps(_golden_runs(tmp_path), indent=1, sort_keys=True) + "\n" == GOLDEN.read_text()


# ================================================================================================================================
# Part 2: the D0 rules on CONSTRUCTED contrast pairs (accused numbering randomised per pair), then the agent wiring.
# ================================================================================================================================
import random  # noqa: E402

from pravrudhi.application import nyaya_attribution as A  # noqa: E402
from pravrudhi.application.nyaya_agent import AgentConfig, load_agent_config  # noqa: E402
from pravrudhi.application.nyaya_attribution import (  # noqa: E402
    AccusedAttributionJudge,
    AccusedRef,
    AttributedJudgeRequest,
    check_attribution,
)

SEEDS = range(25)


def _nums(seed: int) -> tuple[int, int, int]:
    """(accused under review, a co-accused, a third), all distinct, randomised, so no number ever encodes the expected label."""
    r = random.Random(seed)
    n, m, k = r.sample(range(1, 30), 3)
    return n, m, k


def _ref(n: int) -> AccusedRef:
    return AccusedRef(f"acc{n}", (f"Accused No.{n}", f"A{n}", f"Petitioner No.{n}"))


def _res(sentence: str, n: int, **kw: Any) -> A.AttributionResult:
    return check_attribution(sentence, kw.get("ref", _ref(n)))


@pytest.mark.parametrize("seed", SEEDS)
def test_correct_named_accused_passes_in_every_spelling(seed: int) -> None:
    n, m, _ = _nums(seed)
    for s in (
        f"TOY: Accused No.{n} beat the complainant on several evenings.",
        f"TOY: Accused No. {n} beat the complainant.",
        f"TOY: accused no {n} threatened the complainant.",
        f"TOY: A{n} harassed the complainant for money.",
        f"TOY: Petitioner No.{n} demanded cash from the complainant.",
        f"TOY: Accused No.{n} allegedly used to taunt the complainant repeatedly.",
    ):
        r = _res(s, n)
        assert r.passed and r.reason is None, (s, r.as_dict())


@pytest.mark.parametrize("seed", SEEDS)
def test_co_accused_act_is_not_matched(seed: int) -> None:
    n, m, _ = _nums(seed)
    for s in (
        f"TOY: Accused No.{m} beat the complainant on several evenings.",
        f"TOY: A{m} harassed the complainant.",
        f"TOY: Petitioner No.{m} demanded cash from the complainant.",
    ):
        r = _res(s, n)
        assert not r.passed and r.reason == A.REASON_NOT_MATCHED and r.rule == "R2", (s, r.as_dict())


@pytest.mark.parametrize("seed", SEEDS)
def test_number_that_merely_contains_the_accused_number_is_not_the_accused(seed: int) -> None:
    n, _, _ = _nums(seed)
    r = _res(f"TOY: Accused No.{n}7 beat the complainant.", n)
    assert not r.passed and r.reason == A.REASON_NOT_MATCHED


@pytest.mark.parametrize("seed", SEEDS)
def test_collective_and_plural_forms_are_refused(seed: int) -> None:
    n, m, k = _nums(seed)
    lo, hi = sorted((m, k))
    cases = [
        "TOY: The accused harassed the complainant for money.",
        "TOY: The accused persons beat the complainant.",
        "TOY: All the accused threatened the complainant.",
        f"TOY: Accused Nos. {lo} to {hi} harassed the complainant.",
        f"TOY: Accused Nos. {lo}, {hi} and {n} harassed the complainant.",
        "TOY: Her in-laws harassed the complainant.",
        "TOY: The petitioners tortured the complainant.",
        "TOY: They demanded cash from the complainant.",
        f"TOY: Accused No.{n} and Accused No.{m} beat the complainant.",
        f"TOY: Accused No.{n} along with Accused No.{m} harassed the complainant.",
        f"TOY: Accused No.{n} along with his family members harassed the complainant.",
        f"TOY: Accused No.{n} and his mother beat the complainant.",
        f"TOY: Accused No.{n}/ Accused No.{m} threatened the complainant.",
    ]
    for s in cases:
        r = _res(s, n)
        assert not r.passed and r.reason == A.REASON_COLLECTIVE and r.rule == "R3", (s, r.as_dict())


@pytest.mark.parametrize("seed", SEEDS)
def test_pronoun_only_and_subjectless_quotes_are_unresolved_never_guessed(seed: int) -> None:
    n, _, _ = _nums(seed)
    for s in (
        "TOY: He demanded cash from the complainant.",
        "TOY: She threatened the complainant repeatedly.",
        "TOY: subjected her to cruelty and demanded dowry.",
        "TOY: The complainant was beaten repeatedly.",
        f"TOY: Accused No.{n} was present at the house.",
    ):
        r = _res(s, n)
        assert not r.passed and r.reason == A.REASON_UNRESOLVED, (s, r.as_dict())
    assert _res("TOY: He demanded cash.", n).rule == "R4"


@pytest.mark.parametrize("seed", SEEDS)
def test_object_position_collective_does_not_refuse(seed: int) -> None:
    """A collective word AFTER the verb is not the actor: the accused is still the only one who acted."""
    n, m, _ = _nums(seed)
    for s in (
        f"TOY: Accused No.{n} beat the complainant in front of her in-laws.",
        f"TOY: Accused No.{n} threatened the complainant and told the family members about it.",
        f"TOY: Accused No.{n} harassed the complainant while the accused persons watched.",
    ):
        r = _res(s, n)
        assert r.passed, (s, r.as_dict())


@pytest.mark.parametrize("seed", SEEDS)
def test_passive_voice_reads_the_agent_after_by(seed: int) -> None:
    n, m, _ = _nums(seed)
    assert _res(f"TOY: The complainant was beaten by Accused No.{n}.", n).passed
    r = _res(f"TOY: The complainant was beaten by Accused No.{m}.", n)
    assert not r.passed and r.reason == A.REASON_NOT_MATCHED
    r = _res("TOY: The complainant was beaten by the accused.", n)
    assert not r.passed and r.reason == A.REASON_COLLECTIVE
    r = _res(f"TOY: The complainant was beaten by Accused No.{n} and Accused No.{m}.", n)
    assert not r.passed and r.reason == A.REASON_COLLECTIVE


@pytest.mark.parametrize("seed", SEEDS)
def test_numbering_does_not_encode_the_label(seed: int) -> None:
    """The same sentence flips between pass and not_matched purely by which accused is under review."""
    n, m, _ = _nums(seed)
    s = f"TOY: Accused No.{n} beat the complainant."
    assert _res(s, n).passed
    assert _res(s, m).reason == A.REASON_NOT_MATCHED


def test_named_people_and_other_party_aliases() -> None:
    ref = AccusedRef("meena", ("Meena", "the wife's sister-in-law"), other_parties=(("Ravi", "the husband"),))
    assert check_attribution("TOY: Meena beat the complainant.", ref).passed
    r = check_attribution("TOY: Ravi beat the complainant.", ref)
    assert not r.passed and r.reason == A.REASON_NOT_MATCHED
    r = check_attribution("TOY: Meena and Ravi beat the complainant.", ref)
    assert not r.passed and r.reason == A.REASON_COLLECTIVE
    r = check_attribution("TOY: The husband beat the complainant.", ref)
    assert not r.passed and r.reason == A.REASON_NOT_MATCHED


def test_a_possessive_or_unlisted_subject_is_unresolved_not_credited_to_the_accused() -> None:
    n = 7
    r = _res(f"TOY: Accused No.{n}'s mother harassed the complainant.", n)
    assert not r.passed and r.reason in (A.REASON_UNRESOLVED, A.REASON_COLLECTIVE, A.REASON_NOT_MATCHED)


def test_r0_no_accused_given_refuses_with_accused_not_specified() -> None:
    r = check_attribution("TOY: Accused No.3 beat the complainant.", None)
    assert not r.passed and r.reason == A.REASON_NOT_SPECIFIED and r.rule == "R0"


def test_r5_any_error_refuses_never_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(A, "extract_candidates", boom)
    r = check_attribution("TOY: Accused No.3 beat the complainant.", _ref(3))
    assert not r.passed and r.rule == "R5" and "synthetic failure" in (r.error or "")
    monkeypatch.undo()
    for q in ("", "   ", None, "x" * (A.MAX_QUOTE_CHARS + 1)):
        r = check_attribution(q, _ref(3))  # type: ignore[arg-type]
        assert not r.passed and r.reason == A.REASON_UNRESOLVED


def test_accused_ref_validation() -> None:
    for bad in (
        dict(id="", aliases=("A1",)),
        dict(id="x", aliases=()),
        dict(id="x", aliases=("",)),
        dict(id="x", aliases=("a" * 81,)),
        dict(id="x", aliases=tuple(f"a{i}" for i in range(21))),
        dict(id="x", aliases=("A1",), other_parties=((),)),
    ):
        with pytest.raises(ValueError):
            AccusedRef(**bad)  # type: ignore[arg-type]


def test_result_dict_carries_offsets_and_candidates() -> None:
    d = _res("TOY: Accused No.4 beat the complainant.", 4).as_dict()
    assert d["passed"] is True and d["variant"] == "D0" and d["actor_span"] == "Accused No.4"
    assert d["actor_start"] == 5 and d["actor_end"] == 17 and d["verb"] == "beat" and d["n_candidates"] >= 1


# -- the judge wrapper and the agent ---------------------------------------------------------------------------------------------
def _on_cfg(tmp_path: Path, **over: Any):
    return _cfg(tmp_path, accused_attribution_enabled=True, requires_actor={"bns85": (TOY_KEY,)}, **over)


def _script_with(el3_quote_fact: str, quote: str) -> dict[str, list[ElementJudgment | Exception]]:
    s = _script()
    s[EL3] = [_est("F2", quote)]
    return s


def _run_on(tmp_path: Path, facts: list[str], quote: str, accused: AccusedRef | None, **cfg: Any):
    inner = ScriptedJudge({EL1: [_est("F1", "the husband")], EL2: [_est("F2", quote)], EL3: [_est("F2", quote)]})
    judge = AccusedAttributionJudge(inner)
    agent = NyayaAgent(judge, _registry(), _on_cfg(tmp_path, **cfg))
    return agent.run(facts, narrative="TOY.", contract_ids=["bns85"], accused=accused), inner


def _facts(sentence: str) -> list[str]:
    return ["TOY: Dev is the husband of Nila.", sentence, "TOY: Another unrelated fact."]


@pytest.mark.parametrize("seed", range(8))
def test_agent_proof_when_the_quote_names_the_accused(tmp_path: Path, seed: int) -> None:
    n, _, _ = _nums(seed)
    sent = f"TOY: Accused No.{n} beat Nila on several evenings."
    run, inner = _run_on(tmp_path, _facts(sent), f"Accused No.{n} beat Nila", _ref(n))
    c = run.contracts[0]
    assert c.outcome == "PROOF" and c.attribution_refused == []
    el3 = next(e for e in c.elements if e.element == EL3)
    assert el3.status == "established" and el3.attribution["passed"] is True
    # only the element that requires an actor carries an AttributedJudgeRequest; the others are plain requests
    kinds = {r.element: isinstance(r, AttributedJudgeRequest) for r in inner.requests}
    assert kinds[EL3] is True and kinds[EL1] is False and kinds[EL2] is False


@pytest.mark.parametrize("seed", range(8))
def test_agent_refers_a_co_accused_quote_status_stays_established(tmp_path: Path, seed: int) -> None:
    n, m, _ = _nums(seed)
    run, _ = _run_on(
        tmp_path, _facts(f"TOY: Accused No.{m} beat Nila on several evenings."), f"Accused No.{m} beat Nila", _ref(n)
    )
    c = run.contracts[0]
    assert (c.outcome, c.reason) == ("REFER_TO_LAWYER", "accused_attribution_not_matched")
    el3 = next(e for e in c.elements if e.element == EL3)
    assert el3.status == "established" and el3.quote == f"Accused No.{m} beat Nila"  # never rewritten to not_established
    assert c.attribution_refused == [EL3] and c.lean_outcome == "PROOF"


def test_agent_refers_collective_unresolved_and_not_specified(tmp_path: Path) -> None:
    n = 5
    cases = [
        (
            "TOY: The accused persons beat Nila on several evenings.",
            "The accused persons beat Nila",
            _ref(n),
            "accused_attribution_collective",
        ),
        ("TOY: He beat Nila on several evenings.", "He beat Nila", _ref(n), "accused_attribution_unresolved"),
        (f"TOY: Accused No.{n} beat Nila on several evenings.", f"Accused No.{n} beat Nila", None, "accused_not_specified"),
    ]
    for i, (sent, quote, ref, reason) in enumerate(cases):
        run, _ = _run_on(tmp_path / f"c{i}", _facts(sent), quote, ref)
        c = run.contracts[0]
        assert (c.outcome, c.reason) == ("REFER_TO_LAWYER", reason), (reason, c.reason)


def test_agent_refer_is_never_not_established_and_audit_records_the_check(tmp_path: Path) -> None:
    run, _ = _run_on(tmp_path, _facts("TOY: Accused No.9 beat Nila on several evenings."), "Accused No.9 beat Nila", _ref(2))
    lines = [json.loads(x) for x in run.audit_path.read_text().splitlines()]
    out = next(x for x in lines if x["step"] == "outcome")["output"]
    assert out["reason"] == "accused_attribution_not_matched" and out["attribution_refused"] == [EL3]
    judge_lines = [x for x in lines if x["step"] == "judge" and x["output"].get("judgment", {}).get("attribution")]
    assert len(judge_lines) == 1 and judge_lines[0]["output"]["judgment"]["status"] == "established"


def test_agent_unmatched_config_key_fails_closed_before_any_judge_call(tmp_path: Path) -> None:
    inner = ScriptedJudge(_script())
    cfg = _cfg(tmp_path, accused_attribution_enabled=True, requires_actor={"bns85": ("a phrase no element contains",)})
    run = NyayaAgent(AccusedAttributionJudge(inner), _registry(), cfg).run(
        FACTS, narrative="TOY.", contract_ids=["bns85"], accused=_ref(1)
    )
    c = run.contracts[0]
    assert (c.outcome, c.reason) == ("REFER_TO_LAWYER", "accused_attribution_config_unmatched") and inner.requests == []


def test_span_check_demotion_comes_first_and_the_attribution_check_is_not_asked(tmp_path: Path) -> None:
    check = ScriptedJudge({EL1: [_est("F1", "x")], EL2: [_est("F1", "x")], EL3: [ElementJudgment("not_established", 0.1)]})
    inner = ScriptedJudge(_script())
    stack = AccusedAttributionJudge(SpanRelevanceJudge(inner, check))
    run = NyayaAgent(stack, _registry(), _on_cfg(tmp_path)).run(FACTS, narrative="TOY.", contract_ids=["bns85"], accused=_ref(1))
    c = run.contracts[0]
    el3 = next(e for e in c.elements if e.element == EL3)
    assert el3.status == "not_established" and el3.attribution is None and c.attribution_refused == []


def test_denial_elements_and_non_established_elements_are_never_checked() -> None:
    class Stub:
        name = "stub"

        def judge(self, request: Any) -> ElementJudgment:
            return ElementJudgment("not_established", 0.01) if not request.is_denial else _est("F1", "x")

    j = AccusedAttributionJudge(Stub())
    req = AttributedJudgeRequest("bns85", "e", False, "s", "n", (("F1", "x"),), accused=None, requires_actor=True)
    assert j.judge(req).attribution is None  # not established -> not asked
    deny = AttributedJudgeRequest("bns85", "e", True, "s", "n", (("F1", "x"),), accused=None, requires_actor=True)
    assert j.judge(deny).attribution is None  # a defeater is never checked


def test_flag_off_ignores_an_accused_and_stays_byte_identical_to_the_golden(tmp_path: Path) -> None:
    golden = json.loads(GOLDEN.read_text())
    run = NyayaAgent(ScriptedJudge(_script()), _registry(), _cfg(tmp_path / "a")).run(
        FACTS, narrative="TOY.", contract_ids=["bns85"], accused=_ref(2)
    )
    assert _snapshot(run) == golden["proof"]
    assert json.dumps(_snapshot(run), sort_keys=True) == json.dumps(golden["proof"], sort_keys=True)


def test_flag_off_plain_requests_and_no_attribution_keys_anywhere(tmp_path: Path) -> None:
    judge = ScriptedJudge(_script())
    run = NyayaAgent(judge, _registry(), _cfg(tmp_path)).run(FACTS, narrative="TOY.", contract_ids=["bns85"])
    assert all(type(r).__name__ == "JudgeRequest" for r in judge.requests)
    assert "attribution" not in json.dumps(run.to_dict()) and "attribution" not in run.audit_path.read_text()


# -- configuration ---------------------------------------------------------------------------------------------------------------
def test_config_defaults_off_and_yaml_default_lists_bns85(monkeypatch: pytest.MonkeyPatch) -> None:
    assert AgentConfig.__dataclass_fields__["accused_attribution_enabled"].default is False
    root = Path(__file__).resolve().parents[1]
    monkeypatch.delenv("NYAYA_ACCUSED_ATTRIBUTION_ENABLED", raising=False)
    cfg = load_agent_config(root)
    assert cfg.accused_attribution_enabled is False and cfg.requires_actor == {"bns85": (SHIPPED_KEY,)}
    monkeypatch.setenv("NYAYA_ACCUSED_ATTRIBUTION_ENABLED", "1")
    assert load_agent_config(root).accused_attribution_enabled is True


def test_config_rejects_malformed_or_unknown_requires_actor(tmp_path: Path) -> None:
    import shutil

    root = Path(__file__).resolve().parents[1]
    for bad, why in (
        ("accused_attribution_requires_actor:\n  bns85: []\n", "non-empty"),
        ("accused_attribution_requires_actor:\n  not_a_real_contract: [x]\n", "pinned registry"),
    ):
        d = tmp_path / why.replace(" ", "_")
        (d / "configs").mkdir(parents=True)
        text = (root / "configs" / "nyaya_agent.yaml").read_text()
        head = text.split("accused_attribution_requires_actor:")[0]
        (d / "configs" / "nyaya_agent.yaml").write_text(head + bad)
        for f in (root / "configs").iterdir():
            if f.name != "nyaya_agent.yaml" and f.is_file():
                shutil.copy(f, d / "configs" / f.name)
        with pytest.raises(ValueError, match=why):
            load_agent_config(d)


def test_house_wires_the_check_after_the_span_check_only_when_on(tmp_path: Path) -> None:
    from pravrudhi.application.nyaya_judges import HouseJudge

    hj = {
        "base_url": "http://h/v1",
        "model": "m",
        "statute_chars": 600,
        "max_tokens": 30,
        "top_logprobs": 20,
        "timeout_s": 5,
        "label_mass_floor": 0.5,
    }
    sb = tmp_path / "score"
    sb.write_bytes(b"fake binary")
    off = NyayaAgent.house(
        tmp_path, config=_cfg(tmp_path, house_judge=hj, score_bin=sb, pinned_score_sha256=None, span_relevance_enabled=True)
    )
    assert isinstance(off.judge, SpanRelevanceJudge)
    on = NyayaAgent.house(
        tmp_path,
        config=_cfg(
            tmp_path,
            house_judge=hj,
            score_bin=sb,
            pinned_score_sha256=None,
            span_relevance_enabled=True,
            accused_attribution_enabled=True,
            requires_actor={"bns85": ("specific acts",)},
        ),
    )
    assert isinstance(on.judge, AccusedAttributionJudge) and isinstance(on.judge.inner, SpanRelevanceJudge)
    assert isinstance(on.judge.inner.inner, HouseJudge)


def test_the_shipped_config_key_matches_the_registrys_own_element_wording_and_only_element_3() -> None:
    """The key was first written from an analysis note and matched nothing; pin it to the wording the pinned binary
    reports (`score --describe-contract bns85`, verified 5 Oct on the binary with sha256 cca4d963...)."""
    import yaml

    cfg = yaml.safe_load((Path(__file__).resolve().parent.parent / "configs" / "nyaya_agent.yaml").read_text())
    (key,) = cfg["accused_attribution_requires_actor"]["bns85"]
    assert key == SHIPPED_KEY and key.lower() in EL3_REAL.lower()
    others = (
        "the accused is the husband, or a relative of the husband, of the woman",
        "subjects the woman to cruelty (s.86(a) or (b))",
    )
    assert not any(key.lower() in o.lower() for o in others)

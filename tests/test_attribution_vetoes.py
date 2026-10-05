"""Hard deterministic vetoes (M1 safety fixes, Lead-2 5 Oct): they apply whatever else says PASS.
V1: an unlisted co-actor joined to the actor by "together with", "along with", "alongwith" (anywhere after the actor) or by
"and"/"&" before the act verb. V2: a range written with short aliases ("A-9 to A-17", "A1-A5", "P.2 and P.4"). Constructed
only."""

from __future__ import annotations

import pytest

from pravrudhi.application.nyaya_attribution import AccusedRef, check_attribution


def _ref(n: int) -> AccusedRef:
    return AccusedRef(f"a{n}", (f"Accused No.{n}", f"A{n}", f"Petitioner No.{n}", "Rekha Menon"), (("Accused No.9", "A9"),))


@pytest.mark.parametrize("seed_n", [2, 5, 11])
@pytest.mark.parametrize(
    "sentence",
    [
        "TOY: Rekha Menon, together with Gopal Rao, beat Nila.",
        "TOY: Accused No.{n} along with Gopal Rao abused Nila.",
        "TOY: Accused No.{n} alongwith Gopal Rao abused Nila.",
        "TOY: Accused No.{n} and Gopal Rao slapped Nila.",
        "TOY: Accused No.{n} & Gopal Rao slapped Nila.",
        "TOY: Accused No.{n} beat Nila together with Gopal Rao.",
        "TOY: Accused No.{n} beat Nila along with Meera Joshi on several evenings.",
        "TOY: Accused No.{n}, together with Accused No.30, beat Nila.",
    ],
)
def test_an_unlisted_co_actor_vetoes_a_pass(sentence: str, seed_n: int) -> None:
    r = check_attribution(sentence.format(n=seed_n), _ref(seed_n))
    assert not r.passed and r.reason == "accused_attribution_collective"


@pytest.mark.parametrize(
    "sentence",
    [
        "TOY: A-9 to A-17 abused Nila.",
        "TOY: A1-A5 beat Nila.",
        "TOY: P.2 and P.4 beat Nila.",
        "TOY: A 3 to A 7 harassed Nila.",
        "TOY: A9 – A12 beat Nila.",
    ],
)
def test_a_short_alias_range_is_collective(sentence: str) -> None:
    r = check_attribution(sentence, AccusedRef("a17", ("Accused No.17", "A17", "Petitioner No.17")))
    assert not r.passed and r.reason == "accused_attribution_collective"


@pytest.mark.parametrize(
    "sentence",
    [
        "TOY: Accused No.2 beat Nila.",
        "TOY: Accused No.2 beat Nila in front of her parents and Gopal Rao.",
        "TOY: Accused No.2 threatened Nila and her mother.",
        "TOY: Nila was beaten by Accused No.2.",
        "TOY: When Nila met Gopal Rao and Meera Joshi, Accused No.2 beat her.",
        "TOY: Accused No.2 beat Nila with a stick.",
        "TOY: Accused No.2 beat Nila. Gopal Rao together with Meera Joshi watched.",
    ],
)
def test_object_position_names_and_unrelated_connectors_do_not_veto(sentence: str) -> None:
    assert check_attribution(sentence, _ref(2)).passed


def test_a_veto_never_creates_a_pass_and_is_recorded() -> None:
    r = check_attribution("TOY: Accused No.2 and Gopal Rao slapped Nila.", _ref(2))
    assert not r.passed and r.rule == "V1"
    r2 = check_attribution("TOY: A-9 to A-17 abused Nila.", AccusedRef("a17", ("A17",)))
    assert not r2.passed and r2.rule == "V2"


@pytest.mark.parametrize(
    "link",
    ["with the help of", "in concert with", "as well as", "aided by", "assisted by", "accompanied by", "in collusion with",
     "jointly with", "with the assistance of", "abetted by"],
)
@pytest.mark.parametrize("who", ["Gopal Rao", "Accused No.9", "her husband"])
def test_v1_link_phrases_bring_in_a_second_actor(link: str, who: str) -> None:
    from pravrudhi.application.nyaya_attribution import AccusedRef, _veto

    ref = AccusedRef("a1", ("Accused No.3",), (("Accused No.9",), ("her husband",)))
    assert _veto(f"TOY: Accused No.3 {link} {who} beat Nila.", ref) == "V1"


@pytest.mark.parametrize(
    "sentence",
    [
        "TOY: Accused No.3 beat Nila with a hockey stick.",
        "TOY: Accused No.3 beat Nila with the help of a stick.",
        "TOY: Accused No.3 beat Nila, as well as she could not leave the house.",
    ],
)
def test_v1_link_phrases_do_not_fire_without_a_second_actor(sentence: str) -> None:
    from pravrudhi.application.nyaya_attribution import AccusedRef, _veto

    assert _veto(sentence, AccusedRef("a1", ("Accused No.3",), ())) is None

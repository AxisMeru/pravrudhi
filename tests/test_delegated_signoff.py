"""Autonomous gate closure, and the conditions that stand in for the person who used to look.

ADR-REF: ADR-0040. The delegation is the easy half; the conditions are the substance. `sign_gate` never
checked whether a gate's other closure layers passed -- it set `closure.signoff.verdict = "pass"` and trusted
the signer, which was defensible while a person was reading the pack and is not once nothing is.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from pravrudhi.application.delegation import (
    AGENT_IDENTITIES,
    Delegation,
    load_delegation,
    unmet_conditions,
)
from pravrudhi.application.inbox_sign import record_decision
from pravrudhi_kernel.schema.ledger_event import LedgerEvent

LIVE = Path("configs/delegation.yaml")


def _delegation(tmp_path: Path, **over: object) -> Delegation:
    body = {
        "active": True,
        "granted": "2026-09-10",
        "instruction": "proceed autonomously",
        "signature_identity": "agent-for-operator",
        "scope": {"gate_signoff": True, "promote_t2": True, "interp_claim": False},
        "conditions": {"gate_signoff": {"require_closure_layers_pass": True}, "promote_t2": {"require_green_badge": True}},
    }
    body.update(over)
    (tmp_path / "configs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "configs" / "delegation.yaml").write_text(yaml.safe_dump(body))
    d = load_delegation(tmp_path)
    assert d is not None
    return d


def test_no_delegation_file_means_signoff_is_still_a_human_act(tmp_path: Path) -> None:
    """The safe default, and the behaviour of an end-user install whose operator has delegated nothing."""
    assert load_delegation(tmp_path) is None


def test_inactive_stops_everything_and_says_so(tmp_path: Path) -> None:
    d = _delegation(tmp_path, active=False)
    for act in ("gate_signoff", "promote_t2"):
        allowed, why = d.permits(act)
        assert not allowed
        assert "active: false" in why


def test_an_act_outside_scope_is_refused_by_name(tmp_path: Path) -> None:
    d = _delegation(tmp_path)
    assert d.permits("interp_claim")[0] is False
    # An act the config says nothing about is refused too, not defaulted to allowed.
    allowed, why = d.permits("merge_to_main")
    assert not allowed
    assert "says nothing about" in why


def test_conditions_are_scoped_to_the_act_because_the_artefacts_differ(tmp_path: Path) -> None:
    """The first version applied a gate JSON's closure-layer condition to inbox packs, which have no gate JSON,
    so every autonomous approval was refused for "no gate check was run" -- demanding a check that cannot exist
    for that artefact rather than one that was skipped."""
    d = _delegation(tmp_path)
    assert d.requires("require_closure_layers_pass", "gate_signoff")
    assert not d.requires("require_closure_layers_pass", "promote_t2")
    assert d.requires("require_green_badge", "promote_t2")
    # A pack with a green badge and no gate to read is allowed.
    assert unmet_conditions(d, act="promote_t2", badge="green") == []


def test_a_flat_conditions_block_still_works(tmp_path: Path) -> None:
    """An older config predates the per-act split and must not silently lose its conditions."""
    d = _delegation(tmp_path, conditions={"require_green_badge": True})
    assert d.requires("require_green_badge", "promote_t2")
    assert unmet_conditions(d, act="promote_t2", badge="amber")


def test_an_unchecked_condition_counts_as_unmet(tmp_path: Path) -> None:
    """A check nobody ran must not read as a check that held. This is the whole reason the conditions are worth
    anything: the person they replace was doing them by looking."""
    d = _delegation(tmp_path)
    reasons = unmet_conditions(d, act="gate_signoff")
    assert reasons and "were not read" in reasons[0]
    reasons = unmet_conditions(d, act="promote_t2")
    assert reasons and "no badge was resolved" in reasons[0]


def test_amber_is_refused_with_the_next_step_not_just_a_no(tmp_path: Path) -> None:
    """Equivocal evidence calls for the experiment that resolves it (CHARTER §6), so the refusal has to point
    there rather than reading as "not allowed"."""
    d = _delegation(tmp_path)
    (reason,) = unmet_conditions(d, act="promote_t2", badge="amber")
    assert "equivocal" in reason
    assert "run the experiment" in reason


def test_the_ledger_can_record_an_agent_actor_without_impersonating_a_person() -> None:
    """The kernel's `Actor` was a closed set ending at `human:<name>`, so an autonomous sign-off could only be
    written as `human:agent-for-operator`. ADR-0040 widened it instead, because a reader must always be able
    to tell a promotion a person looked at from one an agent closed."""
    # Taken from a real signoff line rather than invented, so the test cannot drift from the envelope.
    row = next(
        (
            json.loads(line)
            for line in Path("research/ledger.jsonl").read_text().splitlines()
            if '"signoff"' in line
        ),
        None,
    ) if Path("research/ledger.jsonl").exists() else None
    if row is None:
        pytest.skip("no signoff line to model the envelope on")
    assert LedgerEvent.model_validate({**row, "actor": "agent:agent-for-operator"}).actor.startswith("agent:")
    assert LedgerEvent.model_validate({**row, "actor": "human:operator"}).actor == "human:operator"
    # Still a closed set: an actor nobody can enumerate is not provenance.
    for bad in ("agent:has spaces", "agent:", "robot:x", "agent-for-operator"):
        with pytest.raises(ValueError):
            LedgerEvent.model_validate({**row, "actor": bad})


def test_agent_for_operator_is_treated_as_an_agent_identity() -> None:
    """Otherwise it would fall through the human branch and sign without the delegation's conditions."""
    assert "agent-for-operator" in AGENT_IDENTITIES


def test_an_unknown_pack_is_refused_and_names_what_it_knows(tmp_path: Path) -> None:
    _delegation(tmp_path)
    with pytest.raises(FileNotFoundError, match="not a promotion pack"):
        record_decision(tmp_path, pack=tmp_path / "nope", decision="approve")


def test_a_bad_decision_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    _delegation(tmp_path)
    with pytest.raises(ValueError, match="decision must be one of"):
        record_decision(tmp_path, pack=tmp_path / "nope", decision="looks-fine")


def test_the_live_delegation_matches_what_was_actually_signed() -> None:
    """Guards the config this repository is running under, not a fixture: three green packs were closed as
    `agent-for-operator` on 2026-09-10 and the grant that allowed it has to still say so."""
    if not LIVE.exists():
        pytest.skip("no delegation recorded in this workspace")
    body = yaml.safe_load(LIVE.read_text())
    assert body["active"] is True
    assert body["signature_identity"] == "agent-for-operator"
    assert body["scope"]["promote_t2"] is True
    assert body["scope"]["interp_claim"] is False, "an interp claim is a scientific claim, not a gate approval"
    assert body["conditions"]["promote_t2"]["require_green_badge"] is True
    assert "no point in waiting" in body["instruction"], "the grant must quote what was actually said"


def test_the_signoffs_written_today_are_agent_actors_in_the_live_ledger() -> None:
    ledger = Path("research/ledger.jsonl")
    if not ledger.exists():
        pytest.skip("no ledger in this workspace")
    rows = [json.loads(line) for line in ledger.read_text().splitlines() if '"signoff"' in line]
    autonomous = [r for r in rows if str(r.get("actor", "")).startswith("agent:")]
    if not autonomous:
        pytest.skip("no autonomous signoff recorded yet")
    for r in autonomous:
        assert r["actor"] == "agent:agent-for-operator"
        assert r["payload"]["scope"] == "promote_T2"
        assert r["payload"]["note"], "an autonomous signature must carry its authority"

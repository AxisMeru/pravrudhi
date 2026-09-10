"""One prompt set, many vendors, one scoring path.

The fleet already reaches Claude, GPT, Qwen and GLM from a product install -- `pravrudhi agents` says `ready`
for all four. What it does NOT do is compare them: routing exists to pick ONE seat by cost and availability
and get work done, so a fallback is a success there and a missing vendor is invisible. A comparison needs the
opposite discipline: every named vendor asked the same thing, no substitution, and a vendor that could not
answer recorded as a gap rather than quietly replaced.

Track A depends on this and on nothing else about the model: it is a layer over any vendor, and comparing them
is one of its outputs (ADR-0001 in AxisMeru/prabhasa-nyaya).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pravrudhi.application.panel import (
    VENDORS,
    Answer,
    Vendor,
    load_vendors,
    panel_manifest,
    run_panel,
)


def _stub(vendor: Vendor, prompt: str) -> Answer:
    return Answer(
        vendor=vendor.id,
        interface=vendor.interface,
        model=vendor.model,
        prompt_id="",
        text=f"[{vendor.id}] {prompt[:20]}",
        wall_s=0.1,
        tokens=None,
        error=None,
    )


PROMPTS = [{"id": "q1", "prompt": "Is section 378A real?"}, {"id": "q2", "prompt": "Cite Pierce v. State."}]


def test_every_named_vendor_is_asked_every_prompt(tmp_path: Path) -> None:
    vendors = load_vendors(["claude-cli", "codex-cli", "qwen-dashscope"])
    out = run_panel(tmp_path, PROMPTS, vendors, ask=_stub)
    assert {a.vendor for a in out} == {"claude-cli", "codex-cli", "qwen-dashscope"}
    assert len(out) == 3 * len(PROMPTS)
    for a in out:
        assert a.prompt_id in {"q1", "q2"}


def test_a_vendor_that_cannot_answer_is_a_gap_not_a_substitution(tmp_path: Path) -> None:
    """The discipline that separates a comparison from a dispatch.

    `swarm` falls back to another seat when one is down, and that is right when the goal is to get work done.
    Here a fallback would silently attribute one vendor's answer to another, which is the worst possible
    failure in a comparison: it would read as a result.
    """

    def failing(vendor: Vendor, prompt: str) -> Answer:
        if vendor.id == "codex-cli":
            raise RuntimeError("not logged in")
        return _stub(vendor, prompt)

    out = run_panel(tmp_path, PROMPTS, load_vendors(["claude-cli", "codex-cli"]), ask=failing)
    failed = [a for a in out if a.vendor == "codex-cli"]
    assert len(failed) == len(PROMPTS)
    assert all(a.error and a.text == "" for a in failed), "a gap is recorded, never filled by another vendor"
    assert all(a.error is None for a in out if a.vendor == "claude-cli")


def test_the_interface_travels_with_every_answer(tmp_path: Path) -> None:
    """The operator's judgement is that a CLI and an API do not differ for the VERDICT, and this does not
    argue with it. It records which interface answered anyway, because it costs one field and it is the only
    way a later reader can check that judgement rather than inherit it."""
    out = run_panel(tmp_path, PROMPTS, load_vendors(["claude-cli", "qwen-dashscope"]), ask=_stub)
    by = {a.vendor: a.interface for a in out}
    assert by["claude-cli"] == "cli"
    assert by["qwen-dashscope"] == "openai_compat"


def test_results_are_written_with_a_manifest_that_pins_the_parameters(tmp_path: Path) -> None:
    run_panel(tmp_path, PROMPTS, load_vendors(["claude-cli"]), ask=_stub)
    manifest = json.loads((tmp_path / "panel" / "manifest.json").read_text())
    assert manifest["n_prompts"] == 2
    assert manifest["vendors"][0]["id"] == "claude-cli"
    # Parameters are part of the result: the same vendor at a different temperature is a different measurement.
    assert "params" in manifest["vendors"][0]
    assert len(manifest["prompts_sha256"]) == 64


def test_an_unknown_vendor_is_refused_rather_than_skipped() -> None:
    with pytest.raises(KeyError, match="frontier-model-9"):
        load_vendors(["claude-cli", "frontier-model-9"])


def test_api_vendors_are_declared_and_say_what_they_need() -> None:
    """Built before the keys arrive, on the operator's instruction, so that when a key lands nothing is
    designed under time pressure. Each one names the environment variable it reads and never holds a secret."""
    api = [v for v in VENDORS.values() if v.interface == "openai_compat" and v.credential]
    assert api, "the API vendors must be declared even while unrunnable"
    for v in api:
        assert v.credential.endswith("_API_KEY") or v.credential.endswith("_KEY")
        assert v.base_url.startswith("https://"), v.id
        assert v.params.get("temperature") is not None, f"{v.id} must pin its sampling"


def test_byok_reads_the_key_from_the_environment_and_never_from_config() -> None:
    """When the product reaches another user's hands they bring their own key. A vendor record holds the NAME
    of the variable, never a value, so a config file cannot leak one and a manifest cannot record one."""
    for v in VENDORS.values():
        blob = json.dumps(panel_manifest(PROMPTS, [v])).lower()
        assert "sk-" not in blob and "api_key=" not in blob
        assert "bearer" not in blob

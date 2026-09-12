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
    parse_override,
    run_panel,
    tuned,
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


def test_answers_stream_as_they_arrive_and_the_manifest_is_written_first(tmp_path: Path) -> None:
    """A run of 240 CLI calls held its only copy in memory for the best part of an hour: nothing to watch
    while it ran, and nothing left if it died on the last vendor."""
    seen: list[int] = []
    prompts = [{"id": f"p{i}", "prompt": "hi"} for i in range(4)]
    dest = tmp_path / "panel" / "answers.jsonl"

    def ask(vendor: Vendor, prompt: str) -> Answer:
        # Count the rows already on disk at the moment each answer is produced.
        seen.append(len(dest.read_text().splitlines()) if dest.exists() else 0)
        return Answer(vendor.id, vendor.interface, vendor.model, "", "ok", 0.1, None, None)

    run_panel(tmp_path, prompts, load_vendors(["claude-cli"]), ask=ask)
    assert seen == [0, 1, 2, 3], "each answer should be on disk before the next is asked"
    assert (tmp_path / "panel" / "manifest.json").exists()


def test_parameters_layer_config_then_explicit_and_a_typo_is_refused(tmp_path: Path) -> None:
    """Refused rather than ignored: a typo in a tuning flag that silently does nothing produces a run
    labelled with parameters it did not use."""
    cfg = tmp_path / "panel.yaml"
    cfg.write_text("vendors:\n  claude-cli: {temperature: 0.3, max_tokens: 99}\n")
    vs = load_vendors(["claude-cli", "codex-cli"])

    got = {v.id: v.params for v in tuned(vs, config=cfg, overrides=["claude-cli:temperature=0.9"])}
    assert got["claude-cli"]["temperature"] == 0.9   # explicit beats config
    assert got["claude-cli"]["max_tokens"] == 99     # config beats the registry
    assert got["codex-cli"] == dict(VENDORS["codex-cli"].params), "untouched vendors keep their defaults"
    # The registry itself is not mutated: a frozen vendor means the defaults stay the defaults.
    assert VENDORS["claude-cli"].params["temperature"] == 0.0

    with pytest.raises(KeyError, match="not in this panel"):
        tuned(vs, overrides=["clude-cli:temperature=0.9"])
    with pytest.raises(ValueError, match="vendor:key=value"):
        parse_override("temperature=0.9")


def test_an_override_value_is_typed_by_what_it_looks_like() -> None:
    """Left as a string, `temperature="0.2"` reaches an HTTP body as a string and some endpoints accept it,
    coerce it, and answer -- so the run succeeds and the manifest records a parameter nobody set to that."""
    assert parse_override("v:temperature=0.2") == ("v", "temperature", 0.2)
    assert parse_override("v:seed=7") == ("v", "seed", 7)
    assert parse_override("v:thinking=TRUE") == ("v", "thinking", True)
    assert parse_override("v:seed=none") == ("v", "seed", None)
    assert parse_override("v:model=gpt-5") == ("v", "model", "gpt-5")


def test_a_panel_vendor_uses_a_key_the_product_stored(tmp_path: Path) -> None:
    """"Expand the fleet into the product" is untrue in the only way that matters if a key a user pasted into
    the app is invisible to a panel run. `/api/providers/{id}/key` writes into
    `<root>/.pravrudhi/credentials/<provider>.key`, and this is the panel reading it."""
    from pravrudhi.application.credentials import FileCredentialStore

    FileCredentialStore(tmp_path).put("openai", "sk-stored-by-the-product")
    vendor = VENDORS["openai-api"]
    assert vendor.provider == "openai"
    assert vendor.key(tmp_path) == "sk-stored-by-the-product"
    # No root, no store: a vendor asked in isolation falls back to the environment alone and finds nothing.
    assert vendor.key() is None


def test_an_explicit_store_wins_over_a_bare_root_and_needs_no_disk_access_of_its_own(tmp_path: Path) -> None:
    """`api.nyaya._session` resolves a `CredentialStore` from `credentials.store_for_session` once, per request,
    and hands it here rather than letting `key()` re-derive one from a bare root -- the boundary between a
    signed-in user's store and the engine's own is `credentials.py`'s job, not this method's."""
    from pravrudhi.application.credentials import FileCredentialStore

    store_root = tmp_path / "the-callers-own-workspace"
    store_root.mkdir()
    FileCredentialStore(store_root).put("openai", "sk-from-the-resolved-session")
    other_root = tmp_path / "unrelated-directory-with-no-key"
    other_root.mkdir()

    vendor = VENDORS["openai-api"]
    assert vendor.key(other_root, store=FileCredentialStore(store_root)) == "sk-from-the-resolved-session"


def test_the_environment_outranks_the_stored_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A headless or CI install supplies its key in the environment, and that has to win over whatever a
    previous interactive session left in the store."""
    from pravrudhi.application.credentials import FileCredentialStore

    FileCredentialStore(tmp_path).put("openai", "sk-stored")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    assert VENDORS["openai-api"].key(tmp_path) == "sk-from-env"


def test_an_api_vendors_endpoint_comes_from_the_products_registry() -> None:
    """One registry for a base URL. Two that name the same endpoint drift: one gets a new URL and the other
    keeps answering. The compatibility suffix is per provider and NOT derived, because a single rule was wrong
    for one of them -- Anthropic's OpenAI-compatible endpoint is the same /v1 as its native API, so appending
    /openai produced https://api.anthropic.com/v1/openai, which does not exist."""
    from pravrudhi.application.credentials import PROVIDERS

    assert VENDORS["openai-api"].base_url == PROVIDERS["openai"].base_url
    assert VENDORS["anthropic-api"].base_url == PROVIDERS["anthropic"].base_url
    assert VENDORS["google-api"].base_url == PROVIDERS["google"].base_url.rstrip("/") + "/openai"
    for vid in ("openai-api", "anthropic-api", "google-api", "qwen-dashscope"):
        v = VENDORS[vid]
        assert v.credential == PROVIDERS[v.provider].key_env, vid

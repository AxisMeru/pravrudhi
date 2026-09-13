"""Tests for `model_io.py`'s backend dispatch (370M custom-loop vs 1.13B
Megatron-Core) added for the G0 evaluation-harness integration.

Collection-safe on the bare host: no `torch` import at module level, no
`megatron.core` import at all, and no GPU. The dispatch tests monkeypatch
`model_io._detect_backend` / `model_io._load_370m_model` /
`model_io._load_megatron_model` with plain stub callables/objects so
`load_model`'s routing logic (explicit `backend=` vs auto-detect, and the
`_nyaya_backend` tag it stamps on the returned model) is exercised without
ever touching a real checkpoint, torch, or the GPU.

`_detect_backend` itself does need `torch.load` to peek a checkpoint's
top-level keys, so its tests `pytest.importorskip("torch")` locally, inside
the test function -- this only skips those specific tests on a host without
torch; it never prevents collecting or running the dispatch tests above,
which need no torch at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prototypes.nyaya_ttt_rsi import model_io


class _StubModel:
    """Bare object standing in for an `nn.Module` -- dispatch logic never
    inspects anything about the model beyond setting `_nyaya_backend` on it,
    so a plain object is sufficient."""


class _StubTok:
    pass


# ---------------------------------------------------------------------------
# load_model dispatch (explicit backend=, no auto-detection involved)
# ---------------------------------------------------------------------------


def test_load_model_explicit_370m_backend(monkeypatch):
    stub_model, stub_tok = _StubModel(), _StubTok()
    calls = []

    def fake_load_370m(ckpt_path, device):
        calls.append(("370m", ckpt_path, device))
        return stub_model, stub_tok

    def fail_megatron(*a, **k):
        raise AssertionError("megatron loader must not be called for backend='370m'")

    monkeypatch.setattr(model_io, "_load_370m_model", fake_load_370m)
    monkeypatch.setattr(model_io, "_load_megatron_model", fail_megatron)

    model, tok = model_io.load_model("/some/ckpt.pt", device="cpu", backend="370m")

    assert model is stub_model
    assert tok is stub_tok
    assert model._nyaya_backend == "370m"
    assert calls == [("370m", Path("/some/ckpt.pt"), "cpu")]


def test_load_model_explicit_megatron_backend(monkeypatch):
    stub_model, stub_tok = _StubModel(), _StubTok()
    calls = []

    def fake_load_megatron(ckpt_path, device, config_path):
        calls.append(("megatron", ckpt_path, device, config_path))
        return stub_model, stub_tok

    def fail_370m(*a, **k):
        raise AssertionError("370m loader must not be called for backend='megatron'")

    monkeypatch.setattr(model_io, "_load_megatron_model", fake_load_megatron)
    monkeypatch.setattr(model_io, "_load_370m_model", fail_370m)

    model, tok = model_io.load_model(
        "/some/final.pt", device="cuda:0", backend="megatron", config_path="/cfg.yaml"
    )

    assert model is stub_model
    assert tok is stub_tok
    assert model._nyaya_backend == "megatron"
    assert calls == [("megatron", Path("/some/final.pt"), "cuda:0", "/cfg.yaml")]


def test_load_model_rejects_unknown_backend():
    with pytest.raises(ValueError, match="unknown backend"):
        model_io.load_model("/some/ckpt.pt", backend="not-a-real-backend")


# ---------------------------------------------------------------------------
# load_model auto-detection (backend=None, the default): _detect_backend's
# own result decides which loader runs. Both loaders and the detector are
# stubbed, so no real checkpoint file is read.
# ---------------------------------------------------------------------------


def _fail(*_a, **_k):
    raise AssertionError("wrong backend loader was called")


def test_load_model_auto_detects_megatron(monkeypatch):
    stub_model, stub_tok = _StubModel(), _StubTok()

    monkeypatch.setattr(model_io, "_detect_backend", lambda p: "megatron")
    monkeypatch.setattr(
        model_io, "_load_megatron_model", lambda ckpt, device, config_path: (stub_model, stub_tok)
    )
    monkeypatch.setattr(model_io, "_load_370m_model", _fail)

    model, tok = model_io.load_model("/lab/prototypes/nyaya_ttt_rsi/runs/g0_sft_round1/final.pt")

    assert model._nyaya_backend == "megatron"
    assert tok is stub_tok


def test_load_model_auto_detects_370m(monkeypatch):
    stub_model, stub_tok = _StubModel(), _StubTok()

    monkeypatch.setattr(model_io, "_detect_backend", lambda p: "370m")
    monkeypatch.setattr(
        model_io, "_load_370m_model", lambda ckpt, device: (stub_model, stub_tok)
    )
    monkeypatch.setattr(model_io, "_load_megatron_model", _fail)

    model, tok = model_io.load_model("/trackB-local/m7/m7_retry_checkpoint.pt")

    assert model._nyaya_backend == "370m"
    assert tok is stub_tok


# ---------------------------------------------------------------------------
# _detect_backend: needs real torch.load, so these are the only tests here
# that require torch -- imported lazily, inside the test, so a host without
# torch skips just these two rather than failing collection of the file.
# ---------------------------------------------------------------------------


def test_detect_backend_megatron_blob(tmp_path):
    torch = pytest.importorskip("torch")

    blob_path = tmp_path / "fake_megatron.pt"
    torch.save(
        {
            "step": 1,
            "tokens_seen": 100,
            "cursor": 2,
            "model": {"w": torch.zeros(2, 2)},
            "opt": None,
            "arm": "baseline",
            "override_pattern": "M-M-M-*-",
        },
        blob_path,
    )

    assert model_io._detect_backend(blob_path) == "megatron"


def test_detect_backend_370m_blob(tmp_path):
    torch = pytest.importorskip("torch")

    blob_path = tmp_path / "fake_370m.pt"
    torch.save(
        {
            "model": {"w": torch.zeros(2, 2)},
            "config": {"d_model": 16, "n_layers": 4, "attention_every": 2},
            "arm": "baseline",
        },
        blob_path,
    )

    assert model_io._detect_backend(blob_path) == "370m"


def test_detect_backend_unknown_format_raises(tmp_path):
    torch = pytest.importorskip("torch")

    blob_path = tmp_path / "fake_unknown.pt"
    torch.save({"nonsense": 1}, blob_path)

    with pytest.raises(ValueError, match="cannot detect checkpoint backend"):
        model_io._detect_backend(blob_path)


# ---------------------------------------------------------------------------
# attention_block_indices dispatch
# ---------------------------------------------------------------------------


def test_attention_block_indices_megatron_pattern():
    model = _StubModel()
    model._nyaya_backend = "megatron"
    # Real pattern shape from runs/g0_sft_round1/train_report.json:
    # n_layers=32, attention_every=8 -> attention blocks at 7, 15, 23, 31.
    model.pattern = (
        "M-M-M-M-M-M-M-*-M-M-M-M-M-M-M-*-M-M-M-M-M-M-M-*-M-M-M-M-M-M-M-*-"
    )
    assert model_io.attention_block_indices(model) == [7, 15, 23, 31]


def test_attention_block_indices_megatron_pattern_small():
    model = _StubModel()
    model._nyaya_backend = "megatron"
    model.pattern = "M-M-*-"  # n_layers=3, attention_every=3 -> block 2 only
    assert model_io.attention_block_indices(model) == [2]


def test_attention_block_indices_370m_dispatches_to_model_config(monkeypatch):
    model = _StubModel()
    model._nyaya_backend = "370m"
    monkeypatch.setattr(
        model_io, "model_config", lambda m: {"attention_every": 8, "n_layers": 24}
    )
    assert model_io.attention_block_indices(model) == [7, 15, 23]


def test_attention_block_indices_defaults_to_370m_when_untagged(monkeypatch):
    """A model with no `_nyaya_backend` attribute at all (e.g. one built by
    older test doubles, or `ttt.py`'s synthetic test model) must still
    dispatch to the 370M path rather than crashing on a missing attribute --
    `load_model` always sets the tag, but `attention_block_indices` itself
    should be defensive."""
    model = _StubModel()
    monkeypatch.setattr(
        model_io, "model_config", lambda m: {"attention_every": 4, "n_layers": 8}
    )
    assert model_io.attention_block_indices(model) == [3, 7]


# ---------------------------------------------------------------------------
# g0/generate_megatron.py: generate/sequence_nll delegate to model_io's
# single implementation instead of keeping their own copies. No torch, no
# megatron.core import needed to verify the delegation wiring itself.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _load_megatron_model's config_path default (the "missing CLI flag"
# workaround: evaluate.py/loop.py never pass config_path at all, so
# model_io.py itself must supply a working default for G0's checkpoint
# layout -- see _load_megatron_model's docstring).
# ---------------------------------------------------------------------------


def test_load_megatron_model_defaults_config_path(monkeypatch):
    from prototypes.nyaya_ttt_rsi.g0 import generate_megatron

    calls = []

    def fake_load(ckpt_path, device, config_path):
        calls.append((ckpt_path, device, config_path))
        return _StubModel(), _StubTok()

    monkeypatch.setattr(generate_megatron, "load_model", fake_load)

    model_io._load_megatron_model(Path("/lab/runs/g0_sft_round1/final.pt"), "cuda:0", None)

    assert calls == [
        (
            Path("/lab/runs/g0_sft_round1/final.pt"),
            "cuda:0",
            model_io._DEFAULT_MEGATRON_CONFIG,
        )
    ]


def test_load_megatron_model_respects_explicit_config_path(monkeypatch):
    from prototypes.nyaya_ttt_rsi.g0 import generate_megatron

    calls = []

    def fake_load(ckpt_path, device, config_path):
        calls.append(config_path)
        return _StubModel(), _StubTok()

    monkeypatch.setattr(generate_megatron, "load_model", fake_load)

    model_io._load_megatron_model(
        Path("/lab/runs/g0_sft_round1/final.pt"), "cuda:0", "/custom/config.yaml"
    )

    assert calls == ["/custom/config.yaml"]


def test_generate_megatron_generate_delegates_to_model_io(monkeypatch):
    from prototypes.nyaya_ttt_rsi.g0 import generate_megatron

    calls = []

    class _FakeModelIO:
        def generate(self, model, tok, prompts, max_new_tokens=256, stop=None, max_batch_size=16):
            calls.append((model, tok, prompts, max_new_tokens, stop, max_batch_size))
            return ["stub-output"]

        def sequence_nll(self, model, tok, prompt, continuation):
            calls.append((model, tok, prompt, continuation))
            return 0.5

    fake = _FakeModelIO()
    monkeypatch.setattr(generate_megatron, "_model_io_module", lambda: fake)

    out = generate_megatron.generate(_StubModel(), _StubTok(), ["hi"], max_new_tokens=8)
    assert out == ["stub-output"]

    nll = generate_megatron.sequence_nll(_StubModel(), _StubTok(), "prompt", "cont")
    assert nll == 0.5
    assert len(calls) == 2

"""Tests for ttt.py, using a tiny synthetic model with dotted names shaped
like a NemotronH block (layers.N.mixer.{in,out}_proj, layers.N.attn.{q,o}_proj,
lm_head) so LoRA targeting by dotted-name regex is genuinely exercised.
No real Track B model or GPU needed.
"""

from __future__ import annotations

import torch
import torch.nn.functional as F
from torch import nn

from prototypes.nyaya_ttt_rsi.ttt import (
    LoRALinear,
    adapt,
    inject_lora,
    merged_delta_norm,
    restore,
    snapshot,
)

VOCAB = 64
DIM = 16
N_LAYERS = 8
LORA_TARGET_REGEX = r"\.(in_proj|out_proj|q_proj|o_proj)$"


class Mixer(nn.Module):
    def __init__(self, dim: int):
        super().__init__()
        self.in_proj = nn.Linear(dim, dim)
        self.out_proj = nn.Linear(dim, dim)

    def forward(self, x):
        return self.out_proj(torch.relu(self.in_proj(x)))


class Attn(nn.Module):
    def __init__(self, dim: int):
        super().__init__()
        self.q_proj = nn.Linear(dim, dim)
        self.o_proj = nn.Linear(dim, dim)

    def forward(self, x):
        return self.o_proj(torch.relu(self.q_proj(x)))


class Block(nn.Module):
    def __init__(self, dim: int, kind: str):
        super().__init__()
        self.kind = kind
        if kind == "mixer":
            self.mixer = Mixer(dim)
        else:
            self.attn = Attn(dim)

    def forward(self, x):
        sub = self.mixer if self.kind == "mixer" else self.attn
        return x + sub(x)


class TinyNemotronHLike(nn.Module):
    """layers.0..6 are 'mixer' blocks, layers.7 is an 'attn' block, matching
    the mixed-block structure of a real NemotronH-style model closely enough
    to exercise dotted-name regex targeting."""

    def __init__(self, vocab: int = VOCAB, dim: int = DIM, n_layers: int = N_LAYERS):
        super().__init__()
        kinds = ["mixer"] * (n_layers - 1) + ["attn"]
        self.embed = nn.Embedding(vocab, dim)
        self.layers = nn.ModuleList([Block(dim, k) for k in kinds])
        self.lm_head = nn.Linear(dim, vocab)

    def forward(self, input_ids: torch.Tensor) -> torch.Tensor:
        x = self.embed(input_ids)
        for layer in self.layers:
            x = layer(x)
        return self.lm_head(x)


def make_model(seed: int = 0) -> TinyNemotronHLike:
    torch.manual_seed(seed)
    model = TinyNemotronHLike()
    for p in model.parameters():
        p.requires_grad_(False)
    model.eval()
    return model


def byte_ids(text: str) -> torch.Tensor:
    return torch.tensor([[b % VOCAB for b in text.encode("utf-8")]], dtype=torch.long)


def nll_loss_fn(model, text: str) -> torch.Tensor:
    ids = byte_ids(text)
    if ids.shape[1] < 2:
        return torch.zeros(())
    logits = model(ids)
    return F.cross_entropy(logits[:, :-1].reshape(-1, VOCAB), ids[:, 1:].reshape(-1))


def test_inject_lora_targets_only_matching_linears():
    model = make_model()
    loras = inject_lora(model, LORA_TARGET_REGEX, r=4, alpha=8)

    # 7 mixer blocks * 2 linears + 1 attn block * 2 linears = 16
    assert len(loras) == 16
    for m in loras:
        assert isinstance(m, LoRALinear)
    # lm_head must NOT have been wrapped -- regex specificity check.
    assert isinstance(model.lm_head, nn.Linear) and not isinstance(model.lm_head, LoRALinear)
    assert isinstance(model.layers[0].mixer.in_proj, LoRALinear)
    assert isinstance(model.layers[7].attn.q_proj, LoRALinear)


def test_forward_unchanged_before_adapt_since_b_is_zero():
    model = make_model()
    ids = byte_ids("hello world")
    with torch.no_grad():
        out_before = model(ids).clone()

    inject_lora(model, LORA_TARGET_REGEX, r=4, alpha=8)
    with torch.no_grad():
        out_after_inject = model(ids)

    assert torch.allclose(out_before, out_after_inject, atol=1e-6)


def test_adapt_changes_forward_and_reset_restores_exactly():
    model = make_model()
    ids = byte_ids("the quick brown fox")
    with torch.no_grad():
        out_frozen = model(ids).clone()

    loras = inject_lora(model, LORA_TARGET_REGEX, r=4, alpha=8)

    adapt(model, tok=None, text="the quick brown fox jumps", loras=loras,
          steps=5, lr=1e-2, loss_fn=nll_loss_fn)

    with torch.no_grad():
        out_after_adapt = model(ids)
    assert not torch.allclose(out_frozen, out_after_adapt, atol=1e-6)
    assert merged_delta_norm(loras) > 0.0

    for m in loras:
        m.reset()
    with torch.no_grad():
        out_after_reset = model(ids)
    assert torch.allclose(out_frozen, out_after_reset, atol=1e-6)
    assert merged_delta_norm(loras) == 0.0


def test_snapshot_restore_round_trip():
    model = make_model()
    loras = inject_lora(model, LORA_TARGET_REGEX, r=4, alpha=8)
    snap0 = snapshot(loras)

    adapt(model, tok=None, text="round trip snapshot text", loras=loras,
          steps=3, lr=1e-2, loss_fn=nll_loss_fn)
    assert merged_delta_norm(loras) > 0.0

    restore(loras, snap0)
    assert merged_delta_norm(loras) == 0.0
    for m, (a0, b0) in zip(loras, snap0):
        assert torch.equal(m.lora_A, a0)
        assert torch.equal(m.lora_B, b0)


def test_ephemeral_adaptation_is_deterministic_across_resets():
    model = make_model()
    loras = inject_lora(model, LORA_TARGET_REGEX, r=4, alpha=8)
    text = "deterministic ephemeral adaptation trajectory"

    def recording_loss_fn(record):
        def fn(m, t):
            loss = nll_loss_fn(m, t)
            record.append(float(loss.detach().item()))
            return loss
        return fn

    trace_1: list[float] = []
    for m in loras:
        m.reset()
    adapt(model, tok=None, text=text, loras=loras, steps=5, lr=1e-2,
          loss_fn=recording_loss_fn(trace_1))

    trace_2: list[float] = []
    for m in loras:
        m.reset()
    adapt(model, tok=None, text=text, loras=loras, steps=5, lr=1e-2,
          loss_fn=recording_loss_fn(trace_2))

    assert len(trace_1) == 5
    assert trace_1 == trace_2


def test_adapt_sets_eval_mode_after_and_only_lora_has_grad():
    model = make_model()
    loras = inject_lora(model, LORA_TARGET_REGEX, r=4, alpha=8)
    adapt(model, tok=None, text="mode check text", loras=loras, steps=1, lr=1e-2,
          loss_fn=nll_loss_fn)
    assert model.training is False
    for name, p in model.named_parameters():
        if "lora_" in name:
            assert p.requires_grad
        else:
            assert not p.requires_grad

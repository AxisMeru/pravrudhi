"""LoRA injection + ephemeral test-time-training adaptation.

Generalizes the hand-rolled `LoRALinear` pattern from the throwaway spike
(docs/research-spikes/2026-09-13-ttt-rsi-track-b/run_ttt_rsi_prototype.py):
a frozen base nn.Linear wrapped with a low-rank trainable delta, A fixed at
construction (random, small-scale) and B zero-initialized so injection is a
no-op until trained. `reset()` restores that exact starting point.

No peft dependency (same reasoning as the spike: keep this self-contained
and not hostage to a training container's peft/torchao pairing).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import torch
from torch import nn


class LoRALinear(nn.Module):
    """Wraps a frozen nn.Linear with a low-rank trainable delta.

    forward(x) = base(x) + scaling * (x @ A^T @ B^T)

    A is fixed random at construction time (stored in `_init_A`) and B starts
    at zero, so a freshly-injected or freshly-reset LoRALinear is exactly a
    no-op wrapper around `base`. dtype/device always follow the base weight.
    """

    def __init__(self, base: nn.Linear, r: int = 8, alpha: int = 16):
        super().__init__()
        self.base = base
        for p in self.base.parameters():
            p.requires_grad_(False)
        device, dtype = base.weight.device, base.weight.dtype
        self.r = r
        self.alpha = alpha
        self.scaling = alpha / r
        self.lora_A = nn.Parameter(
            torch.randn(r, base.in_features, device=device, dtype=dtype) * 0.01
        )
        self.lora_B = nn.Parameter(
            torch.zeros(base.out_features, r, device=device, dtype=dtype)
        )
        # Fixed at construction so reset() always returns to the *same*
        # starting point. Zeroing A too would zero dL/dB (it depends on
        # x @ A^T), so a true reset restores the original random A, not an
        # all-zero A -- matching the spike's rationale exactly.
        self._init_A = self.lora_A.detach().clone()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.base(x) + (x @ self.lora_A.T @ self.lora_B.T) * self.scaling

    def reset(self) -> None:
        with torch.no_grad():
            self.lora_A.copy_(self._init_A)
            self.lora_B.zero_()


def _set_module_by_dotted_name(root: nn.Module, dotted_name: str, new_module: nn.Module) -> None:
    """Replace the submodule at `dotted_name` (e.g. 'layers.0.mixer.in_proj')
    with `new_module`, handling nn.ModuleList / nn.Sequential integer indices
    via setattr on the container (Module.__setattr__ does not accept
    numeric string attribute names for list-style children, so those need
    `container[idx] = new_module` semantics instead)."""
    parts = dotted_name.split(".")
    parent = root
    for part in parts[:-1]:
        if part.isdigit():
            parent = parent[int(part)]
        else:
            parent = getattr(parent, part)
    last = parts[-1]
    if last.isdigit():
        parent[int(last)] = new_module
    else:
        setattr(parent, last, new_module)


def inject_lora(model: nn.Module, target_regex: str, r: int = 8, alpha: int = 16) -> list[LoRALinear]:
    """Replace every nn.Linear whose dotted name matches `target_regex` with
    a LoRALinear wrapper. Returns the list of injected LoRALinear modules in
    traversal order."""
    pattern = re.compile(target_regex)
    targets: list[str] = []
    for name, module in model.named_modules():
        if isinstance(module, nn.Linear) and pattern.search(name):
            targets.append(name)

    wrapped: list[LoRALinear] = []
    for name in targets:
        parts = name.split(".")
        parent = model
        for part in parts[:-1]:
            parent = parent[int(part)] if part.isdigit() else getattr(parent, part)
        last = parts[-1]
        base = parent[int(last)] if last.isdigit() else getattr(parent, last)
        lora = LoRALinear(base, r=r, alpha=alpha)
        _set_module_by_dotted_name(model, name, lora)
        wrapped.append(lora)
    return wrapped


def snapshot(loras: list[LoRALinear]) -> list[tuple[torch.Tensor, torch.Tensor]]:
    """Detached clones of (lora_A, lora_B) for every module, in order."""
    return [(m.lora_A.detach().clone(), m.lora_B.detach().clone()) for m in loras]


def restore(loras: list[LoRALinear], snap: list[tuple[torch.Tensor, torch.Tensor]]) -> None:
    with torch.no_grad():
        for m, (a, b) in zip(loras, snap):
            m.lora_A.copy_(a)
            m.lora_B.copy_(b)


def merged_delta_norm(loras: list[LoRALinear]) -> float:
    """Frobenius norm of the sum of each module's effective delta
    (B @ A * scaling) -- a single scalar for logging how far the ephemeral
    adaptation moved the weights."""
    total_sq = 0.0
    for m in loras:
        delta = (m.lora_B @ m.lora_A) * m.scaling
        total_sq += float(torch.sum(delta.float() ** 2).item())
    return total_sq ** 0.5


def _default_loss_fn(model, tok, text: str) -> torch.Tensor:
    """Lazy import so ttt.py never hard-depends on model_io (owned by the
    loader agent, written concurrently). Falls back to a plain byte-level
    next-token NLL if model_io isn't importable or doesn't expose the
    expected helper."""
    try:
        import model_io  # type: ignore
    except ImportError:
        model_io = None

    if model_io is not None and hasattr(model_io, "sequence_nll"):
        # sequence_nll(model, tok, prompt, continuation) -> float (mean NLL,
        # a plain float not a tensor). Treat the whole text as context with
        # an empty prompt so every byte after the first is scored.
        loss_val = model_io.sequence_nll(model, tok, "", text)
        return torch.as_tensor(loss_val, dtype=torch.float32, device=next(model.parameters()).device)

    # Generic fallback: byte-level next-token NLL via the tokenizer's
    # encode-to-ids interface (works for a plain ByteTokenizer or anything
    # HF-tokenizer-shaped that returns input_ids on __call__).
    device = next(model.parameters()).device
    if hasattr(tok, "encode"):
        ids = tok.encode(text)
    else:
        ids = tok(text)["input_ids"]
    ids_t = torch.as_tensor(ids, dtype=torch.long, device=device).unsqueeze(0)
    if ids_t.shape[1] < 2:
        return torch.zeros((), device=device)
    out = model(input_ids=ids_t, labels=ids_t)
    return out.loss


def adapt(
    model,
    tok,
    text: str,
    loras: list[LoRALinear],
    steps: int,
    lr: float,
    loss_fn=None,
    optimizer: str = "adamw",
    max_grad_norm: float = 1.0,
    clip_delta: float | None = None,
) -> float:
    """Ephemeral self-supervised TTT: `steps` gradient steps on `text` only,
    using a FRESH optimizer every call (no momentum carried between calls --
    that is what makes adaptation ephemeral/reset-safe). Only LoRA params
    get grads. model.train() during the steps, model.eval() after. Returns
    the final step's loss (float).

    `loss_fn(model, text) -> torch.Tensor` is injected so this module never
    needs to know model_io's internals; if omitted, a lazy-imported default
    tries model_io.sequence_nll-style scoring and otherwise falls back to a
    generic byte-level next-token NLL.
    """
    if loss_fn is None:
        loss_fn = lambda m, t: _default_loss_fn(m, tok, t)

    params = [p for m in loras for p in (m.lora_A, m.lora_B)]
    if optimizer == "adamw":
        opt = torch.optim.AdamW(params, lr=lr)
    elif optimizer == "sgd":
        opt = torch.optim.SGD(params, lr=lr)
    else:
        raise ValueError(f"unknown optimizer: {optimizer!r}")

    model.train()
    final_loss = 0.0
    snap0 = snapshot(loras) if clip_delta is not None else None
    try:
        for _ in range(steps):
            loss = loss_fn(model, text)
            opt.zero_grad()
            loss.backward()
            if max_grad_norm is not None and max_grad_norm > 0:
                torch.nn.utils.clip_grad_norm_(params, max_grad_norm)
            opt.step()
            final_loss = float(loss.detach().item())

            if clip_delta is not None:
                norm = merged_delta_norm(loras)
                if norm > clip_delta:
                    scale = clip_delta / max(norm, 1e-12)
                    with torch.no_grad():
                        for m in loras:
                            m.lora_B.mul_(scale)
    finally:
        model.eval()

    return final_loss

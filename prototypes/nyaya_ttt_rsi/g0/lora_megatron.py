"""LoRA injection for the 1.13B Megatron-Core model, extending `ttt.py`'s
`inject_lora` to modules that are not `torch.nn.Linear` subclasses.

Why this file exists (do not edit `ttt.py`): `ttt.py::inject_lora` finds
targets with `isinstance(module, nn.Linear)`. That is correct for Track B's
370M custom-loop `NemotronH` (plain `nn.Linear` throughout, confirmed by
`model_io.py::linear_module_names`'s own docstring listing `mixer.in_proj`,
`mixer.out_proj`, `mixer.qkv`, `mlp.1`, `mlp.3` as ordinary `nn.Linear`s), but
it is very likely wrong for the 1.13B line: `StructuredNemotronH.core` is a
`megatron.core.models.mamba.MambaModel` built from `mamba_stack_spec`, whose
projections are Megatron-Core's own `ColumnParallelLinear` /
`RowParallelLinear` (`megatron.core.tensor_parallel.layers`) or, depending on
the spec's transformer-engine submodule choices, `transformer_engine.pytorch`
`Linear` layers. Both of those are `nn.Module` subclasses with their own
`weight`/`bias` parameters and their own `forward` -- neither inherits from
`torch.nn.Linear`. That has two consequences worth being explicit about,
since neither can be verified from this host (megatron.core is a
container-only dependency; this claim is standard-library/framework
knowledge about Megatron-Core, not something this file's author ran):

1. `isinstance(module, nn.Linear)` will match zero modules on the Megatron
   model, so `ttt.py::inject_lora` (unmodified) is a silent no-op for it.
2. Megatron's parallel linear layers commonly return a `(output, output_bias)`
   tuple from `forward` (bias-add deferred/fused separately for
   tensor-parallel efficiency) rather than a plain tensor -- unlike
   `nn.Linear.forward`, which always returns a tensor. A generic wrapper
   therefore cannot assume the base module's return type and must handle
   both shapes.

`inject_lora_generic` below targets any submodule that merely *looks* like a
linear layer -- has a 2-D `.weight` parameter and is callable as `forward(x)`
-- rather than checking a specific base class, and its wrapper module
inspects the base's return value at call time so it works whether the base
returns a tensor or a `(tensor, bias)` tuple. This has NOT been run against
an actual `megatron.core.models.mamba.MambaModel` (no GPU/container access
during this write-up); it should be smoke-tested inside the training
container (see g0/README.md's "syntax/import check" step, and then the
first real run) before being trusted for a real TTT adaptation loop.
"""

from __future__ import annotations

import re

import torch
from torch import nn


class GenericLoRA(nn.Module):
    """Wraps an arbitrary linear-like base module with a low-rank trainable
    delta, computed from the base's own `.weight` shape rather than
    `in_features`/`out_features` attributes (which `nn.Linear` has but a
    Megatron `ColumnParallelLinear`/`RowParallelLinear` or a
    transformer-engine `Linear` may name differently or not expose at all).

    `forward(x) = base(x) [+ bias, if any] + scaling * (x @ A^T @ B^T)`.
    If `base(x)` returns a tensor, the delta is added directly. If it returns
    a tuple (Megatron's `(output, output_bias)` convention), the delta is
    added to element 0 only and the rest of the tuple is passed through
    unchanged -- the delta never touches `output_bias`, so the fused-bias
    path Megatron relies on downstream is left intact.

    `A` is fixed random at construction (stored in `_init_A`); `B` starts at
    zero, so injection is a no-op until trained, exactly like
    `ttt.py::LoRALinear`. Weight shape convention follows `nn.Linear` /
    Megatron parallel linears: `weight.shape == (out_features, in_features)`.
    """

    def __init__(self, base: nn.Module, r: int = 8, alpha: int = 16):
        super().__init__()
        if not hasattr(base, "weight") or base.weight.dim() != 2:
            raise ValueError(
                f"GenericLoRA requires a base module with a 2-D `.weight`, "
                f"got {type(base).__name__} with "
                f"weight shape {getattr(base, 'weight', None) if not hasattr(base, 'weight') else base.weight.shape}"
            )
        self.base = base
        for p in self.base.parameters():
            p.requires_grad_(False)
        out_features, in_features = base.weight.shape
        device, dtype = base.weight.device, base.weight.dtype
        self.r = r
        self.alpha = alpha
        self.scaling = alpha / r
        self.lora_A = nn.Parameter(torch.randn(r, in_features, device=device, dtype=dtype) * 0.01)
        self.lora_B = nn.Parameter(torch.zeros(out_features, r, device=device, dtype=dtype))
        self._init_A = self.lora_A.detach().clone()

    def forward(self, x: torch.Tensor):
        out = self.base(x)
        delta = (x @ self.lora_A.T @ self.lora_B.T) * self.scaling
        if isinstance(out, tuple):
            head, *rest = out
            return (head + delta, *rest)
        return out + delta

    def reset(self) -> None:
        with torch.no_grad():
            self.lora_A.copy_(self._init_A)
            self.lora_B.zero_()


def _is_linear_like(module: nn.Module) -> bool:
    """True for `nn.Linear` itself and for any module exposing a plain 2-D
    `.weight` Parameter (the Megatron/TE convention this file targets).
    Deliberately excludes modules whose `.weight` is not 2-D (e.g.
    `nn.Embedding`'s weight is 2-D too but embeddings are never LoRA targets
    here, so callers should scope `target_regex` to exclude embeddings;
    excluding by shape alone is not sufficient and this function does not
    try to be)."""
    w = getattr(module, "weight", None)
    return isinstance(w, torch.Tensor) and w.dim() == 2


def _set_module_by_dotted_name(root: nn.Module, dotted_name: str, new_module: nn.Module) -> None:
    parts = dotted_name.split(".")
    parent = root
    for part in parts[:-1]:
        parent = parent[int(part)] if part.isdigit() else getattr(parent, part)
    last = parts[-1]
    if last.isdigit():
        parent[int(last)] = new_module
    else:
        setattr(parent, last, new_module)


def find_lora_targets(model: nn.Module, target_regex: str) -> list[str]:
    """Dotted names of every linear-like submodule matching `target_regex`.
    Excludes `nn.Embedding` explicitly (its `.weight` is 2-D but it is never
    a LoRA target); everything else with a 2-D `.weight` is a candidate,
    covering `nn.Linear`, Megatron `ColumnParallelLinear`/`RowParallelLinear`,
    and transformer-engine `Linear` uniformly."""
    pattern = re.compile(target_regex)
    targets: list[str] = []
    for name, module in model.named_modules():
        if isinstance(module, nn.Embedding):
            continue
        if _is_linear_like(module) and pattern.search(name):
            targets.append(name)
    return targets


def inject_lora_generic(
    model: nn.Module, target_regex: str, r: int = 8, alpha: int = 16
) -> list[GenericLoRA]:
    """Like `ttt.py::inject_lora`, but for the Megatron model: matches any
    linear-like submodule (see `_is_linear_like`), not only `nn.Linear`.
    Returns the injected `GenericLoRA` modules in traversal order."""
    targets = find_lora_targets(model, target_regex)
    wrapped: list[GenericLoRA] = []
    for name in targets:
        parts = name.split(".")
        parent = model
        for part in parts[:-1]:
            parent = parent[int(part)] if part.isdigit() else getattr(parent, part)
        last = parts[-1]
        base = parent[int(last)] if last.isdigit() else getattr(parent, last)
        lora = GenericLoRA(base, r=r, alpha=alpha)
        _set_module_by_dotted_name(model, name, lora)
        wrapped.append(lora)
    return wrapped

"""Model + tokenizer loading, generation, and NLL scoring for both NemotronH
checkpoint families used by this prototype: the 370M law-tuned custom-loop
line (Track B's `m7_retry_checkpoint.pt`, loaded directly by this module) and
the 1.13B Megatron-Core line (`g0/train_megatron_sft.py`'s round-1 SFT
output, delegated to `g0/generate_megatron.py::load_model`, which itself
wraps Track B's `scripts/m4/eval_adapter.py::load_megatron_blob`).

`load_model` auto-detects which family a checkpoint belongs to (or takes an
explicit `backend="megatron"|"370m"` override) and tags the returned model
with a private `_nyaya_backend` attribute so `attention_block_indices` below
can dispatch without re-deriving the family from scratch. `generate` and
`sequence_nll` need NO dispatch at all: both model classes expose the exact
same `forward(tokens, boundary, roles) -> logits` signature (boundary/roles
always zeros -- inert structured-channels input on both lines), so the single
implementation below already works for either family unchanged.
`g0/generate_megatron.py` re-exports these two functions from here instead of
keeping its own byte-identical copies, so there is exactly one implementation
of the batched-greedy-decode / NLL logic in this codebase.

Wraps Track B's own code rather than reimplementing it:
  - `NemotronH` is imported by file path from `/trackB/scripts/m2/train_130m.py`
    (same technique as `prabhasa.infrastructure.ml.inference.NemotronHRunner`,
    which calls this "single source of truth -- imported by path so the
    architecture never diverges").
  - `ByteTokenizer` is imported by file path from
    `/trackB/src/prabhasa/application/tokenizer/bytelevel.py` (loaded directly,
    not via the `prabhasa` package, so this module has no dependency on the
    rest of that package's import graph).
  - Batched greedy generation mirrors `/trackB/scripts/m7/generate.py`
    (`group_by_length` / `batched_greedy_decode`): Mamba2 has no attention
    mask, so same-length prompts are batched with NO padding, never padded
    across lengths. The only addition beyond Track B's script is optional
    stop-string truncation (post-hoc string truncation after generation,
    so it never disturbs the no-padding batching invariant).

Bug found in Track B's own loading pattern (see `load_model` docstring below):
`torch.load(..., map_location=device, weights_only=False)` on the 4.2 GB
checkpoint OOM-kills inside the 8 GiB RAM-capped `ttt-lab` container when
`device` is `cpu` (or when loading to a `cuda` device from a build without
`mmap=True`) -- the pickle load stages the full state dict in host RAM before
any device transfer, and with the model's constructed CPU copy alongside it
that exceeds the cap. Fix: load straight to the CUDA device (skips the CPU
staging buffer for tensor storages) and pass `mmap=True` (torch >= 2.1) so the
file's storages are mapped rather than fully read into memory up front. Filed
as F5 in
docs/research-spikes/2026-09-13-ttt-rsi-track-b/FIXES-FOR-MAIN-SESSIONS.md.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
from pathlib import Path
from typing import Any

TRACKB_ROOT = Path("/trackB")
_TRAIN_MODULE = TRACKB_ROOT / "scripts" / "m2" / "train_130m.py"
_BYTETOKENIZER_MODULE = (
    TRACKB_ROOT / "src" / "prabhasa" / "application" / "tokenizer" / "bytelevel.py"
)


def _load_module_from_path(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load module {name!r} from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _nemotron_class() -> Any:
    return _load_module_from_path("prabhasa_train_130m", _TRAIN_MODULE).NemotronH


def _byte_tokenizer_class() -> Any:
    return _load_module_from_path("prabhasa_bytelevel", _BYTETOKENIZER_MODULE).ByteTokenizer


def checkpoint_sha256_prefix(ckpt_path: str | Path, n_bytes: int = 1 << 20) -> str:
    """sha256 of the first `n_bytes` of the checkpoint file (default 1 MiB) --
    cheap fingerprint for the report, avoids hashing the full 4.2 GB file."""
    h = hashlib.sha256()
    with open(ckpt_path, "rb") as f:
        h.update(f.read(n_bytes))
    return h.hexdigest()


def _detect_backend(ckpt_path: str | Path) -> str:
    """Peek only the top-level keys of a checkpoint blob -- `mmap=True` +
    `weights_only=True`, the same technique `g0/train_megatron_sft.py::
    _peek_arm` uses to read the `arm` field -- to decide which backend can
    load it, without ever staging the multi-GB tensor storages in host RAM.

    Mirrors the detection rule in Track B's own
    `scripts/m4/eval_adapter.py::detect_checkpoint_format`
    (`"override_pattern" in blob and "config" not in blob` => Megatron-Core;
    `"config" in blob` => the 370M custom-loop format this module was
    originally written for) rather than reimplementing new rules, but that
    function omits `mmap=True` -- see F5 in FIXES-FOR-MAIN-SESSIONS.md for
    why an un-mmap'd `torch.load` on a multi-GB blob is unsafe in this
    container even with `weights_only=True` (the pickle load still stages
    the full state dict in host RAM before any device transfer).
    """
    import torch  # local: torch is a GPU-container-only dependency

    blob = torch.load(Path(ckpt_path), map_location="cpu", weights_only=True, mmap=True)
    try:
        if "override_pattern" in blob and "config" not in blob:
            return "megatron"
        if "config" in blob:
            return "370m"
        raise ValueError(
            f"cannot detect checkpoint backend for {ckpt_path}: "
            f"unrecognized top-level keys {sorted(blob.keys())}"
        )
    finally:
        del blob


def _load_370m_model(ckpt_path: Path, device: str) -> tuple[Any, Any]:
    """Load the 370M custom-loop NemotronH model + a ByteTokenizer instance
    from a Track-B-format checkpoint blob (`{"model", "config", "arm", ...}`).

    Loads storages directly onto `device` with `mmap=True` -- see the module
    docstring for why (`map_location="cpu"` OOM-kills the 8 GiB-capped
    container on this 4.2 GB checkpoint).
    """
    import torch  # local: torch is a GPU-container-only dependency

    blob = torch.load(ckpt_path, map_location=device, weights_only=False, mmap=True)
    cfg = dict(blob["config"])
    nemotron_cls = _nemotron_class()
    model = nemotron_cls(cfg)
    state = blob["model"]
    # Strip a possible `_orig_mod.` prefix left by torch.compile (same fix as
    # NemotronHRunner).
    state = {k.replace("_orig_mod.", ""): v for k, v in state.items()}
    model.to(device)
    model.load_state_dict(state)
    model.eval()

    tok = _byte_tokenizer_class()()
    return model, tok


_DEFAULT_MEGATRON_CONFIG = TRACKB_ROOT / "configs" / "train" / "nemotron_h_1b.yaml"


def _load_megatron_model(
    ckpt_path: Path, device: str, config_path: str | Path | None
) -> tuple[Any, Any]:
    """Delegate to `g0/generate_megatron.py::load_model`, which itself wraps
    Track B's `scripts/m4/eval_adapter.py::load_megatron_blob` (mmap +
    process-group-init discipline; see F5/F6/F18 in
    FIXES-FOR-MAIN-SESSIONS.md) -- single source of truth for the 1.13B
    Megatron-Core line, not reimplemented here. Imported lazily so importing
    `model_io` never requires `megatron.core` or a GPU.

    `config_path=None` is given a real default here rather than passed
    straight through: `load_megatron_blob`'s own default walks UP from
    `ckpt_path` looking for `configs/train/nemotron_h_1b.yaml`
    (`eval_adapter.py::_find_repo_config`), which only finds it for
    checkpoints that live inside `/trackB`'s own directory tree at a
    matching depth. G0's checkpoints (e.g.
    `runs/g0_sft_round1/final.pt`) live under this prototype's own
    `/lab` mount instead, so that walk would raise `FileNotFoundError` for
    every checkpoint this module is actually meant to load. Defaulting to
    the one config this codebase currently trains the 1.13B line from
    (`/trackB/configs/train/nemotron_h_1b.yaml`) means `evaluate.py`'s
    unmodified `model_io.load_model(ckpt_path, device=...)` call (no
    `config_path` parameter exists on that call site) still works for this
    checkpoint without a new CLI flag. A future Megatron checkpoint trained
    from a *different* config would need an explicit `config_path=` --
    there is no flag on `evaluate.py`/`loop.py` to supply one; see
    `g0/README.md`."""
    if config_path is None:
        config_path = _DEFAULT_MEGATRON_CONFIG

    from .g0 import generate_megatron as megatron_io

    return megatron_io.load_model(ckpt_path, device=device, config_path=config_path)


def load_model(
    ckpt_path: str | Path,
    device: str = "cuda:0",
    backend: str | None = None,
    config_path: str | Path | None = None,
) -> tuple[Any, Any]:
    """Load a model + a ByteTokenizer instance from either supported
    checkpoint family and return `(model, tok)` in the exact shape
    `evaluate.py`/`loop.py` already call: `load_model(ckpt_path, device=...)`.

    `backend` is `"370m"` (Track B's custom-loop NemotronH,
    `_load_370m_model`) or `"megatron"` (the 1.13B Megatron-Core line,
    `_load_megatron_model`); `None` (the default) auto-detects it from the
    checkpoint's own top-level keys via `_detect_backend`, so existing call
    sites that only ever pass `(ckpt_path, device=...)` keep working
    unchanged when pointed at a Megatron checkpoint. `config_path` is
    Megatron-only (the YAML `eval_adapter.load_megatron_blob` needs to
    rebuild the transformer config; ignored for `"370m"`).

    The returned model is tagged with a private `_nyaya_backend` attribute
    (`"370m"` or `"megatron"`) so `attention_block_indices` can dispatch
    without re-deriving the family. `generate`/`sequence_nll` need no such
    tag -- see the module docstring for why they already work unchanged for
    either family.
    """
    ckpt_path = Path(ckpt_path)
    resolved_backend = backend if backend is not None else _detect_backend(ckpt_path)

    if resolved_backend == "megatron":
        model, tok = _load_megatron_model(ckpt_path, device, config_path)
    elif resolved_backend == "370m":
        model, tok = _load_370m_model(ckpt_path, device)
    else:
        raise ValueError(f"unknown backend {resolved_backend!r}; expected 'megatron' or '370m'")

    model._nyaya_backend = resolved_backend
    return model, tok


def _batched_greedy_decode(
    model: Any, prompt_ids_batch: list[list[int]], max_new_tokens: int, device: Any
) -> list[list[int]]:
    """All prompts must already be the same length -- mirrors
    `scripts/m7/generate.py::batched_greedy_decode` exactly (no padding;
    Mamba2 has no attention-mask input to make padding safe)."""
    import torch

    lengths = {len(p) for p in prompt_ids_batch}
    if len(lengths) > 1:
        raise ValueError(f"_batched_greedy_decode requires equal-length prompts, got {lengths}")
    ids = torch.tensor(prompt_ids_batch, dtype=torch.long, device=device)
    generated: list[list[int]] = [[] for _ in prompt_ids_batch]
    with torch.no_grad():
        for _ in range(max_new_tokens):
            zeros = torch.zeros_like(ids)
            with torch.autocast(
                ids.device.type, dtype=torch.bfloat16, enabled=(ids.device.type == "cuda")
            ):
                logits = model(ids, zeros, zeros)
            next_ids = logits[:, -1].argmax(dim=-1)
            ids = torch.cat([ids, next_ids.unsqueeze(1)], dim=1)
            for b in range(len(prompt_ids_batch)):
                generated[b].append(int(next_ids[b]))
    return generated


def generate(
    model: Any,
    tok: Any,
    prompts: list[str],
    max_new_tokens: int = 256,
    stop: list[str] | None = None,
    max_batch_size: int = 16,
) -> list[str]:
    """Greedy byte-level generation, batched by exact prompt length (never
    padded -- see module docstring). `stop` is applied as a post-hoc
    truncation of the decoded text at the earliest occurrence of any stop
    string (the byte vocab has no EOS token, so generation always runs the
    full `max_new_tokens`; `stop` only affects what is returned)."""
    device = next(model.parameters()).device
    prompt_ids_list = [tok.encode(p) for p in prompts]

    groups: dict[int, list[int]] = {}
    for i, ids in enumerate(prompt_ids_list):
        groups.setdefault(len(ids), []).append(i)

    out_ids: dict[int, list[int]] = {}
    for _length, indices in groups.items():
        for start in range(0, len(indices), max_batch_size):
            chunk = indices[start : start + max_batch_size]
            chunk_prompts = [prompt_ids_list[i] for i in chunk]
            decoded = _batched_greedy_decode(model, chunk_prompts, max_new_tokens, device)
            for i, ids in zip(chunk, decoded, strict=True):
                out_ids[i] = ids

    texts = [tok.decode(out_ids[i]) for i in range(len(prompts))]
    if stop:
        truncated = []
        for text in texts:
            cut = len(text)
            for s in stop:
                idx = text.find(s)
                if idx != -1:
                    cut = min(cut, idx)
            truncated.append(text[:cut])
        texts = truncated
    return texts


def sequence_nll(model: Any, tok: Any, prompt: str, continuation: str) -> float:
    """Mean negative log-likelihood (natural log, nats) over continuation
    bytes only -- prompt bytes contribute context but are never scored.

    Teacher-forces the full `prompt + continuation` byte sequence in one
    forward pass; byte at position i is predicted from logits at position
    i-1, so a continuation byte can only be scored once it has at least one
    preceding byte of context. If `prompt` is empty, the continuation's own
    first byte therefore has no valid predecessor and is excluded (not
    invented as a from-nothing prediction) -- this matches `ttt.py`'s usage
    of `sequence_nll(model, tok, "", text)` for plain self-supervised
    next-byte NLL over `text`.

    Does NOT wrap the forward pass in `torch.no_grad()`: `ttt.py::adapt` calls
    this (via its default loss_fn) inside a training loop and needs gradients
    to flow back into the injected LoRA parameters.
    """
    import torch

    device = next(model.parameters()).device
    prompt_ids = tok.encode(prompt)
    cont_ids = tok.encode(continuation)
    all_ids = prompt_ids + cont_ids
    if len(all_ids) < 2:
        return 0.0

    n_prompt = len(prompt_ids)
    tokens = torch.tensor([all_ids], dtype=torch.long, device=device)
    zeros = torch.zeros_like(tokens)
    with torch.autocast(device.type, dtype=torch.bfloat16, enabled=(device.type == "cuda")):
        logits = model(tokens, zeros, zeros)[0]  # (L, vocab)

    targets = tokens[0, 1:]  # (L-1,) -- targets[j] = all_ids[j+1]
    preds = logits[:-1]  # (L-1, vocab) -- preds[j] predicts all_ids[j+1]

    j_start = max(n_prompt - 1, 0)
    if j_start >= targets.shape[0]:
        return 0.0

    sel_preds = preds[j_start:].float()
    sel_targets = targets[j_start:]
    nll = torch.nn.functional.cross_entropy(sel_preds, sel_targets, reduction="mean")
    return float(nll.item())


def linear_module_names(model: Any) -> list[str]:
    """Dotted names of every `nn.Linear` submodule, for LoRA targeting.

    On the 370M law-tuned checkpoint (24 blocks, attention_every=8, so blocks
    7/15/23 use attention and every other block uses Mamba2):
      - Mamba2 blocks (21 of 24): `blocks.<i>.mixer.in_proj`, `blocks.<i>.mixer.out_proj`
      - Attention blocks (3 of 24, i in {7,15,23}): `blocks.<i>.mixer.qkv`,
        `blocks.<i>.mixer.out_proj` (attn_impl="sdpa" -> fused qkv, not separate q/k/v)
      - MLP (all 24 blocks): `blocks.<i>.mlp.1` (d -> d_ffn), `blocks.<i>.mlp.3` (d_ffn -> d)
      - `head` (vocab projection, weight-tied to the embedding -- embedding
        itself is `nn.Embedding`, not `nn.Linear`, so it is never in this list)
    """
    import torch.nn as nn

    return [name for name, module in model.named_modules() if isinstance(module, nn.Linear)]


def _attention_block_indices_megatron(model: Any) -> list[int]:
    """Indices of the Megatron-Core model's blocks that use attention,
    parsed straight from the loaded `model.pattern` hybrid override string
    (`derive_override_pattern` in Track B's `scripts/m4/eval_adapter.py`,
    e.g. `"M-M-M-M-M-M-M-*-M-...-*-"` for 32 blocks / attention_every=8:
    seven Mamba2 ('M') blocks then one attention ('*') block, repeated).

    `derive_override_pattern` appends one mixer token immediately followed by
    its own `"-"` for every block (`out.append(tok); out.append("-")`), so
    splitting the joined string on `"-"` yields exactly one entry per block,
    in block order, plus a single trailing empty string from the last
    block's trailing dash (never `"*"`, so it is naturally excluded below --
    confirmed against the real `train_report.json` pattern:
    `"M-M-M-M-M-M-M-*-...-"` .split("-") -> attention indices [7, 15, 23, 31]
    for n_layers=32, attention_every=8, matching the 370M line's own
    `(i + 1) % attention_every == 0` rule)."""
    return [i for i, tok in enumerate(model.pattern.split("-")) if tok == "*"]


def attention_block_indices(model: Any) -> list[int]:
    """Convenience: indices of the model's blocks that use attention (vs
    Mamba2). Dispatches on the `_nyaya_backend` tag `load_model` sets:
    Megatron models are parsed from `model.pattern`
    (`_attention_block_indices_megatron`); the 370M custom-loop model is
    derived from the reconstructed config the same way
    `train_130m.py::NemotronH` does (`(i + 1) % attention_every == 0`)."""
    if getattr(model, "_nyaya_backend", "370m") == "megatron":
        return _attention_block_indices_megatron(model)
    every = int(model_config(model)["attention_every"])
    n_layers = int(model_config(model)["n_layers"])
    return [i for i in range(n_layers) if (i + 1) % every == 0]


def model_config(model: Any) -> dict[str, Any]:
    """Best-effort recovery of the config dict used to build `model`, by
    reconstructing it from live module shapes (NemotronH does not stash its
    own cfg on `self`)."""
    d_model = model.embed.embedding_dim
    vocab_size = model.embed.num_embeddings
    n_layers = len(model.blocks)
    attn_indices = [i for i, b in enumerate(model.blocks) if b.use_attention]
    attention_every = (attn_indices[0] + 1) if attn_indices else n_layers + 1
    return {
        "d_model": d_model,
        "vocab_size": vocab_size,
        "n_layers": n_layers,
        "attention_every": attention_every,
    }

"""Model + tokenizer loading, generation, and NLL scoring for the 370M law-tuned
NemotronH checkpoint (Track B's `m7_retry_checkpoint.pt`).

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


def load_model(ckpt_path: str | Path, device: str = "cuda:0") -> tuple[Any, Any]:
    """Load the NemotronH model + a ByteTokenizer instance from a Track-B-format
    checkpoint blob (`{"model", "config", "arm", ...}`).

    Loads storages directly onto `device` with `mmap=True` -- see the module
    docstring for why (`map_location="cpu"` OOM-kills the 8 GiB-capped
    container on this 4.2 GB checkpoint).
    """
    import torch  # local: torch is a GPU-container-only dependency

    blob = torch.load(Path(ckpt_path), map_location=device, weights_only=False, mmap=True)
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


def attention_block_indices(model: Any) -> list[int]:
    """Convenience: indices of `model.blocks` that use attention (vs Mamba2),
    derived from the loaded config the same way `train_130m.py::NemotronH`
    does (`(i + 1) % attention_every == 0`)."""
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

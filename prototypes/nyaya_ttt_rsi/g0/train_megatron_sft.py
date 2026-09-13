#!/usr/bin/env python3
"""G0 real SFT trainer for the 1.13B Megatron-Core NemotronH line
(`data/checkpoints/m4/final.pt`), promoting the proven dry-run configuration
in `/trackB/scripts/g0/sft_megatron_batched.py` (40/40 steps, batch 4,
seq_len 512, peak VRAM 23.8 GiB, no OOM -- see
`runs/g0_expandable/dry_run_1p13b_batch4.json`) to a trainer that actually
saves a checkpoint. It never edits or imports anything under `/trackB` as
writable; it only reads `/trackB` (read-only mount) for the loader
(`scripts/m4/eval_adapter.load_megatron_blob`) and the config YAML.

Per-step pattern, preflight, loss masking, and grad-accum are all a
deliberate, exact port of the proven dry run plus Track B's own 370M trainer
(`scripts/m7/train_full_sft.py`) for the pieces the dry run didn't need
(grad accumulation, checkpoint saving, multi-epoch shuffling):
  - `model(x, zeros, zeros)` per-step call (StructuredNemotronH.forward's
    (tokens, boundary, roles) signature; boundary/roles are always zeros --
    the M4+ line is clean-spine, ADR-0004/0007, so these channels are inert
    regardless of `structured_channels`).
  - prompt-masked cross-entropy (`ignore_index=-100`), AdamW, grad-clip 1.0,
    bf16 autocast on CUDA (`torch.autocast("cuda", dtype=torch.bfloat16)`) --
    exactly what `sft_megatron_batched.py` and `train_full_sft.py` both use.
  - mandatory worst-case preflight before epoch 1 (Track B house rule, see
    `train_full_sft.py::run_preflight_worst_case` and
    `sft_megatron_batched.py::run_preflight_worst_case`): one throwaway
    forward+backward+optimizer step on a synthetic batch already at the
    seq_len cap. The preflight step mutates the model's weights, so -- like
    `train_full_sft.py` -- this script reloads a fresh model from checkpoint
    before any real training begins.

seq_len decision: the model's own positional cap comes from
`mcfg.get("seq_len", 4096)` (`configs/train/nemotron_h_1b.yaml`'s
`train.seq_len: 4096`), passed as `max_sequence_length` to Megatron's
`MambaModel` (`eval_adapter.py::StructuredNemotronH.__init__`) and used with
`position_embedding_type="none"` (Mamba2 doesn't need position embeddings for
in-window lengths; there is no learned/rotary position table to overflow
below the cap). The dry run's own seq_len=512 was a VRAM-measurement choice,
not an architectural limit. Grounded prompts run ~1.0-1.35 kB + target <= 220
B, i.e. up to roughly 1.57 kB = 1.57k bytes = 1.57k tokens (1 byte = 1 token);
`--seq-len` therefore defaults to 1536, comfortably under the 4096 cap and
large enough to avoid truncating almost every grounded example, while still
being small enough that a batch-4 forward+backward at this size scales
sub-linearly-in-VRAM off the batch-4/seq-len-512 proof point (activation
memory scales with batch_size * seq_len; 1536/512 = 3x the proven 23.8 GiB
figure is the rough ceiling to preflight against, not a re-derived certainty
-- this is exactly why the preflight step below is mandatory and this
script does not skip it).

Padding decision: see `collate.py`'s module docstring in full. Short version:
batches are padded to the FIXED shape `seq_len - 1` (not to each batch's own
max length), matching `pad_to_fixed_len=True` in the proven dry run, because
Megatron/TE/Triton appears to cache a compiled kernel per distinct sequence
shape and a run with varying per-batch shapes previously grew VRAM until it
OOM'd. Padding across examples within one batch is safe here specifically
because `StructuredNemotronH.forward` always calls Megatron's `MambaModel`
with `attention_mask=None` and Mamba2's recurrence is independent per batch
row: a padded row's trailing pad bytes can only affect that same row's own
masked (-100) positions, never another row's loss or hidden state. This is
NOT the same situation as generation (see `generate_megatron.py`), where
padding would sit *before* real content the model still has to predict and
would corrupt genuine context instead of only masked filler.

Checkpoint format: `--save-dir/final.pt` is written in the exact blob shape
`eval_adapter.py::load_megatron_blob` expects -- `{step, tokens_seen, cursor,
model, opt, arm, override_pattern}` -- with `opt: None` (this script never
persists optimizer state; SFT restarts fine from a fresh AdamW state, and
`load_megatron_blob` never reads `opt` for any caller in this codebase
anyway, matching its own docstring). `arm` is copied through from the source
checkpoint's own `arm` field (read once, cheaply, via a separate `mmap=True`
`torch.load` that drops everything but that one key immediately -- the same
technique `load_megatron_blob` itself uses to avoid staging the whole 13.5 GB
blob in RAM) rather than invented, so a checkpoint fine-tuned from a
"baseline" blob stays "baseline"-derived for any code that branches on `arm`.
`override_pattern` is read off the live model instance
(`model.pattern`, set by `StructuredNemotronH.__init__` from
`derive_override_pattern`) so it is always internally consistent with the
weights being saved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collate import collate_ids, load_and_shuffle_rows  # noqa: E402


class _nullcontext:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


def _peek_arm(checkpoint: Path) -> str:
    """Read only the `arm` key from the checkpoint blob without staging the
    rest of the (13.5 GB) file -- same `mmap=True` + immediate-drop technique
    `eval_adapter.py::load_megatron_blob` uses for `opt`."""
    import torch

    blob = torch.load(checkpoint, map_location="cpu", weights_only=True, mmap=True)
    arm = str(blob.get("arm", "baseline"))
    del blob
    return arm


def collate_batch_tensors(examples: list[dict[str, str]], seq_len: int, device: str):
    import torch

    xs, ys = collate_ids(examples, seq_len, pad_to_fixed_len=True)
    x = torch.tensor(xs, dtype=torch.long, device=device)
    y = torch.tensor(ys, dtype=torch.long, device=device)
    return x, y


def run_preflight_worst_case(
    model: Any, cfg: dict[str, Any], device: str, seq_len: int, lr: float, batch_size: int, seed: int
) -> dict[str, Any]:
    """One throwaway forward+backward+optimizer step at the seq_len cap,
    batch_size examples, no padding needed (every row is already exactly
    seq_len-1 long) -- the deterministic worst case for activation memory.
    Mutates `model`'s weights; caller must reload fresh before real training.
    """
    import torch
    from torch import nn

    model = model.to(device).train()
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.0)
    vocab = int(cfg["vocab_size"])
    x_len = seq_len - 1
    gen = torch.Generator().manual_seed(seed)
    x = torch.randint(0, vocab, (batch_size, x_len), generator=gen).to(device)
    y = torch.randint(0, vocab, (batch_size, x_len), generator=gen).to(device)
    zeros = torch.zeros_like(x)

    if device == "cuda":
        torch.cuda.reset_peak_memory_stats(device=0)

    autocast_ctx = (
        torch.autocast("cuda", dtype=torch.bfloat16) if device == "cuda" else _nullcontext()
    )
    try:
        with autocast_ctx:
            logits = model(x, zeros, zeros)
            loss = nn.functional.cross_entropy(logits.reshape(-1, vocab), y.reshape(-1))
        opt.zero_grad(set_to_none=True)
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
    except torch.cuda.OutOfMemoryError as e:
        peak = torch.cuda.max_memory_allocated(device=0) / (1024 * 1024) if device == "cuda" else None
        return {"status": "oom", "peak_vram_mib": peak, "error": str(e)}

    peak = torch.cuda.max_memory_allocated(device=0) / (1024 * 1024) if device == "cuda" else None
    return {"status": "ok", "batch_size": batch_size, "seq_len": seq_len, "peak_vram_mib": peak}


def linear_warmup_lr(step: int, warmup: int, base_lr: float) -> float:
    """Linear warmup from 0 to base_lr over `warmup` optimizer steps, then
    constant -- no decay schedule was requested, so none is invented."""
    if warmup <= 0:
        return base_lr
    return base_lr * min(1.0, (step + 1) / warmup)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def train(
    model: Any,
    cfg: dict[str, Any],
    rows: list[dict[str, str]],
    device: str,
    seq_len: int,
    lr: float,
    warmup: int,
    batch_size: int,
    grad_accum: int,
    epochs: int,
    seed: int,
) -> dict[str, Any]:
    import torch
    from torch import nn

    model = model.to(device).train()
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.0)
    vocab = int(cfg["vocab_size"])

    autocast_ctx_factory = (
        (lambda: torch.autocast("cuda", dtype=torch.bfloat16))
        if device == "cuda"
        else (lambda: _nullcontext())
    )

    micro_batches_per_epoch = len(rows) // batch_size
    losses_every_50: list[dict[str, Any]] = []
    examples_seen = 0
    tokens_seen = 0
    opt_step = 0
    micro_step_in_accum = 0
    t0 = time.time()
    opt.zero_grad(set_to_none=True)

    for epoch in range(epochs):
        import random

        order = list(range(len(rows)))
        random.Random(seed + epoch).shuffle(order)

        for mb in range(micro_batches_per_epoch):
            idx = order[mb * batch_size : (mb + 1) * batch_size]
            chunk = [rows[i] for i in idx]
            x, y = collate_batch_tensors(chunk, seq_len, device)
            zeros = torch.zeros_like(x)

            for lg in opt.param_groups:
                lg["lr"] = linear_warmup_lr(opt_step, warmup, lr)

            with autocast_ctx_factory():
                logits = model(x, zeros, zeros)
                loss = nn.functional.cross_entropy(
                    logits.reshape(-1, vocab), y.reshape(-1), ignore_index=-100
                )
            (loss / grad_accum).backward()
            micro_step_in_accum += 1
            examples_seen += len(chunk)
            tokens_seen += int((y != -100).sum().item())

            loss_val = float(loss.detach())
            global_micro_step = epoch * micro_batches_per_epoch + mb
            if global_micro_step % 50 == 0:
                losses_every_50.append(
                    {
                        "micro_step": global_micro_step,
                        "opt_step": opt_step,
                        "epoch": epoch,
                        "loss": loss_val,
                        "lr": opt.param_groups[0]["lr"],
                        "examples_seen": examples_seen,
                        "tokens_seen": tokens_seen,
                        "wall_seconds": time.time() - t0,
                    }
                )

            if micro_step_in_accum == grad_accum or mb == micro_batches_per_epoch - 1:
                nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step()
                opt.zero_grad(set_to_none=True)
                opt_step += 1
                micro_step_in_accum = 0
                if device == "cuda":
                    torch.cuda.synchronize()

    total_seconds = time.time() - t0
    peak_vram_mib = (
        torch.cuda.max_memory_allocated(device=0) / (1024 * 1024) if device == "cuda" else None
    )
    peak_rss_gib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)

    return {
        "losses_every_50_steps": losses_every_50,
        "final_loss": losses_every_50[-1]["loss"] if losses_every_50 else None,
        "n_optimizer_steps": opt_step,
        "n_micro_steps": epochs * micro_batches_per_epoch,
        "examples_seen": examples_seen,
        "tokens_seen": tokens_seen,
        "total_seconds": total_seconds,
        "peak_vram_mib": peak_vram_mib,
        "peak_rss_gib": peak_rss_gib,
    }


def save_checkpoint(
    model: Any, save_path: Path, step: int, tokens_seen: int, cursor: int | None, arm: str
) -> None:
    import torch

    save_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "step": step,
            "tokens_seen": tokens_seen,
            "cursor": cursor,
            "model": model.state_dict(),
            "opt": None,
            "arm": arm,
            "override_pattern": model.pattern,
        },
        save_path,
    )


def verify_reload(save_path: Path, config_path: Path | None, device: str) -> dict[str, Any]:
    """Reload the just-saved checkpoint in-process via the same
    `load_megatron_blob` this trainer used to load the source checkpoint, and
    run one forward pass -- the round-trip proof the task requires."""
    import torch

    from eval_adapter import load_megatron_blob  # noqa: E402  (sys.path set by caller)

    try:
        reloaded, mcfg = load_megatron_blob(save_path, config_path=config_path, device=device)
        vocab = int(mcfg["vocab_size"])
        x = torch.zeros((1, 8), dtype=torch.long, device=device)
        zeros = torch.zeros_like(x)
        with torch.no_grad():
            logits = reloaded(x, zeros, zeros)
        ok = (
            logits.shape[0] == 1
            and logits.shape[1] == 8
            and logits.shape[2] == vocab
            and torch.isfinite(logits).all().item()
        )
        return {"status": "ok" if ok else "forward_pass_shape_or_nan_mismatch", "logits_shape": list(logits.shape)}
    except Exception as e:  # noqa: BLE001 -- reporting, not handling
        return {"status": "reload_failed", "error": f"{type(e).__name__}: {e}"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--data", type=Path, nargs="+", required=True)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--seq-len", type=int, default=1536)
    parser.add_argument("--lr", type=float, default=1e-5)
    parser.add_argument("--warmup", type=int, default=50)
    parser.add_argument("--grad-accum", type=int, default=1)
    parser.add_argument("--vram-fraction", type=float, default=0.85)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--save-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    sys.path.insert(0, str(args.repo_root / "scripts" / "m4"))
    import torch

    from eval_adapter import load_megatron_blob  # noqa: E402

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda":
        torch.cuda.set_per_process_memory_fraction(args.vram_fraction, device=0)

    arm = _peek_arm(args.checkpoint)

    model, cfg = load_megatron_blob(args.checkpoint, config_path=args.config, device=device)

    preflight = run_preflight_worst_case(
        model, cfg, device, args.seq_len, args.lr, args.batch_size, args.seed
    )
    report: dict[str, Any] = {
        "checkpoint": str(args.checkpoint),
        "n_params": sum(p.numel() for p in model.parameters()),
        "seq_len": args.seq_len,
        "batch_size": args.batch_size,
        "grad_accum": args.grad_accum,
        "vram_fraction": args.vram_fraction,
        "preflight": preflight,
    }
    if preflight["status"] != "ok":
        report["status"] = "preflight_failed_oom"
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 1

    # Preflight mutated the model's weights with one throwaway step -- reload
    # fresh from checkpoint before any real epoch touches the data, exactly
    # as train_full_sft.py does for the 370M line.
    del model
    if device == "cuda":
        torch.cuda.empty_cache()
    model, cfg = load_megatron_blob(args.checkpoint, config_path=args.config, device=device)

    rows = load_and_shuffle_rows([str(p) for p in args.data], args.seed)
    if device == "cuda":
        torch.cuda.reset_peak_memory_stats(device=0)

    result = train(
        model, cfg, rows, device, args.seq_len, args.lr, args.warmup,
        args.batch_size, args.grad_accum, args.epochs, args.seed,
    )

    save_path = args.save_dir / "final.pt"
    save_checkpoint(
        model, save_path, step=result["n_optimizer_steps"],
        tokens_seen=result["tokens_seen"], cursor=result["examples_seen"], arm=arm,
    )
    checkpoint_sha256 = sha256_file(save_path)

    verify = verify_reload(save_path, args.config, device)

    report.update(
        {
            "status": "completed" if verify["status"] == "ok" else "completed_verify_failed",
            "n_examples": len(rows),
            "arm": arm,
            "override_pattern": model.pattern,
            "checkpoint_out": str(save_path),
            "checkpoint_sha256": checkpoint_sha256,
            "verify_reload": verify,
            **result,
        }
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if verify["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

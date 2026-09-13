"""Torch-free prompt-masked byte-level collation for the G0 1.13B Megatron-Core
SFT trainer (`train_megatron_sft.py`).

Pulled out of the trainer into its own module, with zero torch import at
module scope, specifically so `tests/test_collate.py` can exercise the exact
masking/padding arithmetic on the host (no torch, no GPU, no container) --
the trainer itself converts these plain-Python `list[int]` rows to tensors.

Byte-level tokenizer contract (see
`/trackB/src/prabhasa/application/tokenizer/bytelevel.py::ByteTokenizer`):
1 byte = 1 token, vocab 256, no special tokens, no EOS -- so `encode` here is
just `str.encode("utf-8")` and there is nothing to add or strip.

Per-example construction and the -100 prompt mask are a direct port of
`/trackB/scripts/g0/sft_megatron_batched.py::build_example` (itself a port of
`scripts/m4/sft_megatron.py` / `scripts/m7/sft_batched.py`): a single space
joins prompt and target (matching how the grounded/mix prompts are already
punctuated), the sequence is next-token shifted (x = seq[:-1], y = seq[1:]),
and every position covering the prompt span in `y` is set to -100
(`nn.CrossEntropyLoss`'s default `ignore_index`) so prompt bytes contribute
context but never gradient.

Padding: batches are padded to a **fixed** target length of `seq_len - 1`
(never to the batch's own max length) by default. This mirrors
`pad_to_fixed_len=True` in the proven dry run
(`prototypes/nyaya_ttt_rsi/runs/g0_expandable/dry_run_1p13b_batch4.json`:
batch 4, seq_len 512, 40/40 steps, peak VRAM 23.8 GiB, no OOM) rather than
`sft_megatron_batched.py`'s own default of padding to each batch's own max
length. `sft_megatron_batched.py`'s docstring records why: Megatron's
Transformer-Engine/Triton kernels appear to cache a compiled variant per
distinct sequence *shape* seen, so a run whose batches are collated to a
different length each time keeps adding cache entries and its VRAM climbs
over the course of a run -- exactly the failure mode the earlier (non-batched,
non-fixed-length) SFT attempt hit. A run where every batch has the exact same
shape stays flat; that is the only configuration this codebase has actually
measured OOM-free at this model size, so the real trainer reproduces it
rather than reintroducing the variable-length behaviour.

Padding safety for Mamba2: the Megatron `MambaModel` forward
(`scripts/m4/eval_adapter.py::StructuredNemotronH.forward`) is always called
with `attention_mask=None` -- there is no masking path at all, fixed-length
or otherwise. That is fine for *training* specifically because Mamba2's
per-token recurrence runs independently per batch row: padding a row's tail
with `pad_id` only ever contributes to that same row's own trailing (masked,
-100) positions and can never leak into another row's hidden state or loss.
This is different from *generation*, where left-padding (or any padding
before the content whose next byte you still need to predict) would feed
fake zero-byte context ahead of real tokens and corrupt the recurrent state
for that row -- which is why `generate_megatron.py` (like `scripts/m7/
generate.py` and `model_io.py::generate`) groups prompts by exact length
instead of padding them at all. Trailing-only padding used here for training
does not have that problem.
"""

from __future__ import annotations


def encode_utf8(text: str) -> list[int]:
    """1 byte = 1 token, matching `ByteTokenizer.encode`."""
    return list(text.encode("utf-8"))


def build_example(prompt: str, target: str, seq_len: int) -> tuple[list[int], list[int]]:
    """(x, y) for one example: y is -100 over the prompt span.

    Returns ([], []) if the truncated sequence has fewer than 2 bytes (no
    valid next-token pair to train on) -- callers must drop such examples,
    exactly as `sft_megatron_batched.py::collate_batch` does.
    """
    prompt_ids = encode_utf8(prompt + " ")
    target_ids = encode_utf8(target)
    seq = (prompt_ids + target_ids)[:seq_len]
    if len(seq) < 2:
        return [], []
    x = seq[:-1]
    y = list(seq[1:])
    mask_until = max(0, len(prompt_ids) - 1)
    for i in range(min(mask_until, len(y))):
        y[i] = -100
    return x, y


def collate_ids(
    examples: list[dict[str, str]],
    seq_len: int,
    pad_id: int = 0,
    pad_to_fixed_len: bool = True,
) -> tuple[list[list[int]], list[list[int]]]:
    """Build and pad one batch's (x, y) rows.

    `pad_to_fixed_len=True` (the default -- see module docstring) pads every
    row to the fixed length `seq_len - 1` regardless of what this particular
    batch's own examples need; `False` pads to the batch's own max length
    (matching `sft_megatron_batched.py`'s own default, kept here only for
    parity/testing, not used by the real trainer's default CLI).

    Raises ValueError if every example in the batch produced an empty (x, y)
    pair (e.g. every prompt+target truncated to fewer than 2 bytes).
    """
    pairs = [build_example(ex["prompt"], ex["target"], seq_len) for ex in examples]
    pairs = [(x, y) for x, y in pairs if x]
    if not pairs:
        raise ValueError("collate_ids: no example in this batch produced a usable sequence")

    target_len = seq_len - 1 if pad_to_fixed_len else max(len(x) for x, _ in pairs)
    xs: list[list[int]] = []
    ys: list[list[int]] = []
    for x, y in pairs:
        x, y = x[:target_len], y[:target_len]
        pad_n = target_len - len(x)
        xs.append(x + [pad_id] * pad_n)
        ys.append(y + [-100] * pad_n)
    return xs, ys


def load_and_shuffle_rows(
    paths: list[str], seed: int
) -> list[dict[str, str]]:
    """Read one or more jsonl files (each row must have "prompt"/"target"),
    concatenate in the given path order, then shuffle once with `seed`.

    Pure Python / stdlib only (json + random) so this stays importable
    without torch; the trainer calls this directly.
    """
    import json
    import random

    rows: list[dict[str, str]] = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
    random.Random(seed).shuffle(rows)
    return rows

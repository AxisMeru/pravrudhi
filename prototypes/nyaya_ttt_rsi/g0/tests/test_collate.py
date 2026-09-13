"""CPU-only tests for the pure-Python collation/masking logic in
`g0/collate.py`. No torch import anywhere in this file or in `collate.py`
itself, so this runs on the host (`uv run python -m pytest`) where torch is
not installed -- exercises exactly the arithmetic `train_megatron_sft.py`
depends on without needing a model, a GPU, or the `/trackB` mount.
"""

from __future__ import annotations

import json

import pytest

from prototypes.nyaya_ttt_rsi.g0.collate import (
    build_example,
    collate_ids,
    load_and_shuffle_rows,
)


def test_build_example_masks_prompt_span():
    prompt = "ab"  # -> b"ab " (3 bytes, "+ ' '" convention)
    target = "cd"  # -> b"cd" (2 bytes)
    x, y = build_example(prompt, target, seq_len=100)
    # seq = "ab cd" (5 bytes): x = seq[:-1] (4 bytes), y = seq[1:] (4 bytes)
    seq = list(b"ab cd")
    assert x == seq[:-1]
    assert y[: len(prompt) + 1 - 1] == [-100] * (len(prompt) + 1 - 1)
    # unmasked suffix of y must equal the corresponding suffix of the real
    # shifted sequence (the target bytes themselves, unmasked)
    real_y = seq[1:]
    mask_until = len((prompt + " ").encode()) - 1  # len(prompt_ids) - 1, prompt_ids = "ab "
    for i in range(len(y)):
        if i < mask_until:
            assert y[i] == -100
        else:
            assert y[i] == real_y[i]


def test_build_example_truncates_to_seq_len():
    prompt = "hello"
    target = "world!!!"
    seq_len = 5
    x, y = build_example(prompt, target, seq_len)
    assert len(x) == seq_len - 1
    assert len(y) == seq_len - 1


def test_build_example_too_short_returns_empty():
    x, y = build_example("", "", seq_len=100)
    assert x == []
    assert y == []


def test_build_example_prompt_only_target_empty_masks_everything():
    # target empty: entire y should be masked (nothing left to predict from
    # the target span) except any trailing position past the prompt's own
    # implicit last unmasked slot -- with target empty, mask_until == len(y)
    # for a long enough prompt, so this should just come back with no
    # unmasked positions at all when the prompt is short enough to fully
    # fit and leave nothing beyond it.
    prompt = "ab"
    x, y = build_example(prompt, "", seq_len=100)
    # seq = "ab " (3 bytes) -> x,y each length 2; mask_until = len(prompt_ids)-1 = 2
    assert all(v == -100 for v in y)


def test_collate_ids_fixed_len_pads_every_row_to_seq_len_minus_1():
    examples = [
        {"prompt": "a", "target": "bc"},
        {"prompt": "abcdefgh", "target": "ij"},
    ]
    seq_len = 12
    xs, ys = collate_ids(examples, seq_len, pad_id=0, pad_to_fixed_len=True)
    assert len(xs) == 2
    for x, y in zip(xs, ys):
        assert len(x) == seq_len - 1
        assert len(y) == seq_len - 1


def test_collate_ids_fixed_len_pad_is_neutral_on_x_and_masked_on_y():
    examples = [{"prompt": "a", "target": "b"}]  # short row, needs padding
    seq_len = 20
    xs, ys = collate_ids(examples, seq_len, pad_id=0, pad_to_fixed_len=True)
    x, y = xs[0], ys[0]
    real_x, real_y = build_example("a", "b", seq_len)
    pad_n = len(x) - len(real_x)
    assert pad_n > 0
    assert x == real_x + [0] * pad_n
    assert y == real_y + [-100] * pad_n


def test_collate_ids_variable_len_pads_to_batch_max_not_fixed():
    examples = [
        {"prompt": "a", "target": "b"},          # short
        {"prompt": "abcdefghij", "target": "kl"},  # longer
    ]
    seq_len = 100
    xs, ys = collate_ids(examples, seq_len, pad_id=0, pad_to_fixed_len=False)
    lengths = {len(x) for x in xs}
    assert len(lengths) == 1  # padded to a common length
    batch_max = max(len(build_example(e["prompt"], e["target"], seq_len)[0]) for e in examples)
    assert lengths == {batch_max}
    assert batch_max < seq_len - 1  # confirms this is NOT the fixed-length path


def test_collate_ids_drops_unusable_rows_and_raises_if_all_unusable():
    with pytest.raises(ValueError):
        collate_ids([{"prompt": "", "target": ""}], seq_len=100)


def test_collate_ids_padding_never_appears_in_x_where_y_is_unmasked():
    """The property `train_megatron_sft.py` actually relies on: every
    position where y != -100 must come from real data, never from padding,
    regardless of pad_to_fixed_len."""
    examples = [{"prompt": "short", "target": "x"}]
    seq_len = 50
    for fixed in (True, False):
        xs, ys = collate_ids(examples, seq_len, pad_to_fixed_len=fixed)
        real_x, real_y = build_example("short", "x", seq_len)
        x, y = xs[0], ys[0]
        for i in range(len(real_y)):
            if real_y[i] != -100:
                assert y[i] == real_y[i]
                assert x[i] == real_x[i]


def test_load_and_shuffle_rows_concatenates_and_shuffles_deterministically(tmp_path):
    p1 = tmp_path / "a.jsonl"
    p2 = tmp_path / "b.jsonl"
    p1.write_text(
        "\n".join(json.dumps({"prompt": f"p{i}", "target": f"t{i}"}) for i in range(3)) + "\n",
        encoding="utf-8",
    )
    p2.write_text(
        "\n".join(json.dumps({"prompt": f"q{i}", "target": f"u{i}"}) for i in range(2)) + "\n",
        encoding="utf-8",
    )
    rows_a = load_and_shuffle_rows([str(p1), str(p2)], seed=7)
    rows_b = load_and_shuffle_rows([str(p1), str(p2)], seed=7)
    assert len(rows_a) == 5
    assert rows_a == rows_b  # same seed -> same shuffle
    assert {r["prompt"] for r in rows_a} == {"p0", "p1", "p2", "q0", "q1"}


def test_load_and_shuffle_rows_skips_blank_lines(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_text('\n{"prompt": "p", "target": "t"}\n\n', encoding="utf-8")
    rows = load_and_shuffle_rows([str(p)], seed=0)
    assert rows == [{"prompt": "p", "target": "t"}]

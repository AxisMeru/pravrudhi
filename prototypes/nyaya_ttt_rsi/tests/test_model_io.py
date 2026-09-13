"""GPU smoke test for model_io.py -- run inside the ttt-lab container:

    docker exec ttt-lab bash -lc "cd /lab && python3 -m pytest -q \\
        prototypes/nyaya_ttt_rsi/tests/test_model_io.py"

Loads the real 370M law-tuned checkpoint, so it needs the GPU idle and the
`/trackB-local` mount present. Not meant to run on the host (no torch there).
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from prototypes.nyaya_ttt_rsi import model_io  # noqa: E402

CHECKPOINT = Path("/trackB-local/m7/m7_retry_checkpoint.pt")

pytestmark = pytest.mark.skipif(
    not (torch.cuda.is_available() and CHECKPOINT.exists()),
    reason="requires a CUDA GPU and the mounted /trackB-local checkpoint (run inside ttt-lab)",
)


@pytest.fixture(scope="module")
def loaded():
    return model_io.load_model(CHECKPOINT, device="cuda:0")


def test_generate_two_prompts(loaded):
    model, tok = loaded
    prompts = [
        "Under the Contract Act, what section governs void agreements?",
        "State the citation for the definition of consideration.",
    ]
    outputs = model_io.generate(model, tok, prompts, max_new_tokens=16)
    assert len(outputs) == 2
    assert all(isinstance(o, str) for o in outputs)
    assert all(len(o) > 0 for o in outputs)


def test_sequence_nll_is_finite_float(loaded):
    model, tok = loaded
    nll = model_io.sequence_nll(
        model, tok, "The Contract Act defines", "consideration in section 2(d)."
    )
    assert isinstance(nll, float)
    assert math.isfinite(nll)
    assert nll >= 0.0


def test_sequence_nll_empty_prompt(loaded):
    model, tok = loaded
    nll = model_io.sequence_nll(model, tok, "", "consideration in section 2(d).")
    assert isinstance(nll, float)
    assert math.isfinite(nll)


def test_linear_module_names_nonempty_and_covers_mixer_kinds(loaded):
    model, _tok = loaded
    names = model_io.linear_module_names(model)
    assert len(names) > 0
    # Mamba2 projections present.
    assert any(n.endswith("mixer.in_proj") for n in names)
    assert any(n.endswith("mixer.out_proj") for n in names)
    # Attention (fused qkv, attn_impl="sdpa") present.
    assert any(n.endswith("mixer.qkv") for n in names)
    # MLP linears present.
    assert any(".mlp." in n for n in names)

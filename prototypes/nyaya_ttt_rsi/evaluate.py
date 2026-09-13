"""Conditions B/C/D evaluation over the law-QA held-out set, plus paired comparison.

Condition B: grounded prompt (retrieval.build_grounded_prompt), frozen model.
Condition C: B + ephemeral test-time-training (ttt.adapt) on the retrieved passage
text only, reset (ttt.restore) after every query; a regression-probe gate
(gate.decide) decides whether the post-adaptation answer is kept or the
pre-adaptation answer is served instead.
Condition D: identical machinery to C, except the LoRA modules start each query
from a *persistent* snapshot produced by loop.py's gated consolidation, rather
than from the zero-initialized state -- see loop.py.

Model-and-torch-dependent calls (generation, NLL scoring, LoRA adapt/snapshot/
restore, Track B's score_law_qa) are wired through the small `_generate` /
`_sequence_nll` / `_adapt` / `_snapshot` / `_restore` / `_merged_delta_norm` /
`_score_law_qa` / `_citation_correct` / `_is_abstention_answer` module-level
functions below, each of which does its heavy import (torch, or Track B's own
`/trackB` path) lazily, inside the function body. This mirrors ttt.py's own
lazy-import-of-model_io pattern and, more importantly, means this module can be
imported and unit-tested on the host (no torch, no /trackB mount) by
monkeypatching those names -- see tests/test_evaluate.py.

Critical model constraint (370M byte-level checkpoint, trained at seq_len 512):
grounded prompts stay short -- k=3 passages, each passage truncated to
<= MAX_PASSAGE_BYTES at a sentence/space boundary, total prompt logged and
checked against MAX_PROMPT_BYTES. The ephemeral TTT text (concatenated
retrieved passages) is separately capped at ADAPT_MAX_BYTES so it fits in one
forward pass at the model's trained sequence length.
"""

from __future__ import annotations

import dataclasses
import json
import math
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable

from . import retrieval as retrieval_mod
from . import stats

TRACKB_ROOT = Path("/trackB")
DEFAULT_CHECKPOINT = Path("/trackB-local/m7/m7_retry_checkpoint.pt")
DEFAULT_HELDOUT = TRACKB_ROOT / "data" / "eval" / "law_qa_heldout_v3.jsonl"
DEFAULT_TRAIN = TRACKB_ROOT / "data" / "sft" / "law_v3_train.jsonl"
DEFAULT_PROBE_GENERAL = TRACKB_ROOT / "data" / "sft" / "instruct_v1_val.jsonl"

# LoRA targets: attention blocks' q/o projections ONLY -- see the "mamba mixer
# cannot be LoRA-wrapped" finding below. `linear_module_names()` reports both
# 21 Mamba2 blocks' `blocks.<i>.mixer.{in_proj,out_proj}` and 3 attention
# blocks' `blocks.<i>.mixer.{qkv,out_proj}`; the module contract asked for
# both, but wrapping a Mamba2 mixer's in_proj/out_proj in a LoRALinear crashes
# at inference: `mamba_ssm`'s fused CUDA path reads `self.out_proj.weight`
# directly (`mamba2.py`'s `forward`, `outproj_weight=self.out_proj.weight`)
# rather than calling `self.out_proj(x)`, so a LoRALinear wrapper (no
# `.weight` attribute) raises `AttributeError: 'LoRALinear' object has no
# attribute 'weight'`. `CausalSelfAttention.forward` (train_130m.py) calls
# `self.qkv(x)` / `self.out_proj(x)` as ordinary module calls, so only the 3
# attention blocks' q/o projections are actually LoRA-injectable with the
# `ttt.LoRALinear` wrapper as implemented. Filed as F6 in
# docs/research-spikes/2026-09-13-ttt-rsi-track-b/FIXES-FOR-MAIN-SESSIONS.md.
# The regex is built dynamically from `model_io.attention_block_indices`
# (see `attention_lora_target_regex`) rather than hardcoded, since which
# block indices use attention depends on `attention_every`.
LORA_TARGET_REGEX_TEMPLATE = r"blocks\.({idx})\.mixer\.(qkv|out_proj)$"
LORA_R = 8
LORA_ALPHA = 16

# 2026-09-13 pivot, round 3 (compact context, F15/F16's fixes): the
# original k=3/350-byte/1400-byte-prompt budget produced prompts 2.5x the
# model's trained 512-byte context; law_lookup candidates were also full
# passage bodies of wildly different lengths (F15's length-selection bias).
# A first attempt at a compact context (k=5, 120-byte bodies, uncapped
# titles, 900-byte budget) turned out to be arithmetically impossible --
# titles average 54 bytes (real section headings, not short tags) and
# k=5 uncapped-title blocks alone average ~945 bytes before the
# instruction/question/answer overhead (F16). PROMPT_CONFIG below is the
# corrected, measured-to-fit budget: a <=40-byte instruction, k=4
# (recall@4 with titles ~0.77), body capped at 60 bytes, title capped at 90
# bytes (both cut at a space, never mid-word), 950-byte total prompt
# budget, with a last-resort "drop the lowest-ranked passage and rebuild"
# fallback (see `render_prompt`) if a rare item still overflows.
#
# `PROMPT_CONFIG` is the SINGLE shared source of truth for both training
# data generation (`grounded_data.py`) and held-out evaluation
# (`evaluate.py`) -- both go through `render_prompt`/`retrieve_and_build_prompt`
# below, which always reference this one object (never a copy), so the two
# paths cannot silently drift apart. `tests/test_evaluate.py` asserts
# `grounded_data.PROMPT_CONFIG is evaluate.PROMPT_CONFIG` (identity, not just
# equality) as a standing invariant.
INSTRUCTION = "Cite the correct provision below."  # 33 bytes


@dataclasses.dataclass(frozen=True)
class PromptConfig:
    k: int
    body_max_bytes: int
    title_max_bytes: int
    max_prompt_bytes: int
    instruction: str


PROMPT_CONFIG = PromptConfig(k=4, body_max_bytes=60, title_max_bytes=90,
                              max_prompt_bytes=950, instruction=INSTRUCTION)

K_PASSAGES = PROMPT_CONFIG.k
MAX_PASSAGE_BYTES = PROMPT_CONFIG.body_max_bytes
MAX_PROMPT_BYTES = PROMPT_CONFIG.max_prompt_bytes
ADAPT_MAX_BYTES = 480  # concatenated retrieved-passage text fed to ttt.adapt
MAX_NEW_TOKENS_GROUNDED = 96
STOP_STRINGS = ["\n\n", "\nQuestion:"]

DEFAULT_TTT_CFG = {"steps": 4, "lr": 1e-3, "threshold": 0.15}

_ABSTAIN_KIND = "law_abstain"

# ---------------------------------------------------------------------------
# Lazy backends -- imported only when actually invoked, and the sole hook
# tests use to stub out torch/model/Track-B-script dependencies.
# ---------------------------------------------------------------------------


def _generate(model, tok, prompts, max_new_tokens, stop):
    from . import model_io

    return model_io.generate(model, tok, prompts, max_new_tokens=max_new_tokens, stop=stop)


def _sequence_nll(model, tok, prompt, continuation):
    from . import model_io

    return model_io.sequence_nll(model, tok, prompt, continuation)


def _tensor_nll_loss(model, tok, prompt: str, continuation: str):
    """Same math as `model_io.sequence_nll` (mean NLL over `continuation`
    bytes only), but returns a tensor still attached to the autograd graph.

    Needed because `ttt.adapt`'s default `loss_fn` (when none is passed)
    calls `model_io.sequence_nll(...)`, which returns a plain detached
    `float` (`.item()`), then re-wraps it with `torch.as_tensor` -- a leaf
    tensor with no `grad_fn`, so `loss.backward()` cannot flow gradients
    back into the LoRA parameters. Its `import model_io` (unqualified, not
    `from . import model_io`) also fails to resolve inside this package, so
    it silently falls through to a second, also-broken fallback that calls
    `model(input_ids=..., labels=...)` -- a HuggingFace-style signature this
    model's `forward(tokens, boundary, roles)` does not have. Filed as F7 in
    docs/research-spikes/2026-09-13-ttt-rsi-track-b/FIXES-FOR-MAIN-SESSIONS.md.
    `ttt.adapt` and `loop.consolidate` are therefore always called with this
    function as an explicit `loss_fn`, never the library default.
    """
    import torch

    device = next(model.parameters()).device
    prompt_ids = tok.encode(prompt)
    cont_ids = tok.encode(continuation)
    all_ids = prompt_ids + cont_ids
    if len(all_ids) < 2:
        return torch.zeros((), device=device, requires_grad=True)

    n_prompt = len(prompt_ids)
    tokens = torch.tensor([all_ids], dtype=torch.long, device=device)
    zeros = torch.zeros_like(tokens)
    with torch.autocast(device.type, dtype=torch.bfloat16, enabled=(device.type == "cuda")):
        logits = model(tokens, zeros, zeros)[0]

    targets = tokens[0, 1:]
    preds = logits[:-1]
    j_start = max(n_prompt - 1, 0)
    if j_start >= targets.shape[0]:
        return torch.zeros((), device=device, requires_grad=True)

    sel_preds = preds[j_start:].float()
    sel_targets = targets[j_start:]
    return torch.nn.functional.cross_entropy(sel_preds, sel_targets, reduction="mean")


def _adapt(model, tok, text, loras, steps, lr):
    from . import ttt as ttt_mod

    loss_fn = lambda m, t: _tensor_nll_loss(m, tok, "", t)
    return ttt_mod.adapt(model, tok, text, loras, steps=steps, lr=lr, loss_fn=loss_fn)


def _snapshot(loras):
    from . import ttt as ttt_mod

    return ttt_mod.snapshot(loras)


def _restore(loras, snap):
    from . import ttt as ttt_mod

    ttt_mod.restore(loras, snap)


def _merged_delta_norm(loras):
    from . import ttt as ttt_mod

    return ttt_mod.merged_delta_norm(loras)


def attention_lora_target_regex(model) -> str:
    from . import model_io

    indices = model_io.attention_block_indices(model)
    return LORA_TARGET_REGEX_TEMPLATE.format(idx="|".join(str(i) for i in indices))


def persistent_lora_target_regex(model) -> str:
    """Targets for the PERSISTENT consolidation LoRA (loop.py Phase 2/4):
    attention q/o projections (safe, see F8) PLUS every block's MLP
    (`mlp.1`/`mlp.3` -- ordinary `nn.Sequential`-called `nn.Linear`s, never
    routed through `mamba_ssm`'s fused kernel, so LoRA-wrapping them is
    safe). Mamba2 mixer `in_proj`/`out_proj` stay excluded for every LoRA
    injected by this module -- see F8 in FIXES-FOR-MAIN-SESSIONS.md."""
    from . import model_io

    indices = model_io.attention_block_indices(model)
    attn_part = LORA_TARGET_REGEX_TEMPLATE.format(idx="|".join(str(i) for i in indices))
    return rf"(?:{attn_part}|blocks\.\d+\.mlp\.[13]$)"


def _inject_lora(model, target_regex=None, r=LORA_R, alpha=LORA_ALPHA):
    from . import ttt as ttt_mod

    if target_regex is None:
        target_regex = attention_lora_target_regex(model)
    return ttt_mod.inject_lora(model, target_regex, r=r, alpha=alpha)


def _save_lora_state(loras, path) -> None:
    import torch

    torch.save(_snapshot(loras), path)


def _load_lora_state(loras, path) -> None:
    import torch

    snap = torch.load(path, map_location="cpu", weights_only=True)
    _restore(loras, snap)


def _score_law_qa_module():
    sys.path.insert(0, str(TRACKB_ROOT / "scripts" / "eval"))
    import score_law_qa  # type: ignore[import-not-found]

    return score_law_qa


def _score_law_qa(heldout_path, answers_path) -> dict:
    return _score_law_qa_module().score(Path(heldout_path), Path(answers_path))


def _citation_correct(answer_text: str, act: str, section: str) -> bool:
    return _score_law_qa_module()._citation_correct(answer_text, act, section)


def _is_abstention_answer(answer_text: str) -> bool:
    return _score_law_qa_module()._is_abstention_answer(answer_text)


# ---------------------------------------------------------------------------
# Pure-python helpers (safe to unit test without torch).
# ---------------------------------------------------------------------------


def load_jsonl(path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def write_jsonl(path, rows: list[dict]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def truncate_passage_text(text: str, max_bytes: int = MAX_PASSAGE_BYTES) -> str:
    """Truncate `text` to at most `max_bytes` UTF-8 bytes, cutting at the last
    sentence or word boundary within the window rather than mid-word/mid-byte."""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    window = raw[:max_bytes]
    for sep, keep in ((b". ", 2), (b"; ", 2), (b", ", 2), (b" ", 1)):
        idx = window.rfind(sep)
        if idx > max_bytes * 0.4:
            window = window[: idx + keep]
            break
    return window.decode("utf-8", errors="ignore").rstrip()


def truncate_passages(passages: list, max_bytes: int = MAX_PASSAGE_BYTES) -> list:
    return [dataclasses.replace(p, text=truncate_passage_text(p.text, max_bytes)) for p in passages]


def truncate_at_space(text: str, max_bytes: int) -> str:
    """Truncate `text` to at most `max_bytes` UTF-8 bytes, cutting at the
    last space within the window (never mid-word) -- no sentence-boundary
    preference, per the compact-context spec's "cut at a space" wording.
    Used for both passage bodies and titles under `PROMPT_CONFIG`."""
    if not text:
        return text
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    window = raw[:max_bytes]
    idx = window.rfind(b" ")
    if idx > 0:
        window = window[:idx]
    return window.decode("utf-8", errors="ignore").rstrip()


def apply_prompt_config(passages: list, config: PromptConfig = PROMPT_CONFIG) -> list:
    """Truncates every passage's body and title to `config`'s byte budgets
    (space-cut, never mid-word) -- the shared truncation step both
    `render_prompt` (eval) and `grounded_data.build_example`/
    `build_real_abstain_example` (training) go through."""
    return [
        dataclasses.replace(
            p,
            text=truncate_at_space(p.text, config.body_max_bytes),
            title=truncate_at_space(p.title, config.title_max_bytes) if p.title else p.title,
        )
        for p in passages
    ]


def render_prompt(question: str, passages: list, config: PromptConfig = PROMPT_CONFIG) -> tuple[str, list, int, bool]:
    """Builds a compact-context prompt from an already-selected passage list
    (used directly by `grounded_data.py`, which selects/forces passages
    itself) or via `retrieve_and_build_prompt` (which retrieves first).
    Truncates bodies/titles per `config`, then -- if the built prompt still
    exceeds `config.max_prompt_bytes` (should be rare; F16) -- drops the
    LOWEST-RANKED (last) passage and rebuilds, repeating until it fits or
    only one passage remains. Returns (prompt, passages_used, prompt_bytes,
    dropped_a_passage)."""
    trunc = apply_prompt_config(passages, config)
    prompt = retrieval_mod.build_grounded_prompt(question, trunc, instruction=config.instruction)
    dropped = False
    while len(prompt.encode("utf-8")) > config.max_prompt_bytes and len(trunc) > 1:
        trunc = trunc[:-1]
        prompt = retrieval_mod.build_grounded_prompt(question, trunc, instruction=config.instruction)
        dropped = True
    pb = assert_prompt_budget(prompt, config.max_prompt_bytes)
    return prompt, trunc, pb, dropped


def adapt_text_from_passages(passages: list, max_bytes: int = ADAPT_MAX_BYTES) -> str:
    """Self-supervised TTT text: the retrieved passages' text ONLY -- never the
    question or any answer -- concatenated and hard-capped to fit the model's
    trained sequence length in one forward pass."""
    text = "\n\n".join(p.text for p in passages)
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    return raw[:max_bytes].decode("utf-8", errors="ignore")


def assert_prompt_budget(prompt: str, max_bytes: int = MAX_PROMPT_BYTES) -> int:
    """Hard-asserts a built prompt fits the compact-context budget (F15/F16
    -- the model was trained at a 512-byte sequence length). `render_prompt`
    already drops passages to try to fit before calling this, so a failure
    here means even a single passage's (already-truncated) body+title plus
    the instruction/question overflowed -- a genuine anomaly worth
    investigating, not something to silently truncate further. Returns the
    byte length so callers can log stats without a second encode."""
    pb = len(prompt.encode("utf-8"))
    assert pb <= max_bytes, (
        f"prompt exceeds the {max_bytes}-byte compact-context budget ({pb} bytes) "
        "even after dropping passages to fit -- investigate rather than truncating further"
    )
    return pb


def retrieve_and_build_prompt(store, question: str, k: int = K_PASSAGES, config: PromptConfig = PROMPT_CONFIG):
    """Returns (prompt, truncated_passages, prompt_bytes, dropped_a_passage)
    -- see `render_prompt` for the truncation/drop-on-overflow behavior."""
    passages = store.search(question, k)
    return render_prompt(question, passages, config)


# ---------------------------------------------------------------------------
# Scoring mode (2026-09-13 pivot, round 2): greedy free generation hit a
# "greedy-decoding trap" -- see F12 in FIXES-FOR-MAIN-SESSIONS.md -- where a
# single fixed, short, low-entropy abstain string wins byte-1 argmax over
# diverse, longer citation continuations even when a citation continuation
# has higher TOTAL sequence likelihood. Scoring mode sidesteps free
# generation entirely: build one candidate answer per shown passage (plus
# the abstain string), score each candidate's mean per-byte NLL with
# `model_io.sequence_nll` (never argmax-per-byte), and pick the argmin. This
# is grounded BY CONSTRUCTION -- every candidate is either a shown passage's
# citation or the abstain phrase -- so `grounded_rate` is always 1.0 in this
# mode and the interesting metrics become which candidate was picked.
# ---------------------------------------------------------------------------

CANONICAL_CITATION_FORMAT = "{section} ({act})."


def build_candidates(passages: list, kind: str, include_abstain: bool = True) -> list[tuple[str, Any]]:
    """One candidate per shown passage (source = the Passage), plus the
    abstain phrase last (source = None) when `include_abstain`. Every kind
    uses the SAME canonical citation format matching what `score_law_qa.py`
    substring-matches (normalized act + normalized section), which is also
    the format `grounded_data.py`'s round-1e training targets use.

    2026-09-13 pivot, round 3 (F15 -- see FIXES-FOR-MAIN-SESSIONS.md):
    `law_lookup` candidates used to be full passage bodies of wildly
    different lengths, which made length-sensitive selection statistics
    (TOTAL NLL) pick the wrong, shorter passage even when the model was
    genuinely more confident (lower mean NLL) in the longer, correct one.
    Uniform candidates make selection a pure passage-choice task, near
    length-invariant across every kind; the harness composes the actual
    emitted answer text from whichever passage is selected (see
    `compose_answer`) -- `law_lookup`'s emitted answer is still the full
    body + citation, just no longer part of the SCORED candidate. `kind` is
    accepted but currently unused for candidate text (kept for interface
    stability and because a future kind-specific candidate scheme may need
    it again).

    2026-09-13 pivot, round 2 (F14): `include_abstain=False` is used for
    citation-only training data and for calibrated abstention
    (`run_condition_calibrated`): letting a single, byte-identical fixed
    abstain STRING compete inside the likelihood-argmin let the model's
    overfit-by-repetition preference for that string decide abstention,
    rather than an actual calibrated decision rule over candidate scores."""
    candidates: list[tuple[str, Any]] = [
        (CANONICAL_CITATION_FORMAT.format(section=p.section, act=p.act), p) for p in passages
    ]
    if include_abstain:
        candidates.append((retrieval_mod.ABSTAIN_PHRASE, None))
    return candidates


def compose_answer(kind: str, passage, store) -> str:
    """The harness composes the final emitted answer text from whichever
    passage was SELECTED (scoring only ever compares canonical citation
    strings now -- see `build_candidates`): citation kinds emit the
    canonical citation string itself; `law_cite_to_title` emits the
    passage's own title; `law_lookup` emits the FULL (untruncated) body --
    looked up fresh from `store`, since the in-context copy may have had its
    body truncated to fit the compact prompt budget -- plus a trailing
    citation line, so `score_law_qa`'s prefix-similarity and substring
    checks see real, complete text."""
    if kind == "law_lookup":
        full = store.lookup(passage.act, passage.section) or passage
        return f"{full.text}\n\nCitation: {full.act}, {full.section}."
    if kind == "law_cite_to_title" and passage.title:
        return passage.title
    return CANONICAL_CITATION_FORMAT.format(section=passage.section, act=passage.act)


def score_candidates(model, tok, prompt: str, candidates: list[tuple[str, Any]], by: str = "total") -> dict:
    """Scores every candidate by TOTAL NLL (sum over its own continuation
    bytes -- the 2026-09-13 pivot's chosen selection statistic, to stop a
    short fixed string from winning purely on being short) or MEAN per-byte
    NLL (`by="mean"`), and reports BOTH regardless of which one selects.

    Caution (found empirically, round-1d): TOTAL is itself length-biased in
    the other direction once the abstain string is removed from the
    candidate set -- among `law_lookup` candidates (whose bodies vary
    wildly in length), a SHORTER wrong passage can beat a LONGER correct one
    on total NLL even when the correct one has the better (lower) mean
    per-byte NLL. Neither statistic is bias-free; `by` lets a caller pick,
    and both are always reported so the trade-off is inspectable rather than
    silently baked in."""
    mean_nlls = [_sequence_nll(model, tok, prompt, text) for text, _source in candidates]
    total_nlls = [mean * len(text.encode("utf-8")) for mean, (text, _source) in zip(mean_nlls, candidates)]
    key = total_nlls if by == "total" else mean_nlls
    order = sorted(range(len(key)), key=lambda i: key[i])
    best_idx = order[0]
    margin = (key[order[1]] - key[order[0]]) if len(order) > 1 else float("inf")
    return {"best_idx": best_idx, "mean_nlls": mean_nlls, "total_nlls": total_nlls,
            "nlls": mean_nlls, "margin": margin, "by": by}


_QUESTION_RE = re.compile(r"Question:\s*(.*?)\nAnswer:\s*\Z", re.S)
# Bracket content is "{act}, {section}" -- act names can themselves contain a
# comma (e.g. "Indian Evidence Act, 1872"), so split on the LAST ", " inside
# the brackets, never the first (a section string never contains a comma).
_PASSAGE_BLOCK_RE = re.compile(r"^\[(.+)\] (.*)\Z", re.S)


def parse_question_from_prompt(prompt: str) -> str:
    """Recovers the natural-language question from a `build_grounded_prompt`
    output -- used by the in-sample sanity check, which only has the
    already-built training prompt on disk, not the original question
    string."""
    match = _QUESTION_RE.search(prompt)
    return match.group(1) if match else ""


def parse_passages_from_prompt(prompt: str) -> list:
    """Recovers the exact (already-truncated) passages a `build_grounded_prompt`
    output was built from, by re-parsing its own `"[act, section] text"`
    blocks -- exact by construction, unlike re-retrieving from the store
    (which could return a different top-k than what training actually saw,
    e.g. for a forced-gold-inclusion training example)."""
    blocks = prompt.split("\n\n")
    passages = []
    for block in blocks[1:-1]:  # skip the instruction line and the Question/Answer tail
        match = _PASSAGE_BLOCK_RE.match(block)
        if not match:
            continue
        bracket_content, text = match.groups()
        if ", " not in bracket_content:
            continue
        act, section = bracket_content.rsplit(", ", 1)
        passages.append(retrieval_mod.Passage(act, section, text, f"{act}, {section}", None))
    return passages


# ---------------------------------------------------------------------------
# Answer-record construction (used by both B and C/D paths).
# ---------------------------------------------------------------------------


def build_answer_record(qid: str, text: str, prompt_bytes: int, passages: list, gate_info: dict | None = None) -> dict:
    answer = retrieval_mod.parse_answer(text)
    is_grounded = retrieval_mod.grounded(answer, passages)
    record = {
        "id": qid,
        "answer": text,
        "prompt_bytes": prompt_bytes,
        "grounded": is_grounded,
        "cited": bool(answer.citations),
        "abstained": answer.abstained,
    }
    if gate_info is not None:
        record["gate"] = gate_info
    return record


# ---------------------------------------------------------------------------
# run_condition
# ---------------------------------------------------------------------------


def run_condition(
    cond: str,
    model,
    tok,
    store,
    items: list[dict],
    out_dir,
    *,
    loras: list | None = None,
    ttt_cfg: dict | None = None,
    probe=None,
    ledger=None,
    k: int = K_PASSAGES,
    max_new_tokens: int = MAX_NEW_TOKENS_GROUNDED,
    stop: list[str] | None = None,
    heldout_path=DEFAULT_HELDOUT,
    label: str | None = None,
) -> dict:
    if cond not in ("B", "C", "D"):
        raise ValueError(f"unknown condition: {cond!r}")
    if cond in ("C", "D") and (loras is None or probe is None):
        raise ValueError(f"condition {cond} requires loras and probe")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stop = STOP_STRINGS if stop is None else stop
    ttt_cfg = DEFAULT_TTT_CFG if ttt_cfg is None else ttt_cfg

    peak_vram_before = _peak_vram_reset()

    prompt_byte_lengths: list[int] = []
    over_budget = 0
    n_dropped_passage = 0
    answers: list[dict] = []
    retrieval_hit: dict[str, bool] = {}

    base_snap = None
    probe_before = None
    if cond in ("C", "D"):
        base_snap = _snapshot(loras)
        probe_before = probe.nll(model, tok, _sequence_nll)

    t0 = time.perf_counter()
    for rec in items:
        qid, question = rec["id"], rec["prompt"]
        prompt, passages, pb, dropped = retrieve_and_build_prompt(store, question, k)
        prompt_byte_lengths.append(pb)
        if pb > MAX_PROMPT_BYTES:
            over_budget += 1
        if dropped:
            n_dropped_passage += 1
        if "act" in rec and "section" in rec:
            retrieval_hit[qid] = any(
                (p.act, p.section) == (rec["act"], rec["section"]) for p in passages
            )

        if cond == "B":
            text = _generate(model, tok, [prompt], max_new_tokens, stop)[0]
            record = build_answer_record(qid, text, pb, passages)
        else:
            pre_text = _generate(model, tok, [prompt], max_new_tokens, stop)[0]
            adapt_text = adapt_text_from_passages(passages)
            _adapt(model, tok, adapt_text, loras, ttt_cfg.get("steps", 4), ttt_cfg.get("lr", 1e-3))
            post_text = _generate(model, tok, [prompt], max_new_tokens, stop)[0]
            probe_after = probe.nll(model, tok, _sequence_nll)
            post_answer = retrieval_mod.parse_answer(post_text)
            post_grounded = retrieval_mod.grounded(post_answer, passages)
            decision = _decide(
                probe_before, probe_after, post_grounded,
                threshold=ttt_cfg.get("threshold", 0.15),
                delta_norm=_merged_delta_norm(loras),
            )
            if ledger is not None:
                ledger.append(qid, decision)
            _restore(loras, base_snap)

            chosen_text = post_text if decision.accepted else pre_text
            record = build_answer_record(qid, chosen_text, pb, passages, gate_info=dataclasses.asdict(decision))
        answers.append(record)
    t1 = time.perf_counter()

    write_jsonl(out_dir / "answers.jsonl", answers)
    peak_vram_mib = _peak_vram(peak_vram_before)

    score = _score_law_qa(heldout_path, out_dir / "answers.jsonl")
    extra = _extra_metrics(items, answers, retrieval_hit=retrieval_hit)

    report = {
        "condition": label or cond,
        "n_items": len(items),
        "wall_clock_seconds": {"total": t1 - t0, "per_item": (t1 - t0) / len(items) if items else 0.0},
        "peak_vram_mib": peak_vram_mib,
        "prompt_bytes": {
            "mean": sum(prompt_byte_lengths) / len(prompt_byte_lengths) if prompt_byte_lengths else 0.0,
            "max": max(prompt_byte_lengths) if prompt_byte_lengths else 0,
            "over_budget_count": over_budget,
            "budget": MAX_PROMPT_BYTES,
            "dropped_passage_count": n_dropped_passage,
            "dropped_passage_rate": n_dropped_passage / len(items) if items else 0.0,
        },
        "score": score,
        **extra,
    }
    if cond in ("C", "D") and ledger is not None:
        report["gate_summary"] = ledger.summary()
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _decide(probe_before, probe_after, grounded_ok, threshold, delta_norm):
    from . import gate as gate_mod

    return gate_mod.decide(probe_before, probe_after, grounded_ok, threshold=threshold, delta_norm=delta_norm)


def _peak_vram_reset():
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    except ImportError:
        pass
    return None


def _peak_vram(_before):
    try:
        import torch

        if torch.cuda.is_available():
            return torch.cuda.max_memory_allocated() / (1024 * 1024)
    except ImportError:
        pass
    return None


def _extra_metrics(items: list[dict], answers: list[dict], retrieval_hit: dict[str, bool] | None = None) -> dict:
    """grounded_rate, hallucinated_citation_rate, abstention_correct (on the
    abstain items), per-kind gold-citation hit rate with Wilson intervals,
    and (if `retrieval_hit` is given) `abstain_on_miss`: among held-out
    items whose gold (act, section) was NOT among the top-k retrieved
    passages, the fraction where the model correctly abstained instead of
    citing something ungrounded -- the MVP-relevant behaviour under a known
    retrieval ceiling (recall@3 = 0.66)."""
    by_id = {a["id"]: a for a in answers}
    n = len(answers)
    grounded_n = sum(1 for a in answers if a["grounded"])
    hallucinated_n = sum(1 for a in answers if a["cited"] and not a["grounded"])

    abstain_items = [r for r in items if r["kind"] == _ABSTAIN_KIND]
    abstain_correct = sum(1 for r in abstain_items if by_id.get(r["id"], {}).get("abstained"))

    per_kind: dict[str, dict] = {}
    kinds: dict[str, list[dict]] = {}
    for r in items:
        kinds.setdefault(r["kind"], []).append(r)
    for kind, recs in kinds.items():
        hits = 0
        total = 0
        for r in recs:
            a = by_id.get(r["id"])
            if a is None:
                total += 1
                continue
            total += 1
            if _citation_correct(a["answer"], r["act"], r["section"]):
                hits += 1
        lo, hi = stats.wilson(hits, total) if total else (0.0, 1.0)
        per_kind[kind] = {"successes": hits, "total": total, "rate": hits / total if total else 0.0,
                          "ci_low": lo, "ci_high": hi}

    result = {
        "grounded_rate": grounded_n / n if n else 0.0,
        "hallucinated_citation_rate": hallucinated_n / n if n else 0.0,
        "abstention_correct": {
            "successes": abstain_correct, "total": len(abstain_items),
            "rate": abstain_correct / len(abstain_items) if abstain_items else 0.0,
        },
        "per_kind_citation_hit_rate": per_kind,
    }

    if retrieval_hit:
        # Only citation-kind items are eligible (an abstain item's gold is a
        # decoy, not something retrieval should have surfaced).
        miss_ids = [r["id"] for r in items if r["kind"] != _ABSTAIN_KIND
                    and retrieval_hit.get(r["id"]) is False]
        abstain_on_miss = sum(1 for i in miss_ids if by_id.get(i, {}).get("abstained"))
        lo, hi = stats.wilson(abstain_on_miss, len(miss_ids)) if miss_ids else (0.0, 1.0)
        result["abstain_on_miss"] = {
            "successes": abstain_on_miss, "total": len(miss_ids),
            "rate": abstain_on_miss / len(miss_ids) if miss_ids else 0.0,
            "ci_low": lo, "ci_high": hi,
        }

    return result


def run_condition_scoring(
    cond: str,
    model,
    tok,
    store,
    items: list[dict],
    out_dir,
    *,
    loras: list | None = None,
    ttt_cfg: dict | None = None,
    probe=None,
    ledger=None,
    k: int = K_PASSAGES,
    heldout_path=DEFAULT_HELDOUT,
    label: str | None = None,
) -> dict:
    """Scoring-mode counterpart of `run_condition` -- see the "Scoring mode"
    module comment above `build_candidates` for why this exists (the
    greedy-decoding trap, F12). `cond` in {"B","C","D"} means the same thing
    as in `run_condition`: B is frozen (score only, no adapt), C/D adapt
    ephemerally per query (gated on probe regression only -- grounding is
    guaranteed by construction in this mode) before rescoring, falling back
    to the pre-adapt scoring on gate rejection."""
    if cond not in ("B", "C", "D"):
        raise ValueError(f"unknown condition: {cond!r}")
    if cond in ("C", "D") and (loras is None or probe is None):
        raise ValueError(f"condition {cond} requires loras and probe")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ttt_cfg = DEFAULT_TTT_CFG if ttt_cfg is None else ttt_cfg
    peak_vram_before = _peak_vram_reset()

    answers: list[dict] = []
    retrieval_hit: dict[str, bool] = {}
    n_dropped_passage = 0
    base_snap = None
    probe_before = None
    if cond in ("C", "D"):
        base_snap = _snapshot(loras)
        probe_before = probe.nll(model, tok, _sequence_nll)

    t0 = time.perf_counter()
    for rec in items:
        qid, question, kind = rec["id"], rec["prompt"], rec["kind"]
        prompt, passages, pb, dropped = retrieve_and_build_prompt(store, question, k)
        n_dropped_passage += int(dropped)
        if "act" in rec and "section" in rec:
            retrieval_hit[qid] = any(
                (p.act, p.section) == (rec["act"], rec["section"]) for p in passages
            )
        candidates = build_candidates(passages, kind)
        pre_score = score_candidates(model, tok, prompt, candidates)

        gate_info = None
        if cond == "B":
            chosen = pre_score
        else:
            adapt_text = adapt_text_from_passages(passages)
            _adapt(model, tok, adapt_text, loras, ttt_cfg.get("steps", 4), ttt_cfg.get("lr", 1e-3))
            post_score = score_candidates(model, tok, prompt, candidates)
            probe_after = probe.nll(model, tok, _sequence_nll)
            decision = _decide(probe_before, probe_after, True,
                               threshold=ttt_cfg.get("threshold", 0.15), delta_norm=_merged_delta_norm(loras))
            if ledger is not None:
                ledger.append(qid, decision)
            _restore(loras, base_snap)
            chosen = post_score if decision.accepted else pre_score
            gate_info = dataclasses.asdict(decision)

        best_idx = chosen["best_idx"]
        _cand_text, source = candidates[best_idx]
        is_abstain = source is None
        text = retrieval_mod.ABSTAIN_PHRASE if is_abstain else compose_answer(kind, source, store)
        gold_selected = (not is_abstain) and (source.act, source.section) == (rec.get("act"), rec.get("section"))
        record = {
            "id": qid, "kind": kind, "prompt_bytes": pb, "chosen_idx": best_idx, "answer": text,
            "abstained": is_abstain, "cited": not is_abstain, "grounded": True,
            "gold_selected": gold_selected, "nlls": chosen["nlls"], "margin": chosen["margin"],
            "n_candidates": len(candidates),
        }
        if gate_info is not None:
            record["gate"] = gate_info
        answers.append(record)
    t1 = time.perf_counter()

    write_jsonl(out_dir / "answers.jsonl", answers)
    peak_vram_mib = _peak_vram(peak_vram_before)
    score = _score_law_qa(heldout_path, out_dir / "answers.jsonl")
    report = _scoring_report(cond, label, items, answers, retrieval_hit, t1 - t0, peak_vram_mib, score)
    report["dropped_passage_count"] = n_dropped_passage
    report["dropped_passage_rate"] = n_dropped_passage / len(items) if items else 0.0
    if cond in ("C", "D") and ledger is not None:
        report["gate_summary"] = ledger.summary()
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _scoring_report(cond: str, label: str | None, items: list[dict], answers: list[dict],
                     retrieval_hit: dict[str, bool], wall_seconds: float, peak_vram_mib,
                     score: dict) -> dict:
    """Shared report assembly for both `run_condition_scoring` (candidates
    include an abstain option, picked by argmin) and
    `run_condition_calibrated` (candidates are citations only; abstention is
    a separate calibrated threshold decision) -- both produce `answers`
    records shaped alike (id/kind/answer/abstained/cited/grounded/gold_selected/margin)."""
    by_id = {a["id"]: a for a in answers}
    n = len(answers)
    gold_n = sum(1 for a in answers if a["gold_selected"])
    abstain_items = [r for r in items if r["kind"] == _ABSTAIN_KIND]
    abstain_correct = sum(1 for r in abstain_items if by_id.get(r["id"], {}).get("abstained"))

    per_kind: dict[str, dict] = {}
    kinds: dict[str, list[dict]] = {}
    for r in items:
        kinds.setdefault(r["kind"], []).append(r)
    for kind, recs in kinds.items():
        hits = sum(1 for r in recs if by_id.get(r["id"], {}).get("gold_selected"))
        total = len(recs)
        lo, hi = stats.wilson(hits, total) if total else (0.0, 1.0)
        per_kind[kind] = {"successes": hits, "total": total, "rate": hits / total if total else 0.0,
                          "ci_low": lo, "ci_high": hi}

    extra: dict[str, Any] = {
        "grounded_rate": 1.0,
        "hallucinated_citation_rate": 0.0,
        "abstention_correct": {"successes": abstain_correct, "total": len(abstain_items),
                               "rate": abstain_correct / len(abstain_items) if abstain_items else 0.0},
        "per_kind_gold_selected_rate": per_kind,
    }
    if retrieval_hit:
        miss_ids = [r["id"] for r in items if r["kind"] != _ABSTAIN_KIND and retrieval_hit.get(r["id"]) is False]
        shown_ids = [r["id"] for r in items if r["kind"] != _ABSTAIN_KIND and retrieval_hit.get(r["id"]) is True]
        abstain_on_miss = sum(1 for i in miss_ids if by_id.get(i, {}).get("abstained"))
        false_abstain_when_shown = sum(1 for i in shown_ids if by_id.get(i, {}).get("abstained"))
        lo, hi = stats.wilson(abstain_on_miss, len(miss_ids)) if miss_ids else (0.0, 1.0)
        lo2, hi2 = stats.wilson(false_abstain_when_shown, len(shown_ids)) if shown_ids else (0.0, 1.0)
        extra["abstain_on_miss"] = {"successes": abstain_on_miss, "total": len(miss_ids),
                                    "rate": abstain_on_miss / len(miss_ids) if miss_ids else 0.0,
                                    "ci_low": lo, "ci_high": hi}
        extra["false_abstain_when_shown"] = {"successes": false_abstain_when_shown, "total": len(shown_ids),
                                             "rate": false_abstain_when_shown / len(shown_ids) if shown_ids else 0.0,
                                             "ci_low": lo2, "ci_high": hi2}

    margins = [a["margin"] for a in answers if a.get("margin") not in (None, float("inf"))]
    return {
        "condition": label or cond, "mode": "scoring", "n_items": len(items),
        "wall_clock_seconds": {"total": wall_seconds, "per_item": wall_seconds / len(items) if items else 0.0},
        "peak_vram_mib": peak_vram_mib,
        "score": score,
        "gold_selected_overall": {"successes": gold_n, "total": n, "rate": gold_n / n if n else 0.0},
        "margin_stats": {"mean": sum(margins) / len(margins) if margins else 0.0,
                         "min": min(margins) if margins else 0.0},
        **extra,
    }


# ---------------------------------------------------------------------------
# Calibrated abstention (F14 fix, round 2 of the 2026-09-13 pivot): the
# model no longer "decides" abstention by competing an abstain STRING inside
# the likelihood argmin (that let training-set repetition of one fixed
# string dominate); instead the harness picks the best-supported citation
# among the SHOWN passages only, then abstains via an external, calibrated
# threshold rule on that choice's own score (best_total_nll) and its margin
# over the runner-up -- see loop.calibrate_abstention for how (tau, delta)
# are chosen.
# ---------------------------------------------------------------------------


def run_condition_calibrated(
    cond: str,
    model,
    tok,
    store,
    items: list[dict],
    out_dir,
    *,
    tau: float,
    delta: float,
    loras: list | None = None,
    ttt_cfg: dict | None = None,
    probe=None,
    ledger=None,
    k: int = K_PASSAGES,
    heldout_path=DEFAULT_HELDOUT,
    label: str | None = None,
) -> dict:
    if cond not in ("B", "C", "D"):
        raise ValueError(f"unknown condition: {cond!r}")
    if cond in ("C", "D") and (loras is None or probe is None):
        raise ValueError(f"condition {cond} requires loras and probe")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ttt_cfg = DEFAULT_TTT_CFG if ttt_cfg is None else ttt_cfg
    peak_vram_before = _peak_vram_reset()

    answers: list[dict] = []
    retrieval_hit: dict[str, bool] = {}
    n_dropped_passage = 0
    base_snap = None
    probe_before = None
    if cond in ("C", "D"):
        base_snap = _snapshot(loras)
        probe_before = probe.nll(model, tok, _sequence_nll)

    t0 = time.perf_counter()
    for rec in items:
        qid, question, kind = rec["id"], rec["prompt"], rec["kind"]
        prompt, passages, pb, dropped = retrieve_and_build_prompt(store, question, k)
        n_dropped_passage += int(dropped)
        if "act" in rec and "section" in rec:
            retrieval_hit[qid] = any(
                (p.act, p.section) == (rec["act"], rec["section"]) for p in passages
            )
        candidates = build_candidates(passages, kind, include_abstain=False)

        gate_info = None
        chosen = None
        if candidates:
            pre_score = score_candidates(model, tok, prompt, candidates)
            if cond == "B":
                chosen = pre_score
            else:
                adapt_text = adapt_text_from_passages(passages)
                _adapt(model, tok, adapt_text, loras, ttt_cfg.get("steps", 4), ttt_cfg.get("lr", 1e-3))
                post_score = score_candidates(model, tok, prompt, candidates)
                probe_after = probe.nll(model, tok, _sequence_nll)
                decision = _decide(probe_before, probe_after, True,
                                   threshold=ttt_cfg.get("threshold", 0.15), delta_norm=_merged_delta_norm(loras))
                if ledger is not None:
                    ledger.append(qid, decision)
                _restore(loras, base_snap)
                chosen = post_score if decision.accepted else pre_score
                gate_info = dataclasses.asdict(decision)

        if not candidates:
            # Nothing retrieved at all: nothing to cite, must abstain.
            text, is_abstain, gold_selected = retrieval_mod.ABSTAIN_PHRASE, True, False
            best_total_nll = margin = None
        else:
            best_idx = chosen["best_idx"]
            best_total_nll = chosen["total_nlls"][best_idx]
            margin = chosen["margin"]
            should_abstain = (best_total_nll > tau) or (margin < delta)
            if should_abstain:
                text, is_abstain, gold_selected = retrieval_mod.ABSTAIN_PHRASE, True, False
            else:
                _cand_text, source = candidates[best_idx]
                text, is_abstain = compose_answer(kind, source, store), False
                gold_selected = (source.act, source.section) == (rec.get("act"), rec.get("section"))

        record = {
            "id": qid, "kind": kind, "prompt_bytes": pb, "answer": text,
            "abstained": is_abstain, "cited": not is_abstain, "grounded": True,
            "gold_selected": gold_selected, "best_total_nll": best_total_nll, "margin": margin,
        }
        if gate_info is not None:
            record["gate"] = gate_info
        answers.append(record)
    t1 = time.perf_counter()

    write_jsonl(out_dir / "answers.jsonl", answers)
    peak_vram_mib = _peak_vram(peak_vram_before)
    score = _score_law_qa(heldout_path, out_dir / "answers.jsonl")
    report = _scoring_report(cond, label, items, answers, retrieval_hit, t1 - t0, peak_vram_mib, score)
    report["calibration"] = {"tau": tau, "delta": delta}
    report["dropped_passage_count"] = n_dropped_passage
    report["dropped_passage_rate"] = n_dropped_passage / len(items) if items else 0.0
    if cond in ("C", "D") and ledger is not None:
        report["gate_summary"] = ledger.summary()
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


# ---------------------------------------------------------------------------
# Pointwise grounded judgment (TRIZ "segmentation", 2026-09-13): instead of
# one prompt holding k retrieved passages (the scoring-mode conditions
# above, bound by the model's 512-byte trained context to k=4), build k
# SHORT prompts, each holding exactly ONE passage plus the question, and
# score exactly two fixed continuations per prompt: that passage's own
# canonical citation string and the abstain phrase. Because each prompt
# carries only one passage, its body budget can grow a lot (220B vs the
# k=4 prompt's 60B) while still fitting the model's 512-byte context, and k
# itself can grow well past 4 (retrieval recall@8/16/32 is 0.836/0.870/0.909
# vs recall@4's 0.772 -- see docs/ttt-rsi-track-b-research-and-implementation.md
# S10) without ever building an oversized single prompt. The passage with
# the best per-passage "cite over abstain" margin is selected; the emitted
# answer is composed with `compose_answer` exactly as in the other scoring
# modes, so grounded-by-construction is preserved (every emitted answer is
# either a shown passage's citation or the abstain phrase).
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class PointwiseConfig:
    k: int
    # Measured on the real 690 held-out (see runs/pointwise_run1/_driver.py's
    # budget dry run): the single-passage prompt also carries the FULL,
    # untruncated question (held-out questions run up to 195 bytes, mean 75)
    # plus the instruction and act/section bracket, none of which this
    # config truncates -- unlike PROMPT_CONFIG's k=4 budget (950B, spread
    # over up to 4 passages), a single-passage budget of 500B leaves much
    # less room per passage once a long question is included. body=110/
    # title=45 was the largest pair with zero held-out violations at k up
    # to 32 (110/50 already reaches the 500B ceiling exactly at k=8).
    body_max_bytes: int = 110
    title_max_bytes: int = 45
    # NOTE: unlike PromptConfig.max_prompt_bytes (which bounds the prompt
    # alone), this bounds prompt bytes PLUS the longer of the two scored
    # continuations (see `assert_pointwise_budget`) -- the per-item task
    # spec asks for "prompt+continuation <= 500 bytes" directly, since a
    # pointwise prompt's own continuation is part of what must fit the
    # model's 512-byte trained sequence length in one forward pass.
    max_prompt_bytes: int = 500
    lambda_prior: float = 0.0
    instruction: str = INSTRUCTION


def rank_prior(rank: int) -> float:
    """-log(rank) fusion prior term, `rank` 1-indexed (BM25 retrieval order,
    i.e. `store.search`'s own return order -- rank 1 is the top BM25 hit).
    Monotonically decreasing and unbounded above 0 only at rank 1 (= 0.0)."""
    if rank < 1:
        raise ValueError("rank must be >= 1")
    return -math.log(rank)


def build_pointwise_prompt(question: str, passage, config: PointwiseConfig = PointwiseConfig(k=1)):
    """One short prompt holding exactly ONE (already-truncated) passage plus
    the question -- reuses `apply_prompt_config` (generic over any object
    with `.body_max_bytes`/`.title_max_bytes`, `PointwiseConfig` included)
    and `retrieval_mod.build_grounded_prompt` (called with a singleton
    passage list) exactly as the k-passage prompt path does. Returns
    (prompt, truncated_passage)."""
    trunc = apply_prompt_config([passage], config)[0]
    prompt = retrieval_mod.build_grounded_prompt(question, [trunc], instruction=config.instruction)
    return prompt, trunc


def assert_pointwise_budget(prompt: str, candidate_texts: list[str], max_bytes: int = 500) -> int:
    """Hard-asserts `prompt` plus the LONGER of its scored continuations
    fits `max_bytes` (default matches `PointwiseConfig.max_prompt_bytes`,
    the model's 512-byte trained sequence length minus headroom). Mirrors
    `assert_prompt_budget`'s "investigate rather than silently truncate
    further" philosophy. Returns the combined byte length."""
    prompt_bytes = len(prompt.encode("utf-8"))
    max_cont_bytes = max((len(c.encode("utf-8")) for c in candidate_texts), default=0)
    total = prompt_bytes + max_cont_bytes
    assert total <= max_bytes, (
        f"pointwise prompt+continuation exceeds the {max_bytes}-byte budget "
        f"({prompt_bytes} + {max_cont_bytes} = {total} bytes) -- investigate rather "
        "than truncating further"
    )
    return total


def _batch_mean_nll(model, tok, pairs: list[tuple[str, str]]) -> list[float]:
    """Batched counterpart of `_sequence_nll`/`model_io.sequence_nll` (same
    math: mean NLL over continuation bytes only, teacher-forced), computed
    for many (prompt, continuation) pairs in as few forward passes as
    possible. Groups pairs by their EXACT combined token length and runs one
    batched forward per group -- Mamba2 has no attention mask, so, exactly
    like `model_io.generate`'s own batching contract, sequences are never
    padded, only grouped by identical length. A lazy backend (see the module
    docstring): tests stub this name directly, same convention as
    `_generate`/`_sequence_nll`/`_adapt`."""
    import torch

    device = next(model.parameters()).device
    encoded = [(tok.encode(p), tok.encode(c)) for p, c in pairs]
    groups: dict[int, list[int]] = {}
    for i, (p_ids, c_ids) in enumerate(encoded):
        groups.setdefault(len(p_ids) + len(c_ids), []).append(i)

    results = [0.0] * len(pairs)
    with torch.no_grad():
        for total_len, idxs in groups.items():
            if total_len < 2:
                continue
            batch_ids = [encoded[i][0] + encoded[i][1] for i in idxs]
            tokens = torch.tensor(batch_ids, dtype=torch.long, device=device)
            zeros = torch.zeros_like(tokens)
            with torch.autocast(device.type, dtype=torch.bfloat16, enabled=(device.type == "cuda")):
                logits = model(tokens, zeros, zeros)  # (B, L, vocab); batch index 0 is NOT a tuple unwrap here

            for row, i in enumerate(idxs):
                n_prompt = len(encoded[i][0])
                targets = tokens[row, 1:]
                preds = logits[row, :-1]
                j_start = max(n_prompt - 1, 0)
                if j_start >= targets.shape[0]:
                    continue
                sel_preds = preds[j_start:].float()
                sel_targets = targets[j_start:]
                nll = torch.nn.functional.cross_entropy(sel_preds, sel_targets, reduction="mean")
                results[i] = float(nll.item())
    return results


def score_pointwise_passages(model, tok, question: str, passages: list, kind: str,
                              config: PointwiseConfig) -> list[dict]:
    """For each retrieved `passage` (in BM25 rank order, 1-indexed), build
    its own single-passage prompt (`build_pointwise_prompt`), its two
    candidates (`build_candidates([passage], kind)` -- the SAME canonical
    citation format and abstain phrase used everywhere else in this module),
    assert the prompt+continuation budget, and batch every (prompt,
    candidate) pair across every passage into one `_batch_mean_nll` call.
    Returns one dict per passage: `passage`, `rank` (1-indexed), the mean
    and total NLL of each candidate, and `margin_mean`/`margin_total` =
    NLL(abstain) - NLL(cite) (positive means the model prefers citing)."""
    if not passages:
        return []

    prompts: list[str] = []
    trunc_passages: list = []
    cand_texts_per_passage: list[list[str]] = []
    pairs: list[tuple[str, str]] = []
    for passage in passages:
        prompt, trunc = build_pointwise_prompt(question, passage, config)
        candidates = build_candidates([trunc], kind, include_abstain=True)
        cand_texts = [text for text, _source in candidates]
        assert_pointwise_budget(prompt, cand_texts, config.max_prompt_bytes)
        prompts.append(prompt)
        trunc_passages.append(trunc)
        cand_texts_per_passage.append(cand_texts)
        pairs.append((prompt, cand_texts[0]))  # cite
        pairs.append((prompt, cand_texts[1]))  # abstain

    mean_nlls = _batch_mean_nll(model, tok, pairs)

    scored = []
    for rank, (passage, cand_texts) in enumerate(zip(passages, cand_texts_per_passage), start=1):
        cite_mean = mean_nlls[2 * (rank - 1)]
        abstain_mean = mean_nlls[2 * (rank - 1) + 1]
        cite_total = cite_mean * len(cand_texts[0].encode("utf-8"))
        abstain_total = abstain_mean * len(cand_texts[1].encode("utf-8"))
        scored.append({
            "passage": passage, "rank": rank,
            "nll_cite_mean": cite_mean, "nll_abstain_mean": abstain_mean,
            "nll_cite_total": cite_total, "nll_abstain_total": abstain_total,
            "margin_mean": abstain_mean - cite_mean, "margin_total": abstain_total - cite_total,
            "prompt_bytes": len(prompts[rank - 1].encode("utf-8")),
        })
    return scored


def fuse_pointwise_scores(scored: list[dict], lambda_prior: float) -> list[float]:
    """s_i = m_i + lambda * (-log(rank_i)) -- `m_i` is the mean-per-byte
    margin (`margin_mean`, NLL(abstain) - NLL(cite)); `lambda_prior=0`
    recovers pure per-passage margin selection."""
    return [s["margin_mean"] + lambda_prior * rank_prior(s["rank"]) for s in scored]


def select_pointwise(scored: list[dict], lambda_prior: float = 0.0) -> dict:
    """Picks the passage with the max fused score `s_i`. Returns `best_idx`
    (index into `scored`), `best_score`, `second_score` (the runner-up
    fused score, `-inf` if only one passage), `gap` = best - second
    (`inf` if only one passage, matching `score_candidates`'s own
    single-candidate convention), and every fused score."""
    if not scored:
        return {"best_idx": None, "best_score": None, "second_score": None,
                "gap": float("inf"), "fused_scores": []}
    fused = fuse_pointwise_scores(scored, lambda_prior)
    order = sorted(range(len(fused)), key=lambda i: fused[i], reverse=True)
    best_idx = order[0]
    best_score = fused[best_idx]
    if len(order) > 1:
        second_score = fused[order[1]]
        gap = best_score - second_score
    else:
        second_score = float("-inf")
        gap = float("inf")
    return {"best_idx": best_idx, "best_score": best_score, "second_score": second_score,
            "gap": gap, "fused_scores": fused}


def pointwise_selection_correct(scored: list[dict], act: str, section: str, lambda_prior: float) -> bool:
    """True iff `select_pointwise(scored, lambda_prior)`'s arg-max passage
    is exactly `(act, section)` -- the "selection_correct" calibration
    label (see `calibrate_pointwise`), factored out as its own testable
    function rather than a closure."""
    sel = select_pointwise(scored, lambda_prior)
    if sel["best_idx"] is None:
        return False
    picked = scored[sel["best_idx"]]["passage"]
    return (picked.act, picked.section) == (act, section)


def calibrate_pointwise(model, tok, store, train_path, *, k: int, n: int = 500, seed: int = 1,
                        frac_miss: float = 0.3, exclude_ids: set | None = None,
                        lambda_grid: tuple = (0.0, 0.25, 0.5, 1.0),
                        config_kwargs: dict | None = None,
                        label_mode: str = "selection_correct") -> dict:
    """Calibrates (tau_m, delta_m, lambda_prior) on a TRAIN-split dev slice,
    reusing `loop.build_calibration_examples` (same gold-shown/gold-missing
    construction the k-passage calibration already uses, parameterized by
    the same `k`) and `loop.choose_calibration_thresholds` (the SAME
    balanced-accuracy grid-search objective -- no new objective is
    introduced here, only a re-expression of the pointwise fused score into
    that function's expected {cost, margin} shape and a `lambda_prior`
    sweep wrapped around it, per the task spec: "reuse that code path").

    `loop.choose_calibration_thresholds` expects "abstain if best_total_nll
    > tau" (higher cost = more abstain-worthy) and "abstain if margin <
    delta" (lower margin = more ambiguous = more abstain-worthy). The
    pointwise fused score `s` is the opposite sign of a cost (higher s =
    more confident = less abstain-worthy), so it is negated into a `cost =
    -best_score` column before calling the shared routine, and the returned
    `tau` is negated back (`tau_m = -tau`) so the report expresses the
    threshold directly in score units ("abstain when best margin below
    tau_m"), per the task's own phrasing. The `gap` column already matches
    the reused function's "margin" sign convention (smaller gap = more
    ambiguous) unchanged.

    Scoring (the expensive part) is done ONCE per dev example -- selection
    is the only thing that depends on `lambda_prior`, so the lambda grid
    sweep is pure re-selection over already-computed per-passage scores, not
    `lambda_grid` extra full model-scoring passes.

    `label_mode` (2026-09-13 correction after a degenerate-calibration bug
    report): "selection_correct" (the default) labels an example positive
    (should cite) only when the FUSED-score argmax actually equals the
    example's own gold passage -- i.e. what `choose_calibration_thresholds`
    is really supposed to discriminate ("would citing be right, or should
    we abstain") -- rather than the legacy "gold_shown" label (positive
    whenever gold was merely among the shown passages, regardless of
    whether the model's own argmax picked it). The legacy label is
    degenerate whenever retrieval recall@k is very high (e.g. a tuned BM25
    store, recall@4=0.98): almost every dev example is then "gold shown",
    so the balanced-accuracy grid effectively has no negative class to
    calibrate against, and it converges on thresholds that abstain on the
    vast majority of correctly-shown cases too (observed: false-abstain-
    when-shown 0.72-0.82 with the tuned store). "selection_correct" stays
    well-posed regardless of retrieval quality, since even a shown gold
    passage can lose the argmax to a wrong passage. `label_mode="gold_shown"`
    is kept only so the legacy (buggy) calibration can still be reproduced
    for the record."""
    from . import loop as loop_mod

    if label_mode not in ("selection_correct", "gold_shown"):
        raise ValueError(f"unknown label_mode: {label_mode!r}")

    config_kwargs = config_kwargs or {}
    config = PointwiseConfig(k=k, **config_kwargs)
    examples = loop_mod.build_calibration_examples(
        train_path, store, n=n, seed=seed, frac_miss=frac_miss, exclude_ids=exclude_ids, k=k,
    )

    per_example: list[dict] = []
    for e in examples:
        passages = e["passages"]
        if not passages:
            continue
        question = parse_question_from_prompt(e["prompt"])
        scored = score_pointwise_passages(model, tok, question, passages, e["kind"], config)
        per_example.append({"gold_shown": e["gold_shown"], "act": e["act"], "section": e["section"],
                            "scored": scored})

    grid = []
    for lam in lambda_grid:
        rows = []
        for pe in per_example:
            sel = select_pointwise(pe["scored"], lam)
            label = (pe["gold_shown"] if label_mode == "gold_shown"
                    else pointwise_selection_correct(pe["scored"], pe["act"], pe["section"], lam))
            rows.append({"gold_shown": label, "best_total_nll": -sel["best_score"], "margin": sel["gap"]})
        result = loop_mod.choose_calibration_thresholds(rows)
        grid.append({"lambda_prior": lam, "tau_m": -result["tau"], "delta_m": result["delta"],
                     "balanced_accuracy": result["balanced_accuracy"], "confusion": result["confusion"]})

    best = max(grid, key=lambda g: g["balanced_accuracy"])
    return {
        "lambda_prior": best["lambda_prior"], "tau_m": best["tau_m"], "delta_m": best["delta_m"],
        "balanced_accuracy": best["balanced_accuracy"], "confusion": best["confusion"],
        "n_examples": len(per_example), "k": k, "grid": grid, "label_mode": label_mode,
    }


# ---------------------------------------------------------------------------
# Scoring mode + BM25-rank prior + selection-correctness calibration
# (2026-09-13 correction): applies the same two ideas -- a `-log(rank)`
# fusion prior and a selection-correctness (not gold-shown) calibration
# label -- to the EXISTING multi-passage-per-prompt scoring mode (the S4t*
# conditions), not just the pointwise mode. Built on a "natural" dev slice
# (retrieved via `store.search` directly, unshuffled, gold never forced
# in/out) rather than `loop.build_calibration_examples`'s forced-shown/
# forced-miss construction: rank fidelity matters here (the prior is a
# function of true BM25 rank), which a shuffled, gold-forced dev slice
# would not preserve, and the "selection_correct" label is well-posed on a
# natural sample regardless of how often gold happens to be retrieved.
# ---------------------------------------------------------------------------


def build_natural_dev_examples(train_path, store, *, k: int, n: int = 500, seed: int = 1) -> list[dict]:
    """Samples `n` TRAIN rows (citation kinds AND `law_abstain`, in their
    natural proportion) and retrieves each one's real top-k via
    `store.search` -- no shuffling, no forced gold inclusion/exclusion.
    `law_abstain` rows carry a real `(act, section)` for a document that
    does not exist in the corpus, so no candidate can ever match it: they
    contribute honestly-negative examples (should always abstain) without
    any special-casing in the scoring/labeling code that consumes this."""
    import random

    from . import grounded_data

    records = load_jsonl(train_path)
    pool = [r for r in records if r["kind"] in grounded_data.CITATION_KINDS or r["kind"] == "law_abstain"]
    rng = random.Random(seed)
    rng.shuffle(pool)
    sampled = pool[:n]

    examples = []
    for rec in sampled:
        passages = store.search(rec["prompt"], k)
        examples.append({"id": rec["id"], "kind": rec["kind"], "act": rec["act"], "section": rec["section"],
                         "prompt": rec["prompt"], "passages": passages})
    return examples


def score_scoring_mode_dev_examples(model, tok, examples: list[dict], by: str = "total") -> list[dict]:
    """Scores each natural-dev example's shown passages as ordinary scoring-
    mode candidates (`build_candidates`/`score_candidates`, citation-only,
    same as `run_condition_calibrated`), caching the per-candidate NLLs and
    candidate sources (but not recomputing anything) so a `lambda_prior`
    sweep over the rank prior is pure CPU re-selection, not new forward
    passes."""
    scored = []
    for e in examples:
        candidates = build_candidates(e["passages"], e["kind"], include_abstain=False)
        if not candidates:
            continue
        result = score_candidates(model, tok, e["prompt"], candidates, by=by)
        nlls = result["total_nlls"] if by == "total" else result["mean_nlls"]
        scored.append({"act": e["act"], "section": e["section"],
                       "sources": [source for _text, source in candidates], "nlls": nlls})
    return scored


def fuse_scoring_with_rank_prior(nlls: list[float], lambda_prior: float) -> list[float]:
    """s_i = -NLL_i + lambda * (-log(rank_i)), rank 1-indexed by `nlls`'
    own (already rank-ordered) position -- the scoring-mode analogue of
    `fuse_pointwise_scores`."""
    return [-nll + lambda_prior * rank_prior(i + 1) for i, nll in enumerate(nlls)]


def select_scoring_with_rank_prior(nlls: list[float], lambda_prior: float) -> dict:
    """Scoring-mode analogue of `select_pointwise`, operating on a fused
    `-NLL + lambda*rank_prior` score instead of raw NLL."""
    if not nlls:
        return {"best_idx": None, "best_score": None, "gap": float("inf"), "fused_scores": []}
    fused = fuse_scoring_with_rank_prior(nlls, lambda_prior)
    order = sorted(range(len(fused)), key=lambda i: fused[i], reverse=True)
    best_idx = order[0]
    gap = (fused[best_idx] - fused[order[1]]) if len(order) > 1 else float("inf")
    return {"best_idx": best_idx, "best_score": fused[best_idx], "gap": gap, "fused_scores": fused}


def calibrate_scoring_with_rank_prior(model, tok, store, train_path, *, k: int, n: int = 500, seed: int = 1,
                                      lambda_grid: tuple = (0.0, 0.25, 0.5, 1.0)) -> dict:
    """Two-stage calibration on ONE natural dev slice (`build_natural_dev_examples`):
    (1) choose `lambda_prior` from `lambda_grid` by PRE-ABSTENTION selection
    accuracy (fused-score argmax == gold, ignoring abstention entirely --
    the question "does the rank prior make the model pick the right
    passage more often", not a balanced-accuracy abstention objective); (2)
    at that lambda, calibrate (tau_m, delta_m) by the SAME selection-
    correctness balanced-accuracy grid search `calibrate_pointwise` uses
    (`loop.choose_calibration_thresholds`, reused unmodified)."""
    from . import loop as loop_mod

    examples = build_natural_dev_examples(train_path, store, k=k, n=n, seed=seed)
    scored = score_scoring_mode_dev_examples(model, tok, examples)

    def is_correct(se: dict, idx: int | None) -> bool:
        if idx is None:
            return False
        source = se["sources"][idx]
        return source is not None and (source.act, source.section) == (se["act"], se["section"])

    lambda_grid_results = []
    for lam in lambda_grid:
        n_correct = sum(int(is_correct(se, select_scoring_with_rank_prior(se["nlls"], lam)["best_idx"]))
                        for se in scored)
        lambda_grid_results.append({"lambda_prior": lam,
                                    "selection_accuracy": n_correct / len(scored) if scored else 0.0})
    best_lambda = max(lambda_grid_results, key=lambda g: g["selection_accuracy"])["lambda_prior"]

    rows = []
    for se in scored:
        sel = select_scoring_with_rank_prior(se["nlls"], best_lambda)
        rows.append({"gold_shown": is_correct(se, sel["best_idx"]),
                     "best_total_nll": -sel["best_score"] if sel["best_score"] is not None else float("inf"),
                     "margin": sel["gap"]})
    result = loop_mod.choose_calibration_thresholds(rows)

    return {
        "lambda_prior": best_lambda, "lambda_grid": lambda_grid_results,
        "tau_m": -result["tau"], "delta_m": result["delta"],
        "balanced_accuracy": result["balanced_accuracy"], "confusion": result["confusion"],
        "n_examples": len(scored), "k": k, "label_mode": "selection_correct",
    }


def run_condition_scoring_with_rank_prior(
    model, tok, store, items: list[dict], out_dir, *,
    k: int, tau_m: float, delta_m: float, lambda_prior: float,
    heldout_path=DEFAULT_HELDOUT, label: str | None = None,
) -> dict:
    """Held-out scoring-mode evaluation with the BM25-rank-prior fusion:
    like `run_condition_calibrated`, but selects by `fuse_scoring_with_rank_prior`
    instead of raw min-NLL, and abstains on the SAME calibrated-threshold
    shape as `run_condition_pointwise` (`tau_m`/`delta_m` on the fused
    score, not `run_condition_calibrated`'s raw-NLL `tau`/`delta`). Reuses
    `retrieve_and_build_prompt`, `build_candidates`, `score_candidates`,
    `compose_answer`, `_scoring_report` exactly as every other scoring-mode
    condition."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    peak_vram_before = _peak_vram_reset()

    answers: list[dict] = []
    retrieval_hit: dict[str, bool] = {}
    n_dropped_passage = 0

    t0 = time.perf_counter()
    for rec in items:
        qid, question, kind = rec["id"], rec["prompt"], rec["kind"]
        prompt, passages, pb, dropped = retrieve_and_build_prompt(store, question, k)
        n_dropped_passage += int(dropped)
        if "act" in rec and "section" in rec:
            retrieval_hit[qid] = any(
                (p.act, p.section) == (rec["act"], rec["section"]) for p in passages
            )
        candidates = build_candidates(passages, kind, include_abstain=False)

        if not candidates:
            text, is_abstain, gold_selected = retrieval_mod.ABSTAIN_PHRASE, True, False
            best_score = margin = None
        else:
            result = score_candidates(model, tok, prompt, candidates)
            sel = select_scoring_with_rank_prior(result["total_nlls"], lambda_prior)
            best_score, margin = sel["best_score"], sel["gap"]
            should_abstain = (best_score < tau_m) or (margin < delta_m)
            if should_abstain:
                text, is_abstain, gold_selected = retrieval_mod.ABSTAIN_PHRASE, True, False
            else:
                _cand_text, source = candidates[sel["best_idx"]]
                text, is_abstain = compose_answer(kind, source, store), False
                gold_selected = (source.act, source.section) == (rec.get("act"), rec.get("section"))

        record = {
            "id": qid, "kind": kind, "prompt_bytes": pb, "answer": text,
            "abstained": is_abstain, "cited": not is_abstain, "grounded": True,
            "gold_selected": gold_selected, "best_score": best_score, "margin": margin,
        }
        answers.append(record)
    t1 = time.perf_counter()

    write_jsonl(out_dir / "answers.jsonl", answers)
    peak_vram_mib = _peak_vram(peak_vram_before)
    score = _score_law_qa(heldout_path, out_dir / "answers.jsonl")
    report = _scoring_report(label or "scoring_with_rank_prior", label, items, answers, retrieval_hit,
                             t1 - t0, peak_vram_mib, score)
    report["mode"] = "scoring_with_rank_prior"
    report["calibration"] = {"tau_m": tau_m, "delta_m": delta_m, "lambda_prior": lambda_prior, "k": k}
    report["dropped_passage_count"] = n_dropped_passage
    report["dropped_passage_rate"] = n_dropped_passage / len(items) if items else 0.0
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def run_condition_pointwise(
    model, tok, store, items: list[dict], out_dir, *,
    config: PointwiseConfig, tau_m: float, delta_m: float, lambda_prior: float | None = None,
    heldout_path=DEFAULT_HELDOUT, label: str | None = None,
) -> dict:
    """Pointwise grounded-judgment evaluation over `items`: retrieves
    `config.k` passages per question (`store.search`, BM25 rank order),
    scores each pointwise (`score_pointwise_passages`), fuses and selects
    (`select_pointwise`), then abstains when the winning fused score is
    below `tau_m` or its margin over the runner-up is below `delta_m` --
    the SAME calibrated-threshold shape as `run_condition_calibrated`
    (`best_score`/`gap` here play the role `best_total_nll`/`margin` play
    there; the sign is handled by `calibrate_pointwise`, not here). The
    chosen passage's answer is composed with `compose_answer`, exactly as
    every other scoring mode -- grounded by construction. Report assembly
    reuses `_scoring_report`, so the schema matches `run_condition_scoring`/
    `run_condition_calibrated` output (`mode` is overridden to
    `"pointwise"` afterwards)."""
    lambda_prior = config.lambda_prior if lambda_prior is None else lambda_prior
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    peak_vram_before = _peak_vram_reset()

    answers: list[dict] = []
    retrieval_hit: dict[str, bool] = {}

    t0 = time.perf_counter()
    for rec in items:
        qid, question, kind = rec["id"], rec["prompt"], rec["kind"]
        passages = store.search(question, config.k)
        if "act" in rec and "section" in rec:
            retrieval_hit[qid] = any(
                (p.act, p.section) == (rec["act"], rec["section"]) for p in passages
            )

        if not passages:
            text, is_abstain, gold_selected = retrieval_mod.ABSTAIN_PHRASE, True, False
            best_score = margin = None
        else:
            scored = score_pointwise_passages(model, tok, question, passages, kind, config)
            sel = select_pointwise(scored, lambda_prior)
            best_score, margin = sel["best_score"], sel["gap"]
            should_abstain = (best_score < tau_m) or (margin < delta_m)
            if should_abstain:
                text, is_abstain, gold_selected = retrieval_mod.ABSTAIN_PHRASE, True, False
            else:
                source = scored[sel["best_idx"]]["passage"]
                text, is_abstain = compose_answer(kind, source, store), False
                gold_selected = (source.act, source.section) == (rec.get("act"), rec.get("section"))

        record = {
            "id": qid, "kind": kind, "answer": text,
            "abstained": is_abstain, "cited": not is_abstain, "grounded": True,
            "gold_selected": gold_selected, "best_score": best_score, "margin": margin,
            "n_candidates": len(passages),
        }
        answers.append(record)
    t1 = time.perf_counter()

    write_jsonl(out_dir / "answers.jsonl", answers)
    peak_vram_mib = _peak_vram(peak_vram_before)
    score = _score_law_qa(heldout_path, out_dir / "answers.jsonl")
    report = _scoring_report(label or "pointwise", label, items, answers, retrieval_hit,
                             t1 - t0, peak_vram_mib, score)
    report["mode"] = "pointwise"
    report["pointwise_config"] = dataclasses.asdict(config)
    report["calibration"] = {"tau_m": tau_m, "delta_m": delta_m, "lambda_prior": lambda_prior}
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


def compare(cond_a_dir, cond_b_dir, heldout_path=DEFAULT_HELDOUT, out_path=None) -> dict:
    """Paired McNemar + bootstrap on per-item 'gold citation present in
    answer' (score_law_qa's own matching logic, item-level boolean) and on
    'grounded', over the items answered by both conditions."""
    heldout_by_id = {r["id"]: r for r in load_jsonl(heldout_path)}
    a_by_id = {r["id"]: r for r in load_jsonl(Path(cond_a_dir) / "answers.jsonl")}
    b_by_id = {r["id"]: r for r in load_jsonl(Path(cond_b_dir) / "answers.jsonl")}
    common_ids = [i for i in heldout_by_id if i in a_by_id and i in b_by_id]

    has_gold_selected = all("gold_selected" in a_by_id[i] for i in common_ids) and \
        all("gold_selected" in b_by_id[i] for i in common_ids) and common_ids

    cite_a, cite_b, grounded_a, grounded_b = [], [], [], []
    gold_sel_a, gold_sel_b = [], []
    for qid in common_ids:
        rec = heldout_by_id[qid]
        cite_a.append(_citation_correct(a_by_id[qid]["answer"], rec["act"], rec["section"]))
        cite_b.append(_citation_correct(b_by_id[qid]["answer"], rec["act"], rec["section"]))
        grounded_a.append(bool(a_by_id[qid].get("grounded")))
        grounded_b.append(bool(b_by_id[qid].get("grounded")))
        if has_gold_selected:
            gold_sel_a.append(bool(a_by_id[qid]["gold_selected"]))
            gold_sel_b.append(bool(b_by_id[qid]["gold_selected"]))

    metrics = {
        "gold_citation_present": (cite_a, cite_b),
        "grounded": (grounded_a, grounded_b),
    }
    if has_gold_selected:
        # The precise, index-based ground truth from scoring-mode conditions
        # (which passage was actually selected) -- stronger than the
        # substring-matched `gold_citation_present` above, available only
        # when both answers.jsonl files carry it (run_condition_scoring /
        # run_condition_calibrated).
        metrics["gold_selected"] = (gold_sel_a, gold_sel_b)

    result: dict[str, Any] = {"n": len(common_ids)}
    for name, (aa, bb) in metrics.items():
        if not aa:
            result[name] = {"n": 0}
            continue
        b_disc, c_disc = stats.paired_table(aa, bb)
        p = stats.mcnemar_exact(b_disc, c_disc)
        lo, hi = stats.bootstrap_diff(aa, bb)
        result[name] = {
            "n": len(aa),
            "a_rate": sum(aa) / len(aa),
            "b_rate": sum(bb) / len(bb),
            "mcnemar_b": b_disc,
            "mcnemar_c": c_disc,
            "mcnemar_p": p,
            "bootstrap_diff_95ci": [lo, hi],
        }

    if out_path is not None:
        Path(out_path).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_probe():
    from . import gate as gate_mod

    return gate_mod.RegressionProbe.from_files(str(DEFAULT_PROBE_GENERAL), n_general=16, seed=0)


def main(argv: list[str] | None = None) -> int:
    import argparse

    from . import model_io

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--condition", choices=["B", "C", "D"], required=True)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--heldout", type=Path, default=DEFAULT_HELDOUT)
    parser.add_argument("--train", type=Path, default=DEFAULT_TRAIN)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--device", type=str, default="cuda:0")
    parser.add_argument("--limit", type=int, default=None, help="use only the first N held-out items")
    parser.add_argument("--persistent-lora", type=Path, default=None, help="condition D: path to a saved persistent LoRA state")
    parser.add_argument("--steps", type=int, default=DEFAULT_TTT_CFG["steps"])
    parser.add_argument("--lr", type=float, default=DEFAULT_TTT_CFG["lr"])
    parser.add_argument("--threshold", type=float, default=DEFAULT_TTT_CFG["threshold"])
    args = parser.parse_args(argv)

    model, tok = model_io.load_model(args.checkpoint, device=args.device)
    store = retrieval_mod.PassageStore.from_law_files(args.train, args.heldout)
    items = load_jsonl(args.heldout)
    if args.limit:
        items = items[: args.limit]

    loras = None
    probe = None
    ledger = None
    if args.condition in ("C", "D"):
        loras = _inject_lora(model)
        probe = _build_probe()
        from . import gate as gate_mod

        ledger = gate_mod.GateLedger(str(args.out))
        if args.condition == "D" and args.persistent_lora:
            _load_lora_state(loras, args.persistent_lora)

    ttt_cfg = {"steps": args.steps, "lr": args.lr, "threshold": args.threshold}
    report = run_condition(
        args.condition, model, tok, store, items, args.out,
        loras=loras, ttt_cfg=ttt_cfg, probe=probe, ledger=ledger, heldout_path=args.heldout,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

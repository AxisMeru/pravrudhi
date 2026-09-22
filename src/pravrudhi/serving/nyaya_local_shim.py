"""SPIKE (throwaway-until-reviewed) -- minimal local OpenAI-compatible shim for a trained HF checkpoint.

Purpose: prove that `pravrudhi.models.openai_compat.ChatClient` (the client panel.py's
`openai_compat` Vendor interface already uses) can round-trip against a *local* server, so a
future P2b checkpoint plugs into the existing, working vendor interface with zero new interface
code in panel.py -- only a new VENDORS registry entry (Studio's file, not edited here).

Contract this must match exactly (read from pravrudhi/src/pravrudhi/models/openai_compat.py):
  POST {base_url}/chat/completions
    body: {"model", "messages", "temperature", "max_tokens", "seed"?}
    resp: {"choices": [{"message": {"content": str}, "finish_reason": str}],
           "model": str, "usage": {"prompt_tokens": int, "completion_tokens": int}}
  GET {base_url minus '/v1'}/health -> 200

BACKEND MODES (env NYAYA_SHIM_BACKEND):
  "stub" (default): deterministic echo, no GPU, no model load. Proves the wire contract only.
  "hf": lazy-loads a real HF causal LM (NYAYA_SHIM_MODEL, default Qwen/Qwen2.5-3B-Instruct) and
        generates for real. NOT run in this spike: the 5090 was at 98% util / 32080/32607 MiB
        (Studio's Step-2 eval) when this was written -- one-GPU-job-at-a-time house rule blocks it
        until that job finishes. Code path is written and left for the next session to exercise
        once the GPU is free, or once a real checkpoint needs testing.

Run (from a checkout of this repo):  NYAYA_SHIM_BACKEND=stub uv run --project . uvicorn \
        --app-dir src/pravrudhi/serving nyaya_local_shim:app --port 8099
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

BACKEND = os.environ.get("NYAYA_SHIM_BACKEND", "stub")
MODEL_NAME = os.environ.get("NYAYA_SHIM_MODEL", "Qwen/Qwen2.5-3B-Instruct")

_hf_state: dict[str, Any] = {}


def _load_hf() -> None:
    """Lazy, one-time load. Not called by the stub backend; left for a GPU-free session."""
    if _hf_state:
        return
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForCausalLM.from_pretrained(MODEL_NAME, torch_dtype=torch.bfloat16, device_map="auto")
    model.eval()
    _hf_state["tok"] = tok
    _hf_state["model"] = model


def _generate_stub(messages: list[dict[str, str]], max_tokens: int) -> tuple[str, int, int]:
    """No model load, no GPU -- proves the FastAPI/JSON contract only."""
    user_text = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    reply = f"[nyaya-local-shim stub] received {len(user_text)} chars, echoing first 80: {user_text[:80]!r}"
    prompt_tokens = max(1, len(user_text.split()))
    completion_tokens = max(1, len(reply.split()))
    return reply, prompt_tokens, completion_tokens


#: Decoding config copied verbatim from prabhasa_nyaya.p2b_sft.HfPredictor (Track A's real eval
#: predictor, 2026-09-17 chat-template+decoding fix) -- NOT reinvented here. Values and the reasons
#: for them are documented in that module; repeated here only enough to point back at the source.
_CHAT_END_OF_TURN_TOKEN = "<|im_end|>"
_REPETITION_PENALTY = 1.15  # soft defense-in-depth; chat-template EOS stop is primary
_NO_REPEAT_NGRAM_SIZE = 0  # MUST stay 0 -- a nonzero value corrupts legitimate repeated structure


#: The wire format's own last field (`p2b_preflight.WIRE_FORMAT_INSTRUCTION`-adjacent prompts end every
#: answer "CONFIDENCE: high/medium/low"), used here as a CONTENT-based stop signal independent of whether
#: the model ever emits <|im_end|>.
_CONFIDENCE_LINE = re.compile(r"CONFIDENCE:\s*\S+\s")


def _confidence_line_complete(text: str) -> bool:
    """True once a `CONFIDENCE: <word>` line has been fully emitted -- the trailing `\\s` in the pattern
    requires something (a newline, a space) AFTER the word, so a still-growing partial word ("CONFIDENCE:
    hig") never matches; only a word already followed by whitespace counts as complete.

    Root cause this exists to work around (2026-09-21/22 live demo): the token-id `StoppingCriteria`
    below only fires if the model actually emits `<|im_end|>`, and on this shim's free-text legal-QA
    prompt shape -- out of distribution for a model SFT'd on the P2b IR wire format -- arm_c does not
    reliably do that; observed behaviour was a clean CONFIDENCE line followed by a fabricated new
    conversation turn, run to `max_tokens`. A token-id criteria cannot stop content the model never
    signals; this checks what the model has actually SAID instead of waiting for a signal it may never
    send.
    """
    m = _CONFIDENCE_LINE.search(text)
    return m is not None


def _end_of_turn_token_id(tokenizer: Any) -> int:
    """Same lookup as prabhasa_nyaya.p2b_sft._end_of_turn_token_id: explicit, never assumed to
    coincide with tokenizer.eos_token_id by chance."""
    token_id = tokenizer.convert_tokens_to_ids(_CHAT_END_OF_TURN_TOKEN)
    unk_id = getattr(tokenizer, "unk_token_id", None)
    if token_id is None or (unk_id is not None and token_id == unk_id):
        raise ValueError(
            f"tokenizer has no single-token {_CHAT_END_OF_TURN_TOKEN!r} -- this shim's HF backend "
            f"is ChatML-specific, matching HfPredictor's own requirement"
        )
    return token_id


def _generate_hf(messages: list[dict[str, str]], max_tokens: int, temperature: float, seed: int | None) -> tuple[str, int, int]:
    import torch
    from transformers import StoppingCriteria, StoppingCriteriaList

    _load_hf()
    tok = _hf_state["tok"]
    model = _hf_state["model"]
    if seed is not None:
        torch.manual_seed(seed)
    # Same rendering point as HfPredictor._render_chat_prompt (tokenizer's own chat template,
    # add_generation_prompt=True) -- `messages` here already carries just the one user turn the
    # nyaya panel sends, so this is the direct equivalent, not a reimplementation.
    chat_text = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tok(chat_text, return_tensors="pt", add_special_tokens=False).to(next(model.parameters()).device)
    n_prompt = inputs["input_ids"].shape[1]
    pad_token_id = tok.pad_token_id
    if pad_token_id is None:
        pad_token_id = tok.eos_token_id
    eot_id = _end_of_turn_token_id(tok)

    class _EndOfTurnStoppingCriteria(StoppingCriteria):
        def __call__(self, input_ids, scores, **kwargs) -> bool:
            return bool((input_ids[:, -1] == eot_id).all())

    class _ContentBoundaryStoppingCriteria(StoppingCriteria):
        """Batch-size-1 only (this shim never batches, matching its single-request contract): re-decodes
        the full generated span each call and stops once `_confidence_line_complete` says the answer's
        last field is done. O(n) redecode per step is negligible at demo scale (max_tokens in the low
        hundreds); not worth the complexity of an incremental decode for this shim."""

        def __call__(self, input_ids, scores, **kwargs) -> bool:
            generated = input_ids[0, n_prompt:]
            text = tok.decode(generated, skip_special_tokens=True)
            return _confidence_line_complete(text)

    # Greedy whenever temperature==0 (the panel's default and this demo's setting), matching
    # HfPredictor's do_sample=False/num_beams=1 -- sampling only if a caller explicitly asks for it.
    do_sample = temperature > 0
    with torch.no_grad():
        out = model.generate(
            **inputs,
            max_new_tokens=max_tokens,
            do_sample=do_sample,
            **({"temperature": temperature} if do_sample else {}),
            num_beams=1,
            pad_token_id=pad_token_id,
            eos_token_id=eot_id,
            stopping_criteria=StoppingCriteriaList(
                [_EndOfTurnStoppingCriteria(), _ContentBoundaryStoppingCriteria()]
            ),
            repetition_penalty=_REPETITION_PENALTY,
            no_repeat_ngram_size=_NO_REPEAT_NGRAM_SIZE,
        )
    completion_ids = out[0][n_prompt:]
    text = tok.decode(completion_ids, skip_special_tokens=True)
    return text, n_prompt, len(completion_ids)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "backend": BACKEND}


@app.post("/chat/completions")
@app.post("/v1/chat/completions")  # ChatClient appends "/chat/completions" to base_url; the VENDORS
# entry's base_url ends in "/v1" (matching every other openai_compat vendor's convention), so this
# route must exist at that path too. Root-level route kept for direct testing against base_url
# with no "/v1" suffix.
async def chat_completions(request: Request) -> JSONResponse:
    body = await request.json()
    messages = body.get("messages", [])
    max_tokens = int(body.get("max_tokens", 2048))
    temperature = float(body.get("temperature", 0.0))
    seed = body.get("seed")
    model_id = body.get("model", MODEL_NAME)

    if BACKEND == "hf":
        text, prompt_tokens, completion_tokens = _generate_hf(messages, max_tokens, temperature, seed)
    else:
        text, prompt_tokens, completion_tokens = _generate_stub(messages, max_tokens)

    return JSONResponse(
        {
            "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "model": model_id,
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
            "created": int(time.time()),
        }
    )

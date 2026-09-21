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

Run:  NYAYA_SHIM_BACKEND=stub uv run --project /home/ss/projects/pravrudhi uvicorn \
        --app-dir <this dir> nyaya_local_shim:app --port 8099

Not a duplicate of `pravrudhi.models.llama_server.LlamaServer`: that class manages a llama.cpp/Docker
server for a GGUF-quantized model. The P2b checkpoint is HF safetensors (+ PEFT LoRA), not GGUF, so this
module serves it directly via `transformers` in-process instead of adding a GGUF-conversion step.
"""

from __future__ import annotations

import os
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


def _generate_hf(messages: list[dict[str, str]], max_tokens: int, temperature: float, seed: int | None) -> tuple[str, int, int]:
    import torch

    _load_hf()
    tok = _hf_state["tok"]
    model = _hf_state["model"]
    if seed is not None:
        torch.manual_seed(seed)
    chat_text = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tok(chat_text, return_tensors="pt", add_special_tokens=False).to(next(model.parameters()).device)
    n_prompt = inputs["input_ids"].shape[1]
    out = model.generate(
        **inputs,
        max_new_tokens=max_tokens,
        do_sample=temperature > 0,
        temperature=max(temperature, 1e-5),
        pad_token_id=tok.pad_token_id or tok.eos_token_id,
    )
    completion_ids = out[0][n_prompt:]
    text = tok.decode(completion_ids, skip_special_tokens=True)
    return text, n_prompt, len(completion_ids)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "backend": BACKEND}


@app.post("/chat/completions")
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

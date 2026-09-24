"""Minimal OpenAI-compatible chat client (stdlib only). Local llama.cpp by default; any endpoint by config."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from pydantic import BaseModel, ConfigDict


class ChatResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    text: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    wall_s: float
    finish_reason: str | None = None


class CompletionResult(BaseModel):
    """A raw-text `/completions` answer. `top_logprobs[i]` is the server's top-k `{token: logprob}` at generated
    position `i` -- position 0 is what a first-token classifier (the Nyaya element judge) reads.
    `backend_index` records which backend answered (0=primary, 1+=fallback, None if not tracked)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    text: str
    model: str
    top_logprobs: list[dict[str, float]]
    wall_s: float
    finish_reason: str | None = None
    backend_index: int | None = None


class HTTPStatusError(RuntimeError):
    """The server answered with an HTTP error. `status` lets a caller tell a transient 5xx/429 from a 4xx
    configuration fault; the message carries the server's own body, never the request's credentials."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class ChatClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080/v1",
        model: str = "local",
        api_key: str | None = None,
        timeout_s: int = 600,
        thinking: bool | None = False,
    ) -> None:
        self.thinking = thinking
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        seed: int | None = None,
        json_mode: bool = False,
        json_schema: dict[str, Any] | None = None,
    ) -> ChatResult:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if seed is not None:
            body["seed"] = seed
        if json_schema is not None:
            # llama.cpp compiles the schema to a grammar: the sampler cannot emit EOS before the array closes
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "out", "schema": json_schema}}
        elif json_mode:
            body["response_format"] = {"type": "json_object"}
        if self.thinking is not None:
            body["chat_template_kwargs"] = {"enable_thinking": self.thinking}
        req = urllib.request.Request(self.base_url + "/chat/completions", data=json.dumps(body).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            # A night once died on "HTTP Error 400: Bad Request" and nothing more: the server's explanation was in
            # the body the exception discards. Surface it, with the endpoint, so the cause is readable in the log.
            detail = exc.read().decode(errors="replace")[:600] if exc.fp else ""
            raise RuntimeError(f"{self.base_url}/chat/completions answered {exc.code}: {detail or exc.reason}") from exc
        usage = data.get("usage") or {}
        return ChatResult(
            text=data["choices"][0]["message"]["content"],
            model=str(data.get("model", self.model)),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            wall_s=time.monotonic() - t0,
            finish_reason=data["choices"][0].get("finish_reason"),
        )

    def _call(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """POST `body` (or GET when `None`) to `base_url + path`; an HTTP error surfaces the server's own body."""
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method="POST" if body is not None else "GET")
        req.add_header("Content-Type", "application/json")
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                out: dict[str, Any] = json.loads(resp.read().decode())
                return out
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:600] if exc.fp else ""
            raise HTTPStatusError(exc.code, f"{self.base_url}{path} answered {exc.code}: {detail or exc.reason}") from exc

    def complete(
        self, prompt: str, *, max_tokens: int = 16, temperature: float = 0.0, logprobs: int | None = None,
        stop: list[str] | None = None,
    ) -> CompletionResult:
        """Raw-text completion (no chat template) -- the shape a model fine-tuned on `prompt + completion` text
        was trained on. `logprobs=k` asks for the top-k alternatives at every generated position. `stop`, when
        given, is passed straight through to `/completions` as the OpenAI-compatible `stop` field (a list of
        strings; generation halts the moment any of them appears) -- a base/non-instruct model has no chat
        template or natural end-of-turn signal, so without an explicit stop it CAN continue generating past a
        single answer into unrelated follow-on text; P1 adds stops for base-model QA/LSI cells for this reason
        (a predicted risk this param defends against, not something P1 has observed happen -- as of this
        commit no base-model QA/LSI cell has been run at all, only an Adalat MCQ cell)."""
        body: dict[str, Any] = {"model": self.model, "prompt": prompt, "max_tokens": max_tokens, "temperature": temperature}
        if logprobs is not None:
            body["logprobs"] = logprobs
        if stop is not None:
            body["stop"] = stop
        t0 = time.monotonic()
        data = self._call("/completions", body)
        choice = data["choices"][0]
        top = (choice.get("logprobs") or {}).get("top_logprobs") or []
        return CompletionResult(
            text=choice["text"],
            model=str(data.get("model", self.model)),
            top_logprobs=[{str(k): float(v) for k, v in (pos or {}).items()} for pos in top],
            wall_s=time.monotonic() - t0,
            finish_reason=choice.get("finish_reason"),
        )

    def list_models(self) -> list[str]:
        """The model ids the server's own `/models` lists, in its order."""
        return [str(m["id"]) for m in self._call("/models").get("data", [])]

    def healthy(self) -> bool:
        try:
            with urllib.request.urlopen(self.base_url.removesuffix("/v1") + "/health", timeout=5) as r:
                return bool(r.status == 200)
        except (urllib.error.URLError, OSError, ValueError):
            return False

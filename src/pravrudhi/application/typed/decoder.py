"""TypedDecoder: one backend-agnostic protocol for a completions transport with first-token top-k
logprobs, plus the decision-field scoring primitive built on it (T1, docs/decisions/
TYPED-LAYER-PLAN-2026-09-24.md).

vLLM and SGLang both serve an OpenAI-compatible `/completions` endpoint that returns top-k logprobs per
generated position -- the exact shape `pravrudhi.models.openai_compat.CompletionResult` already carries, and
the exact shape `nyaya_judges.HouseJudge` already reads. Both adapters below are thin wrappers around that
one real transport shape (via an injected `complete` callable, same dependency-injection pattern HouseJudge
itself uses for testability) -- not a new protocol invented ahead of what either server actually does.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Protocol

from pravrudhi.application.typed.schema import Field, FieldKind
from pravrudhi.models.openai_compat import ChatClient, CompletionResult


class DecodeError(ValueError):
    """A field could not be decoded from the backend's reply -- never guessed. Mirrors
    nyaya_judges.JudgeOutputError's own house rule: an unreadable reply is a failure, not a default value."""


class TypedDecoder(Protocol):
    name: str

    def complete(
        self, prompt: str, *, max_tokens: int, temperature: float, logprobs: int | None
    ) -> CompletionResult: ...


_CompleteFn = Callable[..., CompletionResult]


def _client_complete(*, base_url: str, model: str | None, timeout_s: int, api_key: str | None) -> tuple[str, _CompleteFn]:
    """The shared transport both adapters below wrap: an OpenAI-compatible ChatClient, with the model id
    read from the server's own `/models` when none is configured (the same fallback HouseJudge.__init__
    uses). No multi-backend fallback here (nyaya_judges.HouseJudge's `fallback_urls`/`backend_used` feature,
    added after T1's design was written) -- a real, stated gap for a follow-up, not hidden: T1's scope is
    the typed-layer interface and its parity against a single fixed backend, not production resilience."""
    client = ChatClient(base_url=base_url, model=model or "", api_key=api_key, timeout_s=timeout_s)
    if not model:
        listed = client.list_models()
        if not listed:
            raise RuntimeError(f"{base_url}/models lists no model")
        client.model = listed[0]
    return client.model, client.complete


class VLLMDecoder:
    """vLLM's OpenAI-compatible `/completions` endpoint (the transport `nyaya_judges.HouseJudge` already
    uses in production, e.g. `http://127.0.0.1:8110/v1`)."""

    name = "vllm"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout_s: int = 60,
        api_key: str | None = None,
        complete: _CompleteFn | None = None,
    ) -> None:
        if complete is None:
            if base_url is None:
                raise ValueError("VLLMDecoder needs a base_url or an injected complete transport")
            self.model, complete = _client_complete(base_url=base_url, model=model, timeout_s=timeout_s, api_key=api_key)
        else:
            self.model = model or "injected"
        self._complete = complete

    def complete(self, prompt: str, *, max_tokens: int, temperature: float, logprobs: int | None) -> CompletionResult:
        return self._complete(prompt, max_tokens=max_tokens, temperature=temperature, logprobs=logprobs)


class SGLangDecoder:
    """SGLang's OpenAI-compatible `/completions` endpoint -- the same wire shape as vLLM's (top-k logprobs
    per generated position), so the same `score_decision` primitive below applies unchanged. A genuinely
    different SGLang-native path (its own `sgl.gen(..., choices=[...])` constrained-choice API) is a future
    addition behind this same protocol, not something T1 needs: the parity pass bar is measured against the
    vLLM backend only."""

    name = "sglang"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout_s: int = 60,
        api_key: str | None = None,
        complete: _CompleteFn | None = None,
    ) -> None:
        if complete is None:
            if base_url is None:
                raise ValueError("SGLangDecoder needs a base_url or an injected complete transport")
            self.model, complete = _client_complete(base_url=base_url, model=model, timeout_s=timeout_s, api_key=api_key)
        else:
            self.model = model or "injected"
        self._complete = complete

    def complete(self, prompt: str, *, max_tokens: int, temperature: float, logprobs: int | None) -> CompletionResult:
        return self._complete(prompt, max_tokens=max_tokens, temperature=temperature, logprobs=logprobs)


def score_decision(result: CompletionResult, field: Field) -> dict[str, float]:
    """Decide an enum/bool field by SCORING, never sampling (design principle 1): softmax, over the field's
    options, of each option's best-matching token variant's log-probability at the FIRST generated position
    -- generalizes `nyaya_judges.p_established_from_top_logprobs` from 2 options to N.

    Specialized to exactly HouseJudge's own two options and token variants, this reduces to the identical
    softmax HouseJudge already computes (see test_typed_decoder.py's own cross-check against
    `p_established_from_top_logprobs` across a range of logprob pairs, max abs difference <= 1e-12) -- the
    generic path here just subtracts the shared max first for numerical stability, which reorders the
    floating-point ops but not the mathematics.

    Raises DecodeError, never returns a guess, when no option's tokens appear in the top-k at all -- that is
    no evidence either way, and an even split would be a number the model never gave.
    """
    if field.kind not in (FieldKind.ENUM, FieldKind.BOOL):
        raise ValueError(f"score_decision is only for enum/bool fields, got {field.kind.value} ({field.name!r})")
    assert field.options is not None  # Field.__post_init__ guarantees this for ENUM/BOOL
    if not result.top_logprobs:
        raise DecodeError(f"{field.name}: the server returned no logprobs for the first token")
    top: Mapping[str, float] = result.top_logprobs[0]
    raw = {name: max((top[t] for t in variants if t in top), default=-math.inf) for name, variants in field.options.items()}
    if all(v == -math.inf for v in raw.values()):
        raise DecodeError(f"{field.name}: none of the option tokens are among the first token's top logprobs: {dict(top)}")
    m = max(v for v in raw.values() if v != -math.inf)
    exps = {k: (math.exp(v - m) if v != -math.inf else 0.0) for k, v in raw.items()}
    total = sum(exps.values())
    return {k: v / total for k, v in exps.items()}

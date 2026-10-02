"""OpenRouter bring-your-own-key (BYOK) as a verified, opt-in element judge (prabhasa-nyaya #304).

DESIGN (tests/test_openrouter_byok.py is the executable spec).

Scope. Production only, customers who bring their own OpenRouter key. Not the MVP, not the #306 head-to-head, and
never the operator's own key. A judge is "verified" the same way every `FrontierJudge` is: the model's quote is
checked mechanically against the facts downstream (`nyaya_agent`); this module adds no trust in the model.

Five properties, each pinned by a test:
 1. Key source is the customer's store ONLY. `panel.Vendor.key` reads the environment first, which on a hosted
    engine is the operator's key; that path would bill the operator for a customer's call. `resolve_key` reads
    `store.get("openrouter")` and nothing else, and a missing key raises `ByokKeyMissing` with no fallback.
 2. The key is a `credentials.Secret` end to end. It is `reveal()`ed once, into the Authorization header, and
    appears in no repr, exception, ledger record, log line or `Answer`. Every error text passes `redact`, and
    the 401/403/402 cases are mapped to typed errors that carry no upstream body.
 3. The model is pinned. The customer supplies an OpenRouter slug (`vendor/model`, validated by `MODEL_SLUG_RE`).
    The request sets `provider.allow_fallbacks=false` and `provider.data_collection="deny"` (legal text), and the
    response's `model` must equal the slug or the call is `ModelMismatch`, an error and never a verdict.
 4. Opt-in. `OpenRouterJudge` refuses to build unless `opted_in=True` (the org's explicit setting); the default
    judge stack never constructs one.
 5. Tenants do not mix. A judge is bound to one `CredentialStore`; two customers' judges send two different keys.

The transport is injected (`Transport`), so every test runs offline with fake keys and zero paid calls.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Mapping
from typing import Any

from pravrudhi.application import panel
from pravrudhi.application.credentials import CredentialStore, Secret, redact
from pravrudhi.application.nyaya_judges import FRONTIER_PROMPT, ElementJudgment, JudgeRequest, parse_frontier_reply

PROVIDER_ID = "openrouter"
BASE_URL = "https://openrouter.ai/api/v1"
MODEL_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._:-]*$")

#: (url, headers, json_body) -> (http_status, json_body). Injected; tests use a fake.
Transport = Callable[[str, Mapping[str, str], Mapping[str, Any]], tuple[int, Mapping[str, Any]]]
#: Receives one dict per call: model, status, tokens, wall_s, key_fingerprint. Never the key, never the prompt.
Ledger = Callable[[dict[str, Any]], None]


class OpenRouterByokError(RuntimeError):
    """Base class. Messages never contain the key or an upstream response body."""


class ByokKeyMissing(OpenRouterByokError): ...


class ByokKeyRejected(OpenRouterByokError): ...


class ByokCreditsExhausted(OpenRouterByokError): ...


class ModelMismatch(OpenRouterByokError): ...


class ByokNotOptedIn(OpenRouterByokError): ...


def resolve_key(store: CredentialStore) -> Secret:
    """The customer's key from their own store. Never the environment, never the operator's credential file."""
    secret = store.get(PROVIDER_ID)
    if secret is None:
        raise ByokKeyMissing("no OpenRouter key stored for this account")
    return secret


def key_fingerprint(secret: Secret) -> str:
    """8 hex chars of a salted hash: tells two keys apart in a ledger without revealing either."""
    return hashlib.sha256(b"pravrudhi-byok-fp:" + secret.reveal().encode()).hexdigest()[:8]


def ask(
    model: str, prompt: str, *, store: CredentialStore, transport: Transport, ledger: Ledger | None = None
) -> panel.Answer:
    if not MODEL_SLUG_RE.fullmatch(model) or len(model) > 200:
        raise OpenRouterByokError("malformed OpenRouter model slug")
    secret = resolve_key(store)
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "provider": {"allow_fallbacks": False, "data_collection": "deny"},
    }
    headers = {"Authorization": f"Bearer {secret.reveal()}", "Content-Type": "application/json"}
    started = time.monotonic()
    failure: str | None = None
    status, payload = 0, {}
    try:
        status, payload = transport(BASE_URL + "/chat/completions", headers, body)
    except Exception as e:  # noqa: BLE001 - re-raised below, outside the except block, with the key redacted
        failure = redact(f"{type(e).__name__}: {e}")
    if failure is not None:
        raise OpenRouterByokError(f"OpenRouter transport failure: {failure}")
    if status in (401, 403):
        raise ByokKeyRejected(f"OpenRouter rejected the stored key (HTTP {status})")
    if status == 402:
        raise ByokCreditsExhausted("OpenRouter account has no credits (HTTP 402)")
    if status != 200:
        raise OpenRouterByokError(f"OpenRouter returned HTTP {status}")
    resolved = payload.get("model")
    if resolved != model:
        raise ModelMismatch(redact(f"pinned {model!r}, OpenRouter served {str(resolved)[:80]!r}"))
    try:
        text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        text = None
    if not isinstance(text, str) or not text:
        raise OpenRouterByokError("OpenRouter returned no message content")
    usage = payload.get("usage") or {}
    tokens = usage.get("completion_tokens")
    wall = time.monotonic() - started
    if ledger is not None:
        ledger({"model": model, "status": status, "tokens": tokens, "wall_s": wall,
                "key_fingerprint": key_fingerprint(secret)})
    return panel.Answer(
        vendor=f"openrouter:{model}", interface="openrouter_byok", model=model, prompt_id="", text=text,
        wall_s=wall, tokens=tokens if isinstance(tokens, int) else None, error=None, resolved_model=resolved,
    )


class OpenRouterJudge:
    """A `nyaya_judges.Judge` over `ask`, bound to one customer's store."""

    def __init__(
        self, model: str, *, store: CredentialStore, transport: Transport, opted_in: bool = False,
        ledger: Ledger | None = None,
    ) -> None:
        if not opted_in:
            raise ByokNotOptedIn("OpenRouter BYOK is off unless the organisation opted in")
        if not MODEL_SLUG_RE.fullmatch(model):
            raise OpenRouterByokError("malformed OpenRouter model slug")
        self.model, self.name = model, f"openrouter:{model}"
        self._store, self._transport, self._ledger = store, transport, ledger

    def judge(self, request: JudgeRequest) -> ElementJudgment:
        prompt = FRONTIER_PROMPT.format(
            statute=request.statute,
            narrative=request.narrative or "(none)",
            element=request.element,
            facts="\n".join(f"[{fid}] {text}" for fid, text in request.facts),
        )
        answer = ask(self.model, prompt, store=self._store, transport=self._transport, ledger=self._ledger)
        return parse_frontier_reply(answer.text)

"""OpenRouter bring-your-own-key (BYOK) as a verified, opt-in element judge (prabhasa-nyaya #304).

DESIGN (slice 1: stubs only; tests/test_openrouter_byok.py is the executable spec and fails until slice 2).

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

import re
from collections.abc import Callable, Mapping
from typing import Any

from pravrudhi.application import panel
from pravrudhi.application.credentials import CredentialStore, Secret
from pravrudhi.application.nyaya_judges import ElementJudgment, JudgeRequest

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
    raise NotImplementedError("slice 2")


def key_fingerprint(secret: Secret) -> str:
    raise NotImplementedError("slice 2")


def ask(
    model: str, prompt: str, *, store: CredentialStore, transport: Transport, ledger: Ledger | None = None
) -> panel.Answer:
    raise NotImplementedError("slice 2")


class OpenRouterJudge:
    """A `nyaya_judges.Judge` over `ask`, bound to one customer's store."""

    def __init__(
        self, model: str, *, store: CredentialStore, transport: Transport, opted_in: bool = False,
        ledger: Ledger | None = None,
    ) -> None:
        raise NotImplementedError("slice 2")

    def judge(self, request: JudgeRequest) -> ElementJudgment:
        raise NotImplementedError("slice 2")

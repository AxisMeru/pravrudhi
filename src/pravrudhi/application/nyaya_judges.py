"""Element judges for the Nyaya agentic loop (`nyaya_agent`): one required element of one registry Contract,
judged against the user's facts, as `{status, p_established, fact_id, quote}`. A judge never supplies
character offsets: `nyaya_quote` locates the quote in the named fact itself (`offsets_source: "system"`).

A judge only *proposes*. Every judgment it returns goes through `nyaya_quote`'s mechanical verbatim check
before the agent lets it near the Lean wire, whichever judge produced it -- so neither judge below is trusted
with its own quote.

* `HouseJudge` -- the fine-tuned element judge served by an OpenAI-compatible vLLM server (the 5090's is
  `http://127.0.0.1:8110/v1`). Mirrors prabhasa-nyaya's element-judge harness exactly: the raw-text training
  prompt (`Statute:` truncated to 600 characters / `Scenario:` / `Element to judge:` / `Available facts:` /
  `Answer:`, no chat template), and `p_established` = the two-way softmax of the FIRST generated token's
  `max(" established", "established")` vs `max(" not", "not")` logprobs, a token absent from the top-k read
  as -inf. The status is `p >= tau`, never the greedy text. The model was trained to answer
  `established <fact_id>:<start>:<end>` and never to emit quote text, so its evidential claim is taken at FACT
  granularity: the fact id from the greedy completion, the quote = that fact's whole text
  (`quote_source: "whole_fact"`). Its offsets are ignored (they were often past the fact's end: live
  `F1:0:103` for an 87-character fact). A fact id that is not among the facts has no quote, and is rejected.
* `FrontierJudge` -- any `panel` vendor, asked for `{status, fact_id, quote}` as JSON (never offsets). It has
  no calibrated probability, so it reports `p_established` as exactly 1.0 or 0.0 (never inside a refer
  band), and its quote is passed through verbatim (`quote_source: "model"`) for the check to accept or refuse.
"""

from __future__ import annotations

import json
import math
import re
import urllib.error
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pravrudhi.application import panel
from pravrudhi.models.openai_compat import ChatClient, CompletionResult

if TYPE_CHECKING:
    from pravrudhi.application.credentials import CredentialStore

Status = Literal["established", "not_established"]


class JudgeOutputError(ValueError):
    """A judge's reply could not be read as a judgment. The agent retries, then records the element failed --
    it never guesses a status from an unreadable reply."""


@dataclass(frozen=True)
class JudgeRequest:
    """One (contract, element) pair to judge. `facts` are `(fact_id, text)` in prompt order; `statute` is the
    statute text this attempt shows the judge (the agent picks which, see `nyaya_agent`)."""

    contract_id: str
    element: str
    is_denial: bool
    statute: str
    narrative: str
    facts: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ElementJudgment:
    status: Status
    p_established: float
    fact_id: str | None = None
    quote: str | None = None
    #: "model" -- words the judge wrote; "whole_fact" -- the named fact's full text (house judge, see module doc).
    quote_source: Literal["model", "whole_fact"] | None = None
    raw: str = ""
    #: Which backend answered: primary URL (index 0) or fallback (index 1+), or None if not tracked
    backend_used: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class Judge(Protocol):
    name: str

    def judge(self, request: JudgeRequest) -> ElementJudgment: ...


# -- house judge -------------------------------------------------------------------------------------------

_EST_TOKENS = (" established", "established")
_NOT_TOKENS = (" not", "not")
#: `established <fact_id>[:<start>:<end>]` -- only the fact id is read; the offsets are the model's and unused.
_HOUSE_FACT = re.compile(r"^\s*established\s+([^\s:]+)")


def build_house_prompt(request: JudgeRequest, *, statute_chars: int) -> str:
    """The element judge's training prompt, byte for byte (no few-shots)."""
    facts_block = "\n".join(f"[{fid}] {text}" for fid, text in request.facts)
    return (
        f"Statute: {request.statute[:statute_chars]}\n"
        f"Scenario: {request.narrative}\n"
        f"Element to judge: {request.element}\n"
        f"Available facts:\n{facts_block}\n"
        "Answer:"
    )


def p_established_from_top_logprobs(top: Mapping[str, float]) -> float:
    """Two-way softmax of the first token's established-vs-not logprobs. Raises when neither token is in the
    top-k: that is no evidence either way, and 0.5 would be a number the model never gave."""
    est = max((top[t] for t in _EST_TOKENS if t in top), default=-math.inf)
    neg = max((top[t] for t in _NOT_TOKENS if t in top), default=-math.inf)
    if est == -math.inf and neg == -math.inf:
        raise JudgeOutputError(f"neither ' established' nor ' not' among the first token's top logprobs: {dict(top)}")
    if neg == -math.inf:
        return 1.0
    if est == -math.inf:
        return 0.0
    return 1.0 / (1.0 + math.exp(neg - est))


def parse_house_fact_id(text: str) -> str | None:
    """`established <fact_id>[:<start>:<end>]` -> `fact_id`; anything else -> `None`. Offsets are ignored."""
    m = _HOUSE_FACT.match(text)
    return m.group(1) if m else None


class HouseJudge:
    """The house element judge over an OpenAI-compatible `/completions` endpoint. `complete` is the transport
    (prompt -> `CompletionResult`); by default a `ChatClient` against `base_url`, with the model id read from
    the server's own `/models` when none is configured.

    Supports fallback to additional base URLs on connection/timeout errors (primary serverless -> local 5090 fallback).
    The result's `backend_used` field records which URL answered."""

    name = "house"

    def __init__(
        self,
        *,
        tau: float,
        statute_chars: int,
        base_url: str | None = None,
        model: str | None = None,
        max_tokens: int = 30,
        top_logprobs: int = 20,
        timeout_s: int = 60,
        api_key: str | None = None,
        fallback_urls: list[str] | None = None,
        complete: Callable[[str], CompletionResult] | None = None,
    ) -> None:
        self.tau = tau
        self.statute_chars = statute_chars
        self.primary_base_url = base_url
        self.fallback_urls = fallback_urls or []
        self.api_key = api_key

        if complete is None:
            if base_url is None:
                raise ValueError("HouseJudge needs a base_url or a complete transport")

            # Create clients for primary and fallback URLs
            self.clients: list[ChatClient] = []
            for url in [base_url] + self.fallback_urls:
                client = ChatClient(base_url=url, model=model or "", api_key=api_key, timeout_s=timeout_s)
                self.clients.append(client)

            if not model:
                # Try to list models from primary client
                listed = self.clients[0].list_models()
                if not listed:
                    raise RuntimeError(f"{base_url}/models lists no model")
                model = listed[0]

            self.model = model

            def _complete_with_fallback(prompt: str) -> CompletionResult:
                """Try primary, then fallback URLs on connection/timeout errors."""
                last_error = None
                for i, client in enumerate(self.clients):
                    try:
                        client.model = self.model
                        result = client.complete(prompt, max_tokens=max_tokens, temperature=0.0, logprobs=top_logprobs)
                        # Record which backend answered (0=primary, 1+=fallback)
                        # Use object.__setattr__ since result is frozen
                        object.__setattr__(result, "backend_index", i)
                        return result
                    except (OSError, RuntimeError, urllib.error.HTTPError, urllib.error.URLError) as e:
                        last_error = e
                        if i < len(self.clients) - 1:
                            # Fallback available, try next
                            continue
                        else:
                            # Last fallback failed
                            raise RuntimeError(f"All judge backends failed. Last error: {last_error}") from e
                raise RuntimeError(f"All judge backends failed. Last error: {last_error}")

            complete = _complete_with_fallback
        else:
            self.model = model or "injected"

        self._complete = complete

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any], *, tau: float) -> HouseJudge:
        return cls(
            tau=tau,
            statute_chars=int(cfg["statute_chars"]),
            base_url=str(cfg["base_url"]),
            model=cfg.get("model") or None,
            max_tokens=int(cfg["max_tokens"]),
            top_logprobs=int(cfg["top_logprobs"]),
            timeout_s=int(cfg["timeout_s"]),
        )

    @classmethod
    def from_config_with_fallback(cls, cfg: Mapping[str, Any], *, tau: float) -> HouseJudge:
        """Load config with optional fallback URLs and API key.

        Config keys:
        - base_url (required): primary judge endpoint
        - base_urls_fallback (optional): list of fallback endpoints [local 5090, etc.]
        - api_key (optional): Bearer token for serverless endpoints
        - Other keys as in from_config: statute_chars, model, max_tokens, top_logprobs, timeout_s
        """
        return cls(
            tau=tau,
            statute_chars=int(cfg["statute_chars"]),
            base_url=str(cfg["base_url"]),
            model=cfg.get("model") or None,
            max_tokens=int(cfg["max_tokens"]),
            top_logprobs=int(cfg["top_logprobs"]),
            timeout_s=int(cfg["timeout_s"]),
            api_key=cfg.get("api_key") or None,
            fallback_urls=cfg.get("base_urls_fallback") or [],
        )

    def judge(self, request: JudgeRequest) -> ElementJudgment:
        res = self._complete(build_house_prompt(request, statute_chars=self.statute_chars))
        if not res.top_logprobs:
            raise JudgeOutputError("the server returned no logprobs for the first token")
        p = p_established_from_top_logprobs(res.top_logprobs[0])
        backend_idx = res.backend_index
        if p < self.tau:
            return ElementJudgment("not_established", p, raw=res.text, backend_used=backend_idx)
        fact_id = parse_house_fact_id(res.text)
        if fact_id is None:
            return ElementJudgment("established", p, raw=res.text, backend_used=backend_idx)
        # Fact granularity (module doc): the claim is "this fact, whole". A fact id not among the facts gets no
        # quote -- never a nearest-match fact -- so the quote check rejects it as unknown.
        text = dict(request.facts).get(fact_id)
        return ElementJudgment(
            "established", p, fact_id, text, "whole_fact" if text is not None else None, raw=res.text, backend_used=backend_idx
        )


# -- frontier judge ----------------------------------------------------------------------------------------

FRONTIER_PROMPT = (
    "You are checking one element of an offence against a set of facts. Decide only from the facts listed.\n\n"
    "STATUTE:\n{statute}\n\nSCENARIO:\n{narrative}\n\nELEMENT TO JUDGE:\n{element}\n\nFACTS:\n{facts}\n\n"
    "Reply with ONE JSON object and nothing else.\n"
    'If a single fact establishes the element: {{"status": "established", "fact_id": "<id>", '
    '"quote": "<exact words copied from that fact>"}}\n'
    'Otherwise: {{"status": "not_established"}}\n'
    "The quote must be copied character for character; it will be checked mechanically against the fact."
)

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def parse_frontier_reply(text: str) -> ElementJudgment:
    m = _JSON_OBJECT.search(text)
    if not m:
        raise JudgeOutputError(f"no JSON object in the reply: {text[-200:]!r}")
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise JudgeOutputError(f"unparseable JSON in the reply: {e}") from e
    if not isinstance(obj, dict):
        raise JudgeOutputError("the reply's JSON is not an object")
    status = obj.get("status")
    if status == "not_established":
        return ElementJudgment("not_established", 0.0, raw=text)
    if status != "established":
        raise JudgeOutputError(f"status {status!r} is neither established nor not_established")
    fid, quote = obj.get("fact_id"), obj.get("quote")
    if quote is not None and not isinstance(quote, str):
        raise JudgeOutputError(f"quote {quote!r} is not a string")
    # Any `start`/`end` a model volunteers is ignored: offsets are the system's (nyaya_quote).
    return ElementJudgment(
        "established", 1.0, str(fid) if fid is not None else None, quote, "model" if quote is not None else None, raw=text
    )


class FrontierJudge:
    """Any `panel` vendor as an element judge, through `panel.ask_vendor` (or an injected `ask_fn`)."""

    def __init__(
        self,
        vendor: panel.Vendor,
        *,
        ask_fn: panel.AskFn | None = None,
        root: Path | None = None,
        store: CredentialStore | None = None,
    ) -> None:
        self.vendor = vendor
        self.name = f"frontier:{vendor.id}"
        if ask_fn is None:

            def ask_fn(v: panel.Vendor, p: str) -> panel.Answer:
                return panel.ask_vendor(v, p, root=root, store=store)

        self._ask = ask_fn

    def judge(self, request: JudgeRequest) -> ElementJudgment:
        prompt = FRONTIER_PROMPT.format(
            statute=request.statute,
            narrative=request.narrative or "(none)",
            element=request.element,
            facts="\n".join(f"[{fid}] {text}" for fid, text in request.facts),
        )
        return parse_frontier_reply(self._ask(self.vendor, prompt).text)

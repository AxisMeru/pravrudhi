"""Element judges for the Nyaya agentic loop (`nyaya_agent`): one required element of one registry Contract,
judged against the user's facts, as `{status, p_established, fact_id, quote, start, end}`.

A judge only *proposes*. Every judgment it returns goes through `nyaya_quote`'s mechanical span check before
the agent lets it near the Lean wire, whichever judge produced it -- so neither judge below is trusted with
its own quote.

* `HouseJudge` -- the fine-tuned element judge served by an OpenAI-compatible vLLM server (the 5090's is
  `http://127.0.0.1:8110/v1`). Mirrors prabhasa-nyaya's element-judge harness exactly: the raw-text training
  prompt (`Statute:` truncated to 600 characters / `Scenario:` / `Element to judge:` / `Available facts:` /
  `Answer:`, no chat template), and `p_established` = the two-way softmax of the FIRST generated token's
  `max(" established", "established")` vs `max(" not", "not")` logprobs, a token absent from the top-k read
  as -inf. The status is `p >= tau`, never the greedy text; the span comes from the same greedy completion
  (`established F1:0:120`), and the judge reports the offsets it was given without clipping them into range.
* `FrontierJudge` -- any `panel` vendor, asked for a JSON judgment. It has no calibrated probability, so it
  reports `p_established` as exactly 1.0 or 0.0 (never inside a refer band), and its quote is passed through
  verbatim for the quote check to accept or refuse.
"""

from __future__ import annotations

import json
import math
import re
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
    start: int | None = None
    end: int | None = None
    raw: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class Judge(Protocol):
    name: str

    def judge(self, request: JudgeRequest) -> ElementJudgment: ...


# -- house judge -------------------------------------------------------------------------------------------

_EST_TOKENS = (" established", "established")
_NOT_TOKENS = (" not", "not")
_HOUSE_SPAN = re.compile(r"^\s*established\s+(\S+?):(\d+):(\d+)")


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


def parse_house_span(text: str) -> tuple[str, int, int] | None:
    """`established <fact_id>:<start>:<end>` -> `(fact_id, start, end)`; anything else -> `None`."""
    m = _HOUSE_SPAN.match(text)
    return (m.group(1), int(m.group(2)), int(m.group(3))) if m else None


class HouseJudge:
    """The house element judge over an OpenAI-compatible `/completions` endpoint. `complete` is the transport
    (prompt -> `CompletionResult`); by default a `ChatClient` against `base_url`, with the model id read from
    the server's own `/models` when none is configured."""

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
        complete: Callable[[str], CompletionResult] | None = None,
    ) -> None:
        self.tau = tau
        self.statute_chars = statute_chars
        if complete is None:
            if base_url is None:
                raise ValueError("HouseJudge needs a base_url or a complete transport")
            client = ChatClient(base_url=base_url, model=model or "", timeout_s=timeout_s)
            if not model:
                listed = client.list_models()
                if not listed:
                    raise RuntimeError(f"{base_url}/models lists no model")
                client.model = listed[0]
            self.model = client.model

            def _complete(prompt: str) -> CompletionResult:
                return client.complete(prompt, max_tokens=max_tokens, temperature=0.0, logprobs=top_logprobs)

            complete = _complete
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

    def judge(self, request: JudgeRequest) -> ElementJudgment:
        res = self._complete(build_house_prompt(request, statute_chars=self.statute_chars))
        if not res.top_logprobs:
            raise JudgeOutputError("the server returned no logprobs for the first token")
        p = p_established_from_top_logprobs(res.top_logprobs[0])
        if p < self.tau:
            return ElementJudgment("not_established", p, raw=res.text)
        span = parse_house_span(res.text)
        if span is None:
            return ElementJudgment("established", p, raw=res.text)
        fact_id, start, end = span
        text = dict(request.facts).get(fact_id)
        # The quote is the slice ONLY when the offsets are really inside the fact; otherwise no quote, so the
        # quote check refuses it -- Python's clipping slice would otherwise turn 0:63 into "the whole fact".
        quote = text[start:end] if text is not None and 0 <= start < end <= len(text) else None
        return ElementJudgment("established", p, fact_id, quote, start, end, raw=res.text)


# -- frontier judge ----------------------------------------------------------------------------------------

FRONTIER_PROMPT = (
    "You are checking one element of an offence against a set of facts. Decide only from the facts listed.\n\n"
    "STATUTE:\n{statute}\n\nSCENARIO:\n{narrative}\n\nELEMENT TO JUDGE:\n{element}\n\nFACTS:\n{facts}\n\n"
    "Reply with ONE JSON object and nothing else.\n"
    'If a single fact establishes the element: {{"status": "established", "fact_id": "<id>", '
    '"quote": "<exact words copied from that fact>", "start": <character offset where the quote starts in that '
    'fact>, "end": <offset one past its last character>}}\n'
    'Otherwise: {{"status": "not_established"}}\n'
    "The quote must be copied character for character; it will be checked mechanically against the fact."
)

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def _opt_int(v: object) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise JudgeOutputError(f"offset {v!r} is not an integer")
    return v


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
    return ElementJudgment(
        "established",
        1.0,
        str(fid) if fid is not None else None,
        str(quote) if quote is not None else None,
        _opt_int(obj.get("start")),
        _opt_int(obj.get("end")),
        raw=text,
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

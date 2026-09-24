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
import os
import re
import urllib.error
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pravrudhi.application import panel
from pravrudhi.models.openai_compat import ChatClient, CompletionResult, HTTPStatusError

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

    # -- AND-gate fields (AndGateJudge, below). All None/False when no second judge ran (single-judge config,
    # or the primary already rejected so the second was never asked): reading them costs nothing when there
    # is nothing to read.
    #: The second judge's `name` (e.g. "house" for a 32B HouseJudge instance), else None.
    second_judge: str | None = None
    second_status: Status | None = None
    p_established_second: float | None = None
    tau_primary: float | None = None
    tau_second: float | None = None
    backend_used_second: int | None = None
    #: The second was never asked because the primary already said not_established (cost saved).
    second_skipped: bool = False
    #: Why the AND gate did not run both judges to a clean AND: "primary_not_established", or
    #: "second_unavailable: <exception>" when a configured second judge errored (fail closed, never a 4xx).
    second_skip_reason: str | None = None
    #: The second judge's own fact_id, kept for the record even though the primary's span is what is used.
    second_fact_id: str | None = None
    fact_id_disagreement: bool = False
    #: Which judge is why the element is not established: "primary" (it rejected outright), "second" (the
    #: primary accepted but the second rejected, or the second was configured and unavailable), or None when
    #: established (or when there is no second judge and the primary alone decided).
    vetoed_by: Literal["primary", "second"] | None = None

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

            # One client per backend. The bearer key is the primary's (the RunPod endpoint); a fallback is the
            # operator's own vLLM and never receives it. Each backend answers under its own model id.
            self.clients: list[ChatClient] = [
                ChatClient(base_url=url, model="", api_key=api_key if i == 0 else None, timeout_s=timeout_s)
                for i, url in enumerate([base_url, *self.fallback_urls])
            ]
            self._models: list[str | None] = [model or None] + [None] * len(self.fallback_urls)

            def _model_for(i: int) -> str:
                if self._models[i] is None:
                    listed = self.clients[i].list_models()
                    if not listed:
                        raise RuntimeError(f"{self.clients[i].base_url}/models lists no model")
                    self._models[i] = listed[0]
                resolved = self._models[i]
                assert resolved is not None
                return resolved

            self.model = _model_for(0)

            def _transient(e: BaseException) -> bool:
                """Only these move to the next backend; a 4xx (bad key, unknown model, bad request) surfaces."""
                if isinstance(e, HTTPStatusError):
                    return e.status >= 500 or e.status == 429
                return isinstance(e, (TimeoutError, urllib.error.URLError, ConnectionError))

            def _complete_with_fallback(prompt: str) -> CompletionResult:
                for i, client in enumerate(self.clients):
                    try:
                        client.model = _model_for(i)
                        result = client.complete(prompt, max_tokens=max_tokens, temperature=0.0, logprobs=top_logprobs)
                    except Exception as e:  # noqa: BLE001 -- classified just below, re-raised unless transient
                        if not _transient(e) or i == len(self.clients) - 1:
                            raise RuntimeError(f"judge backend {i} ({client.base_url}) failed: {e}") from e
                        continue
                    object.__setattr__(result, "backend_index", i)  # the result is frozen
                    return result
                raise RuntimeError("no judge backend configured")

            complete = _complete_with_fallback
        else:
            self.model = model or "injected"

        self._complete = complete

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any], *, tau: float, api_key_env: str = "NYAYA_HOUSE_JUDGE_API_KEY") -> HouseJudge:
        """Load config with optional fallback URLs and API key.

        Supports same config keys as from_config_with_fallback, with graceful fallback
        to None for optional fields. Reads the `api_key_env` env var (default NYAYA_HOUSE_JUDGE_API_KEY; the
        AND-gate's second judge passes NYAYA_SECOND_JUDGE_API_KEY here so the two keys are never confused).
        """
        # Read api_key from env var first, then config
        api_key = os.environ.get(api_key_env) or cfg.get("api_key") or None

        return cls(
            tau=tau,
            statute_chars=int(cfg["statute_chars"]),
            base_url=str(cfg["base_url"]),
            model=cfg.get("model") or None,
            max_tokens=int(cfg["max_tokens"]),
            top_logprobs=int(cfg["top_logprobs"]),
            timeout_s=int(cfg["timeout_s"]),
            api_key=api_key,
            fallback_urls=cfg.get("base_urls_fallback") or [],
        )

    @classmethod
    def from_config_with_fallback(
        cls, cfg: Mapping[str, Any], *, tau: float, api_key_env: str = "NYAYA_HOUSE_JUDGE_API_KEY"
    ) -> HouseJudge:
        """Load config with optional fallback URLs and API key.

        Config keys:
        - base_url (required): primary judge endpoint
        - base_urls_fallback (optional): list of fallback endpoints [local 5090, etc.]
        - api_key (optional): Bearer token for serverless endpoints
        - Other keys as in from_config: statute_chars, model, max_tokens, top_logprobs, timeout_s

        Environment variables (override config):
        - `api_key_env` (default NYAYA_HOUSE_JUDGE_API_KEY): Bearer token for serverless endpoints
        """
        # Read api_key from config or env var (env var takes precedence)
        api_key = os.environ.get(api_key_env) or cfg.get("api_key") or None

        return cls(
            tau=tau,
            statute_chars=int(cfg["statute_chars"]),
            base_url=str(cfg["base_url"]),
            model=cfg.get("model") or None,
            max_tokens=int(cfg["max_tokens"]),
            top_logprobs=int(cfg["top_logprobs"]),
            timeout_s=int(cfg["timeout_s"]),
            api_key=api_key,
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


# -- AND-gate judge ------------------------------------------------------------------------------------------


def _config_fault_status(e: BaseException) -> int | None:
    """The HTTP status of a 4xx (other than 429) anywhere in `e`'s cause chain, else None. Mirrors
    `nyaya_agent._judge_config_fault` exactly; duplicated rather than imported because judges never import the
    agent (the dependency runs the other way -- nyaya_agent imports nyaya_judges)."""
    seen: BaseException | None = e
    while seen is not None:
        if isinstance(seen, HTTPStatusError) and 400 <= seen.status < 500 and seen.status != 429:
            return seen.status
        seen = seen.__cause__
    return None


class AndGateJudge:
    """Config C: AND(primary @ its own tau, second @ its own tau) -- e.g. the 4B house judge @ 0.74 AND the
    32B QLoRA judge @ 0.97. Both slots are ordinary `Judge`s (in production, two `HouseJudge`s: same prompt
    template, same first-token logprob code, module doc above -- this class never re-derives either).

    The second is asked ONLY when the primary already says established: the majority of elements the primary
    already rejects never reach the (expensive) second judge, and that skip is recorded rather than silently
    saving the call. An element is established iff BOTH say established -- each already applying its OWN tau
    inside its own `judge()`, so this class only ANDs the two `status` fields, never recomputes a threshold.

    fact_id / quote / quote_source always come from the PRIMARY: the Lean check and the mechanical quote check
    (`nyaya_quote`) decide on that span, never on the second's. A second-reported fact_id that disagrees is
    recorded (`second_fact_id`, `fact_id_disagreement`), never silently dropped and never substituted in.

    A second judge that errors after being asked (all its own transient/fallback retries exhausted, see
    `HouseJudge`'s own primary/fallback rules) never falls back to scoring the primary alone: the element is
    NOT established (fail closed), and `second_skip_reason` / `vetoed_by="second"` record why. The one
    exception is a 4xx from the second (its OWN configuration fault -- bad key, unknown model): that surfaces
    exactly as the primary's would, via `RuntimeError`, so `nyaya_agent`'s `_judge_config_fault` stops the run
    instead of reading a broken second judge as an ordinary veto."""

    def __init__(
        self,
        primary: Judge,
        second: Judge,
        *,
        tau_primary: float | None = None,
        tau_second: float | None = None,
        name: str = "and_gate",
    ) -> None:
        self.primary = primary
        self.second = second
        #: Recorded on every judgment for the audit trail; defaults to the wrapped judge's own `.tau` when it
        #: has one (a real HouseJudge does), else None (an injected test double need not have one).
        self.tau_primary = tau_primary if tau_primary is not None else getattr(primary, "tau", None)
        self.tau_second = tau_second if tau_second is not None else getattr(second, "tau", None)
        self.name = name

    @classmethod
    def from_config(
        cls,
        house_cfg: Mapping[str, Any],
        second_cfg: Mapping[str, Any],
        *,
        tau: float,
        second_api_key_env: str = "NYAYA_SECOND_JUDGE_API_KEY",
        name: str = "and_gate",
    ) -> AndGateJudge:
        """Both slots as `HouseJudge`s: the primary from `house_cfg` at `tau` (the existing `tau:` key), the
        second from `second_cfg` at ITS OWN `tau` (a distinct threshold, e.g. 0.97 for the 32B)."""
        primary = HouseJudge.from_config(house_cfg, tau=tau)
        second_tau = float(second_cfg["tau"])
        second = HouseJudge.from_config(second_cfg, tau=second_tau, api_key_env=second_api_key_env)
        return cls(primary, second, tau_primary=tau, tau_second=second_tau, name=name)

    def judge(self, request: JudgeRequest) -> ElementJudgment:
        p = self.primary.judge(request)
        if p.status != "established":
            # Cost saved: the second is never asked once the primary has already rejected the element.
            return replace(
                p,
                tau_primary=self.tau_primary,
                tau_second=self.tau_second,
                second_skipped=True,
                second_skip_reason="primary_not_established",
                vetoed_by="primary",
            )
        try:
            s = self.second.judge(request)
        except Exception as e:  # noqa: BLE001 -- classified just below, re-raised unless it should fail closed
            if _config_fault_status(e) is not None:
                raise  # a second-judge configuration fault surfaces like the primary's would (never fail closed)
            return replace(
                p,
                status="not_established",
                tau_primary=self.tau_primary,
                tau_second=self.tau_second,
                second_judge=getattr(self.second, "name", None),
                vetoed_by="second",
                second_skip_reason=f"second_unavailable: {type(e).__name__}: {e}"[:400],
            )
        established = s.status == "established"  # p.status == "established" already, checked above
        disagreement = bool(p.fact_id and s.fact_id and p.fact_id != s.fact_id)
        return ElementJudgment(
            status="established" if established else "not_established",
            p_established=p.p_established,
            fact_id=p.fact_id,
            quote=p.quote,
            quote_source=p.quote_source,
            raw=p.raw,
            backend_used=p.backend_used,
            second_judge=getattr(self.second, "name", None),
            second_status=s.status,
            p_established_second=s.p_established,
            tau_primary=self.tau_primary,
            tau_second=self.tau_second,
            backend_used_second=s.backend_used,
            second_fact_id=s.fact_id,
            fact_id_disagreement=disagreement,
            vetoed_by=None if established else "second",
        )

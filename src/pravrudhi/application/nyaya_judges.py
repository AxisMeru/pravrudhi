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
    #: Set by `nyaya_agent._judge_element` when `contract_id` is NOT in `AgentConfig.validated_contracts`
    #: (Lead-2, 2026-09-26; allowlist inverted from a deny-list, issue #36): the contract-level safety gate
    #: already refuses to let this element's judgment reach the user as a real PROOF/DENIAL regardless of
    #: what the second judge would have said, so
    #: `AndGateJudge` reads this to skip the second judge's call entirely -- saving its latency (~90s
    #: observed) and serverless spend on every unvalidated-contract call, never changing the validated-
    #: contract path. Default False so every OTHER caller (a test double, a request built by hand) is
    #: unaffected.
    skip_second: bool = False


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

    # -- Gate 1 fields (Gate1Judge, below). All None when Gate 1 is not configured, or was never asked
    # because neither judge above established the element (the same cost-saving convention the second judge
    # itself uses -- there is nothing to entailment-check yet).
    #: max(entailment - contradiction) over the element's disjuncts (see `split_disjuncts`), else None.
    gate1_score: float | None = None
    #: What was actually scored -- the disjuncts `split_disjuncts` found, or `[element_desc]` when it found
    #: none. None only when Gate 1 was never asked at all.
    gate1_disjuncts: list[str] | None = None
    #: Set (to `f"gate1_unavailable: {type(e).__name__}: {e}"`) when Gate 1 was asked but its model failed to
    #: answer (never loaded, or errored on this call) -- fail closed, same as `second_skip_reason`'s
    #: `"second_unavailable: ..."` prefix: the element is NOT established and this is a REFER, never a silent
    #: not-established that could drive a false DENIAL. None whenever Gate 1 actually answered (pass or fail).
    gate1_skip_reason: str | None = None
    #: Why the element is not established: "primary" (it rejected outright), "second" (the primary accepted
    #: but the second rejected, or the second was configured and unavailable), "gate1" (both judges
    #: established but the entailment check failed, or the Gate 1 model itself was unavailable), or None
    #: when established (or when a given gate is not configured and so never has an opinion).
    vetoed_by: Literal["primary", "second", "gate1"] | None = None
    #: Which Gate 1 MODE produced a `vetoed_by="gate1"` veto -- "not_entailed" (the entailment mode, the
    #: original design) or "contradiction" (Arm C, GATE1-ARM-C-2026-09-26.md: a REFER fired because the fact
    #: explicitly contradicts the element, with no entailment requirement at all). None whenever `vetoed_by
    #: != "gate1"`, or Gate 1 was never configured/asked.
    gate1_veto_kind: Literal["not_entailed", "contradiction"] | None = None

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


#: The training data (element_judgment_combined.jsonl) carries the scenario narrative as a fact row under
#: this reserved id, but the judge was TRAINED on prompts that drop it from `Available facts:` -- it is
#: already the `Scenario:` line, and showing it again as `[F_narrative] ...` collapses the model's
#: established-probability (measured live: ~0.99 -> ~0.08 on affected elements). A caller whose
#: `request.facts` still carries this row (e.g. built straight off that jsonl schema, as batch/eval scoring
#: is) must not have it doubled here.
_NARRATIVE_FACT_ID = "F_narrative"


def build_house_prompt(request: JudgeRequest, *, statute_chars: int) -> str:
    """The element judge's training prompt, byte for byte (no few-shots). `request.facts` may carry a
    `F_narrative` row (see `_NARRATIVE_FACT_ID`); it is excluded from `Available facts:` here, never shown
    twice with `Scenario:`."""
    facts_block = "\n".join(f"[{fid}] {text}" for fid, text in request.facts if fid != _NARRATIVE_FACT_ID)
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
    """`established <fact_id>[:<start>:<end>]` -> `fact_id`; anything else -> `None`. Offsets are ignored.

    `F_narrative` is never a legitimate answer -- `build_house_prompt` never shows it in `Available facts:`
    (above), so a completion naming it anyway (a caller whose `request.facts` still carries that row, or a
    hallucination) is read as no fact id at all, exactly like an unparseable completion. This is the single
    choke point both `HouseJudge` and `TypedHouseJudge` read a fact id through, so the guard covers both."""
    m = _HOUSE_FACT.match(text)
    if m is None or m.group(1) == _NARRATIVE_FACT_ID:
        return None
    return m.group(1)


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

            # Lazy: `_model_for` is NOT called here. Resolving the model id means an HTTP round-trip
            # (`/v1/models`) when `model` wasn't given; calling it eagerly, at construction time, meant an
            # unreachable or not-yet-warm judge crashed `NyayaAgent.house()` itself with an unhandled error
            # -- before any request was even attempted, and (for the second judge specifically) before
            # AndGateJudge's own try/except around the per-request call ever got a chance to convert the
            # failure into a fail-closed REFER (found 2026-09-25 validating the config-C switch-on smoke
            # test against a real, briefly-unreachable second judge). `_model_for(0)` now runs on first
            # actual use instead -- see the `model` property below and `_complete_with_fallback`'s own
            # per-request `_model_for(i)` call, which already re-resolves lazily and was never the problem.
            self._model_for: Callable[[int], str] | None = _model_for

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
            self._model_for = None
            self._injected_model = model or "injected"

        self._complete = complete

    @property
    def model(self) -> str:
        """The resolved model id. Lazy: for a real (non-injected) transport, this calls `/v1/models` on
        FIRST ACCESS, not at construction -- see the note in `__init__`. Cached after the first successful
        resolution (`self._models[0]`), same as every other backend's `_model_for` call."""
        if self._model_for is not None:
            return self._model_for(0)
        return self._injected_model

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
        if request.skip_second:
            # Cost saved: `nyaya_agent._run_contract`'s own validated_contracts allowlist gate already refuses
            # to let ANY element of this contract reach the user as a real PROOF/DENIAL, so calling the second
            # judge here could only ever be discarded -- never asked, never vetoes, the primary's own
            # established call stands (audit still shows what the primary alone decided).
            return replace(
                p,
                tau_primary=self.tau_primary,
                tau_second=self.tau_second,
                second_skipped=True,
                second_skip_reason="contract_not_validated",
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


# -- Gate 1: NLI entailment check ---------------------------------------------------------------------------

#: The two disjunct-split patterns frozen on heldout_v1 (Track-C, GATE1-DISJUNCT-FIX-2026-09-26.md,
#: develop_gate1_disjunct_fix.py sha 7897c381) and re-verified unchanged on the full 377/225 eval
#: (run_configc_gate1_v2_eval.py sha 7ed02c49). Known gap, documented not fixed: mishandles a
#: SUBJECT-EMBEDDED disjunction ("the consequence [intended, or known to be likely,] is X"), producing two
#: grammatically broken halves -- it still matches by shape and degrades gracefully (never flips a correct
#: not-established to a false established), it just doesn't rescue that one shape (see `split_disjuncts`'s
#: own docstring).
#:
#: PAREN-AWARE, 2026-09-26 (Track-C, GATE1-PAREN-SPLIT-BUG-2026-09-26.md): the original version matched
#: "or" inside a statutory citation parenthetical (e.g. bns85 el1's "(s.86(a) or (b))") as if it were a
#: real top-level disjunction, producing paren-unbalanced garbage on 4/30 disjunctive-matched elements
#: across the 26-contract registry. `split_disjuncts` below now masks any "or" sitting at parenthesis
#: depth > 0 before applying these two patterns, so they only ever see a genuine top-level "or" -- these
#: two regexes themselves are UNCHANGED (still applied to a same-length masked copy of the text, matched
#: spans sliced back out of the ORIGINAL text), so every already-correct split is untouched.
_GATE1_SUFFIX3 = re.compile(r"^(?P<pre>.*?),\s*or\s+(?P<b>[^,]+),\s*(?P<suf>.*)$")
_GATE1_SIMPLE2 = re.compile(r"^(?P<pre>.*?),?\s+or\s+(?P<suf>.*)$")


def _mask_nested_or(text: str) -> str:
    """Same-length copy of `text` with every whole-word "or" that sits inside parentheses (paren depth > 0
    at that position) replaced by two NUL characters -- invisible to `_GATE1_SUFFIX3`/`_GATE1_SIMPLE2`
    (neither can match a NUL byte as "or"), while every other character, including all parentheses
    themselves, is left in place so span positions/offsets are identical to the original text."""
    depth = 0
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif (
            depth > 0
            and text[i : i + 2] == "or"
            and (i == 0 or not text[i - 1].isalnum())
            and (i + 2 == n or not text[i + 2].isalnum())
        ):
            out[i] = out[i + 1] = "\0"
            i += 2
            continue
        i += 1
    return "".join(out)


def split_disjuncts(element_desc: str) -> list[str]:
    """`element_desc` split into its disjuncts, each scored separately by `gate1_check` (max over all of
    them): `"A, or B, suffix"` -> `["A, suffix", "B, suffix"]`; `"A, or B"` -> `["A", "B"]`; anything else ->
    `[element_desc]` unchanged (no TOP-LEVEL disjunction found -- either none at all, an "or" that only
    ever appears inside parentheses (a citation, not a disjunction -- see `_mask_nested_or` above), or the
    SUBJECT-EMBEDDED shape this heuristic cannot parse, see the module-level comment above). Matches against
    a `_mask_nested_or`-masked copy of `element_desc`, then slices the ORIGINAL text using the match's own
    spans -- the masking only decides WHERE a split may happen, never what text ends up in the disjuncts."""
    masked = _mask_nested_or(element_desc)
    m = _GATE1_SUFFIX3.match(masked)
    if m:
        pre = element_desc[: m.end("pre")]
        b = element_desc[m.start("b") : m.end("b")]
        suf = element_desc[m.start("suf") :]
        return [f"{pre}, {suf}", f"{b}, {suf}"]
    m = _GATE1_SIMPLE2.match(masked)
    if m:
        pre = element_desc[: m.end("pre")]
        suf = element_desc[m.start("suf") :]
        return [pre, suf]
    return [element_desc]


#: Sentence splitter for Arm C (contradiction_veto mode) -- splits on sentence-ending punctuation followed
#: by whitespace + a capital letter. Documented simplification (GATE1-ARM-C-2026-09-26.md): does not handle
#: abbreviations specially; every calibration/eval fact this was validated against is plain declarative
#: English, not abbreviation-heavy. Falls back to the whole text as one "sentence" when nothing splits.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def split_sentences(fact_text: str) -> list[str]:
    parts = [s.strip() for s in _SENTENCE_SPLIT.split(fact_text.strip()) if s.strip()]
    return parts or [fact_text.strip()]


GATE1_THRESHOLD_DEFAULT = 0.04074102267622948
#: Arm C's own frozen threshold (GATE1-ARM-C-2026-09-26.md): the LOWEST tau_c such that the block rate on
#: positives was <=5% on the tune half of a 60-item calibration set (disjoint from 377/225, the 13 OOS
#: negation items, and heldout_v1). Test-half: leak 0.0% (0/30), block 6.7% (2/30). Only used when
#: `Gate1Judge.mode == "contradiction_veto"` -- inert in the default "entailment" mode.
GATE1_TAU_C_DEFAULT = 0.321158230304718
GATE1_MODEL_DEFAULT = "MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli"
#: Pinned by commit sha, never a branch/tag (a model card edit or a weights update on `main` must never
#: silently change what a running deployment scores with). Confirmed (Lead-2, 2026-09-26) to be the exact
#: revision `GATE1_THRESHOLD_DEFAULT` above was calibrated against, not just "whatever main resolved to on
#: the day this was pinned": the upstream repo has not moved since 2024-04-11, and the 5090's own HF cache
#: -- what Track-C's Gate 1 evals actually ran against -- holds exactly this one snapshot.
GATE1_MODEL_REVISION_DEFAULT = "6f5cf0a2b59cabb106aca4c287eed12e357e90eb"


@dataclass(frozen=True)
class Gate1Result:
    score: float
    passed: bool
    disjuncts: list[str]
    model: str


def gate1_check(
    fact_text: str, element_desc: str, *, score_fn: Callable[[str, str], float], threshold: float, model: str,
) -> Gate1Result:
    """`score_fn(fact_text, hypothesis_text) -> entailment - contradiction` is the only thing that touches a
    real model (`Gate1NLIModel.score_one`, below, in production; a test double in tests) -- this function is
    the pure disjunct-max logic Track-C froze (`run_configc_gate1_v2_eval.py`'s own `gate1_score`), unchanged:
    split `element_desc` into its disjuncts, score each as its own hypothesis, take the max."""
    disjuncts = split_disjuncts(element_desc)
    score = max(score_fn(fact_text, d) for d in disjuncts)
    return Gate1Result(score=score, passed=score >= threshold, disjuncts=disjuncts, model=model)


@dataclass(frozen=True)
class Gate1ContradictionResult:
    score: float
    vetoed: bool
    disjuncts: list[str]
    sentences: list[str]
    model: str


def gate1_contradiction_check(
    fact_text: str, element_desc: str, *, score_fn: Callable[[str, str], float], tau_c: float, model: str,
) -> Gate1ContradictionResult:
    """Arm C (GATE1-ARM-C-2026-09-26.md): `score_fn(sentence, hypothesis_text) -> p_contradiction` is the
    only thing that touches a real model. Splits `fact_text` into sentences and `element_desc` into its
    disjuncts (the SAME `split_disjuncts` the entailment mode uses), scores every (sentence, disjunct) pair,
    and takes the max -- vetoed iff that max clears `tau_c`. No entailment requirement at all: this is a
    narrower check than `gate1_check`, scoped to explicit contradiction only (Lead-2's diagnosis, 2026-09-26:
    the entailment mode's calibration trained it to do element discrimination, the JUDGE's job, where NLI is
    weak -- Arm C is scoped to the (d)-class gap alone, catching a fact that contradicts its element, never
    asking whether the fact affirmatively supports it)."""
    disjuncts = split_disjuncts(element_desc)
    sentences = split_sentences(fact_text)
    score = max(score_fn(s, d) for s in sentences for d in disjuncts)
    return Gate1ContradictionResult(
        score=score, vetoed=score >= tau_c, disjuncts=disjuncts, sentences=sentences, model=model,
    )


class Gate1NLIModel:
    """Loads the real DeBERTa-v3 NLI model once (lazily, on first `score_one` call, never at construction --
    a deployment that never enables Gate 1 must never pay for it) and exposes the `score_fn` shape
    `gate1_check` needs. Runs in-process on whatever device is available (CPU on the product tier -- Lead-2's
    explicit deviation from Track-C's own sidecar recommendation, GATE1-PRODUCT-WIRING-SPEC-2026-09-26.md
    §6: measured 110ms/element on 2 vCPU is small next to the judge calls it follows, which take seconds, and
    a sidecar is one more failure mode)."""

    def __init__(
        self, *, model_id: str = GATE1_MODEL_DEFAULT, revision: str = GATE1_MODEL_REVISION_DEFAULT,
    ) -> None:
        self.model_id = model_id
        self.revision = revision
        self._tokenizer: Any = None
        self._model: Any = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        import torch
        from huggingface_hub import snapshot_download
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        # Only the safetensors weight format, never the duplicate .bin the upstream repo also carries
        # (749MB vs ~380MB) -- GATE1-PRODUCT-WIRING-SPEC-2026-09-26.md §7.
        local_dir = snapshot_download(
            self.model_id, revision=self.revision, allow_patterns=["*.safetensors", "*.json", "*.model"],
        )
        self._tokenizer = AutoTokenizer.from_pretrained(local_dir)
        self._model = AutoModelForSequenceClassification.from_pretrained(local_dir, dtype=torch.float32)
        self._model.eval()

    def _raw_probs(self, premise: str, hypothesis_text: str) -> dict[str, float]:
        """(entailment, neutral, contradiction) probabilities, keyed by the model's own label names (read
        from `model.config.id2label`, never a hardcoded index order) -- the single real model call both
        `score_one` (entailment mode) and `score_contradiction_one` (Arm C, contradiction_veto mode) build
        on, so there is exactly one place that ever talks to the model."""
        self._ensure_loaded()
        import torch

        hyp = f"It is true that: {hypothesis_text}"
        enc = self._tokenizer(premise, hyp, return_tensors="pt", truncation=True, max_length=512)
        with torch.no_grad():
            logits = self._model(**enc).logits[0]
        probs = torch.softmax(logits, dim=-1).tolist()
        labels = self._model.config.id2label
        return {labels[i].lower(): probs[i] for i in range(len(probs))}

    def score_one(self, fact_text: str, hypothesis_text: str) -> float:
        """`entailment - contradiction`, the exact shape `run_configc_gate1_v2_eval.py`'s own `score_one`
        computes -- the entailment-mode `score_fn` `gate1_check` expects."""
        p = self._raw_probs(fact_text, hypothesis_text)
        return float(p.get("entailment", 0.0) - p.get("contradiction", 0.0))

    def score_contradiction_one(self, premise: str, hypothesis_text: str) -> float:
        """Raw `p_contradiction` -- the contradiction_veto-mode `score_fn` `gate1_contradiction_check`
        expects (Arm C, GATE1-ARM-C-2026-09-26.md: no entailment requirement, contradiction only). `premise`
        is a single FACT SENTENCE here, not the whole fact -- `gate1_contradiction_check` calls this once
        per (sentence, disjunct) pair."""
        p = self._raw_probs(premise, hypothesis_text)
        return float(p.get("contradiction", 0.0))


class Gate1ScoreModel(Protocol):
    """What `Gate1Judge` needs from a model in the default "entailment" mode -- `Gate1NLIModel` in
    production, a lightweight test double in tests (mirrors `Judge`'s own role: a Protocol, never a
    concrete-class requirement, so a test never needs a real model just to satisfy a type check)."""

    model_id: str

    def score_one(self, fact_text: str, hypothesis_text: str) -> float: ...


class Gate1ContradictionScoreModel(Protocol):
    """What `Gate1Judge` needs from a model in `mode="contradiction_veto"` (Arm C) -- a SEPARATE Protocol
    from `Gate1ScoreModel` so an entailment-mode test double never needs to implement
    `score_contradiction_one`, and vice versa. `Gate1NLIModel` satisfies both."""

    model_id: str

    def score_contradiction_one(self, premise: str, hypothesis_text: str) -> float: ...


class Gate1Judge:
    """Wraps ANY `Judge` (a bare `HouseJudge`, or `AndGateJudge` for config C) as a THIRD gate, composing the
    same way the second judge does: Gate 1 is asked ONLY when the wrapped judge already says established --
    never on an element neither judge (nor Gate 1) has anything to check yet.

    Two MODES, mutually exclusive, selected at construction (`mode=`), never both at once:
    - `"entailment"` (default): the original design, `gate1_check` -- REFER unless the fact affirmatively
      entails the element. Measured cost: -19%/-9% coverage on the 377/225 set for no demonstrated
      false-prove benefit there (GATE1-PRODUCT-WIRING-SPEC-2026-09-26.md).
    - `"contradiction_veto"` (Arm C, GATE1-ARM-C-2026-09-26.md): `gate1_contradiction_check` -- REFER only
      if the fact explicitly CONTRADICTS the element, no entailment requirement. Narrower job, much cheaper
      coverage cost (89/377 vs config C alone's 109/377), 12/13 catch on the out-of-sample negation set.
      Known weakness, measured not glossed over: narrative-framed denials leak at 33-40%
      (GATE1-ARM-C-ROBUSTNESS-2026-09-26.md) -- RJ-Bench v2.2 reports Arm C's catch rate on real
      narrative-framed denials separately, per Lead-2's instruction.

    An established call that fails Gate 1 (either mode) does NOT become an ordinary not-established fact:
    `vetoed_by="gate1"` and `gate1_veto_kind` (`"not_entailed"` or `"contradiction"`) are set so
    `nyaya_agent._run_contract` can surface this as REFER_TO_LAWYER with a mode-specific reason
    (`gate1_not_entailed` or `gate1_contradiction`), a reviewable outcome, rather than an indistinguishable
    coverage loss (GATE1-PRODUCT-WIRING-SPEC-2026-09-26.md §4). A Gate 1 model that fails to load or errors
    on this call fails the SAME way in both modes: NOT established, `gate1_skip_reason` records why, and the
    agent surfaces a REFER (`gate1_unavailable`) -- never a silent pass, never a silent not-established that
    could drive a false DENIAL."""

    def __init__(
        self, inner: Judge, model: Gate1ScoreModel | Gate1ContradictionScoreModel, *,
        mode: Literal["entailment", "contradiction_veto"] = "entailment",
        threshold: float = GATE1_THRESHOLD_DEFAULT, tau_c: float = GATE1_TAU_C_DEFAULT,
        name: str | None = None,
    ) -> None:
        self.inner = inner
        self.model = model
        self.mode = mode
        self.threshold = threshold
        self.tau_c = tau_c
        self.name: str = name or str(getattr(inner, "name", "gate1"))

    def judge(self, request: JudgeRequest) -> ElementJudgment:
        judgment = self.inner.judge(request)
        if judgment.status != "established":
            return judgment  # nothing established yet -- Gate 1 has nothing to check (cost saved)
        fact_text = dict(request.facts).get(judgment.fact_id) if judgment.fact_id else None
        if fact_text is None:
            return judgment  # no resolvable fact id -- the quote check downstream will reject this anyway

        if self.mode == "contradiction_veto":
            try:
                c_result = gate1_contradiction_check(
                    fact_text, request.element, score_fn=self.model.score_contradiction_one,  # type: ignore[union-attr]
                    tau_c=self.tau_c, model=self.model.model_id,
                )
            except Exception as e:  # noqa: BLE001 -- fail closed, mirrors AndGateJudge's second-judge-error path
                return replace(
                    judgment, status="not_established", vetoed_by="gate1",
                    gate1_skip_reason=f"gate1_unavailable: {type(e).__name__}: {e}"[:400],
                )
            if not c_result.vetoed:
                return replace(judgment, gate1_score=c_result.score, gate1_disjuncts=c_result.disjuncts)
            return replace(
                judgment, status="not_established", vetoed_by="gate1", gate1_veto_kind="contradiction",
                gate1_score=c_result.score, gate1_disjuncts=c_result.disjuncts,
            )

        try:
            result = gate1_check(
                fact_text, request.element, score_fn=self.model.score_one,  # type: ignore[union-attr]
                threshold=self.threshold, model=self.model.model_id,
            )
        except Exception as e:  # noqa: BLE001 -- fail closed, mirrors AndGateJudge's second-judge-error path
            return replace(
                judgment, status="not_established", vetoed_by="gate1",
                gate1_skip_reason=f"gate1_unavailable: {type(e).__name__}: {e}"[:400],
            )
        if result.passed:
            return replace(judgment, gate1_score=result.score, gate1_disjuncts=result.disjuncts)
        return replace(
            judgment, status="not_established", vetoed_by="gate1", gate1_veto_kind="not_entailed",
            gate1_score=result.score, gate1_disjuncts=result.disjuncts,
        )

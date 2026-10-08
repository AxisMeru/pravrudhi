"""The partner API (L4, `docs/decisions/LEG-PLAN-2026-09-23.md`): `/api/v1`.

First endpoint only: `POST /api/v1/analyse-facts`, a thin HTTP wrapper over `application.nyaya_agent.
NyayaAgent` -- facts in, one `ContractResult` per selected registry contract out, `quote_source` visible per
element in the response (not just the final PROOF/DENIAL/ABSTAIN/REFER_TO_LAWYER verdict), so a partner's UI
can show a user which fact grounded each element rather than asking them to trust an opaque outcome.

Tenancy (`application/tenancy.py`: orgs, memberships, org-scoped API keys) now lives alongside
`analyse-facts` in this router -- `POST /api/v1/orgs` and the key-management routes below it. These are
deliberately gated with `tenancy.require_tenancy_admin`, not `roles.require_admin`: this deployment can run
with `PRAVRUDHI_AUTH` left at its `disabled` default (the 5090 demo-mode config), where `roles.role_of(None)`
resolves an anonymous caller to `ADMIN` by construction -- correct for the engine-improvement surfaces
`roles.ADMIN_ONLY` gates, and exactly wrong for provisioning a partner's first live API key, which must never
be mintable by an anonymous caller regardless of this deployment's auth mode (reviewer 1, fix-before-merge:
demonstrated with a real, unoverridden `TestClient` that the `roles.require_admin` version returned 200 to
POST /api/v1/orgs with no identity at all). `analyse-facts` itself still rides the optional Supabase session
identity every other user-facing nyaya route uses (`CurrentUserDep`), unchanged, so its existing anonymous
and Supabase-authenticated response SHAPE stays byte-identical; an org API key is now also accepted there as
an *alternative* identity (`tenancy.principal_from_headers`), but today only for one purpose -- QUEUE.md
2026-09-27's "per-leg scores for authenticated callers only" (see `ElementResultOut`'s field doc and
`analyse_facts_ep`'s `authenticated` gate): a caller with either a verified session or a valid key sees the
second judge's per-element fields, an anonymous caller never does. Scoping the partner resources the plan
still lists (matters, documents, verify-citations, research, draft, audit export) to an org once they exist
is the next slice of this card, not this one.

**Reviewer 1's rejection of the first version (8344594), addressed here.** On the self-hosted 5090
deployment, `identity.guard_boot` only refuses an unauthenticated deploy on VERCEL/RENDER -- so this route is
reachable by anyone, with no other gate. Two limits exist purely because of that, not as a secondary
defense on top of real auth:

* A per-client-IP fixed-window rate limit (`RateLimiter`), 429 + `Retry-After` when exceeded.
* A global cap on in-flight `agent.run()` calls (`ConcurrencyLimiter`) -- there is one vLLM server on one
  5090 behind this; a request past the cap gets 503 immediately, never queued, because there is no queue
  depth that makes waiting sensible for an anonymous caller.

Both read from `configs/partner_api.yaml` (`load_partner_api_config`), never hardcoded. Request shape is
also capped (`AnalyseFactsRequest`'s `Field` constraints) so a caller cannot ask for all 23 registry
contracts, or for an unbounded number/size of facts, in one anonymous request -- `contract_ids` is required
(1-5 ids), where the first version defaulted to "every contract the registry knows" when omitted. The
response no longer carries `audit_path`, an absolute server filesystem path that had no business reaching
an anonymous caller; `run_id` is kept so the same run can be looked up once an authenticated audit surface
exists. `nyaya_agent.BinaryShaMismatch` -- a `RuntimeError`, not an `OSError`, so it fell through the first
version's error mapping to a bare 500 -- is now mapped to 503 like every other score-binary failure.
"""

from __future__ import annotations

import contextvars
import functools
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import subprocess
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, computed_field, field_validator
from starlette.responses import JSONResponse

from pravrudhi import __version__
from pravrudhi.api.errors import AGENT_AT_CAPACITY, AGENT_UNAVAILABLE, coded_503
from pravrudhi.api.identity import CurrentUserDep, User
from pravrudhi.application import audit, citation_status, tenancy
from pravrudhi.application import nyaya_lean_registry as reg
from pravrudhi.application.citation_wording import citation_note
from pravrudhi.application.config_files import config_file
from pravrudhi.application.jobs import JobStore
from pravrudhi.application.judge_endpoint_state import Fetch, JudgeOfflineCheck
from pravrudhi.application.nyaya_agent import (
    RETENTION_NOTICE,
    BinaryShaMismatch,
    ContractReason,
    ElementStatus,
    JudgeMisconfigured,
    NyayaAgent,
    Outcome,
)
from pravrudhi.application.nyaya_judges import SecondJudgeCircuitBreaker
from pravrudhi.application.nyaya_quote import Reason as QuoteCheck
from pravrudhi.application.service_window import ServiceWindow
from pravrudhi.application.statute_citations import contract_citations
from pravrudhi.application.verify import exists_in_index, index_coverage
from pravrudhi.application.verify import verify as verify_citation

CONFIG_PATH = Path("configs") / "partner_api.yaml"


class ServiceWindowOut(BaseModel):
    timezone: str
    open: str
    close: str
    enforced: bool
    open_now: bool
    next_open_utc: str


class JudgeStateOut(BaseModel):
    state: Literal["ready", "warming", "unavailable", "unknown"]
    checked_at: str | None


class ServiceStatusOut(BaseModel):
    engine_version: str
    service_window: ServiceWindowOut | None
    judge: JudgeStateOut


@dataclass(frozen=True)
class PartnerApiConfig:
    rate_limit_per_minute: int
    max_concurrent: int
    trust_proxy_header: bool
    #: Hard cap on distinct rate-limiter keys held at once (reviewer 1, fix-before-merge) -- real IP
    #: rotation must not grow the table without bound. Defaults to 10,000 when the config file predates
    #: this field, so an already-deployed config doesn't need a same-day edit to keep working.
    rate_limit_max_keys: int = 10_000
    #: Reviewer 2: X-Forwarded-For is entirely client-controlled, so trusting it with no allowlist makes
    #: the IP-keyed rate limit free for any caller to bypass (spoof a fresh header value per request).
    #: Required, non-empty, whenever trust_proxy_header is True -- see __post_init__.
    trusted_proxies: tuple[str, ...] = ()
    #: Reviewer 1 (post-signoff hardening): per-client-IP limit on the tenancy provisioning routes (create
    #: org, create/list/revoke keys) -- distinct from, and much lower than, `rate_limit_per_minute` above,
    #: because a wrong `X-Pravrudhi-Tenancy-Secret` guess is exactly the kind of call this exists to slow
    #: down, not a normal partner workload. Defaults to 10 when the config file predates this field.
    provision_rate_limit_per_minute: int = 10
    #: Issue #35: how long the second-judge circuit breaker stays open after a real timeout/connection
    #: failure, shared across every request this process serves (see `_get_state`'s own singleton). Defaults
    #: to 60s when the config file predates this field -- the breaker itself is always on (this only tunes
    #: the window), since it can never change PROOF/DENIAL, only how cheaply the fail-closed REFER path is
    #: reached (issue #38's own guard).
    second_judge_circuit_breaker_ttl_s: float = 60.0
    #: Off by default -- this is a public, unauthenticated endpoint (module docstring), so the second
    #: judge's own p_established/tau/skip-reason/logit-distance/refer-band/unavailable fields (already
    #: computed by AndGateJudge, already on ElementResult) are never in a response unless BOTH this
    #: deployment-level gate AND the caller's own `?debug_second_judge=true` are set -- a caller alone
    #: can never turn this on for a deployment that hasn't opted in (Lead-2, 2026-09-25 config-C smoke:
    #: diagnosing a primary/second disagreement needed a direct Python call because the HTTP response
    #: silently dropped every one of these fields).
    debug_second_judge_fields_enabled: bool = False
    #: #311: before admitting a call, read each RunPod judge endpoint's /health and answer 503
    #: `judges_offline` at once when a needed judge is PARKED (no worker, nothing queued or in progress), so no job is
    #: queued for the next warm. A failed or unreadable check proceeds as before (never a false "offline"). Limit: an
    #: idle scale-from-zero endpoint reads the same, so turn this off if a judge goes back to scale-from-zero.
    judge_health_check: bool = False
    #: Seconds a judge-state read is reused.
    judge_health_ttl_s: float = 5.0
    #: Seconds after which /status stops reporting the last analyse-facts judge observation and says "unknown".
    judge_seen_ttl_s: float = 600.0
    #: Serverless judges scale from zero (a cold start takes ~2-3 min). A judge failure with no fresh "ready"
    #: reading is reported as `judges_warming` (503 + Retry-After) for this many seconds after the FIRST such
    #: failure, then as a plain `judge_unavailable`. 0 disables the distinction (the pre-#164 behaviour).
    judge_warm_grace_s: float = 0.0
    #: Retry-After (seconds) sent with a `judges_warming` 503.
    judge_warm_retry_s: float = 30.0
    #: Hours the hosted engine is open. Always reported by GET /api/v1/status when set; analyse-facts refuses
    #: outside it only when `service_window_enforce` is also true, so a local `pravrudhi app` install is
    #: never locked out at night by a hosted-deployment setting.
    service_window: ServiceWindow | None = None
    service_window_enforce: bool = False
    #: Async jobs (#146): seconds a FINISHED job stays collectable, and how many unfinished jobs one key may
    #: hold at once.
    job_retention_s: float = 3600.0
    job_max_unfinished_per_key: int = 8
    #: Audit rows (#148) older than this are dropped on write and never served.
    audit_retention_s: float = 90 * 86400.0
    #: `POST /verify-citations` (#302): how many lookups may run at once (non-blocking: an extra one is a 503) and the
    #: wall-clock bound on one lookup against the case index (an exceeded bound is a 503, never a hung worker).
    verify_max_concurrent: int = 2
    verify_timeout_s: float = 5.0

    def __post_init__(self) -> None:
        if self.trust_proxy_header and not self.trusted_proxies:
            raise ValueError(
                "trust_proxy_header is set but trusted_proxies is empty -- refusing to start: an "
                "X-Forwarded-For header trusted with no allowlist of who may set it is a free rate-limit "
                "bypass for any caller"
            )


def load_partner_api_config(root: Path) -> PartnerApiConfig:
    body = yaml.safe_load(config_file(Path(root), "partner_api.yaml").read_text()) or {}
    # NYAYA_DEBUG_SECOND_JUDGE_FIELDS: an env override (this file's yaml has none of its own precedent for
    # one, but ad-hoc diagnostic on/off is exactly the case the rest of this codebase's NYAYA_* env
    # overrides exist for -- flip it for one deployment without a same-day yaml edit + redeploy). Same
    # truthy-string convention as models.hosted.opted_in().
    debug_env = os.environ.get("NYAYA_DEBUG_SECOND_JUDGE_FIELDS", "").strip().lower()
    debug_second_judge_fields_enabled = (
        debug_env in ("1", "true", "yes") if debug_env else bool(body.get("debug_second_judge_fields_enabled", False))
    )
    window_body = body.get("service_window")
    enforce_env = os.environ.get("PRAVRUDHI_SERVICE_WINDOW_ENFORCE", "").strip().lower()
    enforce = (
        enforce_env in ("1", "true", "yes") if enforce_env else bool((window_body or {}).get("enforce", False))
    )
    return PartnerApiConfig(
        service_window=ServiceWindow.from_config(window_body) if window_body else None,
        service_window_enforce=enforce,
        rate_limit_per_minute=int(body["rate_limit_per_minute"]),
        max_concurrent=int(body["max_concurrent"]),
        trust_proxy_header=bool(body["trust_proxy_header"]),
        rate_limit_max_keys=int(body.get("rate_limit_max_keys", 10_000)),
        trusted_proxies=tuple(body.get("trusted_proxies") or ()),
        provision_rate_limit_per_minute=int(body.get("provision_rate_limit_per_minute", 10)),
        second_judge_circuit_breaker_ttl_s=float(body.get("second_judge_circuit_breaker_ttl_s", 60.0)),
        debug_second_judge_fields_enabled=debug_second_judge_fields_enabled,
        judge_seen_ttl_s=float(body.get("judge_seen_ttl_s", 600.0)),
        judge_health_check=bool(body.get("judge_health_check", False)),
        judge_health_ttl_s=float(body.get("judge_health_ttl_s", 5.0)),
        judge_warm_grace_s=float(body.get("judge_warm_grace_s", 0.0)),
        judge_warm_retry_s=float(body.get("judge_warm_retry_s", 30.0)),
        job_retention_s=float(body.get("job_retention_s", 3600.0)),
        job_max_unfinished_per_key=int(body.get("job_max_unfinished_per_key", 8)),
        audit_retention_s=float(body.get("audit_retention_s", 90 * 86400.0)),
        verify_max_concurrent=int(body.get("verify_max_concurrent", 2)),
        verify_timeout_s=float(body.get("verify_timeout_s", 5.0)),
    )


_RATE_LIMIT_HEADER_DOCS: dict[str, Any] = {
    "X-RateLimit-Limit": {"description": "Calls per minute this API key may make.", "schema": {"type": "integer"}},
    "X-RateLimit-Remaining": {"description": "Calls left in the current one-minute window after this one.",
                              "schema": {"type": "integer"}},
    "X-RateLimit-Reset": {"description": "Seconds until the current window ends and the count resets.",
                          "schema": {"type": "integer"}},
}
_RETRY_AFTER_DOC: dict[str, Any] = {"description": "Seconds to wait before retrying.", "schema": {"type": "integer"}}


class RateLimiter:
    """Fixed-window per-key counter: a key gets `per_minute` calls to `allow` inside any 60-second window,
    then every further call is refused until the window rolls over. A window's count resets at its own
    start, not a rolling average -- simple, and enough to stop one caller from burning the 5090's GPU
    time. Thread-safe: `allow` is called from FastAPI's threadpool, concurrently, by design.

    Bounded (reviewer 1, fix-before-merge on the first version of this class): unbounded IP rotation would
    otherwise mean an unbounded table. The eviction rule is deliberately asymmetric, after reviewer 2
    caught a real bypass in the first attempt at this: evicting an existing, still-current-window key to
    make room for a NEW one lets an attacker fill a target's quota, then flood fresh keys past the cap to
    evict the target's entry and reset their throttle for free. So: every `allow()` call always sweeps
    stale (window-expired) entries off the LRU end first (unconditionally, not just when over budget); an
    EXISTING key is then updated in place regardless of table size (it costs no new slot); a genuinely NEW
    key is admitted only if the table has room after the stale sweep -- if the table is still at `max_keys`
    with nothing stale to reclaim, the new key is refused outright (429/503 to that caller), never admitted
    by evicting a live entry. A key is moved to the most-recently-used end on every access.
    """

    def __init__(
        self, per_minute: int, *, max_keys: int = 10_000, now: Callable[[], float] = time.monotonic
    ) -> None:
        self._per_minute = per_minute
        self._max_keys = max_keys
        self._now = now
        self._lock = threading.Lock()
        self._windows: OrderedDict[str, tuple[int, int]] = OrderedDict()  # key -> (window_start_minute, count)

    def _evict_stale_locked(self, current_window: int) -> None:
        # Oldest-accessed first (that's where staleness concentrates in practice) -- NEVER evicts a
        # current-window entry, staleness is the only eviction criterion here.
        while self._windows:
            oldest_key, (start, _count) = next(iter(self._windows.items()))
            if start == current_window:
                break
            del self._windows[oldest_key]

    def allow(self, key: str) -> bool:
        window = int(self._now() // 60)
        with self._lock:
            self._evict_stale_locked(window)
            if key in self._windows:
                start, count = self._windows.pop(key)
                if start != window:
                    start, count = window, 0
            elif len(self._windows) >= self._max_keys:
                # A genuinely new key with no room and nothing stale to reclaim: refused, never admitted
                # by evicting someone else's live entry.
                return False
            else:
                start, count = window, 0
            if count >= self._per_minute:
                self._windows[key] = (start, count)  # re-insert at MRU end even when refusing
                return False
            self._windows[key] = (start, count + 1)
            return True

    def retry_after_seconds(self) -> int:
        """Seconds until the current window rolls over -- always a whole window's worth or less, never
        computed per-key (a caller who is refused does not need to know anyone else's window state)."""
        return 60 - int(self._now() % 60)


class ConcurrencyLimiter:
    """A non-blocking cap on in-flight calls: `acquire` either succeeds immediately or fails immediately --
    never blocks waiting for room, because queuing an anonymous caller behind someone else's GPU call is
    exactly the unbounded-wait this exists to prevent."""

    def __init__(self, max_concurrent: int) -> None:
        self._sem = threading.Semaphore(max_concurrent)

    def acquire(self) -> bool:
        return self._sem.acquire(blocking=False)

    def release(self) -> None:
        self._sem.release()


class AgentLike(Protocol):
    def run(
        self,
        facts: list[str],
        *,
        narrative: str = "",
        contract_ids: list[str] | None = None,
        sections: list[str] | None = None,
        client_data: bool = True,
        proceeding_posture: str | None = None,
    ) -> Any: ...


@dataclass(frozen=True)
class _Admitted:
    cfg: PartnerApiConfig
    concurrency: ConcurrencyLimiter
    authenticated: bool


class VerifyCitationRequest(BaseModel):
    citation: str = Field(min_length=1, max_length=500)
    #: Optional (#832 E1). Absent or null = existence only (`IN_INDEX`, never `VERIFIED`). A present quote is checked as before; a
    #: quote that is empty or only whitespace is a 422, never an existence check.
    quote: str | None = Field(default=None, min_length=1, max_length=4000)

    @field_validator("quote")
    @classmethod
    def _quote_not_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("quote must not be empty or only whitespace; omit it to check existence only")
        return v


class VerifyCitationResponse(BaseModel):
    result: str
    note: str
    #: #533 (preview): the product wording. `status` is one of verified, quote_not_found, not_in_index, conflict, malformed;
    #: `label` is the text to show verbatim; `verified` is true only for `result` VERIFIED (a resolved key AND the exact
    #: quote); `preview` is true while the check is a preview. None of the labels says a citation is fake or invalid.
    status: str
    label: str
    verified: bool
    preview: bool
    #: #832 E2: what the index holds and what a citation can resolve to (courts held vs courts resolvable), so a client reads
    #: "Supreme Court judgments only" from the engine. Null only if the index could not be summarised.
    coverage: dict[str, Any] | None = None


_VERIFY_NOTES = {
    "VERIFIED": "The citation resolves to an indexed case and the quote appears in its text.",
    "EXISTS_QUOTE_NOT_FOUND": "The citation resolves to an indexed case but the quote was not found in its text.",
    "IN_INDEX": "Found in the index (existence only): no quote was checked, so this is not a verification.",
    "NOT_IN_INDEX": (
        "The case was not found in our index. "
        "That does not show whether the citation is real: the index does not hold every judgment."
    ),
    "MALFORMED": "Exactly one parseable citation is required.",
    "CONFLICT": "The citation maps to conflicting indexed cases; verify by hand.",
}


class AnalyseFactsRequest(BaseModel):
    #: At most 8 facts, each at most 4,000 characters -- an anonymous caller cannot ask this route to judge
    #: an unbounded amount of text (reviewer 1, point (b)).
    #: The per-fact cap is enforced in `_admit` (a 422 after metering), not by validation, so the contract
    #: states it through `json_schema_extra` and the error shape stays what clients already see.
    facts: list[str] = Field(
        min_length=1, max_length=8, json_schema_extra={"items": {"type": "string", "maxLength": 4000}}
    )
    narrative: str = Field(default="", max_length=4000)
    #: Required, not optional: omitting this used to mean "every contract the registry knows" (23 of them,
    #: dozens of GPU calls) for one anonymous request. 1-5 explicit ids only.
    contract_ids: list[str] = Field(min_length=1, max_length=5)
    sections: list[str] | None = None
    #: The proceeding stage the analysis is for. Optional: absent means the engine's stricter default
    #: ("proved"), recorded as standard_source=default_proved. An unknown value is a 422 (the Literal).
    #: It changes the judge prompt only when the deployment's `prompt_template` is not `legacy` (the default).
    proceeding_posture: Literal["quash", "discharge", "trial", "appeal"] | None = Field(
        default=None,
        description=(
            "Stage of the proceeding: quash/discharge judge whether the record prima facie discloses each element; "
            "trial/appeal judge whether the evidence proves it. Omit for the stricter default (proved). "
            "No effect unless the engine runs a standard-aware prompt template."
        ),
    )


#: The exact keys `analyse_facts_ep` strips out of each element's dict when the debug gate is off -- listed
#: once here and reused both for the strip and (in tests) for the presence/absence assertions, so the two
#: can never silently drift apart.
_SECOND_JUDGE_DEBUG_FIELDS = (
    "p_established_second", "tau_second", "second_skip_reason", "second_logit_distance",
    "second_refer_band_fired", "second_unavailable", "second_fact_id", "fact_id_disagreement",
    "defeater_second_disagreement",
)


SCREENING_SIGNAL_LABEL = "Suggested by the screening judge; check it."


class ElementResultOut(BaseModel):
    element: str
    is_denial: bool
    status: ElementStatus
    claimed: bool
    p_established: float | None
    fact_id: str | None
    quote: str | None
    start: int | None
    end: int | None
    quote_check: QuoteCheck | None = Field(
        description="Why the judge's quote was accepted or rejected (the system's own word-for-word check): ok, "
        "not_established, no_quote, unknown_fact, empty_quote, non_evidential_quote, quote_not_found or "
        "ambiguous_quote. Null when no quote check ran. A quote that occurs more than once is ambiguous_quote: the "
        "element is not counted as shown. Plain-language text for each value: docs/api/reason-codes.md."
    )
    attempts: int
    occurrences: int
    offsets_source: str | None
    #: Who supplied the quote text: `"model"` (a judge wrote words, the opt-in frontier judge) or `"whole_fact"` (the house judges
    #: name a fact and the text is that fact in full; nothing is quoted); null on an element that is not established.
    #: Surfaced per element, per Lead-2-assistant's explicit requirement, so a whole-fact claim is visible to the user rather than
    #: folded silently into the verdict. `citation_note` is its plain-language reading.
    quote_source: str | None
    error: str | None
    #: Issue #37: which judge's tau a non-established element failed to clear ("primary" or "second", never
    #: "both" -- issue #57, Tag's review of #37: that would mean both judges independently failed their own
    #: tau, which the current AND-gate's cost-saving early return never produces), or None when established,
    #: second-judge-unavailable, or a Gate 1 veto) -- always present, never gated behind the config-C debug
    #: flag below, since the truthful status itself is a first-class response field, not a debug-only
    #: internal.
    binding_leg: str | None = None
    #: Second-judge fields, visible in two independent cases (`analyse_facts_ep`'s `show_second_judge_fields`):
    #: (1) the caller is AUTHENTICATED -- a verified Supabase session or a valid org API key (never an
    #: anonymous caller, `PRAVRUDHI_DEMO_ANON_PATHS` notwithstanding: QUEUE.md 2026-09-27, "per-leg scores for
    #: authenticated callers only") -- or (2) the deployment-level debug gate (Lead-2, 2026-09-25 config-C
    #: smoke): both `PartnerApiConfig.debug_second_judge_fields_enabled` and the request's own
    #: `?debug_second_judge=true` are set, for an operator diagnosing a disagreement on a deployment that has
    #: opted in, authenticated or not. `analyse_facts_ep` pops these eight keys out of every element's dict
    #: when NEITHER case holds, so an anonymous, non-debug response is byte-for-byte what it was before this
    #: field existed. Declared here (rather than left for Pydantic to silently strip) so they validate through
    #: when present; same names as `ElementResult`'s own fields, not renamed, so there is no separate
    #: translation to keep in sync.
    p_established_second: float | None = None
    tau_second: float | None = None
    second_skip_reason: str | None = None
    second_logit_distance: float | None = None
    second_refer_band_fired: bool | None = None
    second_unavailable: bool | None = None
    #: The second judge's own fact_id, and whether it disagreed with the primary's (2026-09-27) -- same gate
    #: as the fields above, added to `_SECOND_JUDGE_DEBUG_FIELDS` rather than a new one: this is exactly the
    #: same "config-C internal, not for a public unauthenticated caller by default" category.
    second_fact_id: str | None = None
    fact_id_disagreement: bool | None = None
    defeater_second_disagreement: bool | None = None

    #: #832 S1 source value; never returned (the signal below is derived from it). Excluded from the response.
    screening_supported: bool | None = Field(default=None, exclude=True)

    @computed_field(  # type: ignore[prop-decorator]
        description="A SCREENING signal from the first (screening) judge alone, shown even when the element is not a proof: "
        "`supported` is true when that judge's score cleared its threshold. It is a suggestion to check, not a finding, never "
        "changes status, outcome or reason, and carries no probability. On the CAL CHEAT set (70 rows, dev stack, one look) "
        "it accepted 8 of 41 established and 4 of 29 not-established rows. Null when it produced no score."
    )
    @property
    def screening_signal(self) -> dict[str, Any] | None:
        if self.screening_supported is None:
            return None
        return {"supported": self.screening_supported, "label": SCREENING_SIGNAL_LABEL}

    @computed_field(  # type: ignore[prop-decorator]
        description="Plain-language reading of this element's citation, chosen by quote_source: a judge-written quote that "
        "passed the word-for-word quote check (model), or a fact cited in full with no words quoted from it (whole_fact: the "
        "house judges). Null when no citation is claimed (an element that is not established). Docs: docs/api/reason-codes.md."
    )
    @property
    def citation_note(self) -> str | None:
        return citation_note(self.quote_source, self.fact_id)


class CitationOut(BaseModel):
    """A statute reference from the contract's own source column, resolved against the shipped corpus.
    `in_corpus: false` means the corpus does not hold that provision (corpus_id and title null); it is not a
    finding about the law. Never derived from model output."""

    act: str
    section: str | None
    corpus_id: str | None
    in_corpus: bool
    title: str | None


class ContractResultOut(BaseModel):
    contract_id: str
    outcome: Outcome
    reason: ContractReason
    elements: list[ElementResultOut]
    assertions: dict[str, bool] | None
    lean: dict[str, Any] | None
    lean_outcome: Outcome | None
    #: `{binary_sha256, wire_sha256, verdict}` for the pinned Lean checker's scoring of this contract.
    #: `binary_sha256`: SHA-256 of the pinned Lean `score` binary (same value as the top-level `score_sha256`).
    #: `wire_sha256`: SHA-256 of the exact REG wire line sent to it (contract id + the Met assertions),
    #: so anyone can recompute it from `assertions` and see what was scored. `verdict`: the binary's own.
    #: Attests WHAT was scored and by WHICH binary; it does not attest the assertions are true (the judge
    #: decided those) and the Lean check is structural, not a verification of the law. Null when no Lean call ran.
    lean_attestation: dict[str, str] | None = Field(
        default=None,
        description="Per-contract attestation of the pinned Lean check: binary_sha256 (the pinned `score` "
        "binary), wire_sha256 (SHA-256 of the exact REG wire line sent to it, recomputable from `assertions`) "
        "and verdict. Attests what was scored and by which binary; not that the assertions are true, and the "
        "Lean check is structural, not a verification.",
    )
    uncertain: list[str]
    statute_text_mismatch: bool | None
    rule_text: str | None = Field(
        default=None,
        description="The provision text the contract is checked against: the registry's recorded source text from the Lean "
        "checker's --describe-source (lean_describe_source); unofficial, not the official text of the law. Null when the "
        "contract was not described. Returned only when the deployment enables rule-text disclosure (expose_rule_text); "
        "otherwise absent.",
    )
    judge_rule_text: str | None = Field(
        default=None,
        description="Exactly the first statute_chars characters (600 in the shipped config) of the statute text the judge was "
        "configured with, i.e. the text AS SENT in the judge prompt. Returned when statute_text_mismatch is "
        "true or when the judge's text was cut (longer than statute_chars), so a reader can see what the judge worked "
        "from; null otherwise, and null when no judge statute text is configured for the contract. Returned only when the "
        "deployment enables rule-text disclosure (expose_rule_text); otherwise absent.",
    )
    rule_text_source: str | None = Field(
        default=None,
        description="Where rule_text came from: lean_describe_source (the pinned Lean checker's --describe-source; "
        "the registry's recorded source text; unofficial). Returned only when the deployment enables rule-text disclosure "
        "(expose_rule_text); otherwise absent.",
    )
    citations: list[CitationOut] | None = Field(
        default=None,
        description="The contract's statute references with a corpus check, identical whatever the verdict. "
        "Taken from the contract sources the run already read once for selection (no second registry call). Null "
        "only when those sources or the corpus could not be read on this request.",
    )


class StandardOut(BaseModel):
    """#220: the standard of proof the request asked for, whether the judge was told it, and where it came from.
    Unknown values from a newer engine pass through verbatim, so `requested`, `applied` and `source` are plain
    strings here."""

    requested: str = Field(
        description='The standard the posture asks for: "proved" or "prima_facie_disclosed". Recorded in the audit '
        "row whether or not the judge was told it."
    )
    applied: str | None = Field(
        description="The standard actually stated in the judge's prompt: the same value as `requested` when "
        "`in_judge_prompt` is true, and null when it is false (the judge never saw a standard, so none was applied)."
    )
    source: str = Field(description='"proceeding_posture", "proceeding_type" or "default".')
    proceeding_posture: str | None = Field(default=None, description="The caller's posture, echoed; null if absent.")
    in_judge_prompt: bool = Field(
        description="True only when the house judge's prompt stated the requested standard "
        "(judge_prompt.standard_line on). False means the basis is recorded (`requested`) but the judge never saw "
        "it, so `applied` is null."
    )


def contract_set_digest(sources: Mapping[str, str]) -> str:
    """SHA-256 over the sorted (contract id, source-text SHA-256) pairs: order-independent, and any change to an id
    or to one source text changes it (#832 S7)."""
    pairs = sorted((cid, hashlib.sha256(text.encode("utf-8")).hexdigest()) for cid, text in sources.items())
    return hashlib.sha256(json.dumps(pairs, separators=(",", ":")).encode("utf-8")).hexdigest()


def validated_set_version(ids: Iterable[str]) -> str:
    return hashlib.sha256(json.dumps(sorted(set(ids)), separators=(",", ":")).encode("utf-8")).hexdigest()


_SET_VERSION_CACHE: dict[str, str | None] = {}


def _contract_set_version(agent: Any) -> str | None:
    """`contract_set_digest` over every contract the agent's scorer knows, cached per score binary (one read of the
    sources per process). None when the agent has no registry or a source cannot be read -- never a guess."""
    registry = getattr(agent, "registry", None)
    key = getattr(registry, "sha256", None)
    if registry is None or not isinstance(key, str):
        return None
    if key not in _SET_VERSION_CACHE:
        try:
            _SET_VERSION_CACHE[key] = contract_set_digest(
                {cid: registry.source_text(cid) for cid in sorted(reg.KNOWN_CONTRACT_IDS)}
            )
        except Exception:
            _logger.warning("contract set version unavailable", exc_info=True)
            return None
    return _SET_VERSION_CACHE[key]


class AnalyseFactsResponse(BaseModel):
    run_id: str
    judge: str
    #: SHA-256 of the pinned Lean `score` BINARY (not a content hash of this request or result); see each
    #: contract's `lean_attestation` for the per-contract binding to the exact input scored.
    score_sha256: str = Field(
        description="SHA-256 of the pinned Lean `score` BINARY, not a content hash of the request or result. "
        "See each contract's `lean_attestation` for the binding to the exact input scored.",
    )
    facts: list[dict[str, str]]
    contracts: list[ContractResultOut]
    provenance: str = Field(default="agama")
    #: Issue #39: the exact retention notice text (nyaya_agent.RETENTION_NOTICE), on every response -- a
    #: partner API caller who never sees the web UI still gets this verbatim, not just in documentation.
    retention_notice: str = Field(default=RETENTION_NOTICE)
    contract_set_version: str | None = Field(
        default=None,
        description="Additive (#832 S7): SHA-256 over the sorted (contract id, source-text SHA-256) pairs of every "
        "contract the pinned scorer knows. Order-independent; changes when any contract's source text or the id set "
        "changes. Null when the scorer's sources could not be read.",
    )
    validated_set_version: str | None = Field(
        default=None,
        description="Additive (#832 S7): SHA-256 over the sorted ids in the agent's `validated_contracts` allowlist.",
    )
    standard: StandardOut | None = Field(
        default=None,
        description="Additive (#220): the standard the request asked for (`requested`) and the standard the judge's "
        "prompt actually stated (`applied`, null under the legacy prompt template, where the judge is never told "
        "a standard), from the same values as the audit row.",
    )


AgentFactory = Callable[[Path], AgentLike]


class CreateOrgRequest(BaseModel):
    org_id: str = Field(min_length=2, max_length=63)
    name: str = Field(min_length=1, max_length=200)


class OrgOut(BaseModel):
    id: str
    name: str
    created: str


class CreateKeyRequest(BaseModel):
    label: str = Field(default="", max_length=200)
    rate_limit_per_minute: int = Field(default=60, ge=1, le=100_000)


class ApiKeyOut(BaseModel):
    key_id: str
    org_id: str
    label: str
    created: str
    revoked: bool
    revoked_at: str | None
    rate_limit_per_minute: int


class CreatedKeyOut(ApiKeyOut):
    #: Present only in the create-key response, and only that one time -- nothing else this router returns
    #: ever carries a key's secret.
    secret: str


class ApiKeysOut(BaseModel):
    keys: list[ApiKeyOut]


class JobOut(BaseModel):
    job_id: str
    status: Literal["pending", "running", "done", "failed"]
    #: Present once `status` is `done`: exactly the body POST /analyse-facts returns for the same request.
    result: dict[str, Any] | None = None
    #: Present once `status` is `failed`: the HTTP status and body the synchronous call would have returned.
    error: dict[str, Any] | None = None


class AuditRowOut(BaseModel):
    ts: str
    key_id: str
    mode: Literal["sync", "job", "verify"]
    status_code: int
    run_id: str | None = None
    contract_ids: list[str]
    outcomes: dict[str, str]


class AuditPageOut(BaseModel):
    rows: list[AuditRowOut]
    next_offset: int | None = None


class UsageDayOut(BaseModel):
    day: str
    calls: int
    failed: int


class KeyUsageOut(BaseModel):
    key_id: str
    label: str
    revoked: bool
    days: list[UsageDayOut]


class UsageSummaryOut(BaseModel):
    org_id: str
    keys: list[KeyUsageOut]


class UsageOut(BaseModel):
    key_id: str
    org_id: str
    calls_since_process_start: int
    #: Persistent (survives restart): analyse-facts calls admitted for this key (including those that
    #: then failed), and `failed`, the 503s among them. A call refused with 429 is in neither.
    calls: int = 0
    failed: int = 0


_logger = logging.getLogger(__name__)

#: Carries the real caller IP the Cloudflare Worker read from `CF-Connecting-IP` -- an edge-assigned value a
#: caller cannot spoof by hitting Cloudflare directly, unlike `X-Forwarded-For`. Trusted only alongside
#: `CLIENT_IP_SECRET_HEADER` (below); presented alone it is exactly as unbelievable as a raw
#: `X-Forwarded-For` from an untrusted peer, so it must never be consulted without the secret check passing
#: first.
CLIENT_IP_HEADER = "x-pravrudhi-client-ip"

#: A shared secret only the Worker and this engine know, proving `CLIENT_IP_HEADER` was set by the Worker at
#: the edge and not by whoever is actually making the HTTP request (RunPod's load balancer or the operator's
#: cloudflared tunnel, either of which would otherwise present every caller as the same peer -- exactly the
#: "one shared budget for everyone behind the proxy" gap this exists to close). Compared with
#: `hmac.compare_digest`, never `==`, for the same timing-leak reason `tenancy.TENANCY_PROVISION_HEADER` is.
CLIENT_IP_SECRET_HEADER = "x-pravrudhi-client-ip-secret"

#: The engine's half of the shared secret; the Worker's half is a Cloudflare Worker secret of the same
#: value, set independently (`wrangler secret put`) -- never committed, never passed through `configs/
#: partner_api.yaml`, the same reasoning `tenancy.TENANCY_PROVISION_SECRET_ENV` uses for its own secret.
CLIENT_IP_SECRET_ENV = "PRAVRUDHI_CLIENT_IP_SECRET"

#: Below this length, a configured secret is treated as though it were never set at all -- fail closed, the
#: same threshold and reasoning as `tenancy.MIN_PROVISION_SECRET_LENGTH`.
MIN_CLIENT_IP_SECRET_LENGTH = 32

_client_ip_secret_warned_lock = threading.Lock()
_client_ip_secret_warned = False


def _warn_short_client_ip_secret_once() -> None:
    """Exactly one warning per process for a too-short configured secret -- mirrors `tenancy._warn_short_
    secret_once`; never logs the value itself."""
    global _client_ip_secret_warned
    with _client_ip_secret_warned_lock:
        if _client_ip_secret_warned:
            return
        _client_ip_secret_warned = True
    _logger.warning(
        "%s is set but shorter than %d characters -- treating the Worker's client-IP header as untrusted "
        "(failing closed to the shared rate-limit key) rather than accepting a weak secret. Set a longer "
        "value to use this path.",
        CLIENT_IP_SECRET_ENV, MIN_CLIENT_IP_SECRET_LENGTH,
    )


def _client_ip(request: Request, *, trust_proxy_header: bool, trusted_proxies: tuple[str, ...]) -> str:
    # Preferred over everything below: a caller-specific key proven, by the shared secret, to have come
    # from the Worker's own read of Cloudflare's CF-Connecting-IP -- correct even when this engine sits
    # behind a proxy (RunPod's LB, a cloudflared tunnel) that presents every real caller as the same socket
    # peer, which would otherwise turn the per-IP rate limit into one shared budget for every caller at
    # once. Fails CLOSED, never open: a missing or too-short secret, a missing client-IP header, or a
    # mismatched secret all fall through to the existing (stricter, shared-key-prone) behavior below rather
    # than trusting an unproven header.
    secret = os.environ.get(CLIENT_IP_SECRET_ENV, "")
    if secret and len(secret) < MIN_CLIENT_IP_SECRET_LENGTH:
        _warn_short_client_ip_secret_once()
        secret = ""
    if secret:
        presented = request.headers.get(CLIENT_IP_SECRET_HEADER, "")
        if presented and hmac.compare_digest(presented, secret):
            client_ip = request.headers.get(CLIENT_IP_HEADER, "").strip()
            if client_ip:
                return client_ip

    client = request.client
    socket_peer = client.host if client is not None else "unknown"
    # The header is only trusted when the DIRECT socket peer -- who actually opened this TCP connection,
    # not anything the connection itself claims -- is one of the configured trusted proxies. A caller
    # connecting straight to this process (skipping the real proxy) cannot set X-Forwarded-For and have it
    # believed just because trust_proxy_header is on; PartnerApiConfig already refuses to construct at all
    # if trust_proxy_header is set with an empty allowlist, so trusted_proxies here is never empty when
    # trust_proxy_header is True.
    if trust_proxy_header and socket_peer in trusted_proxies:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return socket_peer


@functools.lru_cache(maxsize=1)
def _shipped_corpus() -> Any:
    from pravrudhi.application import nyaya

    return nyaya.load_corpus()


#: Licence hold (#308, pending counsel #506): withheld from the response unless `expose_rule_text` is on.
_RULE_TEXT_FIELDS = ("rule_text", "judge_rule_text", "rule_text_source")


def _attach_citations(agent: Any, body: dict[str, Any], listed: dict[str, list[str]] | None = None) -> None:
    """Add `citations` to each contract result from the contract's own sources (#142).

    `listed` is the contract -> sources map the run already read for selection, so no second `--list-contracts`
    subprocess is spawned; only a run that did not carry one (a hand-built double) reads the registry here. Any
    failure to read the sources or the corpus (including a subprocess error or timeout) leaves `citations` null on
    every contract: a visible gap, never a fabricated list, and never a failed response for an analysis already
    done."""
    try:
        if listed is None:
            listed = agent.registry.list_contracts()
        corpus = _shipped_corpus()
    except (OSError, RuntimeError, ValueError, AttributeError, KeyError, subprocess.SubprocessError) as e:
        logging.getLogger(__name__).warning("citations unavailable: %s", type(e).__name__)
        for contract in body.get("contracts", []):
            contract["citations"] = None
        return
    for contract in body.get("contracts", []):
        contract["citations"] = contract_citations(list(listed.get(contract["contract_id"], [])), corpus)


def build_partner_router(
    root: Path,
    *,
    agent_factory: AgentFactory | None = None,
    judge_health_fetch: Fetch | None = None,
    config: PartnerApiConfig | None = None,
    clock: Callable[[], datetime] | None = None,
    job_executor: Callable[[Callable[[], None]], Any] | None = None,
    citation_index_path: Path | None = None,
    rate_clock: Callable[[], float] | None = None,
) -> APIRouter:
    """`citation_index_path` (default: env `PRAVRUDHI_CITATION_INDEX`) is the case-law index
    `POST /api/v1/verify-citations` reads; unset or missing means that route answers 503.
    `rate_clock` (seconds, monotonic) drives the per-key rate-limit window; tests inject a fake clock and advance it
    instead of depending on the wall clock (#303). Production leaves it `None` (`time.monotonic`).
    `agent_factory` is injectable (mirrors `nyaya.py`'s `ask_fn` pattern): production leaves it `None` and
    gets the configured house agent (`NyayaAgent.house`, real vLLM judge + real pinned Lean binary); tests
    supply a factory returning an agent built from scripted test doubles, the same shape `test_nyaya_agent.py`
    itself uses, so this router's own tests cover HTTP wiring only, not re-proving the agent's decision logic.
    `config` is likewise injectable for tests; production reads `configs/partner_api.yaml`.
    """
    engine_root = Path(root)
    # `factory`'s definition references `_get_state` below (a closure, resolved at call time, not here) --
    # production leaves `agent_factory` None and gets ONE breaker shared across every request via
    # `_get_state`'s own singleton; a test-supplied `agent_factory` never sees this at all, since it built
    # its own scripted agent already and never calls `.house()`.
    # Config (and the limiter/breaker state built from it) is resolved lazily, on the first request, not at
    # router-build time: build_partner_router runs during create_app for EVERY app instance, including ones
    # built over a bare tmp_path with no configs/ directory at all (test_roles.py's route-table tests) --
    # reading configs/partner_api.yaml eagerly here broke every one of those, since nothing else in create_app
    # touches disk before the app is fully built. NyayaAgent.house(root) already follows this same lazy
    # pattern (only called inside the request handler via `factory`), mirrored here for the same reason.
    _state_lock = threading.Lock()
    _state: dict[str, Any] = {}

    def _get_state() -> tuple[PartnerApiConfig, RateLimiter, ConcurrencyLimiter, RateLimiter, SecondJudgeCircuitBreaker]:
        with _state_lock:
            if not _state:
                cfg = config or load_partner_api_config(engine_root)
                _state["cfg"] = cfg
                _state["rate_limiter"] = RateLimiter(cfg.rate_limit_per_minute, max_keys=cfg.rate_limit_max_keys)
                _state["concurrency"] = ConcurrencyLimiter(cfg.max_concurrent)
                # A separate, much lower-budget limiter for the tenancy provisioning routes: sharing the
                # analyse-facts limiter would mean a normal partner workload and a guessed-secret attempt
                # against /api/v1/orgs draw from the same 6-per-minute budget, which is generous for the
                # latter and would also let provisioning traffic starve analyse-facts's own limit.
                _state["provision_rate_limiter"] = RateLimiter(
                    cfg.provision_rate_limit_per_minute, max_keys=cfg.rate_limit_max_keys
                )
                # Issue #35: ONE breaker for this process's whole lifetime, shared across every request's
                # NyayaAgent (and its whole judge_pool) -- a real second-judge failure trips it for every
                # later request within the TTL, not just the rest of the one that hit it.
                _state["second_judge_breaker"] = SecondJudgeCircuitBreaker(
                    ttl_s=cfg.second_judge_circuit_breaker_ttl_s
                )
            return (
                _state["cfg"], _state["rate_limiter"], _state["concurrency"], _state["provision_rate_limiter"],
                _state["second_judge_breaker"],
            )

    factory: AgentFactory = agent_factory or (
        lambda r: NyayaAgent.house(r, second_judge_breaker=_get_state()[4])
    )

    def _provision_rate_limit(request: Request) -> JSONResponse | None:
        """`None` when the call may proceed; a ready-to-return 429 otherwise. Keyed by client IP the same
        way `analyse_facts_ep` keys its own limiter (`_client_ip`, honoring `trust_proxy_header` /
        `trusted_proxies` identically) -- a caller who can burn analyse-facts's GPU-time budget from one IP
        can burn the provisioning budget from that same IP, and both must be told apart the same way."""
        cfg, _rate_limiter, _concurrency, provision_rate_limiter, _breaker = _get_state()
        ip = _client_ip(request, trust_proxy_header=cfg.trust_proxy_header, trusted_proxies=cfg.trusted_proxies)
        if provision_rate_limiter.allow(ip):
            return None
        return JSONResponse(
            status_code=429,
            content={"detail": "rate limit exceeded"},
            headers={"Retry-After": str(provision_rate_limiter.retry_after_seconds())},
        )

    _key_rate_limiter = tenancy.KeyRateLimiter(now=rate_clock) if rate_clock is not None else tenancy.KeyRateLimiter()
    # Passive judge observation: the last analyse-facts result, never a probe. A probe of a scaled-to-zero
    # serverless judge would itself wake it (spend, outside the serving windows), so /status reports only
    # what real traffic last saw and says "unknown" once that is stale.
    _judge_seen: dict[str, Any] = {}

    def _now() -> datetime:
        return clock() if clock else datetime.now(UTC)

    def _warming(cfg: PartnerApiConfig | None, now: datetime) -> bool:
        first = _judge_seen.get("first_failure")
        grace = cfg.judge_warm_grace_s if cfg else 0.0
        return first is not None and grace > 0 and (now - first).total_seconds() <= grace

    def _window_closed(cfg: PartnerApiConfig) -> JSONResponse | None:
        w = cfg.service_window
        if w is None or not cfg.service_window_enforce:
            return None
        now = _now()
        if w.is_open(now):
            return None
        return JSONResponse(
            status_code=503,
            content={
                "error": "outside_service_window",
                "window": {
                    "timezone": w.tz,
                    "open": w.open.isoformat(timespec="minutes"),
                    "close": w.close.isoformat(timespec="minutes"),
                },
                "next_open_utc": w.next_open(now).isoformat(timespec="seconds"),
            },
            headers={"Retry-After": str(w.retry_after_seconds(now))},
        )

    _offline_check = JudgeOfflineCheck(fetch=judge_health_fetch) if judge_health_fetch else JudgeOfflineCheck()

    def _judges_offline(cfg: PartnerApiConfig) -> JSONResponse | None:
        """#311: 503 `judges_offline` when a needed judge endpoint is parked, before anything is metered or queued."""
        if not cfg.judge_health_check:
            return None
        try:
            ac = getattr(factory(engine_root), "config", None)
            judges = {"house": dict(ac.house_judge or {})} if ac is not None else {}
            if ac is not None and ac.second_judge:
                judges["second"] = dict(ac.second_judge)
            _offline_check.ttl_s = cfg.judge_health_ttl_s
            keys = {"house": "NYAYA_HOUSE_JUDGE_API_KEY", "second": "NYAYA_SECOND_JUDGE_API_KEY"}
            parked = _offline_check.offline(judges, keys)
        except Exception:  # noqa: BLE001 - the check must never turn a working request into an error
            _logger.warning("judge health check failed; proceeding")
            return None
        if not parked:
            return None
        _judge_seen.update(state="unavailable", at=_now(), first_failure=None)
        return JSONResponse(status_code=503, content={"error": "judges_offline"})

    router = APIRouter(prefix="/api/v1")

    @router.get("/status", response_model=ServiceStatusOut)
    def status_ep() -> ServiceStatusOut:
        # The one endpoint that must answer on a fresh install: a root with no partner_api.yaml has no
        # window to report (null), it is not a server error.
        try:
            cfg: PartnerApiConfig | None = _get_state()[0]
        except FileNotFoundError:
            cfg = None
        now = _now()
        w = cfg.service_window if cfg else None
        window: ServiceWindowOut | None = None
        if cfg is not None and w is not None:
            window = ServiceWindowOut(
                timezone=w.tz,
                open=w.open.isoformat(timespec="minutes"),
                close=w.close.isoformat(timespec="minutes"),
                enforced=cfg.service_window_enforce,
                open_now=w.is_open(now),
                next_open_utc=w.next_open(now).isoformat(timespec="seconds"),
            )
        seen = _judge_seen.get("at")
        ttl = cfg.judge_seen_ttl_s if cfg else PartnerApiConfig.judge_seen_ttl_s
        fresh = seen is not None and (now - seen).total_seconds() <= ttl
        state: Literal["ready", "warming", "unavailable", "unknown"] = _judge_seen["state"] if fresh else "unknown"
        if state == "unavailable" and _warming(cfg, now):
            state = "warming"
        return ServiceStatusOut(
            engine_version=__version__,
            service_window=window,
            judge=JudgeStateOut(
                state=state,
                checked_at=seen.isoformat(timespec="seconds") if (fresh and seen) else None,
            ),
        )

    # exclude_unset=True: the debug fields are POPPED from each element dict (never set) when the gate is
    # off, and must actually disappear from the JSON, not reappear as their Pydantic default (None) --
    # FastAPI otherwise fills in every declared field's default when constructing the response model,
    # regardless of what the source dict contained. Safe for every other field: each of those is always
    # present as an explicit key in `body` (even when its value is None, e.g. `fact_id`), and a key that IS
    # present counts as "set" for exclude_unset's purposes, so nothing else in the response shape changes.
    def _record_usage(key_id: str, *, failed: bool = False) -> None:
        try:
            tenancy.record_usage(engine_root, key_id, failed=failed, now=_now())
        except OSError:
            _logger.exception("usage metering write failed for key %s", key_id)

    def _rate_headers(key_id: str, per_minute: int) -> dict[str, str]:
        limit, remaining, reset = _key_rate_limiter.snapshot(key_id, per_minute)
        return {"X-RateLimit-Limit": str(limit), "X-RateLimit-Remaining": str(remaining), "X-RateLimit-Reset": str(reset)}

    def _audit(
        metered: list[str],
        req: AnalyseFactsRequest,
        mode: str,
        *,
        status_code: int,
        out: dict[str, Any] | None = None,
    ) -> None:
        """One audit row per key-admitted call. Failure to write is logged, never allowed to change the answer."""
        if not metered:
            return
        try:
            cfg = _get_state()[0]
            outcomes = {c["contract_id"]: str(c["outcome"]) for c in (out or {}).get("contracts", [])}
            audit.record(
                engine_root,
                key_id=metered[0],
                mode=mode,
                status_code=status_code,
                contract_ids=req.contract_ids,
                run_id=(out or {}).get("run_id"),
                outcomes=outcomes,
                retention_s=cfg.audit_retention_s,
                now=_now(),
            )
        except Exception:
            _logger.exception("audit write failed for key %s", metered[0])

    @router.post(
        "/analyse-facts", response_model=AnalyseFactsResponse, response_model_exclude_unset=True,
        responses={
            200: {"description": "Successful analysis.", "headers": _RATE_LIMIT_HEADER_DOCS},
            401: {"description": "The API key is invalid or revoked."},
            429: {"description": "Over the rate limit; wait Retry-After seconds.",
                  "headers": {**_RATE_LIMIT_HEADER_DOCS, "Retry-After": _RETRY_AFTER_DOC}},
            503: {"description": "Outside the service window, judges warming (retry_after_s), service "
                  "config missing, or the agent at capacity (agent_at_capacity) or unavailable (agent_unavailable)."},
        },
    )
    def analyse_facts_ep(
        req: AnalyseFactsRequest,
        request: Request,
        response: Response,
        user: User | None = CurrentUserDep,
        debug_second_judge: bool = Query(
            False,
            description="Include second-judge diagnostic fields per element (only for deployments that use two judges) "
            "even when not "
            "authenticated. Only takes effect when this deployment's own debug_second_judge_fields_enabled "
            "is also set -- a caller cannot turn this on for a deployment that hasn't opted in. An "
            "authenticated caller (Supabase session or org API key) always gets these fields regardless of "
            "this flag or the deployment gate.",
        ),
    ) -> dict[str, Any] | JSONResponse:
        metered: list[str] = []
        per_minute: list[int] = []
        try:
            out = _analyse_facts(req, request, user, debug_second_judge, metered, per_minute)
        except HTTPException as e:
            if metered and e.status_code == 503:
                _record_usage(metered[0], failed=True)
            _audit(metered, req, "sync", status_code=e.status_code)
            raise
        if metered and isinstance(out, JSONResponse) and out.status_code == 503:
            _record_usage(metered[0], failed=True)
        if metered and isinstance(out, dict):
            response.headers.update(_rate_headers(metered[0], per_minute[0]))
        if isinstance(out, JSONResponse):
            _audit(metered, req, "sync", status_code=out.status_code)
        else:
            _audit(metered, req, "sync", status_code=200, out=out)
        return out

    def _analyse_facts(
        req: AnalyseFactsRequest,
        request: Request,
        user: User | None,
        debug_second_judge: bool,
        metered: list[str],
        per_minute: list[int],
    ) -> dict[str, Any] | JSONResponse:
        admitted = _admit(req, request, user, metered, per_minute)
        if isinstance(admitted, JSONResponse):
            return admitted
        return _run(req, admitted, debug_second_judge)

    def _admit(
        req: AnalyseFactsRequest, request: Request, user: User | None, metered: list[str], per_minute: list[int]
    ) -> _Admitted | JSONResponse:
        """Everything that decides whether a call may run at all (window, limits, metering, validation), shared
        by the synchronous route and job submission so a job is admitted by exactly the same rules."""
        try:
            cfg, rate_limiter, concurrency, _provision_rate_limiter, _breaker = _get_state()
        except FileNotFoundError:
            # No partner_api.yaml means no rate limit, concurrency cap or service window to apply: refuse
            # (fail closed) rather than run the agent unguarded.
            return JSONResponse(status_code=503, content={"error": "service_config_missing"})
        closed = _window_closed(cfg)
        if closed is not None:
            return closed
        offline = _judges_offline(cfg)
        if offline is not None:
            return offline
        # QUEUE.md 2026-09-27: per-leg second-judge scores are visible to an AUTHENTICATED caller -- a
        # verified Supabase session (`user`) or a valid org API key -- never to an anonymous one, even on a
        # deployment that answers analyse-facts anonymously (`PRAVRUDHI_DEMO_ANON_PATHS`). `principal_from_
        # headers` raises 401 itself when a key header WAS sent but does not verify, the same as `usage_ep`;
        # it never treats a bad key as "no key" (a caller who supplied a bad key is never silently anonymous).
        principal = tenancy.principal_from_headers(engine_root, request.headers)
        if principal is not None:
            from pravrudhi.application.credentials import serving_org

            serving_org.set(principal.org_id)
        authenticated = user is not None or principal is not None
        ip = _client_ip(request, trust_proxy_header=cfg.trust_proxy_header, trusted_proxies=cfg.trusted_proxies)
        if not rate_limiter.allow(ip):
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded"},
                headers={"Retry-After": str(rate_limiter.retry_after_seconds())},
            )
        if principal is not None:
            key = next((k for k in tenancy.keys_for_org(engine_root, principal.org_id)
                        if k.key_id == principal.key_id), None)
            if key is None:
                raise HTTPException(401, "Invalid or revoked API key")
            if not _key_rate_limiter.allow(key.key_id, key.rate_limit_per_minute):
                return JSONResponse(
                    status_code=429,
                    content={"detail": "rate limit exceeded"},
                    headers={**_rate_headers(key.key_id, key.rate_limit_per_minute),
                             "Retry-After": str(_key_rate_limiter.retry_after_seconds())},
                )
            metered.append(key.key_id)
            per_minute.append(key.rate_limit_per_minute)
            _record_usage(key.key_id)
        if not any(f.strip() for f in req.facts):
            raise HTTPException(422, "at least one non-empty fact is required")
        for f in req.facts:
            if len(f) > 4000:
                raise HTTPException(422, "a fact may not exceed 4000 characters")
        return _Admitted(cfg, concurrency, authenticated)

    def _run(req: AnalyseFactsRequest, admitted: _Admitted, debug_second_judge: bool) -> dict[str, Any] | JSONResponse:
        cfg, concurrency, authenticated = admitted.cfg, admitted.concurrency, admitted.authenticated
        if not concurrency.acquire():
            return coded_503(AGENT_AT_CAPACITY)
        try:
            agent = factory(engine_root)
            # client_data=True is already this call's default, made explicit here (issue #39): this is a
            # public, unauthenticated endpoint (module docstring), so every run through it is exactly the
            # anonymous-submission case the retention/training-corpus guard exists for -- a reader should
            # never have to check NyayaAgent.run's own default to know that.
            # Passed only when stated, so an agent that predates the argument keeps working unchanged.
            posture = {"proceeding_posture": req.proceeding_posture} if req.proceeding_posture is not None else {}
            result = agent.run(
                req.facts, narrative=req.narrative, contract_ids=req.contract_ids, sections=req.sections,
                client_data=True, **posture,
            )
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        except reg.UnknownContractError as e:
            raise HTTPException(422, str(e)) from e
        except (JudgeMisconfigured, BinaryShaMismatch, OSError):  # FileNotFoundError is an OSError
            _logger.warning("nyaya agent unavailable", exc_info=True)  # the exception is logged server-side only (#318)
            return coded_503(AGENT_UNAVAILABLE)
        finally:
            concurrency.release()
        # R1, 2026-09-25: a PRIMARY judge that is unreachable from boot (model resolution or a connection
        # failure after `_judge_element`'s own transient-retry loop is exhausted) does not raise out of
        # `agent.run()` -- `NyayaAgent`'s own philosophy is that this is a recorded, non-evidentiary outcome
        # (`reason="judge_error"`, contract ABSTAIN; see nyaya_agent.py's module doc, "nothing here is
        # evidence"), which is correct for the engine's own testimony record but wrong for an HTTP API: an
        # infrastructure failure must never look like a legal outcome to a caller or to monitoring. This is
        # unambiguous -- "judge_error" can ONLY arise from the primary exhausting its retries (a second-judge
        # config fault raises JudgeMisconfigured before ever reaching this reason, above; a second-judge
        # CONNECTION failure is caught inside AndGateJudge itself and becomes `second_judge_unavailable` /
        # REFER_TO_LAWYER, a legitimate safety outcome, never this reason) -- so no other ABSTAIN reason is
        # touched here, and REFER_TO_LAWYER outcomes are never affected. Mid-run transient blips that the
        # retry loop successfully rode out never reach this reason either; only exhausted retries do.
        if any(c.reason == "judge_error" for c in result.contracts):
            now = _now()
            # A failure straight after a fresh "ready" is a fault (the judge was warm); with no fresh reading it
            # may be a cold start (#164), so the first failure opens a warming window. A run of failures keeps
            # the window's start; a stale reading (judge idle, scaled to zero again) restarts it.
            seen_at = _judge_seen.get("at")
            prev = _judge_seen.get("state") if seen_at and (now - seen_at).total_seconds() <= cfg.judge_seen_ttl_s else None
            if prev == "ready":
                _judge_seen["first_failure"] = None
            elif prev != "unavailable":
                _judge_seen["first_failure"] = now
            _judge_seen.update(state="unavailable", at=now)
            if cfg.judge_warm_grace_s > 0 and _warming(cfg, now):
                retry = int(cfg.judge_warm_retry_s)
                return JSONResponse(
                    status_code=503,
                    content={"error": "judge_unavailable", "reason": "judges_warming", "retry_after_s": retry},
                    headers={"Retry-After": str(retry)},
                )
            return JSONResponse(status_code=503, content={"error": "judge_unavailable"})
        _judge_seen.update(state="ready", at=_now(), first_failure=None)
        body: dict[str, Any] = result.to_dict()
        body.pop("audit_path", None)
        _attach_citations(agent, body, getattr(result, "listed_sources", None))
        body["contract_set_version"] = _contract_set_version(agent)
        body["validated_set_version"] = validated_set_version(getattr(getattr(agent, "config", None), "validated_contracts", ()))
        if not getattr(getattr(agent, "config", None), "expose_rule_text", False):
            for contract in body.get("contracts", []):
                for name in _RULE_TEXT_FIELDS:
                    contract.pop(name, None)
        show_second_judge_fields = authenticated or (debug_second_judge and cfg.debug_second_judge_fields_enabled)
        if not show_second_judge_fields:
            for contract in body.get("contracts", []):
                for element in contract.get("elements", []):
                    for field in _SECOND_JUDGE_DEBUG_FIELDS:
                        element.pop(field, None)
        return body

    # --- async job mode (#146): same admission, same agent run, collected by polling -------------------------
    _jobs_lock = threading.Lock()
    _jobs_holder: dict[str, JobStore] = {}
    _pool: dict[str, ThreadPoolExecutor] = {}

    def _job_store(cfg: PartnerApiConfig) -> JobStore:
        with _jobs_lock:
            if "s" not in _jobs_holder:
                _jobs_holder["s"] = JobStore(
                    retention_s=cfg.job_retention_s, clock=clock, max_unfinished_per_key=cfg.job_max_unfinished_per_key
                )
            return _jobs_holder["s"]

    def _submit(task: Callable[[], None]) -> None:
        # The job runs on another thread, which does not inherit the request's ContextVars (the serving guards
        # `serving_api`/`serving_org` live there): run it in a copy of THIS thread's context, taken now.
        ctx = contextvars.copy_context()
        if job_executor is not None:
            job_executor(lambda: ctx.run(task))
            return
        with _jobs_lock:
            pool = _pool.setdefault("p", ThreadPoolExecutor(max_workers=4, thread_name_prefix="analyse-job"))
        pool.submit(ctx.run, task)

    def _job_principal(request: Request) -> tenancy.OrgPrincipal:
        principal = tenancy.principal_from_headers(engine_root, request.headers)
        if principal is None:
            raise HTTPException(401, "jobs require an API key (X-Pravrudhi-Api-Key)")
        return principal

    @router.post(
        "/analyse-facts/jobs", status_code=202, response_model=JobOut, response_model_exclude_none=True,
        responses={
            401: {"description": "Jobs require a valid API key."},
            429: {"description": "Too many unfinished jobs for this key; wait Retry-After seconds.",
                  "headers": {"Retry-After": _RETRY_AFTER_DOC}},
            503: {"description": "Service config missing, or the engine is unavailable."},
        },
    )
    def submit_job_ep(
        req: AnalyseFactsRequest,
        request: Request,
        user: User | None = CurrentUserDep,
        debug_second_judge: bool = Query(False),
    ) -> dict[str, Any] | JSONResponse:
        principal = _job_principal(request)
        try:
            cfg = _get_state()[0]
        except FileNotFoundError:
            return JSONResponse(status_code=503, content={"error": "service_config_missing"})
        store = _job_store(cfg)
        job_id = store.create(principal.key_id)
        if job_id is None:
            return JSONResponse(
                status_code=429,
                content={"detail": "too many unfinished jobs for this key"},
                headers={"Retry-After": "30"},
            )
        metered: list[str] = []
        try:
            admitted = _admit(req, request, user, metered, [])
        except HTTPException:
            store.discard(job_id)
            raise
        if isinstance(admitted, JSONResponse):
            store.discard(job_id)
            return admitted

        def task() -> None:
            store.start(job_id)
            try:
                out = _run(req, admitted, debug_second_judge)
                if isinstance(out, dict):
                    out = AnalyseFactsResponse(**out).model_dump(mode="json", exclude_unset=True)
            except HTTPException as e:
                if e.status_code == 503 and metered:
                    _record_usage(metered[0], failed=True)
                _audit(metered, req, "job", status_code=e.status_code)
                store.fail(job_id, status_code=e.status_code, body={"detail": e.detail})
                return
            except Exception:
                _logger.exception("analyse-facts job %s crashed", job_id)
                _audit(metered, req, "job", status_code=500)
                store.fail(job_id, status_code=500, body={"detail": "internal error"})
                return
            if isinstance(out, JSONResponse):
                if out.status_code == 503 and metered:
                    _record_usage(metered[0], failed=True)
                _audit(metered, req, "job", status_code=out.status_code)
                store.fail(job_id, status_code=out.status_code, body=json.loads(bytes(out.body)))
                return
            _audit(metered, req, "job", status_code=200, out=out)
            store.finish(job_id, result=out)

        _submit(task)
        return {"job_id": job_id, "status": "pending"}

    @router.get(
        "/analyse-facts/jobs/{job_id}", response_model=JobOut, response_model_exclude_none=True,
        responses={
            401: {"description": "Jobs require a valid API key."},
            404: {"description": "No such job for this key."},
            503: {"description": "Service config missing."},
        },
    )
    def get_job_ep(job_id: str, request: Request) -> dict[str, Any]:
        principal = _job_principal(request)
        try:
            cfg = _get_state()[0]
        except FileNotFoundError:
            raise HTTPException(503, "service_config_missing") from None
        job = _job_store(cfg).get(principal.key_id, job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        return {"job_id": job.job_id, "status": job.status, "result": job.result, "error": job.error}

    @router.get("/audit", response_model=AuditPageOut)
    def audit_ep(
        request: Request, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=audit.MAX_PAGE)
    ) -> dict[str, Any]:
        principal = tenancy.principal_from_headers(engine_root, request.headers)
        if principal is None:
            raise HTTPException(401, "the audit log requires an API key (X-Pravrudhi-Api-Key)")
        try:
            cfg = _get_state()[0]
        except FileNotFoundError:
            raise HTTPException(503, "service_config_missing") from None
        rows, nxt = audit.page(
            engine_root, principal.key_id, offset=offset, limit=limit, retention_s=cfg.audit_retention_s, now=_now
        )
        return {"rows": rows, "next_offset": nxt}

    def _verify_admit(request: Request, metered: list[str], per_minute: list[int]) -> JSONResponse | None:
        """Admission for `/verify-citations`: the per-IP limit, then, for a keyed caller, the key check, the per-key
        limit and metering (the same rules, in the same order, as `analyse-facts`'s `_admit`)."""
        try:
            cfg, rate_limiter = _get_state()[:2]
        except FileNotFoundError:
            return JSONResponse(status_code=503, content={"error": "service_config_missing"})
        ip = _client_ip(request, trust_proxy_header=cfg.trust_proxy_header, trusted_proxies=cfg.trusted_proxies)
        if not rate_limiter.allow(ip):
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded"},
                headers={"Retry-After": str(rate_limiter.retry_after_seconds())},
            )
        principal = tenancy.principal_from_headers(engine_root, request.headers)
        if principal is not None:
            key = next((k for k in tenancy.keys_for_org(engine_root, principal.org_id) if k.key_id == principal.key_id), None)
            if key is None:
                raise HTTPException(401, "Invalid or revoked API key")
            if not _key_rate_limiter.allow(key.key_id, key.rate_limit_per_minute):
                return JSONResponse(
                    status_code=429,
                    content={"detail": "rate limit exceeded"},
                    headers={**_rate_headers(key.key_id, key.rate_limit_per_minute),
                             "Retry-After": str(_key_rate_limiter.retry_after_seconds())},
                )
            metered.append(key.key_id)
            per_minute.append(key.rate_limit_per_minute)
            _record_usage(key.key_id)
        return None

    def _verify_audit(metered: list[str], status_code: int, result: str | None = None) -> None:
        if not metered:
            return
        try:
            cfg = _get_state()[0]
            audit.record(
                engine_root, key_id=metered[0], mode="verify", status_code=status_code, contract_ids=[],
                outcomes={"verify": result} if result else None, retention_s=cfg.audit_retention_s, now=_now(),
            )
        except Exception:
            _logger.exception("audit write failed for key %s", metered[0])

    _verify_gates: dict[int, ConcurrencyLimiter] = {}
    _verify_gates_lock = threading.Lock()

    def _verify_gate(size: int) -> ConcurrencyLimiter:
        with _verify_gates_lock:
            return _verify_gates.setdefault(size, ConcurrencyLimiter(size))

    def _lookup(
        path: Path, req: VerifyCitationRequest, timeout_s: float
    ) -> tuple[citation_status.CitationStatus, dict[str, Any] | None]:
        """One bounded, read-only lookup. The progress handler aborts the statement once the deadline passes, so a
        pathological party pair can hold a worker for `timeout_s` at most."""
        deadline = time.monotonic() + timeout_s
        conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        try:
            conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
            if req.quote is None:
                found = citation_status.status_for(exists_in_index(conn, req.citation))
            else:
                found = citation_status.status_for(verify_citation(conn, req.citation, req.quote))
            conn.set_progress_handler(None, 0)  # the deadline bounds the lookup; the (cached) coverage read is outside it
            try:
                coverage: dict[str, Any] | None = index_coverage(conn, str(path.resolve()), path.stat().st_mtime_ns)
            except Exception:
                # coverage is context for the answer, never a reason to lose it: degrade to the documented null
                _logger.warning("index coverage unavailable", exc_info=True)
                coverage = None
            return found, coverage
        finally:
            conn.close()

    @router.post(
        "/verify-citations", response_model=VerifyCitationResponse,
        responses={
            401: {"description": "The API key is invalid or revoked."},
            429: {"description": "Over the rate limit; wait Retry-After seconds.",
                  "headers": {**_RATE_LIMIT_HEADER_DOCS, "Retry-After": _RETRY_AFTER_DOC}},
            503: {"description": "No case index, the lookup timed out, or too many lookups are running."},
        },
    )
    def verify_citations_ep(req: VerifyCitationRequest, request: Request, response: Response) -> dict[str, Any] | JSONResponse:
        metered: list[str] = []
        per_minute: list[int] = []
        refused = _verify_admit(request, metered, per_minute)
        if refused is not None:
            _verify_audit(metered, refused.status_code)
            return refused
        cfg = _get_state()[0]
        env_path = os.environ.get("PRAVRUDHI_CITATION_INDEX")
        path = citation_index_path or (Path(env_path) if env_path else None)
        if path is None or not Path(path).is_file():
            _verify_audit(metered, 503)
            return JSONResponse(status_code=503, content={"error": "citation_index_unavailable"})
        gate = _verify_gate(cfg.verify_max_concurrent)
        if not gate.acquire():
            _verify_audit(metered, 503)
            return JSONResponse(status_code=503, content={"error": "verify_at_capacity"}, headers={"Retry-After": "5"})
        try:
            result, coverage = _lookup(Path(path), req, cfg.verify_timeout_s)
        except sqlite3.Error as e:
            timed_out = "interrupt" in str(e).lower()
            _verify_audit(metered, 503)
            return JSONResponse(
                status_code=503, content={"error": "verify_timeout" if timed_out else "citation_index_unavailable"}
            )
        finally:
            gate.release()
        if metered:
            response.headers.update(_rate_headers(metered[0], per_minute[0]))
        _verify_audit(metered, 200, result.result)
        return {"result": result.result, "note": _VERIFY_NOTES[result.result], "status": result.status,
                "label": result.label, "verified": result.verified, "preview": result.preview, "coverage": coverage}

    @router.post("/orgs", response_model=OrgOut)
    def create_org_ep(
        req: CreateOrgRequest, request: Request, user: User | None = CurrentUserDep
    ) -> dict[str, Any] | JSONResponse:
        if (limited := _provision_rate_limit(request)) is not None:
            return limited
        tenancy.require_tenancy_admin(user, request.headers)
        try:
            org = tenancy.create_org(engine_root, req.org_id, req.name)
        except tenancy.TenancyError as e:
            raise HTTPException(409, str(e)) from e
        return org.to_dict()

    @router.post("/orgs/{org_id}/keys", response_model=CreatedKeyOut)
    def create_key_ep(
        org_id: str, req: CreateKeyRequest, request: Request, user: User | None = CurrentUserDep
    ) -> dict[str, Any] | JSONResponse:
        if (limited := _provision_rate_limit(request)) is not None:
            return limited
        tenancy.require_tenancy_admin(user, request.headers)
        try:
            created = tenancy.create_key(
                engine_root, org_id, label=req.label, rate_limit_per_minute=req.rate_limit_per_minute
            )
        except tenancy.TenancyError as e:
            raise HTTPException(404, str(e)) from e
        return {**created.record.to_public_dict(), "secret": created.secret}

    @router.get("/orgs/{org_id}/keys", response_model=ApiKeysOut)
    def list_keys_ep(
        org_id: str, request: Request, user: User | None = CurrentUserDep
    ) -> dict[str, Any] | JSONResponse:
        if (limited := _provision_rate_limit(request)) is not None:
            return limited
        tenancy.require_tenancy_admin(user, request.headers)
        return {"keys": [k.to_public_dict() for k in tenancy.keys_for_org(engine_root, org_id)]}

    @router.post("/orgs/{org_id}/keys/{key_id}/revoke", response_model=ApiKeyOut)
    def revoke_key_ep(
        org_id: str, key_id: str, request: Request, user: User | None = CurrentUserDep
    ) -> dict[str, Any] | JSONResponse:
        if (limited := _provision_rate_limit(request)) is not None:
            return limited
        tenancy.require_tenancy_admin(user, request.headers)
        try:
            record = tenancy.revoke_key(engine_root, key_id)
        except tenancy.TenancyError as e:
            raise HTTPException(404, str(e)) from e
        if record.org_id != org_id:
            # The key id exists but under a different org: refuse rather than revoke, and say 404 (not
            # which org it actually belongs to) -- an admin's typo must not become an info leak either.
            raise HTTPException(404, f"key {key_id!r} does not exist under org {org_id!r}")
        return record.to_public_dict()

    @router.get("/orgs/{org_id}/usage/summary", response_model=UsageSummaryOut)
    def usage_summary_ep(
        org_id: str, request: Request, user: User | None = CurrentUserDep, days: int = Query(30, ge=1, le=366)
    ) -> dict[str, Any] | JSONResponse:
        if (limited := _provision_rate_limit(request)) is not None:
            return limited
        # Admin or provisioning credential only. No credential or a partner key is refused 401, a valid non-admin session 403:
        # a partner key reads its own key's /usage, never an org-wide view, and an anonymous caller gets nothing.
        if not tenancy.is_tenancy_admin(user, request.headers):
            if user is None:
                raise HTTPException(401, "usage summary requires the admin or provisioning credential")
            raise HTTPException(403, "usage summary requires an allowlisted admin identity")
        if tenancy.get_org(engine_root, org_id) is None:
            raise HTTPException(404, f"org {org_id!r} does not exist")
        return {"org_id": org_id, "keys": tenancy.usage_summary(engine_root, org_id, now=_now(), days=days)}

    @router.get("/orgs/{org_id}/usage", response_model=UsageOut)
    def usage_ep(
        org_id: str, request: Request, user: User | None = CurrentUserDep
    ) -> dict[str, Any] | JSONResponse:
        principal = tenancy.principal_from_headers(engine_root, request.headers)
        # The admin bypass here is the same fail-closed check as the provisioning routes above -- never
        # roles.is_admin's auth-disabled-means-operator fallback, for the identical reason: an anonymous
        # caller on a deployment that left auth disabled must not be able to read another org's usage.
        tenancy.require_org_access(
            principal.org_id if principal else None,
            org_id,
            is_admin=tenancy.is_tenancy_admin(user, request.headers),
        )
        if principal is None:
            # An admin asking with no key names no particular key's usage -- refuse rather than guess one.
            raise HTTPException(422, "usage is reported per API key; supply X-Pravrudhi-Api-Key")
        key = next((k for k in tenancy.keys_for_org(engine_root, org_id) if k.key_id == principal.key_id), None)
        if key is None:
            raise HTTPException(404, "key not found")
        # Calling /usage is itself the one key-scoped call this slice of the API has to spend a key's own
        # rate budget against -- the per-key limiter (distinct from analyse-facts's per-IP one) is exercised
        # here so a key's `rate_limit_per_minute` is real and testable even before matters/documents exist.
        if not _key_rate_limiter.allow(key.key_id, key.rate_limit_per_minute):
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded"},
                headers={"Retry-After": str(_key_rate_limiter.retry_after_seconds())},
            )
        calls, failed = tenancy.usage_counts(engine_root, key.key_id)
        return UsageOut(
            key_id=key.key_id,
            org_id=key.org_id,
            calls_since_process_start=_key_rate_limiter.usage_total(key.key_id),
            calls=calls,
            failed=failed,
        ).model_dump()

    return router

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

import hmac
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from pravrudhi import __version__
from pravrudhi.api.identity import CurrentUserDep, User
from pravrudhi.application import audit, tenancy
from pravrudhi.application import nyaya_lean_registry as reg
from pravrudhi.application.config_files import config_file
from pravrudhi.application.jobs import JobStore
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
from pravrudhi.application.service_window import ServiceWindow

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
        judge_warm_grace_s=float(body.get("judge_warm_grace_s", 0.0)),
        judge_warm_retry_s=float(body.get("judge_warm_retry_s", 30.0)),
        job_retention_s=float(body.get("job_retention_s", 3600.0)),
        job_max_unfinished_per_key=int(body.get("job_max_unfinished_per_key", 8)),
        audit_retention_s=float(body.get("audit_retention_s", 90 * 86400.0)),
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


class AnalyseFactsRequest(BaseModel):
    #: At most 8 facts, each at most 4,000 characters -- an anonymous caller cannot ask this route to judge
    #: an unbounded amount of text (reviewer 1, point (b)).
    facts: list[str] = Field(min_length=1, max_length=8)
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
    quote_check: str | None
    attempts: int
    occurrences: int
    offsets_source: str | None
    #: Who supplied the quote text (`"model"` today; `nyaya_judges.ElementJudgment`'s own field) -- surfaced
    #: per element, per Lead-2-assistant's explicit requirement, so a whole-fact claim is visible to the
    #: user rather than folded silently into the verdict.
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
    mode: Literal["sync", "job"]
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


def build_partner_router(
    root: Path,
    *,
    agent_factory: AgentFactory | None = None,
    config: PartnerApiConfig | None = None,
    clock: Callable[[], datetime] | None = None,
    job_executor: Callable[[Callable[[], None]], Any] | None = None,
) -> APIRouter:
    """`agent_factory` is injectable (mirrors `nyaya.py`'s `ask_fn` pattern): production leaves it `None` and
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

    _key_rate_limiter = tenancy.KeyRateLimiter()
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
            429: {"description": "Over the rate limit; wait Retry-After seconds.",
                  "headers": {**_RATE_LIMIT_HEADER_DOCS, "Retry-After": _RETRY_AFTER_DOC}},
        },
    )
    def analyse_facts_ep(
        req: AnalyseFactsRequest,
        request: Request,
        response: Response,
        user: User | None = CurrentUserDep,
        debug_second_judge: bool = Query(
            False,
            description="Include config-C second-judge diagnostic fields per element even when not "
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
            raise HTTPException(503, "the nyaya agent is at capacity; retry shortly")
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
        except JudgeMisconfigured as e:
            raise HTTPException(503, f"nyaya agent unavailable: {e}") from e
        except BinaryShaMismatch as e:
            raise HTTPException(503, f"nyaya agent unavailable: {e}") from e
        except (FileNotFoundError, OSError) as e:
            raise HTTPException(503, f"nyaya agent unavailable: {e}") from e
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
        if job_executor is not None:
            job_executor(task)
            return
        with _jobs_lock:
            pool = _pool.setdefault("p", ThreadPoolExecutor(max_workers=4, thread_name_prefix="analyse-job"))
        pool.submit(task)

    def _job_principal(request: Request) -> tenancy.OrgPrincipal:
        principal = tenancy.principal_from_headers(engine_root, request.headers)
        if principal is None:
            raise HTTPException(401, "jobs require an API key (X-Pravrudhi-Api-Key)")
        return principal

    @router.post("/analyse-facts/jobs", status_code=202, response_model=JobOut, response_model_exclude_none=True)
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

    @router.get("/analyse-facts/jobs/{job_id}", response_model=JobOut, response_model_exclude_none=True)
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

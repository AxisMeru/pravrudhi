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
and Supabase-authenticated behavior stays byte-identical; accepting an org API key as an *alternative*
identity on that route, and scoping the partner resources the plan still lists (matters, documents,
verify-citations, research, draft, audit export) to an org once they exist, is the next slice of this card,
not this one.

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
import logging
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from pravrudhi.api.identity import CurrentUserDep, User
from pravrudhi.application import nyaya_lean_registry as reg
from pravrudhi.application import tenancy
from pravrudhi.application.config_files import config_file
from pravrudhi.application.nyaya_agent import BinaryShaMismatch, JudgeMisconfigured, NyayaAgent

CONFIG_PATH = Path("configs") / "partner_api.yaml"


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

    def __post_init__(self) -> None:
        if self.trust_proxy_header and not self.trusted_proxies:
            raise ValueError(
                "trust_proxy_header is set but trusted_proxies is empty -- refusing to start: an "
                "X-Forwarded-For header trusted with no allowlist of who may set it is a free rate-limit "
                "bypass for any caller"
            )


def load_partner_api_config(root: Path) -> PartnerApiConfig:
    body = yaml.safe_load(config_file(Path(root), "partner_api.yaml").read_text()) or {}
    return PartnerApiConfig(
        rate_limit_per_minute=int(body["rate_limit_per_minute"]),
        max_concurrent=int(body["max_concurrent"]),
        trust_proxy_header=bool(body["trust_proxy_header"]),
        rate_limit_max_keys=int(body.get("rate_limit_max_keys", 10_000)),
        trusted_proxies=tuple(body.get("trusted_proxies") or ()),
        provision_rate_limit_per_minute=int(body.get("provision_rate_limit_per_minute", 10)),
    )


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
    ) -> Any: ...


class AnalyseFactsRequest(BaseModel):
    #: At most 8 facts, each at most 4,000 characters -- an anonymous caller cannot ask this route to judge
    #: an unbounded amount of text (reviewer 1, point (b)).
    facts: list[str] = Field(min_length=1, max_length=8)
    narrative: str = Field(default="", max_length=4000)
    #: Required, not optional: omitting this used to mean "every contract the registry knows" (23 of them,
    #: dozens of GPU calls) for one anonymous request. 1-5 explicit ids only.
    contract_ids: list[str] = Field(min_length=1, max_length=5)
    sections: list[str] | None = None


class ElementResultOut(BaseModel):
    element: str
    is_denial: bool
    status: str
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


class ContractResultOut(BaseModel):
    contract_id: str
    outcome: str
    reason: str
    elements: list[ElementResultOut]
    assertions: dict[str, bool] | None
    lean: dict[str, Any] | None
    lean_outcome: str | None
    uncertain: list[str]
    statute_text_mismatch: bool | None


class AnalyseFactsResponse(BaseModel):
    run_id: str
    judge: str
    score_sha256: str
    facts: list[dict[str, str]]
    contracts: list[ContractResultOut]
    provenance: str = Field(default="agama")


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


class UsageOut(BaseModel):
    key_id: str
    org_id: str
    calls_since_process_start: int


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
    root: Path, *, agent_factory: AgentFactory | None = None, config: PartnerApiConfig | None = None
) -> APIRouter:
    """`agent_factory` is injectable (mirrors `nyaya.py`'s `ask_fn` pattern): production leaves it `None` and
    gets the configured house agent (`NyayaAgent.house`, real vLLM judge + real pinned Lean binary); tests
    supply a factory returning an agent built from scripted test doubles, the same shape `test_nyaya_agent.py`
    itself uses, so this router's own tests cover HTTP wiring only, not re-proving the agent's decision logic.
    `config` is likewise injectable for tests; production reads `configs/partner_api.yaml`.
    """
    engine_root = Path(root)
    factory: AgentFactory = agent_factory or (lambda r: NyayaAgent.house(r))
    # Config (and the limiter state built from it) is resolved lazily, on the first request, not at router-
    # build time: build_partner_router runs during create_app for EVERY app instance, including ones built
    # over a bare tmp_path with no configs/ directory at all (test_roles.py's route-table tests) -- reading
    # configs/partner_api.yaml eagerly here broke every one of those, since nothing else in create_app
    # touches disk before the app is fully built. NyayaAgent.house(root) already follows this same lazy
    # pattern (only called inside the request handler via `factory`), mirrored here for the same reason.
    _state_lock = threading.Lock()
    _state: dict[str, Any] = {}

    def _get_state() -> tuple[PartnerApiConfig, RateLimiter, ConcurrencyLimiter, RateLimiter]:
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
            return (
                _state["cfg"], _state["rate_limiter"], _state["concurrency"], _state["provision_rate_limiter"]
            )

    def _provision_rate_limit(request: Request) -> JSONResponse | None:
        """`None` when the call may proceed; a ready-to-return 429 otherwise. Keyed by client IP the same
        way `analyse_facts_ep` keys its own limiter (`_client_ip`, honoring `trust_proxy_header` /
        `trusted_proxies` identically) -- a caller who can burn analyse-facts's GPU-time budget from one IP
        can burn the provisioning budget from that same IP, and both must be told apart the same way."""
        cfg, _rate_limiter, _concurrency, provision_rate_limiter = _get_state()
        ip = _client_ip(request, trust_proxy_header=cfg.trust_proxy_header, trusted_proxies=cfg.trusted_proxies)
        if provision_rate_limiter.allow(ip):
            return None
        return JSONResponse(
            status_code=429,
            content={"detail": "rate limit exceeded"},
            headers={"Retry-After": str(provision_rate_limiter.retry_after_seconds())},
        )

    _key_rate_limiter = tenancy.KeyRateLimiter()

    router = APIRouter(prefix="/api/v1")

    @router.post("/analyse-facts", response_model=AnalyseFactsResponse)
    def analyse_facts_ep(
        req: AnalyseFactsRequest, request: Request, user: User | None = CurrentUserDep
    ) -> dict[str, Any] | JSONResponse:
        del user  # optional session identity today; see module docstring on tenancy/API-key auth
        cfg, rate_limiter, concurrency, _provision_rate_limiter = _get_state()
        ip = _client_ip(request, trust_proxy_header=cfg.trust_proxy_header, trusted_proxies=cfg.trusted_proxies)
        if not rate_limiter.allow(ip):
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded"},
                headers={"Retry-After": str(rate_limiter.retry_after_seconds())},
            )
        if not any(f.strip() for f in req.facts):
            raise HTTPException(422, "at least one non-empty fact is required")
        for f in req.facts:
            if len(f) > 4000:
                raise HTTPException(422, "a fact may not exceed 4000 characters")

        if not concurrency.acquire():
            raise HTTPException(503, "the nyaya agent is at capacity; retry shortly")
        try:
            agent = factory(engine_root)
            result = agent.run(
                req.facts, narrative=req.narrative, contract_ids=req.contract_ids, sections=req.sections,
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
            return JSONResponse(status_code=503, content={"error": "judge_unavailable"})
        body: dict[str, Any] = result.to_dict()
        body.pop("audit_path", None)
        return body

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
        return UsageOut(
            key_id=key.key_id,
            org_id=key.org_id,
            calls_since_process_start=_key_rate_limiter.usage_total(key.key_id),
        ).model_dump()

    return router

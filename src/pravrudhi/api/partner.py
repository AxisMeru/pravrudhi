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
import copy
import functools
import hashlib
import hmac
import json
import logging
import math
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
from pydantic import BaseModel, Field, StrictBool, computed_field, field_validator
from starlette.responses import JSONResponse

from pravrudhi import __version__
from pravrudhi.api.errors import AGENT_AT_CAPACITY, AGENT_UNAVAILABLE, INTAKE_TIMEOUT, coded_500, coded_503
from pravrudhi.api.identity import CurrentUserDep, User
from pravrudhi.application import audit, citation_status, tenancy
from pravrudhi.application import nyaya_lean_registry as reg
from pravrudhi.application.a0_assist.intake_budget import IntakeTimeout
from pravrudhi.application.citation_wording import citation_note
from pravrudhi.application.config_files import config_file
from pravrudhi.application.hearing_pack.driver import PackDriver
from pravrudhi.application.hearing_pack.pack import attach_pack
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
from pravrudhi.application.nyaya_judges import SecondJudgeCircuitBreaker, standard_for_posture
from pravrudhi.application.nyaya_quote import Reason as QuoteCheck
from pravrudhi.application.second_judge_positive_control import fallback_record_ok
from pravrudhi.application.service_window import ServiceWindow
from pravrudhi.application.statute_citations import contract_citations
from pravrudhi.application.steps_appendix.appendix import safe_appendix
from pravrudhi.application.verify import exists_in_index_detail, index_coverage, warm_title_cache
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
    #: the `wf_*` switches of partner_api.yaml that are ON, as the routes read them (only the boolean true counts): `{key: true}`;
    #: an ABSENT key means off or not released (a card whose key is absent is "coming"). Flags only: no counts, no tenant data,
    #: no values of any other key, and the name of an off or unreleased workflow is never published (public, unauthenticated).
    workflows: dict[str, bool] = Field(
        default_factory=dict,
        description=(
            "The workflow switches that ARE ON ({key: true}). "
            "An absent key means off or not released: off names are never published."
        ),
    )


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
    #: Async jobs (#406): how long an accepted job may WAIT for a free agent slot (`max_concurrent`) before it fails with
    #: `agent_at_capacity`. A job was accepted with 202, so it queues; the sync route stays non-blocking (0 = never wait).
    job_slot_wait_s: float = 600.0
    #: Failed-key attempts per address per minute before further key checks from it are refused with 429 (#396). None = the
    #: same as `rate_limit_per_minute` (the default). Set it higher when callers behind a gateway share one peer address and
    #: a flood of bad keys must not lock the valid keyed callers out; env override PRAVRUDHI_BAD_KEY_BUDGET_PER_MINUTE.
    bad_key_budget_per_minute: int | None = None
    #: Audit rows (#148) older than this are dropped on write and never served.
    audit_retention_s: float = 90 * 86400.0
    #: `POST /verify-citations` (#302): how many lookups may run at once (non-blocking: an extra one is a 503) and the
    #: wall-clock bound on one lookup against the case index (an exceeded bound is a 503, never a hung worker).
    verify_max_concurrent: int = 2
    verify_timeout_s: float = 5.0
    #: `POST /intake`: the wall-clock budget of the whole intake (text processing and its citation lookups; the lookups alone are
    #: held to INTAKE_LOOKUP_BUDGET_S). An exceeded budget is a 503 `intake_timeout`; 0 turns the budget off.
    intake_timeout_s: float = 30.0
    #: Per-client-IP requests per minute for the OPTIONAL workflow routes (A1-A5, B3, S1-S3 and the bail draft), each with its own
    #: limiter. None = the same as `rate_limit_per_minute`. The shipped config sets 30: a preview user adding four papers and then
    #: searching made more than 6 calls in a minute and was refused (Track B's quality pass). `rate_limit_per_minute` still guards
    #: analyse-facts.
    slice_rate_limit_per_minute: int | None = None

    @property
    def slice_ip_limit(self) -> int:
        return self.slice_rate_limit_per_minute or self.rate_limit_per_minute

    def __post_init__(self) -> None:
        t = self.intake_timeout_s
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) or t < 0:
            raise ValueError(f"intake_timeout_s must be a finite number of seconds, 0 or more (0 = off), got {t!r}"[:200])
        sl = self.slice_rate_limit_per_minute
        if sl is not None and (isinstance(sl, bool) or not isinstance(sl, int) or not 1 <= sl <= 600):
            raise ValueError(f"slice_rate_limit_per_minute must be None or an integer from 1 to 600, got {sl!r}"[:200])
        b = self.bad_key_budget_per_minute
        if b is not None and (isinstance(b, bool) or not isinstance(b, int) or not 1 <= b <= MAX_BAD_KEY_BUDGET):
            raise ValueError(
                f"bad_key_budget_per_minute must be None or an integer from 1 to {MAX_BAD_KEY_BUDGET}, got {b!r}"[:200]
            )
        if self.trust_proxy_header and not self.trusted_proxies:
            raise ValueError(
                "trust_proxy_header is set but trusted_proxies is empty -- refusing to start: an "
                "X-Forwarded-For header trusted with no allowlist of who may set it is a free rate-limit "
                "bypass for any caller"
            )


BAD_KEY_BUDGET_ENV = "PRAVRUDHI_BAD_KEY_BUDGET_PER_MINUTE"
MAX_BAD_KEY_BUDGET = 1_000  # each failed attempt costs a password-hash check: keep the worst case small


def _bad_key_budget(yaml_value: Any) -> int | None:
    """The failed-key budget per address per minute: the environment variable wins over the yaml key; unset or null = None (the
    caller then uses `rate_limit_per_minute`). A bool, a non-integer, zero, a negative or a value above the cap is a config
    error, never a silent default."""
    raw: Any = os.environ.get(BAD_KEY_BUDGET_ENV, "").strip() or yaml_value
    if raw is None:
        return None
    if isinstance(raw, str):
        if not raw.isascii() or not raw.isdigit():
            raise ValueError(f"{BAD_KEY_BUDGET_ENV} / bad_key_budget_per_minute must be a whole number, got {raw[:40]!r}")
        raw = int(raw)
    if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= MAX_BAD_KEY_BUDGET:
        raise ValueError(f"bad_key_budget_per_minute must be an integer from 1 to {MAX_BAD_KEY_BUDGET}, got {raw!r}"[:200])
    return raw


def _intake_timeout(raw: Any) -> float:
    """The file's `intake_timeout_s`, checked BEFORE it becomes a float: a number only (a `true`, a quoted "30" or a null is a
    typo that would otherwise turn into a 1 s, a 30 s or a bare error that does not name the key). Range and finiteness are
    checked by `PartnerApiConfig`."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"intake_timeout_s must be a number of seconds (0 = off), got {raw!r}"[:200])
    return float(raw)


#: #1038: switches cold start ON without a new image (a template env change). FAIL CLOSED: only the exact value "1" counts,
#: and it can only turn the flag ON; unset or any other value leaves the packaged yaml (`cold_start_wake: false`) in force.
#: The numbers and the waker secret are still validated as before; an unreadable yaml still means everything off.
COLD_START_ENV = "PRAVRUDHI_COLD_START_WAKE"
COLD_START_ENV_ON = "1"


def _peek_flags(engine_root: Path) -> dict[str, Any]:
    """ONE read of partner_api.yaml for the optional slices' switches, importing none of their modules (#986).

    With every flag off the process imports exactly what the released tree imports: the slice config readers
    (`draft_check_config`, `matter_spans_config`, ...) are imported only for a slice whose flag reads true here. A missing or
    unreadable file, or a body that is not a mapping, is `{}` (everything off, whatever the environment says). Never raises.
    """
    try:
        body = yaml.safe_load(config_file(Path(engine_root), "partner_api.yaml").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - an optional slice must never stop the engine's own routes from starting
        return {}
    if not isinstance(body, dict):
        return {}
    if os.environ.get(COLD_START_ENV) == COLD_START_ENV_ON:  # exact match only: "true", "0", " 1", "" and unset all keep the yaml
        return {**body, "cold_start_wake": True}
    return body


def _peek_matter_store_dir(engine_root: Path, flags: dict[str, Any]) -> Path:
    """Where A2's store would be, WITHOUT importing `matter_spans_config` (its `store_dir`, mirrored: the environment variable,
    else the yaml key `matter_spans_dir`, else `<root>/matter_spans`); a test pins this function equal to `store_dir`."""
    env = os.environ.get("PRAVRUDHI_MATTER_SPANS_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    raw = flags.get("matter_spans_dir")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser()
    return Path(engine_root) / "matter_spans"


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
        job_slot_wait_s=_job_slot_wait(body.get("job_slot_wait_s", 600.0)),
        bad_key_budget_per_minute=_bad_key_budget(body.get("bad_key_budget_per_minute")),
        audit_retention_s=float(body.get("audit_retention_s", 90 * 86400.0)),
        verify_max_concurrent=int(body.get("verify_max_concurrent", 2)),
        verify_timeout_s=float(body.get("verify_timeout_s", 5.0)),
        intake_timeout_s=_intake_timeout(body.get("intake_timeout_s", 30.0)),
        slice_rate_limit_per_minute=body.get("slice_rate_limit_per_minute"),
    )


#: One day: more than any job should queue, and far below `threading.TIMEOUT_MAX` (a larger wait raises OverflowError).
MAX_JOB_SLOT_WAIT_S = 86_400.0


def _job_slot_wait(raw: Any) -> float:
    """`job_slot_wait_s`: a finite number from 0 to one day (a bool, NaN, inf, a negative or a larger value is a config
    error, never a silent default)."""
    ok = isinstance(raw, (int, float)) and not isinstance(raw, bool)
    if ok:
        try:
            ok = math.isfinite(raw) and 0 <= raw <= MAX_JOB_SLOT_WAIT_S
        except OverflowError:  # an int too large for a float
            ok = False
    if not ok:
        raise ValueError(f"job_slot_wait_s must be a number from 0 to {MAX_JOB_SLOT_WAIT_S:.0f} seconds, got {raw!r}"[:200])
    return float(raw)


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

    @property
    def per_minute(self) -> int:
        return self._per_minute

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

    def peek(self, key: str) -> bool:
        """Would `allow(key)` admit right now? Reads only: nothing is counted and no entry is created or evicted."""
        window = int(self._now() // 60)
        with self._lock:
            entry = self._windows.get(key)
            if entry is None or entry[0] != window:
                return True
            return entry[1] < self._per_minute

    def retry_after_seconds(self) -> int:
        """Seconds until the current window rolls over -- always a whole window's worth or less, never
        computed per-key (a caller who is refused does not need to know anyone else's window state)."""
        return 60 - int(self._now() % 60)


def rate_limited_response(
    *, limit: int, retry_after: int, scope: str, headers: dict[str, str] | None = None, sign_in: bool = True
) -> JSONResponse:
    """The 429 body (#403): the machine field `detail` and the `Retry-After` header are unchanged; the added fields say the
    limit, the window and what to do. `scope` is "anonymous" (no signed-in user and no key), "caller" (a signed-in user
    or key whose address bucket fired) or "key" (a key's own per-minute limit)."""
    if scope == "anonymous" and not sign_in:
        # a route that only knows API keys (verify-citations): signing in would not give a separate limit there
        message = (
            f"You have reached the limit for callers without an API key ({limit} requests per minute). "
            f"Try again in {retry_after} seconds, or use an API key to get your own limit."
        )
    elif scope == "anonymous":
        message = (
            f"You have reached the limit for visitors who are not signed in ({limit} requests per minute). "
            f"Try again in {retry_after} seconds, or sign in or use an API key to get your own limit."
        )
    elif scope == "key":
        message = f"Your key has reached its limit of {limit} requests per minute. Try again in {retry_after} seconds."
    else:
        message = f"You have reached your limit of {limit} requests per minute. Try again in {retry_after} seconds."
    return JSONResponse(
        status_code=429,
        content={
            "detail": "rate limit exceeded",
            "message": message,
            "limit_per_minute": limit,
            "window_seconds": 60,
            "retry_after_seconds": retry_after,
            "scope": scope,
        },
        headers={**(headers or {}), "Retry-After": str(retry_after)},
    )


class ConcurrencyLimiter:
    """A non-blocking cap on in-flight calls: `acquire` either succeeds immediately or fails immediately --
    never blocks waiting for room, because queuing an anonymous caller behind someone else's GPU call is
    exactly the unbounded-wait this exists to prevent."""

    def __init__(self, max_concurrent: int) -> None:
        self._sem = threading.Semaphore(max_concurrent)

    def acquire(self, timeout: float = 0.0) -> bool:
        """Non-blocking by default. With `timeout` > 0 (async jobs only, #406) waits up to that many seconds for a slot."""
        if timeout > 0:
            # a longer wait than TIMEOUT_MAX raises OverflowError even for a free slot
            return self._sem.acquire(timeout=min(timeout, threading.TIMEOUT_MAX))
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
    #: Who asked, for the assisted route's anti-abuse limits only (never stored, never logged).
    user_id: str | None = None
    org_id: str | None = None
    ip: str | None = None


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
    result: str = Field(
        description=(
            "VERIFIED, EXISTS_QUOTE_NOT_FOUND, IN_INDEX (no quote sent), NOT_IN_INDEX, CONFLICT, MALFORMED or "
            "NAME_MISMATCH (#509: the citation resolves to one indexed case but the case name sent with it does "
            "not match that case). Handle a value you do not know."
        ),
    )
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
    #: #509: present (true) only when a case name typed in front of the citation was COMPARED with the index: an IN_INDEX where
    #: every typed
    #: party matched, or NAME_MISMATCH. Absent from every other reply (no name, a name with no distinctive word, any other
    #: result, the quote
    #: path), which is therefore byte-identical to the reply before this field existed.
    name_compared: bool | None = Field(
        default=None,
        description=(
            "True only when a case name typed in front of the citation was compared with the index: for an "
            "IN_INDEX, when every party typed matched a party of the indexed case (a party named only by common "
            "words matches only when it equals the case's own party), and for NAME_MISMATCH. The field is absent "
            "from every other reply, including an IN_INDEX where the name only partly matched."
        ),
    )


_VERIFY_NOTES = {
    "VERIFIED": "The citation resolves to an indexed case and the quote appears in its text.",
    "EXISTS_QUOTE_NOT_FOUND": "The citation resolves to an indexed case but the quote was not found in its text.",
    "IN_INDEX": "Found in the index (existence only): no quote was checked, so this is not a verification.",
    "NAME_MISMATCH": (
        "The citation resolves to an indexed case, but the case name given does not match that case. "
        "This does not show whether the citation or the name is right."
    ),
    "NOT_IN_INDEX": (
        "We could not match this citation to exactly one case in our index. "
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


class AssistFactsRequest(AnalyseFactsRequest):
    """The assisted route's body: the analyse-facts body plus the caller's per-request consent and an opaque echo id."""

    consent: bool = False
    session_id: str | None = Field(default=None, max_length=128)
    #: Which assisted workflow to run (a workflow with no configured agent answers with the ordinary result, `not_configured`).
    #: Intake has its own routes (`/intake`, `/intake/assist`); it is not a value here (it would only answer `not_configured`).
    workflow: Literal["screening", "hearing_prep"] = "screening"

    @field_validator("workflow", mode="before")
    @classmethod
    def _intake_has_its_own_routes(cls, v: Any) -> Any:
        if v == "intake":
            raise ValueError("intake has its own routes: use POST /api/v1/intake or POST /api/v1/intake/assist")
        return v


#: Citation lookups per intake request; further citations are listed with the status NOT_CHECKED.
INTAKE_MAX_CITATION_LOOKUPS = 50
#: Total seconds one intake request may spend on citation lookups; later citations are listed NOT_CHECKED.
INTAKE_LOOKUP_BUDGET_S = 15.0


class IntakeRequest(BaseModel):
    """Intake (pravrudhi#414): pasted text only. The text is read and cleaned by the engine; nothing is stored."""

    text: str = Field(min_length=1, max_length=200_000)
    consent: StrictBool = False  # strict: "1" or "yes" must never send a user's text to a third party
    session_id: str | None = Field(default=None, max_length=128)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text must not be blank")
        return v


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


class AssistFactsResponse(AnalyseFactsResponse):
    """The analyse-facts response plus the `assistant` block (see application/a0_assist/assist.py)."""

    assistant: dict[str, Any]
    #: A finished assisted run: the steps it took, counted from the recorded tool calls (#416). Absent on every fallback.
    appendix: dict[str, Any] | None = None
    #: Only for `workflow: "hearing_prep"`: the hearing-preparation pack, built by fixed rules from the decide result (#415).
    pack: dict[str, Any] | None = None


class IntakeResponse(BaseModel):
    """The deterministic intake result: the bundle of machine tokens (no model prose)."""

    run_id: str
    client_data: bool
    bundle: dict[str, Any]


class IntakeAssistResponse(IntakeResponse):
    """The intake bundle plus the `assistant` block (see application/a0_assist/assist.py)."""

    assistant: dict[str, Any]


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
    mode: Literal["sync", "job", "verify", "assist", "intake", "intake_assist"]
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


def client_ip_proof(request: Request) -> str:
    """Whether the request carries a valid proof that the gateway read the client address: a label, never a value.

    "valid" (the shared secret matches and an address came with it), "no_ip" (the secret matches, no address came), "mismatch"
    (a proof header was sent and it is wrong), "none" (no proof header was sent: the gateway is not signing), "engine_unset"
    (this engine has no secret) or "engine_short" (this engine's secret is shorter than the minimum, so it is ignored)."""
    secret = os.environ.get(CLIENT_IP_SECRET_ENV, "")
    if not secret:
        return "engine_unset"
    if len(secret) < MIN_CLIENT_IP_SECRET_LENGTH:
        _warn_short_client_ip_secret_once()
        return "engine_short"
    presented = request.headers.get(CLIENT_IP_SECRET_HEADER, "")
    if not presented:
        return "none"
    # bytes, not str: compare_digest raises TypeError on a non-ASCII str (a header byte above 0x7f arrives as latin-1)
    if not hmac.compare_digest(presented.encode("utf-8"), secret.encode("utf-8")):
        return "mismatch"
    return "valid" if request.headers.get(CLIENT_IP_HEADER, "").strip() else "no_ip"


def client_ip_and_source(
    request: Request, *, trust_proxy_header: bool, trusted_proxies: tuple[str, ...], caller_key: str | None = None
) -> tuple[str, str]:
    """(address, source). `source` is "signed" (the proof held), "proxy" (a trusted proxy's X-Forwarded-For), "caller" (no
    proven address: bucketed by the caller's own identity) or "peer" (the socket peer: behind a gateway that is one shared
    address for every anonymous caller)."""
    # Preferred over everything below: a caller-specific key proven, by the shared secret, to have come
    # from the Worker's own read of Cloudflare's CF-Connecting-IP -- correct even when this engine sits
    # behind a proxy (RunPod's LB, a cloudflared tunnel) that presents every real caller as the same socket
    # peer, which would otherwise turn the per-IP rate limit into one shared budget for every caller at
    # once. Fails CLOSED, never open: a missing or too-short secret, a missing client-IP header, or a
    # mismatched secret all fall through to the existing (stricter, shared-key-prone) behavior below rather
    # than trusting an unproven header.
    if client_ip_proof(request) == "valid":
        return request.headers.get(CLIENT_IP_HEADER, "").strip(), "signed"

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
            return forwarded.split(",")[0].strip(), "proxy"
    # No proven client address. Behind the gateway the socket peer is the proxy itself, so every caller would share ONE
    # bucket: a caller that is identified (an API key, or a signed-in user) is bucketed by that identity instead. A client
    # address header that is not signed with the shared secret is never read, so a forged one can neither raise nor dodge
    # a limit: rotating it changes nothing. An anonymous caller keeps the socket peer (the stricter shared bucket).
    if caller_key:
        return caller_key, "caller"
    return socket_peer, "peer"


def _client_ip(
    request: Request, *, trust_proxy_header: bool, trusted_proxies: tuple[str, ...], caller_key: str | None = None
) -> str:
    return client_ip_and_source(
        request, trust_proxy_header=trust_proxy_header, trusted_proxies=trusted_proxies, caller_key=caller_key
    )[0]


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


def _warm_citation_index(path: str) -> None:
    """The start-up warm-up thread's body: fail-open. Nothing waits for it (/health and the routes do not), a failure is logged
    once and the first lookup then loads what it needs itself, as before (#478)."""
    try:
        warm_title_cache(path)
    except Exception:
        _logger.warning("citation index warm-up failed; the first lookup will load the titles itself", exc_info=True)


def _judge_fault_is_warmup(e: BaseException, warm_window: bool) -> bool:
    """#1041: is this judge configuration fault really a judge that is still starting? True for a 409 whose body names
    ENDPOINT_PAUSED (a paused front door is a state, never a bad key or model), and, inside the warm window (the request carries a
    valid wake ticket, i.e. the judges were woken within the ticket's lifetime), for any other 4xx except 429. Anything else
    (an unknown 4xx outside the window) stays a configuration fault. Pure."""
    from pravrudhi.models.openai_compat import HTTPStatusError

    seen: BaseException | None = e
    while seen is not None:
        if isinstance(seen, HTTPStatusError) and 400 <= seen.status < 500 and seen.status != 429:
            return (seen.status == 409 and "ENDPOINT_PAUSED" in str(seen)) or warm_window
        seen = seen.__cause__
    return False


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
    partial_agent_factory: Callable[[Path, SecondJudgeCircuitBreaker], Any] | None = None,
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
    `partial_agent_factory` (#1038, used only by the cold-start wake route when `cold_start_wake` is on): builds the agent for ONE
    partial run (the first judge up, the second not) with a request-scoped, already-tripped breaker; production leaves it `None`
    and gets `NyayaAgent.house(root, second_judge_breaker=...)`, a test that injects only `agent_factory` gets that agent.
    """
    # Engine start: load the citation index's titles into memory (and its file into the OS cache) off the request path, so the
    # first lookup after a start or a long idle does not pay for it inside the 5 s deadline (#478). Only the env-named index:
    # a test that injects `citation_index_path` builds its own small index and needs no warm-up. PRAVRUDHI_CITATION_WARM=0
    # opts out.
    _warm_path = os.environ.get("PRAVRUDHI_CITATION_INDEX")
    if citation_index_path is None and _warm_path and os.environ.get("PRAVRUDHI_CITATION_WARM", "1") != "0":
        threading.Thread(target=_warm_citation_index, args=(_warm_path,), daemon=True, name="citation-warm").start()
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
                clock_kw: dict[str, Any] = {} if rate_clock is None else {"now": rate_clock}
                _state["rate_limiter"] = RateLimiter(cfg.rate_limit_per_minute, max_keys=cfg.rate_limit_max_keys, **clock_kw)
                _state["concurrency"] = ConcurrencyLimiter(cfg.max_concurrent)
                # A separate, much lower-budget limiter for the tenancy provisioning routes: sharing the
                # analyse-facts limiter would mean a normal partner workload and a guessed-secret attempt
                # against /api/v1/orgs draw from the same 6-per-minute budget, which is generous for the
                # latter and would also let provisioning traffic starve analyse-facts's own limit.
                _state["provision_rate_limiter"] = RateLimiter(
                    cfg.provision_rate_limit_per_minute, max_keys=cfg.rate_limit_max_keys, **clock_kw
                )
                # Issue #35: ONE breaker for this process's whole lifetime, shared across every request's
                # NyayaAgent (and its whole judge_pool) -- a real second-judge failure trips it for every
                # later request within the TTL, not just the rest of the one that hit it.
                _state["second_judge_breaker"] = SecondJudgeCircuitBreaker(
                    ttl_s=cfg.second_judge_circuit_breaker_ttl_s
                )
                # Failed-key attempts per proven address (or socket peer): a request with a key that does not verify costs a
                # password-hash check, so these are budgeted BEFORE the check and charged only when it fails.
                _state["bad_key_limiter"] = RateLimiter(
                    cfg.bad_key_budget_per_minute or cfg.rate_limit_per_minute, max_keys=cfg.rate_limit_max_keys, **clock_kw
                )
            return (
                _state["cfg"], _state["rate_limiter"], _state["concurrency"], _state["provision_rate_limiter"],
                _state["second_judge_breaker"],
            )

    def _bad_key_addr(request: Request, cfg: PartnerApiConfig) -> str:
        return _client_ip(request, trust_proxy_header=cfg.trust_proxy_header, trusted_proxies=cfg.trusted_proxies)

    def _charge_bad_key(request: Request, cfg: PartnerApiConfig) -> None:
        with _state_lock:
            limiter = _state["bad_key_limiter"]
        limiter.allow(_bad_key_addr(request, cfg))

    def _principal_or_throttle(
        request: Request, cfg: PartnerApiConfig
    ) -> tuple[tenancy.OrgPrincipal | None, JSONResponse | None]:
        """The verified API-key principal (None when no key header was sent), with the failed-key budget applied.

        Checking a key costs a password-hash verification, so an address that has already failed `rate_limit_per_minute` times
        this minute is refused with 429 BEFORE any further check is spent on it; a failure (an unknown key, a bad secret, a
        revoked key) is charged to that address after the check. A valid key is never charged here. The address is the signed
        client address when the gateway proved one, else the socket peer (shared behind the gateway until the signed path is
        configured: then a flood of bad keys from one peer also refuses keyed callers behind it for the minute)."""
        if not request.headers.get(tenancy.API_KEY_HEADER):
            return None, None
        with _state_lock:
            limiter = _state["bad_key_limiter"]
        addr = _bad_key_addr(request, cfg)
        if not limiter.peek(addr):
            return None, JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded"},
                headers={"Retry-After": str(limiter.retry_after_seconds())},
            )
        try:
            return tenancy.principal_from_headers(engine_root, request.headers), None
        except HTTPException as e:
            if e.status_code == 401:
                limiter.allow(addr)
            raise

    factory: AgentFactory = agent_factory or (
        lambda r: NyayaAgent.house(r, second_judge_breaker=_get_state()[4])
    )
    def _house_with(r: Path, b: SecondJudgeCircuitBreaker) -> Any:
        return NyayaAgent.house(r, second_judge_breaker=b)

    partial_factory: Callable[[Path, SecondJudgeCircuitBreaker], Any] = partial_agent_factory or (
        (lambda r, b: agent_factory(r)) if agent_factory is not None else _house_with
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

    def _judges_offline(cfg: PartnerApiConfig, *, skip_second: bool = False) -> JSONResponse | None:
        """#311: 503 `judges_offline` when a needed judge endpoint is parked, before anything is metered or queued. `skip_second`
        (a valid partial wake ticket, #1038) leaves the second judge out of the check; the first judge is always checked."""
        if not cfg.judge_health_check:
            return None
        try:
            ac = getattr(factory(engine_root), "config", None)
            judges = {"house": dict(ac.house_judge or {})} if ac is not None else {}
            if ac is not None and ac.second_judge and not skip_second:
                judges["second"] = dict(ac.second_judge)
            _offline_check.ttl_s = cfg.judge_health_ttl_s
            keys = {"house": "NYAYA_HOUSE_JUDGE_API_KEY", "second": "NYAYA_SECOND_JUDGE_API_KEY"}
            max_age = float((ac.second_judge_positive_control if ac is not None else {}).get("max_age_hours", 24.0))

            def _fallback_ok(endpoint_id: str, block: Mapping[str, Any]) -> bool:
                # a RunPod fallback the engine would refuse to call (no passing record) is not a way to answer
                return not block.get("fallback_record_dir") or fallback_record_ok(
                    block, endpoint_id, max_age_hours=max_age
                )[0]

            parked = _offline_check.offline(judges, keys, fallback_ok=_fallback_ok)
        except Exception:  # noqa: BLE001 - the check must never turn a working request into an error
            _logger.warning("judge health check failed; proceeding")
            return None
        if not parked:
            return None
        _judge_seen.update(state="unavailable", at=_now(), first_failure=None)
        return JSONResponse(status_code=503, content={"error": "judges_offline"})

    router = APIRouter(prefix="/api/v1")

    _status_judges: list[Callable[[], dict[str, str]]] = []  # filled only when cold-start wake is configured

    class _NoTicket:
        """Cold start is not configured: a ticket header is never read and nothing is extended (no I/O at all)."""

        def mode(self, request: Request, user: User | None) -> str | None:
            return None

        def extend(self) -> None:
            return None

    _tickets: list[Any] = [_NoTicket()]  # replaced by the real hook only when cold-start wake is configured

    def _wp_breaker() -> SecondJudgeCircuitBreaker:
        """The request-scoped, already-tripped breaker of a partial run (reached only with a valid partial ticket)."""
        from pravrudhi.application.wake_policy import partial_run_breaker

        return partial_run_breaker()

    @router.get("/status", response_model=ServiceStatusOut)
    def status_ep() -> Any:
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
        out = ServiceStatusOut(
            engine_version=__version__,
            service_window=window,
            judge=JudgeStateOut(
                state=state,
                checked_at=seen.isoformat(timespec="seconds") if (fresh and seen) else None,
            ),
            workflows={k: True for k, v in sorted(_flags.items()) if isinstance(k, str) and k.startswith("wf_") and v is True},
        )
        if not _status_judges:
            return out
        # #1038: only when cold-start wake is configured, `judges` (first/second: ready|warming|parked|unknown) is added to the
        # body OUTSIDE the published schema; a cached read of each endpoint's own /health, never a probe that wakes anything.
        return JSONResponse(content={**out.model_dump(mode="json"), "judges": _status_judges[0]()})

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
        ticket = _tickets[0].mode(request, user)  # None unless cold start is on AND a valid ticket of an allowlisted user came
        admitted = _admit(req, request, user, metered, per_minute, skip_second=ticket == "partial")
        if isinstance(admitted, JSONResponse):
            return admitted
        if ticket is None:
            return _run(req, admitted, debug_second_judge)
        _tickets[0].extend()  # best effort, rate-deduped, never raises
        try:
            breaker = _wp_breaker() if ticket == "partial" else None
            return _run(req, admitted, debug_second_judge, breaker=breaker, warm_window=True)
        finally:
            _tickets[0].extend()

    def _admit(
        req: Any, request: Request, user: User | None, metered: list[str], per_minute: list[int],
        *, screening: bool = True, skip_second: bool = False,
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
        # intake (not screening) uses no judge; a valid partial wake ticket (#1038) leaves only the second judge out of the check
        offline = _judges_offline(cfg, skip_second=skip_second) if screening else None
        if offline is not None:
            return offline
        # QUEUE.md 2026-09-27: per-leg second-judge scores are visible to an AUTHENTICATED caller -- a
        # verified Supabase session (`user`) or a valid org API key -- never to an anonymous one, even on a
        # deployment that answers analyse-facts anonymously (`PRAVRUDHI_DEMO_ANON_PATHS`). `principal_from_
        # headers` raises 401 itself when a key header WAS sent but does not verify, the same as `usage_ep`;
        # it never treats a bad key as "no key" (a caller who supplied a bad key is never silently anonymous).
        principal, throttled = _principal_or_throttle(request, cfg)
        if throttled is not None:
            return throttled
        if principal is not None:
            from pravrudhi.application.credentials import serving_org

            serving_org.set(principal.org_id)
        authenticated = user is not None or principal is not None
        caller_key = (
            f"key:{principal.key_id}" if principal is not None else (f"user:{user.id}" if user is not None else None)
        )
        ip = _client_ip(
            request, trust_proxy_header=cfg.trust_proxy_header, trusted_proxies=cfg.trusted_proxies, caller_key=caller_key
        )
        if not rate_limiter.allow(ip):
            return rate_limited_response(
                limit=rate_limiter.per_minute, retry_after=rate_limiter.retry_after_seconds(),
                scope="caller" if authenticated else "anonymous",
            )
        if principal is not None:
            key = tenancy.key_record(engine_root, principal.key_id, org_id=principal.org_id)
            if key is None:
                _charge_bad_key(request, cfg)
                raise HTTPException(401, "Invalid or revoked API key")
            if not _key_rate_limiter.allow(key.key_id, key.rate_limit_per_minute):
                return rate_limited_response(
                    limit=key.rate_limit_per_minute, retry_after=_key_rate_limiter.retry_after_seconds(), scope="key",
                    headers=_rate_headers(key.key_id, key.rate_limit_per_minute),
                )
            metered.append(key.key_id)
            per_minute.append(key.rate_limit_per_minute)
            _record_usage(key.key_id)
        if screening:
            if not any(f.strip() for f in req.facts):
                raise HTTPException(422, "at least one non-empty fact is required")
            for f in req.facts:
                if len(f) > 4000:
                    raise HTTPException(422, "a fact may not exceed 4000 characters")
        return _Admitted(
            cfg,
            concurrency,
            authenticated,
            user_id=user.id if user is not None else (f"key:{principal.key_id}" if principal is not None else None),
            org_id=principal.org_id if principal is not None else None,
            ip=ip,
        )

    def _run(
        req: AnalyseFactsRequest, admitted: _Admitted, debug_second_judge: bool, *, slot_held: bool = False,
        breaker: SecondJudgeCircuitBreaker | None = None, warm_window: bool = False,
    ) -> dict[str, Any] | JSONResponse:
        cfg, concurrency, authenticated = admitted.cfg, admitted.concurrency, admitted.authenticated
        if not slot_held and not concurrency.acquire():
            return coded_503(AGENT_AT_CAPACITY)
        try:
            agent = partial_factory(engine_root, breaker) if breaker is not None else factory(engine_root)
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
            if _judge_fault_is_warmup(e, warm_window):
                # #1041: a judge that was just woken answered 4xx (e.g. 409 ENDPOINT_PAUSED while RunPod's front door catches up):
                # not a bad key or model, so the answer is "warming, retry", never the agent-unavailable marker
                _logger.warning("judge refused during warm-up; answering judges_warming", exc_info=True)
                retry = int(cfg.judge_warm_retry_s)
                return JSONResponse(
                    status_code=503,
                    content={"error": "judge_unavailable", "reason": "judges_warming", "retry_after_s": retry},
                    headers={"Retry-After": str(retry)},
                )
            _logger.warning("nyaya agent unavailable", exc_info=True)  # the exception is logged server-side only (#318)
            return coded_503(AGENT_UNAVAILABLE)
        except (BinaryShaMismatch, OSError):  # FileNotFoundError is an OSError
            _logger.warning("nyaya agent unavailable", exc_info=True)  # the exception is logged server-side only (#318)
            return coded_503(AGENT_UNAVAILABLE)
        finally:
            if not slot_held:
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
        listed = getattr(result, "listed_sources", None)
        return _shape_body(agent, result.to_dict(), listed, authenticated, debug_second_judge, cfg)

    def _shape_body(
        agent: Any,
        body: dict[str, Any],
        listed: dict[str, list[str]] | None,
        authenticated: bool,
        debug_second_judge: bool,
        cfg: PartnerApiConfig,
    ) -> dict[str, Any]:
        """The response post-processing shared by the core run and the assisted run: citations, set versions, rule text
        and second-judge diagnostic fields."""
        body.pop("audit_path", None)
        _attach_citations(agent, body, listed)
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

    def _assisted_response(
        req: AssistFactsRequest, admitted: _Admitted, debug_second_judge: bool, rt: Any
    ) -> dict[str, Any] | JSONResponse:
        from pravrudhi.application import assistant_tier
        from pravrudhi.application.a0_assist import assist as a0
        from pravrudhi.application.a0_assist import ref_driver as a0rd

        matter = {"facts": list(req.facts), "narrative": req.narrative, "contract_ids": list(req.contract_ids)}
        box: dict[str, Any] = {}

        hearing = req.workflow == "hearing_prep"

        def make_driver() -> Any:
            box["agent"] = factory(engine_root)
            driver_cls: Any = PackDriver if hearing else a0rd.A0RefDriver
            return driver_cls(box["agent"], request=matter, proceeding_posture=req.proceeding_posture)

        def with_pack(body: Any) -> Any:
            return attach_pack(body, list(req.facts), req.narrative or "") if hearing else body

        def finalize(driver: Any) -> dict[str, Any]:
            agent = box["agent"]
            standard, source = standard_for_posture(req.proceeding_posture)
            in_prompt = getattr(agent.judge, "prompt_template", "legacy") != "legacy"
            body: dict[str, Any] = {
                "run_id": f"nyaya-assist-{os.urandom(5).hex()}",
                "judge": agent.judge.name,
                "score_sha256": agent.registry.sha256,
                "facts": [{"id": f["id"], "sha256": f["sha256"]} for f in driver._ingested["facts"]],
                "contracts": [driver._decided[c] for c in driver._selected or []],
                "provenance": "agama",
                "judge_accounting": {},
                "client_data": True,
                "standard": {
                    "requested": standard,
                    "applied": standard if in_prompt else None,
                    "source": "proceeding_posture" if source == "request" else "default",
                    "proceeding_posture": req.proceeding_posture,
                    "in_judge_prompt": in_prompt,
                },
            }
            shaped = _shape_body(agent, body, driver.listed, admitted.authenticated, debug_second_judge, admitted.cfg)
            appendix = safe_appendix(driver.calls)  # counted from the recorded calls only: no agent text
            if appendix is not None:
                shaped["appendix"] = appendix
            if hearing and driver._pack is not None:
                # the `pack` tool's own output (decide results in, fixed rules): never agent text
                shaped["pack"] = copy.deepcopy(driver._pack)
            return shaped

        deps = a0.Deps(
            cfg=rt.cfg,
            tier_status=lambda: assistant_tier.status(engine_root),
            store=rt.store,
            transport=rt.transport,
            limiter=rt.limiter,
            slots=rt.slots,
            core=lambda: with_pack(_run(req, admitted, debug_second_judge, slot_held=True)),
            make_driver=make_driver,
            finalize=finalize,
            workflows=frozenset({"screening", "hearing_prep"}),
            # a workflow other than screening must name its first message and its summary explicitly (#419)
            user_message=a0.user_message,
            summarise=a0.screening_summarise,
        )
        caller = a0.Caller(admitted.authenticated, admitted.user_id, admitted.org_id, admitted.ip)
        out = a0.run_assisted(matter, caller, req.consent, req.session_id, deps, workflow=req.workflow)
        if isinstance(out, dict):
            # The facts went to a third party for this request (or may have): the ordinary retention notice would be wrong here,
            # so no response of this route carries one. The consent line and the data-handling page say what is true.
            out.pop("retention_notice", None)
        result: dict[str, Any] | JSONResponse = out
        return result

    @router.post(
        "/analyse-facts/assist",
        response_model=AssistFactsResponse,
        response_model_exclude_unset=True,
        include_in_schema=False,  # ships dark: not in the public contract until the route is enabled in a release
        responses={
            400: {"description": "consent_required: the request did not carry consent=true."},
            404: {"description": "The assisted route is not enabled on this deployment."},
            429: {"description": "Over the rate limit; wait Retry-After seconds."},
            503: {"description": "Outside the service window, judges warming, or the agent at capacity or unavailable."},
        },
    )
    def analyse_facts_assist_ep(
        req: AssistFactsRequest,
        request: Request,
        response: Response,
        user: User | None = CurrentUserDep,
        debug_second_judge: bool = Query(False),
    ) -> dict[str, Any] | JSONResponse:
        from pravrudhi.application.a0_assist import runtime as a0rt

        rt = a0rt.get(engine_root)
        if rt is None or not rt.enabled:
            raise HTTPException(404, "assist_not_enabled")
        if req.consent is not True:
            return JSONResponse(status_code=400, content={"error": "consent_required"})
        if req.sections:
            raise HTTPException(422, "the assisted route selects contracts by contract_ids only")
        metered: list[str] = []
        per_minute: list[int] = []
        try:
            admitted = _admit(req, request, user, metered, per_minute)
            if isinstance(admitted, JSONResponse):
                _audit(metered, req, "assist", status_code=admitted.status_code)
                return admitted
            if not admitted.concurrency.acquire():
                return coded_503(AGENT_AT_CAPACITY)
            try:
                out = _assisted_response(req, admitted, debug_second_judge, rt)
            finally:
                admitted.concurrency.release()
        except HTTPException as e:
            _audit(metered, req, "assist", status_code=e.status_code)
            raise
        if metered and isinstance(out, JSONResponse) and out.status_code == 503:
            _record_usage(metered[0], failed=True)
        if metered and isinstance(out, dict):
            response.headers.update(_rate_headers(metered[0], per_minute[0]))
        if isinstance(out, JSONResponse):
            _audit(metered, req, "assist", status_code=out.status_code)
        else:
            _audit(metered, req, "assist", status_code=200, out=out)
        return out

    # --- intake (pravrudhi#414): pasted text -> a verified bundle; plain deterministic route and the assisted route ---------
    def _intake_exists_fn(cfg: PartnerApiConfig) -> Callable[[str], str] | None:
        """Citation text -> the verifier's own existence result, or None when this deployment has no citation index (the
        bundle then marks every citation NOT_CHECKED, never a guessed status)."""
        env_path = os.environ.get("PRAVRUDHI_CITATION_INDEX")
        path = citation_index_path or (Path(env_path) if env_path else None)
        if path is None or not Path(path).is_file():
            return None

        looked_up = [0]
        seen: dict[str, str] = {}
        started = time.monotonic()

        def exists(text: str) -> str:
            # a pasted text can hold thousands of citations: bound the lookups (each can take verify_timeout_s) by count and by
            # total time, and answer a repeated citation from the first answer (consistent, and free)
            if text in seen:
                return seen[text]
            if looked_up[0] >= INTAKE_MAX_CITATION_LOOKUPS or time.monotonic() - started > INTAKE_LOOKUP_BUDGET_S:
                return "NOT_CHECKED"
            looked_up[0] += 1
            # #509: Intake looks up the BARE citation (the extractor passes no case name), so no name is compared here and the
            # third item (name_compared) is always False; Intake never carries it and never sees NAME_MISMATCH.
            result, _cov, _compared = _lookup(Path(path), VerifyCitationRequest(citation=text), cfg.verify_timeout_s)
            seen[text] = result.result
            return result.result

        return exists

    def _intake_core(req: IntakeRequest, exists_fn: Callable[[str], str] | None) -> dict[str, Any]:
        """The plain deterministic intake result (no model): the same bundle the assisted run falls back to."""
        from pravrudhi.application.a0_assist import intake

        try:
            with intake.deadline(_get_state()[0].intake_timeout_s):
                bundle = intake.build_bundle(req.text, verify_fn=exists_fn)
        except intake.IntakeRefused as e:
            raise HTTPException(422, str(e)) from e
        return {"run_id": f"nyaya-intake-{os.urandom(5).hex()}", "client_data": True, "bundle": bundle}

    def _intake_audit(metered: list[str], mode: str, status_code: int, out: dict[str, Any] | None = None) -> None:
        """One audit row per key-admitted intake call: key, time, run id and HTTP status; never the text. A failure to write is
        logged, never allowed to change the answer."""
        if not metered:
            return
        try:
            audit.record(
                engine_root, key_id=metered[0], mode=mode, status_code=status_code, contract_ids=[],
                run_id=(out or {}).get("run_id"), outcomes={}, retention_s=_get_state()[0].audit_retention_s, now=_now(),
            )
        except Exception:
            _logger.exception("audit write failed for key %s", metered[0])

    def _intake_admit(req: IntakeRequest, request: Request, user: User | None, metered: list[str], per_minute: list[int]) -> Any:
        admitted = _admit(req, request, user, metered, per_minute, screening=False)
        if isinstance(admitted, JSONResponse):
            return admitted
        if not admitted.authenticated:
            return JSONResponse(status_code=401, content={"detail": "intake is offered only to signed-in users"})
        return admitted

    @router.post("/intake", include_in_schema=False, response_model=IntakeResponse)
    def intake_ep(
        req: IntakeRequest, request: Request, response: Response, user: User | None = CurrentUserDep
    ) -> dict[str, Any] | JSONResponse:
        metered: list[str] = []
        per_minute: list[int] = []
        admitted = _intake_admit(req, request, user, metered, per_minute)
        if isinstance(admitted, JSONResponse):
            return admitted
        if not admitted.concurrency.acquire():
            _intake_audit(metered, "intake", 503)
            return coded_503(AGENT_AT_CAPACITY)
        try:
            out = _intake_core(req, _intake_exists_fn(admitted.cfg))
        except IntakeTimeout:  # the budget is spent: the coded 503, no Retry-After (the same text would time out again)
            _intake_audit(metered, "intake", 503)
            return coded_503(INTAKE_TIMEOUT)
        except HTTPException as e:
            _intake_audit(metered, "intake", e.status_code)
            raise
        except Exception:
            _intake_audit(metered, "intake", 500)
            raise
        finally:
            admitted.concurrency.release()
        if metered:
            response.headers.update(_rate_headers(metered[0], per_minute[0]))
        _intake_audit(metered, "intake", 200, out)
        return out

    @router.post("/intake/assist", include_in_schema=False, response_model=IntakeAssistResponse)
    def intake_assist_ep(
        req: IntakeRequest, request: Request, response: Response, user: User | None = CurrentUserDep
    ) -> dict[str, Any] | JSONResponse:
        from pravrudhi.application import assistant_tier
        from pravrudhi.application.a0_assist import assist as a0
        from pravrudhi.application.a0_assist import runtime as a0rt
        from pravrudhi.application.a0_assist.intake_driver import IntakeDriver

        rt = a0rt.get(engine_root)
        if rt is None or not rt.enabled or rt.limiter is None or rt.slots is None:
            raise HTTPException(404, "assist_not_enabled")
        if req.consent is not True:
            return JSONResponse(status_code=400, content={"error": "consent_required"})
        metered: list[str] = []
        per_minute: list[int] = []
        admitted = _intake_admit(req, request, user, metered, per_minute)
        if isinstance(admitted, JSONResponse):
            return admitted
        exists_fn = _intake_exists_fn(admitted.cfg)

        def finalize(driver: Any) -> dict[str, Any]:
            return {"run_id": f"nyaya-intake-{os.urandom(5).hex()}", "client_data": True, "bundle": driver.result}

        deps = a0.Deps(
            cfg=rt.cfg,
            tier_status=lambda: assistant_tier.status(engine_root),
            store=rt.store,
            transport=rt.transport,
            limiter=rt.limiter,
            slots=rt.slots,
            core=lambda: _intake_core(req, exists_fn),
            make_driver=lambda: IntakeDriver(req.text, exists_fn=exists_fn, timeout_s=admitted.cfg.intake_timeout_s),
            finalize=finalize,
            workflows=frozenset({"intake"}),
            user_message=a0.intake_user_message,
            summarise=lambda driver, events: None,  # the result is the bundle; the agent's prose is never shown
        )
        caller = a0.Caller(admitted.authenticated, admitted.user_id, admitted.org_id, admitted.ip)
        if not admitted.concurrency.acquire():
            _intake_audit(metered, "intake_assist", 503)
            return coded_503(AGENT_AT_CAPACITY)
        try:
            out = a0.run_assisted({"text": req.text}, caller, req.consent, req.session_id, deps, workflow="intake")
        except IntakeTimeout:
            _intake_audit(metered, "intake_assist", 503)
            return coded_503(INTAKE_TIMEOUT)
        except HTTPException as e:
            _intake_audit(metered, "intake_assist", e.status_code)
            raise
        except Exception:
            _intake_audit(metered, "intake_assist", 500)
            raise
        finally:
            admitted.concurrency.release()
        if isinstance(out, dict):
            out.pop("retention_notice", None)
        if metered and isinstance(out, dict):
            response.headers.update(_rate_headers(metered[0], per_minute[0]))
        status = out.status_code if isinstance(out, JSONResponse) else 200
        _intake_audit(metered, "intake_assist", status, out if isinstance(out, dict) else None)
        result: dict[str, Any] | JSONResponse = out
        return result

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
        ticket = _tickets[0].mode(request, user)
        try:
            admitted = _admit(req, request, user, metered, [], skip_second=ticket == "partial")
        except HTTPException:
            store.discard(job_id)
            raise
        if isinstance(admitted, JSONResponse):
            store.discard(job_id)
            return admitted

        def _run_job() -> None:
            store.start(job_id)
            if ticket is not None:
                _tickets[0].extend()
            try:
                out = _run(
                    req, admitted, debug_second_judge, slot_held=True,
                    breaker=_wp_breaker() if ticket == "partial" else None,
                )
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
                store.fail(job_id, status_code=500, body=json.loads(bytes(coded_500().body)))
                return
            if isinstance(out, JSONResponse):
                if out.status_code == 503 and metered:
                    _record_usage(metered[0], failed=True)
                _audit(metered, req, "job", status_code=out.status_code)
                store.fail(job_id, status_code=out.status_code, body=json.loads(bytes(out.body)))
                return
            _audit(metered, req, "job", status_code=200, out=out)
            store.finish(job_id, result=out)
            if ticket is not None:
                _tickets[0].extend()

        def task() -> None:
            # #406: an accepted job stays "pending" until it holds an agent slot, and waits for one up to job_slot_wait_s
            # (counted from the moment a pool thread picks it up). Only then does it run; running jobs hold the slot.
            if not admitted.concurrency.acquire(admitted.cfg.job_slot_wait_s):
                if metered:
                    _record_usage(metered[0], failed=True)
                _audit(metered, req, "job", status_code=503)
                store.fail(job_id, status_code=503, body=json.loads(bytes(coded_503(AGENT_AT_CAPACITY).body)))
                return
            try:
                _run_job()
            finally:
                admitted.concurrency.release()

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
    def get_job_ep(job_id: str, request: Request) -> dict[str, Any] | JSONResponse:
        principal = _job_principal(request)
        try:
            cfg = _get_state()[0]
        except FileNotFoundError:
            return JSONResponse(status_code=503, content={"error": "service_config_missing"})
        job = _job_store(cfg).get(principal.key_id, job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        return {"job_id": job.job_id, "status": job.status, "result": job.result, "error": job.error}

    @router.get("/audit", response_model=AuditPageOut)
    def audit_ep(
        request: Request, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=audit.MAX_PAGE)
    ) -> dict[str, Any] | JSONResponse:
        principal = tenancy.principal_from_headers(engine_root, request.headers)
        if principal is None:
            raise HTTPException(401, "the audit log requires an API key (X-Pravrudhi-Api-Key)")
        try:
            cfg = _get_state()[0]
        except FileNotFoundError:
            return JSONResponse(status_code=503, content={"error": "service_config_missing"})
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
        principal, throttled = _principal_or_throttle(request, cfg)
        if throttled is not None:
            return throttled
        ip = _client_ip(
            request,
            trust_proxy_header=cfg.trust_proxy_header,
            trusted_proxies=cfg.trusted_proxies,
            caller_key=f"key:{principal.key_id}" if principal is not None else None,
        )
        if not rate_limiter.allow(ip):
            return rate_limited_response(
                limit=rate_limiter.per_minute, retry_after=rate_limiter.retry_after_seconds(),
                scope="caller" if principal is not None else "anonymous", sign_in=False,
            )
        if principal is not None:
            key = tenancy.key_record(engine_root, principal.key_id, org_id=principal.org_id)
            if key is None:
                _charge_bad_key(request, cfg)
                raise HTTPException(401, "Invalid or revoked API key")
            if not _key_rate_limiter.allow(key.key_id, key.rate_limit_per_minute):
                return rate_limited_response(
                    limit=key.rate_limit_per_minute, retry_after=_key_rate_limiter.retry_after_seconds(), scope="key",
                    headers=_rate_headers(key.key_id, key.rate_limit_per_minute),
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

    class _QuoteNotServed(Exception):  # noqa: N818 - control flow inside this closure, never leaves the route
        """The index holds no judgment text, so a quote can never be checked against it."""

    def _existence_only(conn: sqlite3.Connection) -> bool:
        """True when the index says it is existence-only (a slim index: citations without judgment text). FAIL CLOSED: a
        `meta` table with an `existence_only` row whose value is anything but exactly '0' is existence-only. No `meta` table
        at all is a full index (unchanged behaviour). Any other read problem raises and the route answers 503."""
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = 'existence_only'").fetchone()
        except sqlite3.OperationalError as e:
            if "no such table" in str(e).lower():
                return False
            raise
        return row is not None and row[0] != "0"

    def _lookup(
        path: Path, req: VerifyCitationRequest, timeout_s: float
    ) -> tuple[citation_status.CitationStatus, dict[str, Any] | None, bool]:
        """One bounded, read-only lookup; the third item is #509's name_compared (a typed case name was compared with the
        index). The progress handler aborts the statement once the deadline passes, so a
        pathological party pair can hold a worker for `timeout_s` at most."""
        deadline = time.monotonic() + timeout_s
        conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        try:
            conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
            name_compared = False
            if req.quote is None:
                answer = exists_in_index_detail(conn, req.citation)
                found, name_compared = citation_status.status_for(answer.result), answer.name_compared
            else:
                if _existence_only(conn):
                    raise _QuoteNotServed
                found = citation_status.status_for(verify_citation(conn, req.citation, req.quote))
            conn.set_progress_handler(None, 0)  # the deadline bounds the lookup; the (cached) coverage read is outside it
            try:
                coverage: dict[str, Any] | None = index_coverage(conn, str(path.resolve()), path.stat().st_mtime_ns)
            except Exception:
                # coverage is context for the answer, never a reason to lose it: degrade to the documented null
                _logger.warning("index coverage unavailable", exc_info=True)
                coverage = None
            return found, coverage, name_compared
        finally:
            conn.close()

    @router.post(
        "/verify-citations", response_model=VerifyCitationResponse, response_model_exclude_unset=True,
        responses={
            401: {"description": "The API key is invalid or revoked."},
            429: {"description": "Over the rate limit; wait Retry-After seconds.",
                  "headers": {**_RATE_LIMIT_HEADER_DOCS, "Retry-After": _RETRY_AFTER_DOC}},
            422: {"description": "Validation error, or `quote_not_served` ({\"error\": \"quote_not_served\"}): a quote was "
                  "sent but this deployment's index is existence-only (it holds no judgment text), so no quote can be "
                  "checked. Send the citation without a quote.",
                  "content": {"application/json": {"schema": {"$ref": "#/components/schemas/HTTPValidationError"}}}},
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
            result, coverage, name_compared = _lookup(Path(path), req, cfg.verify_timeout_s)
        except _QuoteNotServed:
            _verify_audit(metered, 422)
            return JSONResponse(status_code=422, content={"error": "quote_not_served"})
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
        reply: dict[str, Any] = {
            "result": result.result,
            "note": _VERIFY_NOTES[result.result],
            "status": result.status,
            "label": result.label,
            "verified": result.verified,
            "preview": result.preview,
            "coverage": coverage,
        }
        if name_compared:
            # #509: only when a typed case name was compared; absent otherwise (older replies unchanged)
            reply["name_compared"] = True
        return reply

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
        key = tenancy.key_record(engine_root, principal.key_id, org_id=org_id)
        if key is None:
            raise HTTPException(404, "key not found")
        # Calling /usage is itself the one key-scoped call this slice of the API has to spend a key's own
        # rate budget against -- the per-key limiter (distinct from analyse-facts's per-IP one) is exercised
        # here so a key's `rate_limit_per_minute` is real and testable even before matters/documents exist.
        if not _key_rate_limiter.allow(key.key_id, key.rate_limit_per_minute):
            return rate_limited_response(
                limit=key.rate_limit_per_minute, retry_after=_key_rate_limiter.retry_after_seconds(), scope="key"
            )
        calls, failed = tenancy.usage_counts(engine_root, key.key_id)
        return UsageOut(
            key_id=key.key_id,
            org_id=key.org_id,
            calls_since_process_start=_key_rate_limiter.usage_total(key.key_id),
            calls=calls,
            failed=failed,
        ).model_dump()

    # --- A1 whole-draft citation check: registered ONLY when partner_api.yaml sets wf_a1_draft_citation_check to true ---------
    # Off (the default, and every unreadable or malformed setting) adds nothing: no module import, no route, no limiter, no
    # thread. A new route spends none of an existing route's budget: it has its own limiters and its own slots.
    _flags = _peek_flags(engine_root)  # ONE read; no slice module is imported for a flag that is not true (#986)
    try:
        _a1_cfg = None
        if _flags.get("wf_a1_draft_citation_check") is True:
            from pravrudhi.application.draft_check_config import load_draft_check_config as _load_a1

            _a1_cfg = _load_a1(engine_root)
        if _a1_cfg is not None:
            from pravrudhi.application import draft_check as _a1
    except Exception:  # noqa: BLE001 - an optional route must never stop the engine's own routes from starting
        _logger.warning("draft check unavailable; it stays off", exc_info=True)
        _a1_cfg = None
    if _a1_cfg is not None:
        _a1_state: dict[str, Any] = {}
        _a1_lock = threading.Lock()

        def _a1_limits(cfg: PartnerApiConfig) -> tuple[RateLimiter, Any, ConcurrencyLimiter]:
            with _a1_lock:
                if not _a1_state:
                    clock_kw: dict[str, Any] = {} if rate_clock is None else {"now": rate_clock}
                    _a1_state["ip"] = RateLimiter(cfg.slice_ip_limit, max_keys=cfg.rate_limit_max_keys, **clock_kw)
                    _a1_state["key"] = (
                        tenancy.KeyRateLimiter(now=rate_clock) if rate_clock is not None else tenancy.KeyRateLimiter()
                    )
                    _a1_state["slots"] = ConcurrencyLimiter(_a1_cfg.max_concurrent)
                return _a1_state["ip"], _a1_state["key"], _a1_state["slots"]

        def _a1_admit(
            request: Request, user: User | None, metered: list[str]
        ) -> tuple[PartnerApiConfig, ConcurrencyLimiter] | JSONResponse:
            try:
                cfg = _get_state()[0]
            except FileNotFoundError:
                return JSONResponse(status_code=503, content={"error": "service_config_missing"})
            closed = _window_closed(cfg)
            if closed is not None:
                return closed
            principal, throttled = _principal_or_throttle(request, cfg)
            if throttled is not None:
                return throttled
            if user is None and principal is None:
                return JSONResponse(status_code=401, content={"detail": "the draft check is offered only to signed-in users"})
            caller_key = f"key:{principal.key_id}" if principal is not None else f"user:{user.id}"  # type: ignore[union-attr]
            ip = _client_ip(
                request, trust_proxy_header=cfg.trust_proxy_header, trusted_proxies=cfg.trusted_proxies, caller_key=caller_key
            )
            ip_limiter, key_limiter, slots = _a1_limits(cfg)
            if not ip_limiter.allow(ip):
                return rate_limited_response(
                    limit=ip_limiter.per_minute, retry_after=ip_limiter.retry_after_seconds(), scope="caller"
                )
            if principal is not None:
                key = tenancy.key_record(engine_root, principal.key_id, org_id=principal.org_id)
                if key is None:
                    _charge_bad_key(request, cfg)
                    raise HTTPException(401, "Invalid or revoked API key")
                if not key_limiter.allow(key.key_id, key.rate_limit_per_minute):
                    return rate_limited_response(
                        limit=key.rate_limit_per_minute, retry_after=key_limiter.retry_after_seconds(), scope="key"
                    )
                metered.append(key.key_id)
            return cfg, slots

        # Audit: the existing mode "intake" with a "draft-check-" run id on every row (the audit contract's modes are public and
        # unchanged). Known limits: the limiters are built once from the first request's config (a reload needs a restart); a
        # wrong API key spends the existing per-address bad-key budget, shared with the other routes (by design).
        def draft_check_ep(req: Any, request: Request, user: User | None = CurrentUserDep) -> dict[str, Any] | JSONResponse:
            metered: list[str] = []
            admitted = _a1_admit(request, user, metered)
            if isinstance(admitted, JSONResponse):
                return admitted
            cfg, slots = admitted
            run = {"run_id": f"draft-check-{os.urandom(5).hex()}"}  # on EVERY audit row of this call, whatever the status
            if not slots.acquire():
                _intake_audit(metered, "intake", 503, run)
                return coded_503(AGENT_AT_CAPACITY)
            try:
                text = _a1.read_draft(req, _a1_cfg)
                env_path = os.environ.get("PRAVRUDHI_CITATION_INDEX")
                path = citation_index_path or (Path(env_path) if env_path else None)
                look: Callable[[str], tuple[str, bool]] | None = None
                if path is not None and Path(path).is_file():
                    index_path = Path(path)

                    def _a1_look(citation_text: str) -> tuple[str, bool]:
                        result, _cov, compared = _lookup(
                            index_path, VerifyCitationRequest(citation=citation_text), cfg.verify_timeout_s
                        )
                        return result.result, compared

                    look = _a1_look
                out = _a1.check_draft(text, _a1_cfg, look, run_id=run["run_id"])
            except _a1.DraftRefused as e:
                _intake_audit(metered, "intake", e.status_code, run)
                return JSONResponse(status_code=e.status_code, content={"detail": str(e), "error": e.code})
            except Exception:
                _intake_audit(metered, "intake", 500, run)
                raise
            finally:
                slots.release()
            _intake_audit(metered, "intake", 200, out)
            return out

        draft_check_ep.__annotations__["req"] = _a1.DraftCheckRequest  # a local import is not visible to a string annotation
        router.add_api_route(
            "/draft-citation-check", draft_check_ep, methods=["POST"], include_in_schema=False, response_model=None
        )
        _a1.open_route_lists()  # the key-only gate list and roles.USER_FACING name this one route only while it exists

    # --- B3 s.138 cheque checklist: registered ONLY when partner_api.yaml sets wf_b3_s138_checklist to true --------------------
    # Off (the default, and every unreadable or malformed setting) adds nothing: no module import, no route, no limiter, no
    # thread, no file read. A new route spends none of an existing route's budget: it has its own limiters and its own slots.
    try:
        _b3_cfg = None
        if _flags.get("wf_b3_s138_checklist") is True:
            from pravrudhi.application.s138_checklist_config import load_s138_checklist_config as _load_b3

            _b3_cfg = _load_b3(engine_root)
        if _b3_cfg is not None:
            from pravrudhi.application import s138_checklist as _b3
    except Exception:  # noqa: BLE001 - an optional route must never stop the engine's own routes from starting
        _logger.warning("s.138 checklist unavailable; it stays off", exc_info=True)
        _b3_cfg = None
    if _b3_cfg is not None:
        _b3_state: dict[str, Any] = {}
        _b3_lock = threading.Lock()

        def _b3_limits(cfg: PartnerApiConfig) -> tuple[RateLimiter, Any, ConcurrencyLimiter]:
            with _b3_lock:
                if not _b3_state:
                    clock_kw: dict[str, Any] = {} if rate_clock is None else {"now": rate_clock}
                    _b3_state["ip"] = RateLimiter(cfg.slice_ip_limit, max_keys=cfg.rate_limit_max_keys, **clock_kw)
                    _b3_state["key"] = (
                        tenancy.KeyRateLimiter(now=rate_clock) if rate_clock is not None else tenancy.KeyRateLimiter()
                    )
                    _b3_state["slots"] = ConcurrencyLimiter(_b3_cfg.max_concurrent)
                return _b3_state["ip"], _b3_state["key"], _b3_state["slots"]

        def _b3_admit(
            request: Request, user: User | None, metered: list[str]
        ) -> tuple[PartnerApiConfig, ConcurrencyLimiter] | JSONResponse:
            try:
                cfg = _get_state()[0]
            except FileNotFoundError:
                return JSONResponse(status_code=503, content={"error": "service_config_missing"})
            closed = _window_closed(cfg)
            if closed is not None:
                return closed
            principal, throttled = _principal_or_throttle(request, cfg)
            if throttled is not None:
                return throttled
            if user is None and principal is None:
                return JSONResponse(status_code=401, content={"detail": "the s.138 checklist is offered only to signed-in users"})
            caller_key = f"key:{principal.key_id}" if principal is not None else f"user:{user.id}"  # type: ignore[union-attr]
            ip = _client_ip(
                request, trust_proxy_header=cfg.trust_proxy_header, trusted_proxies=cfg.trusted_proxies, caller_key=caller_key
            )
            ip_limiter, key_limiter, slots = _b3_limits(cfg)
            if not ip_limiter.allow(ip):
                return rate_limited_response(
                    limit=ip_limiter.per_minute, retry_after=ip_limiter.retry_after_seconds(), scope="caller"
                )
            if principal is not None:
                key = tenancy.key_record(engine_root, principal.key_id, org_id=principal.org_id)
                if key is None:
                    _charge_bad_key(request, cfg)
                    raise HTTPException(401, "Invalid or revoked API key")
                if not key_limiter.allow(key.key_id, key.rate_limit_per_minute):
                    return rate_limited_response(
                        limit=key.rate_limit_per_minute, retry_after=key_limiter.retry_after_seconds(), scope="key"
                    )
                metered.append(key.key_id)
            return cfg, slots

        # Audit: the existing mode "intake" with an "s138-checklist-" run id on every row. Same known limits as the A1 route.
        def s138_checklist_ep(req: Any, request: Request, user: User | None = CurrentUserDep) -> dict[str, Any] | JSONResponse:
            metered: list[str] = []
            admitted = _b3_admit(request, user, metered)
            if isinstance(admitted, JSONResponse):
                return admitted
            _cfg, slots = admitted
            run = {"run_id": f"s138-checklist-{os.urandom(5).hex()}"}  # on EVERY audit row of this call, whatever the status
            if not slots.acquire():
                _intake_audit(metered, "intake", 503, run)
                return coded_503(AGENT_AT_CAPACITY)
            try:
                out = _b3.check(req, _b3_cfg, run_id=run["run_id"])
            except _b3.ChecklistRefused as e:
                _intake_audit(metered, "intake", e.status_code, run)
                return JSONResponse(status_code=e.status_code, content={"detail": str(e), "error": e.code})
            except Exception:
                _intake_audit(metered, "intake", 500, run)
                raise
            finally:
                slots.release()
            _intake_audit(metered, "intake", 200, out)
            return out

        s138_checklist_ep.__annotations__["req"] = _b3.ChecklistRequest  # a local import is not visible to a string annotation
        router.add_api_route(
            "/s138-checklist", s138_checklist_ep, methods=["POST"], include_in_schema=False, response_model=None
        )
        _b3.open_route_lists()  # the key-only gate list and roles.USER_FACING name this one route only while it exists

    # --- A2 matter span-index: registered ONLY when partner_api.yaml sets wf_a2_span_index to true; the delete route ALSO when a
    # store directory already exists (data stored while the flag was on must stay deletable after it is turned off). Off with no
    # store directory (the default) adds nothing but one directory-existence check. Own limiters and own slots. Known limits: the
    # limiters are built from the first request's config; a wrong API key spends the existing per-address bad-key budget.
    try:
        _a2_cfg = None
        _a2_on = False
        _a2_delete = False
        _a2_objects_named = bool(
            os.environ.get("PRAVRUDHI_MATTER_OBJECT_ENDPOINT") and os.environ.get("PRAVRUDHI_MATTER_OBJECT_BUCKET")
        )
        if _flags.get("wf_a2_span_index") is True or _peek_matter_store_dir(engine_root, _flags).is_dir() or _a2_objects_named:
            from pravrudhi.application import matter_spans_config as _a2c

            _a2_cfg = _a2c.load_matter_spans_config(engine_root)
            _a2_dir = _a2c.store_dir(engine_root)
            _a2_on = _a2_cfg is not None
            _a2_delete = _a2_on or _a2_dir.is_dir() or _a2_objects_named
        if _a2_delete:
            from pravrudhi.application import matter_spans as _a2
    except Exception:  # noqa: BLE001 - an optional route must never stop the engine's own routes from starting
        _logger.warning("matter span-index unavailable; it stays off", exc_info=True)
        _a2_on = _a2_delete = False
    # #1015 scanned and garbled-page intake: only with its own flag true AND every calibrated number valid; it acts only inside
    # the A2 documents route, so it needs A2 on as well.
    _a2_scan = None
    if _flags.get("wf_c1_scanned_intake") is True:
        from pravrudhi.application import scanned_intake_config as _c1c

        _a2_scan = _c1c.load_scanned_intake_config(engine_root)
    if _a2_delete:
        _a2_cfg_eff = _a2_cfg or _a2c.MatterSpansConfig()
        from pravrudhi.application import matter_store_factory as _a2f

        # a folder, an object store, or the refusing store (503, no write); with A2 off only the delete route exists
        _a2_store = _a2f.open_store(engine_root, _a2_cfg_eff, delete_only=not _a2_on)
        _a2_state: dict[str, Any] = {}
        _a2_lock = threading.Lock()

        def _a2_limits(cfg: PartnerApiConfig) -> tuple[RateLimiter, Any, ConcurrencyLimiter]:
            with _a2_lock:
                if not _a2_state:
                    clock_kw: dict[str, Any] = {} if rate_clock is None else {"now": rate_clock}
                    _a2_state["ip"] = RateLimiter(cfg.slice_ip_limit, max_keys=cfg.rate_limit_max_keys, **clock_kw)
                    _a2_state["key"] = (
                        tenancy.KeyRateLimiter(now=rate_clock) if rate_clock is not None else tenancy.KeyRateLimiter()
                    )
                    _a2_state["slots"] = ConcurrencyLimiter(_a2_cfg_eff.max_concurrent)
                return _a2_state["ip"], _a2_state["key"], _a2_state["slots"]

        def _a2_admit(
            request: Request, user: User | None, metered: list[str]
        ) -> tuple[PartnerApiConfig, ConcurrencyLimiter, str] | JSONResponse:
            try:
                cfg = _get_state()[0]
            except FileNotFoundError:
                return JSONResponse(status_code=503, content={"error": "service_config_missing"})
            closed = _window_closed(cfg)
            if closed is not None:
                return closed
            principal, throttled = _principal_or_throttle(request, cfg)
            if throttled is not None:
                return throttled
            if user is None and principal is None:
                return JSONResponse(status_code=401, content={"detail": "Stored papers are offered only to signed-in users"})
            tenant = f"org:{principal.org_id}" if principal is not None else f"user:{user.id}"  # type: ignore[union-attr]
            caller_key = f"key:{principal.key_id}" if principal is not None else f"user:{user.id}"  # type: ignore[union-attr]
            ip = _client_ip(
                request, trust_proxy_header=cfg.trust_proxy_header, trusted_proxies=cfg.trusted_proxies, caller_key=caller_key
            )
            ip_limiter, key_limiter, slots = _a2_limits(cfg)
            if not ip_limiter.allow(ip):
                return rate_limited_response(
                    limit=ip_limiter.per_minute, retry_after=ip_limiter.retry_after_seconds(), scope="caller"
                )
            if principal is not None:
                key = tenancy.key_record(engine_root, principal.key_id, org_id=principal.org_id)
                if key is None:
                    _charge_bad_key(request, cfg)
                    raise HTTPException(401, "Invalid or revoked API key")
                if not key_limiter.allow(key.key_id, key.rate_limit_per_minute):
                    return rate_limited_response(
                        limit=key.rate_limit_per_minute, retry_after=key_limiter.retry_after_seconds(), scope="key"
                    )
                metered.append(key.key_id)
            return cfg, slots, tenant

        def _a2_call(
            request: Request, user: User | None, op: Callable[[Any, str], dict[str, Any]]
        ) -> dict[str, Any] | JSONResponse:
            """One call: admit, take a slot, run `op(store, tenant)`, audit every status with the run id (mode "intake" with a
            "matter-spans-" run id: the public audit contract is unchanged), release the slot."""
            metered: list[str] = []
            admitted = _a2_admit(request, user, metered)
            if isinstance(admitted, JSONResponse):
                return admitted
            _cfg, slots, tenant = admitted
            run = {"run_id": f"matter-spans-{os.urandom(5).hex()}"}
            if not slots.acquire():
                _intake_audit(metered, "intake", 503, run)
                return coded_503(AGENT_AT_CAPACITY)
            try:
                out = op(_a2_store, tenant)
            except _a2.MatterError as e:
                _intake_audit(metered, "intake", e.status_code, run)
                return JSONResponse(status_code=e.status_code, content={"detail": str(e), "error": e.code})
            except Exception:
                _intake_audit(metered, "intake", 500, run)
                raise
            finally:
                slots.release()
            _intake_audit(metered, "intake", 200, run)
            return out

        _a2_paths: list[str] = []

        def _a2_register(name: str, handler: Callable[..., Any], model: Any, path: str) -> None:
            if model is not None:
                handler.__annotations__["req"] = model  # a local import is not visible to a string annotation
            router.add_api_route(name, handler, methods=["POST"], include_in_schema=False, response_model=None)
            _a2_paths.append(path)

        def a2_delete_ep(req: Any, request: Request, user: User | None = CurrentUserDep) -> dict[str, Any] | JSONResponse:
            def op(store: Any, tenant: str) -> dict[str, Any]:
                if req.doc_id is not None:
                    return store.delete_document(tenant, req.matter_id, req.doc_id)  # type: ignore[no-any-return]
                return store.delete_matter(tenant, req.matter_id)  # type: ignore[no-any-return]

            return _a2_call(request, user, op)

        _a2_register("/matter-spans/delete", a2_delete_ep, _a2.DeleteRequest, _a2.ROUTE_DELETE)
        if _a2_on:

            def a2_create_ep(request: Request, user: User | None = CurrentUserDep) -> dict[str, Any] | JSONResponse:
                return _a2_call(request, user, lambda store, tenant: {"matter_id": store.create_matter(tenant)})

            def a2_documents_ep(req: Any, request: Request, user: User | None = CurrentUserDep) -> dict[str, Any] | JSONResponse:
                def op(store: Any, tenant: str) -> dict[str, Any]:
                    if _a2_scan is not None:  # #1015: wf_c1_scanned_intake on: pages routed text layer / image, additive fields
                        from pravrudhi.application import scanned_intake_ingest as _c1i

                        text, extra = _c1i.ingest_document(req, _a2_cfg_eff, _a2_scan)
                        ocr = [int(p["page"]) for p in extra.get("pages", []) if p.get("source") == "ocr"]  # kept: from_ocr
                        return {**store.add_document(tenant, req.matter_id, text, req.name, ocr or None), **extra}
                    text = _a2.read_document(req, _a2_cfg_eff)
                    return store.add_document(tenant, req.matter_id, text, req.name)  # type: ignore[no-any-return]

                return _a2_call(request, user, op)

            def a2_query_ep(req: Any, request: Request, user: User | None = CurrentUserDep) -> dict[str, Any] | JSONResponse:
                return _a2_call(request, user, lambda store, tenant: store.query(tenant, req.matter_id, req.q))

            def a2_list_ep(req: Any, request: Request, user: User | None = CurrentUserDep) -> dict[str, Any] | JSONResponse:
                return _a2_call(request, user, lambda store, tenant: store.list_documents(tenant, req.matter_id))

            _a2_register("/matter-spans/matters", a2_create_ep, None, _a2.ROUTE_CREATE)
            _a2_register("/matter-spans/documents", a2_documents_ep, _a2.DocumentRequest, _a2.ROUTE_DOCUMENTS)
            _a2_register("/matter-spans/query", a2_query_ep, _a2.QueryRequest, _a2.ROUTE_QUERY)
            _a2_register("/matter-spans/list", a2_list_ep, _a2.MatterRef, _a2.ROUTE_LIST)
            if _flags.get("wf_a2_list_matters") is True:  # the caller's own matters; needs A2 on; no new module, config or secret

                def a2_mine_ep(request: Request, user: User | None = CurrentUserDep) -> dict[str, Any] | JSONResponse:
                    return _a2_call(request, user, lambda store, tenant: store.list_matters(tenant))

                _a2_register("/matter-spans/my-matters", a2_mine_ep, None, _a2.ROUTE_MINE)
        _a2.open_route_lists(_a2_paths)

    # --- A3 chronology: registered ONLY when partner_api.yaml sets wf_a3_chronology to true. It reads A2's stored passages, so it
    # needs A2 ON (wf_a2_span_index true, not merely a store folder): with A2 off it refuses and reads nothing. Off (the default)
    # adds nothing: no module import, no route, no limiter, no thread. Own limiters and own slots; audit mode "intake" with a
    # "chronology-" run id; known limits as for the other optional routes.
    try:
        _a3_cfg = None
        if _flags.get("wf_a3_chronology") is True:
            from pravrudhi.application.chronology_config import load_chronology_config as _load_a3

            _a3_cfg = _load_a3(engine_root)
        if _a3_cfg is not None:
            from pravrudhi.application import chronology_matter as _a3
            from pravrudhi.application import matter_spans_config as _a3m
    except Exception:  # noqa: BLE001 - an optional route must never stop the engine's own routes from starting
        _logger.warning("chronology unavailable; it stays off", exc_info=True)
        _a3_cfg = None
    if _a3_cfg is not None:
        _a3_state: dict[str, Any] = {}
        _a3_lock = threading.Lock()

        def _a3_limits(cfg: PartnerApiConfig) -> tuple[RateLimiter, Any, ConcurrencyLimiter]:
            with _a3_lock:
                if not _a3_state:
                    clock_kw: dict[str, Any] = {} if rate_clock is None else {"now": rate_clock}
                    _a3_state["ip"] = RateLimiter(cfg.slice_ip_limit, max_keys=cfg.rate_limit_max_keys, **clock_kw)
                    _a3_state["key"] = (
                        tenancy.KeyRateLimiter(now=rate_clock) if rate_clock is not None else tenancy.KeyRateLimiter()
                    )
                    _a3_state["slots"] = ConcurrencyLimiter(_a3_cfg.max_concurrent)
                return _a3_state["ip"], _a3_state["key"], _a3_state["slots"]

        def _a3_admit(
            request: Request, user: User | None, metered: list[str]
        ) -> tuple[PartnerApiConfig, ConcurrencyLimiter, str] | JSONResponse:
            try:
                cfg = _get_state()[0]
            except FileNotFoundError:
                return JSONResponse(status_code=503, content={"error": "service_config_missing"})
            closed = _window_closed(cfg)
            if closed is not None:
                return closed
            principal, throttled = _principal_or_throttle(request, cfg)
            if throttled is not None:
                return throttled
            if user is None and principal is None:
                return JSONResponse(status_code=401, content={"detail": "Stored papers are offered only to signed-in users"})
            tenant = f"org:{principal.org_id}" if principal is not None else f"user:{user.id}"  # type: ignore[union-attr]
            caller_key = f"key:{principal.key_id}" if principal is not None else f"user:{user.id}"  # type: ignore[union-attr]
            ip = _client_ip(
                request, trust_proxy_header=cfg.trust_proxy_header, trusted_proxies=cfg.trusted_proxies, caller_key=caller_key
            )
            ip_limiter, key_limiter, slots = _a3_limits(cfg)
            if not ip_limiter.allow(ip):
                return rate_limited_response(
                    limit=ip_limiter.per_minute, retry_after=ip_limiter.retry_after_seconds(), scope="caller"
                )
            if principal is not None:
                key = tenancy.key_record(engine_root, principal.key_id, org_id=principal.org_id)
                if key is None:
                    _charge_bad_key(request, cfg)
                    raise HTTPException(401, "Invalid or revoked API key")
                if not key_limiter.allow(key.key_id, key.rate_limit_per_minute):
                    return rate_limited_response(
                        limit=key.rate_limit_per_minute, retry_after=key_limiter.retry_after_seconds(), scope="key"
                    )
                metered.append(key.key_id)
            return cfg, slots, tenant

        def chronology_ep(req: Any, request: Request, user: User | None = CurrentUserDep) -> dict[str, Any] | JSONResponse:
            metered: list[str] = []
            admitted = _a3_admit(request, user, metered)
            if isinstance(admitted, JSONResponse):
                return admitted
            _cfg, slots, tenant = admitted
            run = {"run_id": f"chronology-{os.urandom(5).hex()}"}  # on EVERY audit row of this call, whatever the status
            if not slots.acquire():
                _intake_audit(metered, "intake", 503, run)
                return coded_503(AGENT_AT_CAPACITY)
            try:
                a2_cfg = _a3m.load_matter_spans_config(engine_root)  # "A2 on" = its flag in the config, never the store folder
                if a2_cfg is None:
                    _intake_audit(metered, "intake", 409, run)
                    return JSONResponse(status_code=409, content={"detail": _a3.STORE_OFF, "error": "store_off"})
                from pravrudhi.application.matter_store_factory import open_store as _Store

                out = _a3.chronology_for_matter(
                    _Store(engine_root, a2_cfg), tenant, req.matter_id, _a3_cfg, run["run_id"]
                )
            except Exception as e:
                status = getattr(e, "status_code", None)
                if isinstance(status, int) and hasattr(e, "code"):
                    _intake_audit(metered, "intake", status, run)
                    return JSONResponse(status_code=status, content={"detail": str(e), "error": e.code})
                _intake_audit(metered, "intake", 500, run)
                raise
            finally:
                slots.release()
            _intake_audit(metered, "intake", 200, run)
            return out

        chronology_ep.__annotations__["req"] = _a3.ChronologyRequest  # a local import is not visible to a string annotation
        router.add_api_route("/chronology", chronology_ep, methods=["POST"], include_in_schema=False, response_model=None)
        _a3.open_route_lists()  # the key-only gate list and roles.USER_FACING name this one route only while it exists


    # --- A5 review agenda: registered ONLY when partner_api.yaml sets wf_a5_ingredient_agenda to true. It reads A2's document
    # list (no passage), so it needs A2 ON (wf_a2_span_index true, not merely a store folder): with A2 off it refuses and reads
    # nothing. Off (the default) adds nothing: no module import, no route, no limiter, no thread. Own limiters and own slots;
    # audit mode "intake" with an "agenda-" run id; known limits as for the other optional routes.
    try:
        _a5_cfg = None
        if _flags.get("wf_a5_ingredient_agenda") is True:
            from pravrudhi.application.agenda_config import load_agenda_config as _load_a5

            _a5_cfg = _load_a5(engine_root)
        if _a5_cfg is not None:
            from pravrudhi.application import agenda_matter as _a5
            from pravrudhi.application import matter_spans_config as _a5m
    except Exception:  # noqa: BLE001 - an optional route must never stop the engine's own routes from starting
        _logger.warning("review agenda unavailable; it stays off", exc_info=True)
        _a5_cfg = None
    if _a5_cfg is not None:
        try:  # the page names the chronology and the cheque checklist only when BOTH are on in this deployment
            _a5_owners = False
            if _flags.get("wf_a3_chronology") is True and _flags.get("wf_b3_s138_checklist") is True:
                from pravrudhi.application.chronology_config import load_chronology_config as _a5_c
                from pravrudhi.application.s138_checklist_config import load_s138_checklist_config as _a5_b

                _a5_owners = _a5_c(engine_root) is not None and _a5_b(engine_root) is not None
        except Exception:  # noqa: BLE001 - the line is optional; without it the page just does not name them
            _a5_owners = False
        _a5_state: dict[str, Any] = {}
        _a5_lock = threading.Lock()

        def _a5_limits(cfg: PartnerApiConfig) -> tuple[RateLimiter, Any, ConcurrencyLimiter]:
            with _a5_lock:
                if not _a5_state:
                    clock_kw: dict[str, Any] = {} if rate_clock is None else {"now": rate_clock}
                    _a5_state["ip"] = RateLimiter(cfg.slice_ip_limit, max_keys=cfg.rate_limit_max_keys, **clock_kw)
                    _a5_state["key"] = (
                        tenancy.KeyRateLimiter(now=rate_clock) if rate_clock is not None else tenancy.KeyRateLimiter()
                    )
                    _a5_state["slots"] = ConcurrencyLimiter(_a5_cfg.max_concurrent)
                return _a5_state["ip"], _a5_state["key"], _a5_state["slots"]

        def _a5_admit(
            request: Request, user: User | None, metered: list[str]
        ) -> tuple[PartnerApiConfig, ConcurrencyLimiter, str] | JSONResponse:
            try:
                cfg = _get_state()[0]
            except FileNotFoundError:
                return JSONResponse(status_code=503, content={"error": "service_config_missing"})
            closed = _window_closed(cfg)
            if closed is not None:
                return closed
            principal, throttled = _principal_or_throttle(request, cfg)
            if throttled is not None:
                return throttled
            if user is None and principal is None:
                return JSONResponse(status_code=401, content={"detail": "Stored papers are offered only to signed-in users"})
            tenant = f"org:{principal.org_id}" if principal is not None else f"user:{user.id}"  # type: ignore[union-attr]
            caller_key = f"key:{principal.key_id}" if principal is not None else f"user:{user.id}"  # type: ignore[union-attr]
            ip = _client_ip(
                request, trust_proxy_header=cfg.trust_proxy_header, trusted_proxies=cfg.trusted_proxies, caller_key=caller_key
            )
            ip_limiter, key_limiter, slots = _a5_limits(cfg)
            if not ip_limiter.allow(ip):
                return rate_limited_response(
                    limit=ip_limiter.per_minute, retry_after=ip_limiter.retry_after_seconds(), scope="caller"
                )
            if principal is not None:
                key = tenancy.key_record(engine_root, principal.key_id, org_id=principal.org_id)
                if key is None:
                    _charge_bad_key(request, cfg)
                    raise HTTPException(401, "Invalid or revoked API key")
                if not key_limiter.allow(key.key_id, key.rate_limit_per_minute):
                    return rate_limited_response(
                        limit=key.rate_limit_per_minute, retry_after=key_limiter.retry_after_seconds(), scope="key"
                    )
                metered.append(key.key_id)
            return cfg, slots, tenant

        def agenda_ep(req: Any, request: Request, user: User | None = CurrentUserDep) -> dict[str, Any] | JSONResponse:
            metered: list[str] = []
            admitted = _a5_admit(request, user, metered)
            if isinstance(admitted, JSONResponse):
                return admitted
            _cfg, slots, tenant = admitted
            run = {"run_id": f"agenda-{os.urandom(5).hex()}"}  # on EVERY audit row of this call, whatever the status
            if not slots.acquire():
                _intake_audit(metered, "intake", 503, run)
                return coded_503(AGENT_AT_CAPACITY)
            try:
                a2_cfg = _a5m.load_matter_spans_config(engine_root)  # "A2 on" = its flag in the config, never the store folder
                if a2_cfg is None:
                    _intake_audit(metered, "intake", 409, run)
                    return JSONResponse(status_code=409, content={"detail": _a5.STORE_OFF, "error": "store_off"})
                from pravrudhi.application.matter_store_factory import open_store as _Store

                out = _a5.agenda_for_matter(_Store(engine_root, a2_cfg), tenant, req, run["run_id"], _a5_owners)
            except Exception as e:
                status = getattr(e, "status_code", None)
                if isinstance(status, int) and hasattr(e, "code"):
                    _intake_audit(metered, "intake", status, run)
                    return JSONResponse(status_code=status, content={"detail": str(e), "error": e.code})
                _intake_audit(metered, "intake", 500, run)
                raise
            finally:
                slots.release()
            _intake_audit(metered, "intake", 200, run)
            return out

        agenda_ep.__annotations__["req"] = _a5.AgendaRequest  # a local import is not visible to a string annotation
        router.add_api_route("/review-agenda", agenda_ep, methods=["POST"], include_in_schema=False, response_model=None)
        _a5.open_route_lists()  # the key-only gate list and roles.USER_FACING name this one route only while it exists

    # --- S3 quashing categories agenda: registered ONLY when partner_api.yaml sets wf_b2_quashing_agenda to true. It reads A2's
    # document list (names, no passage), so it needs A2 ON (wf_a2_span_index true): with A2 off it refuses and reads nothing. The
    # asset is imported ONLY behind the flag; with the flag off nothing is imported, opened or served. At run time the only
    # check is that the pinned case exists in the case index (production holds no judgment text). Own limiters and slots; audit
    # mode "intake" with a "quash-" run id.
    try:
        _s3_cfg = None
        if _flags.get("wf_b2_quashing_agenda") is True:
            from pravrudhi.application.quashing_config import load_quashing_config as _load_s3

            _s3_cfg = _load_s3(engine_root)
        if _s3_cfg is not None:
            from pravrudhi.application import matter_spans_config as _s3m
            from pravrudhi.application import quashing_matter as _s3
    except Exception:  # noqa: BLE001 - an optional route must never stop the engine's own routes from starting
        _logger.warning("quashing agenda unavailable; it stays off", exc_info=True)
        _s3_cfg = None
    if _s3_cfg is not None:
        _s3_state: dict[str, Any] = {}
        _s3_lock = threading.Lock()

        def _s3_limits(cfg: PartnerApiConfig) -> tuple[RateLimiter, Any, ConcurrencyLimiter]:
            with _s3_lock:
                if not _s3_state:
                    clock_kw: dict[str, Any] = {} if rate_clock is None else {"now": rate_clock}
                    _s3_state["ip"] = RateLimiter(cfg.slice_ip_limit, max_keys=cfg.rate_limit_max_keys, **clock_kw)
                    _s3_state["key"] = (
                        tenancy.KeyRateLimiter(now=rate_clock) if rate_clock is not None else tenancy.KeyRateLimiter()
                    )
                    _s3_state["slots"] = ConcurrencyLimiter(_s3_cfg.max_concurrent)
                return _s3_state["ip"], _s3_state["key"], _s3_state["slots"]

        def _s3_admit(
            request: Request, user: User | None, metered: list[str]
        ) -> tuple[PartnerApiConfig, ConcurrencyLimiter, str] | JSONResponse:
            try:
                cfg = _get_state()[0]
            except FileNotFoundError:
                return JSONResponse(status_code=503, content={"error": "service_config_missing"})
            closed = _window_closed(cfg)
            if closed is not None:
                return closed
            principal, throttled = _principal_or_throttle(request, cfg)
            if throttled is not None:
                return throttled
            if user is None and principal is None:
                return JSONResponse(status_code=401, content={"detail": "Stored papers are offered only to signed-in users"})
            tenant = f"org:{principal.org_id}" if principal is not None else f"user:{user.id}"  # type: ignore[union-attr]
            caller_key = f"key:{principal.key_id}" if principal is not None else f"user:{user.id}"  # type: ignore[union-attr]
            ip = _client_ip(
                request, trust_proxy_header=cfg.trust_proxy_header, trusted_proxies=cfg.trusted_proxies, caller_key=caller_key
            )
            ip_limiter, key_limiter, slots = _s3_limits(cfg)
            if not ip_limiter.allow(ip):
                return rate_limited_response(
                    limit=ip_limiter.per_minute, retry_after=ip_limiter.retry_after_seconds(), scope="caller"
                )
            if principal is not None:
                key = tenancy.key_record(engine_root, principal.key_id, org_id=principal.org_id)
                if key is None:
                    _charge_bad_key(request, cfg)
                    raise HTTPException(401, "Invalid or revoked API key")
                if not key_limiter.allow(key.key_id, key.rate_limit_per_minute):
                    return rate_limited_response(
                        limit=key.rate_limit_per_minute, retry_after=key_limiter.retry_after_seconds(), scope="key"
                    )
                metered.append(key.key_id)
            return cfg, slots, tenant

        def quashing_ep(req: Any, request: Request, user: User | None = CurrentUserDep) -> dict[str, Any] | JSONResponse:
            metered: list[str] = []
            admitted = _s3_admit(request, user, metered)
            if isinstance(admitted, JSONResponse):
                return admitted
            _cfg, slots, tenant = admitted
            run = {"run_id": f"quash-{os.urandom(5).hex()}"}  # on EVERY audit row of this call, whatever the status
            if not slots.acquire():
                _intake_audit(metered, "intake", 503, run)
                return coded_503(AGENT_AT_CAPACITY)
            try:
                a2_cfg = _s3m.load_matter_spans_config(engine_root)  # "A2 on" = its flag in the config, never the store folder
                if a2_cfg is None:
                    _intake_audit(metered, "intake", 409, run)
                    return JSONResponse(status_code=409, content={"detail": _s3.STORE_OFF, "error": "store_off"})
                from pravrudhi.application.matter_store_factory import open_store as _Store

                env_path = os.environ.get("PRAVRUDHI_CITATION_INDEX")
                idx = citation_index_path or (Path(env_path) if env_path else None)
                out = _s3.agenda_for_matter(_Store(engine_root, a2_cfg), tenant, req, run["run_id"], idx)
            except Exception as e:
                status = getattr(e, "status_code", None)
                if isinstance(status, int) and hasattr(e, "code"):
                    _intake_audit(metered, "intake", status, run)
                    return JSONResponse(status_code=status, content={"detail": str(e), "error": e.code})
                _intake_audit(metered, "intake", 500, run)
                raise
            finally:
                slots.release()
            _intake_audit(metered, "intake", 200, run)
            return out

        def quashing_health_ep(request: Request, user: User | None = CurrentUserDep) -> dict[str, Any] | JSONResponse:
            metered: list[str] = []
            admitted = _s3_admit(request, user, metered)
            if isinstance(admitted, JSONResponse):
                return admitted
            env_path = os.environ.get("PRAVRUDHI_CITATION_INDEX")
            return _s3.health(citation_index_path or (Path(env_path) if env_path else None))

        quashing_ep.__annotations__["req"] = _s3.QuashingRequest  # a local import is not visible to a string annotation
        router.add_api_route(
            "/quashing-agenda", quashing_ep, methods=["POST"], include_in_schema=False, response_model=None,
            route_class_override=_s3.FixedValidationRoute,
        )
        router.add_api_route(
            "/quashing-agenda/health", quashing_health_ep, methods=["GET"], include_in_schema=False, response_model=None
        )
        _s3.open_route_lists()  # the key-only gate list and roles.USER_FACING name this one route only while it exists

    # --- B1 old-section look-up (IPC / CrPC to BNS / BNSS): registered ONLY when partner_api.yaml sets wf_b1_section_lookup to
    # true. It reads three shipped assets (no matter store, no model, no network, nothing written), so it needs none of A2. The
    # module and the assets are imported ONLY behind the flag; with the flag off nothing is imported, opened or served. Gated like
    # the other partner routes: a signed-in user or an API key. Own limiters and slots; audit "intake", run id "lookup-".
    try:
        _b1_cfg = None
        if _flags.get("wf_b1_section_lookup") is True:
            from pravrudhi.application.section_lookup_config import load_section_lookup_config as _load_b1

            _b1_cfg = _load_b1(engine_root)
        if _b1_cfg is not None:
            from pravrudhi.application import section_lookup as _b1
    except Exception:  # noqa: BLE001 - an optional route must never stop the engine's own routes from starting
        _logger.warning("section look-up unavailable; it stays off", exc_info=True)
        _b1_cfg = None
    if _b1_cfg is not None:
        _b1_state: dict[str, Any] = {}
        _b1_lock = threading.Lock()

        def _b1_limits(cfg: PartnerApiConfig) -> tuple[RateLimiter, Any, ConcurrencyLimiter]:
            with _b1_lock:
                if not _b1_state:
                    clock_kw: dict[str, Any] = {} if rate_clock is None else {"now": rate_clock}
                    _b1_state["ip"] = RateLimiter(cfg.slice_ip_limit, max_keys=cfg.rate_limit_max_keys, **clock_kw)
                    _b1_state["key"] = (
                        tenancy.KeyRateLimiter(now=rate_clock) if rate_clock is not None else tenancy.KeyRateLimiter()
                    )
                    _b1_state["slots"] = ConcurrencyLimiter(_b1_cfg.max_concurrent)
                return _b1_state["ip"], _b1_state["key"], _b1_state["slots"]

        def section_lookup_ep(req: Any, request: Request, user: User | None = CurrentUserDep) -> dict[str, Any] | JSONResponse:
            metered: list[str] = []
            try:
                cfg = _get_state()[0]
            except FileNotFoundError:
                return JSONResponse(status_code=503, content={"error": "service_config_missing"})
            closed = _window_closed(cfg)
            if closed is not None:
                return closed
            principal, throttled = _principal_or_throttle(request, cfg)
            if throttled is not None:
                return throttled
            if user is None and principal is None:
                return JSONResponse(status_code=401, content={"detail": "The section look-up is offered only to signed-in users"})
            caller_key = f"key:{principal.key_id}" if principal is not None else f"user:{user.id}"  # type: ignore[union-attr]
            ip = _client_ip(
                request, trust_proxy_header=cfg.trust_proxy_header, trusted_proxies=cfg.trusted_proxies, caller_key=caller_key
            )
            ip_limiter, key_limiter, slots = _b1_limits(cfg)
            if not ip_limiter.allow(ip):
                return rate_limited_response(
                    limit=ip_limiter.per_minute, retry_after=ip_limiter.retry_after_seconds(), scope="caller"
                )
            if principal is not None:
                key = tenancy.key_record(engine_root, principal.key_id, org_id=principal.org_id)
                if key is None:
                    _charge_bad_key(request, cfg)
                    raise HTTPException(401, "Invalid or revoked API key")
                if not key_limiter.allow(key.key_id, key.rate_limit_per_minute):
                    return rate_limited_response(
                        limit=key.rate_limit_per_minute, retry_after=key_limiter.retry_after_seconds(), scope="key"
                    )
                metered.append(key.key_id)
            run = {"run_id": f"lookup-{os.urandom(5).hex()}"}  # on EVERY audit row of this call, whatever the status
            if not slots.acquire():
                _intake_audit(metered, "intake", 503, run)
                return coded_503(AGENT_AT_CAPACITY)
            try:
                out = _b1.lookup(req)
            except ValueError:
                _intake_audit(metered, "intake", 422, run)
                return JSONResponse(status_code=422, content=_b1.BAD_BODY)  # the code and the section are never echoed
            except _b1.AssetChanged:
                _intake_audit(metered, "intake", 503, run)
                return JSONResponse(status_code=503, content={"detail": _b1.UNAVAILABLE, "error": "lookup_unavailable"})
            except Exception:
                _intake_audit(metered, "intake", 500, run)
                raise
            finally:
                slots.release()
            _intake_audit(metered, "intake", 200, run)
            return {"run_id": run["run_id"], "code": req.code, "section": req.section.upper(), **out}

        section_lookup_ep.__annotations__["req"] = _b1.SectionLookupRequest  # a local import is not visible to a string hint
        router.add_api_route(
            "/section-lookup", section_lookup_ep, methods=["POST"], include_in_schema=False, response_model=None,
            route_class_override=_b1.FixedValidationRoute,
        )
        _b1.open_route_lists()  # the key-only gate list and roles.USER_FACING name this one route only while it exists

    # --- S1 matter citation sweep: registered ONLY when partner_api.yaml sets wf_s1_matter_citation_sweep to true. It reads A2's
    # passages (tenant-checked) and runs A1's citation check over them, so it needs A2 ON (wf_a2_span_index true, not just a store
    # folder) and a configured citation index; A1's own flag is NOT required (only A1's module is imported, lazily). Off (the
    # default) adds nothing: no module import, no route, no limiter, no thread, no read. Own limiters and slots; audit mode
    # "intake" with a "sweep-" run id.
    try:
        _s1_cfg = None
        if _flags.get("wf_s1_matter_citation_sweep") is True:
            from pravrudhi.application.matter_sweep_config import load_sweep_config as _load_s1

            _s1_cfg = _load_s1(engine_root)
        if _s1_cfg is not None:
            from pravrudhi.application import matter_spans_config as _s1m
            from pravrudhi.application import matter_sweep_matter as _s1
    except Exception:  # noqa: BLE001 - an optional route must never stop the engine's own routes from starting
        _logger.warning("matter citation sweep unavailable; it stays off", exc_info=True)
        _s1_cfg = None
    if _s1_cfg is not None:
        _s1_state: dict[str, Any] = {}
        _s1_lock = threading.Lock()

        def _s1_limits(cfg: PartnerApiConfig) -> tuple[RateLimiter, Any, ConcurrencyLimiter]:
            with _s1_lock:
                if not _s1_state:
                    clock_kw: dict[str, Any] = {} if rate_clock is None else {"now": rate_clock}
                    _s1_state["ip"] = RateLimiter(cfg.rate_limit_per_minute, max_keys=cfg.rate_limit_max_keys, **clock_kw)
                    _s1_state["key"] = (
                        tenancy.KeyRateLimiter(now=rate_clock) if rate_clock is not None else tenancy.KeyRateLimiter()
                    )
                    _s1_state["slots"] = ConcurrencyLimiter(_s1_cfg.max_concurrent)
                return _s1_state["ip"], _s1_state["key"], _s1_state["slots"]

        def _s1_admit(
            request: Request, user: User | None, metered: list[str]
        ) -> tuple[PartnerApiConfig, ConcurrencyLimiter, str] | JSONResponse:
            try:
                cfg = _get_state()[0]
            except FileNotFoundError:
                return JSONResponse(status_code=503, content={"error": "service_config_missing"})
            closed = _window_closed(cfg)
            if closed is not None:
                return closed
            principal, throttled = _principal_or_throttle(request, cfg)
            if throttled is not None:
                return throttled
            if user is None and principal is None:
                return JSONResponse(status_code=401, content={"detail": "Stored papers are offered only to signed-in users"})
            tenant = f"org:{principal.org_id}" if principal is not None else f"user:{user.id}"  # type: ignore[union-attr]
            caller_key = f"key:{principal.key_id}" if principal is not None else f"user:{user.id}"  # type: ignore[union-attr]
            ip = _client_ip(
                request, trust_proxy_header=cfg.trust_proxy_header, trusted_proxies=cfg.trusted_proxies, caller_key=caller_key
            )
            ip_limiter, key_limiter, slots = _s1_limits(cfg)
            if not ip_limiter.allow(ip):
                return rate_limited_response(
                    limit=ip_limiter.per_minute, retry_after=ip_limiter.retry_after_seconds(), scope="caller"
                )
            if principal is not None:
                key = tenancy.key_record(engine_root, principal.key_id, org_id=principal.org_id)
                if key is None:
                    _charge_bad_key(request, cfg)
                    raise HTTPException(401, "Invalid or revoked API key")
                if not key_limiter.allow(key.key_id, key.rate_limit_per_minute):
                    return rate_limited_response(
                        limit=key.rate_limit_per_minute, retry_after=key_limiter.retry_after_seconds(), scope="key"
                    )
                metered.append(key.key_id)
            return cfg, slots, tenant

        def sweep_ep(req: Any, request: Request, user: User | None = CurrentUserDep) -> dict[str, Any] | JSONResponse:
            metered: list[str] = []
            admitted = _s1_admit(request, user, metered)
            if isinstance(admitted, JSONResponse):
                return admitted
            cfg, slots, tenant = admitted
            run = {"run_id": f"sweep-{os.urandom(5).hex()}"}  # on EVERY audit row of this call, whatever the status
            if not slots.acquire():
                _intake_audit(metered, "intake", 503, run)
                return coded_503(AGENT_AT_CAPACITY)
            try:
                a2_cfg = _s1m.load_matter_spans_config(engine_root)  # "A2 on" = its flag in the config, never the store folder
                if a2_cfg is None:
                    _intake_audit(metered, "intake", 409, run)
                    return JSONResponse(status_code=409, content={"detail": _s1.STORE_OFF, "error": "store_off"})
                from pravrudhi.application.matter_store_factory import open_store as _Store

                env_path = os.environ.get("PRAVRUDHI_CITATION_INDEX")
                path = citation_index_path or (Path(env_path) if env_path else None)
                look: Callable[[str], tuple[str, bool]] | None = None
                if path is not None and Path(path).is_file():
                    index_path = Path(path)

                    def _s1_look(citation_text: str) -> tuple[str, bool]:
                        result, _cov, compared = _lookup(
                            index_path, VerifyCitationRequest(citation=citation_text), cfg.verify_timeout_s
                        )
                        return result.result, compared

                    look = _s1_look
                out = _s1.sweep_for_matter(_Store(engine_root, a2_cfg), tenant, req, run["run_id"], _s1_cfg, look)
            except Exception as e:
                status = getattr(e, "status_code", None)
                if isinstance(status, int) and hasattr(e, "code"):
                    _intake_audit(metered, "intake", status, run)
                    return JSONResponse(status_code=status, content={"detail": str(e), "error": e.code})
                _intake_audit(metered, "intake", 500, run)
                raise
            finally:
                slots.release()
            _intake_audit(metered, "intake", 200, run)
            return out

        sweep_ep.__annotations__["req"] = _s1.SweepRequest  # a local import is not visible to a string annotation
        router.add_api_route("/matter-citation-sweep", sweep_ep, methods=["POST"], include_in_schema=False, response_model=None)
        _s1.open_route_lists()  # the key-only gate list and roles.USER_FACING name this one route only while it exists

    # --- S2 matter tabular table: registered ONLY when partner_api.yaml sets wf_s2_tabular_fact_table to true AND A2 is on (its
    # flag wf_a2_span_index true, not just a store folder): with A2 off there is NO route and a request gets the 404 of any
    # unknown path. It reads A2's passages (tenant-checked) and A3's date scanner; no index, no model, no A3 flag. Off (the
    # default) adds nothing: no module import, no route, no limiter, no thread, no read. Own limiters and slots; audit mode
    # "intake" with a "tab-" run id.
    try:
        _s2_cfg = None
        _s2_a2 = None
        if _flags.get("wf_s2_tabular_fact_table") is True:
            from pravrudhi.application.tabular_config import load_tabular_config as _load_s2

            _s2_cfg = _load_s2(engine_root)
        if _s2_cfg is not None:
            from pravrudhi.application import matter_spans_config as _s2m

            _s2_a2 = _s2m.load_matter_spans_config(engine_root)
            if _s2_a2 is None:
                _s2_cfg = None  # S2 on, A2 off: the route is not registered at all
        if _s2_cfg is not None:
            from pravrudhi.application import tabular_matter as _s2
    except Exception:  # noqa: BLE001 - an optional route must never stop the engine's own routes from starting
        _logger.warning("matter tabular table unavailable; it stays off", exc_info=True)
        _s2_cfg = None
    if _s2_cfg is not None:
        _s2_state: dict[str, Any] = {}
        _s2_lock = threading.Lock()

        def _s2_limits(cfg: PartnerApiConfig) -> tuple[RateLimiter, Any, ConcurrencyLimiter]:
            with _s2_lock:
                if not _s2_state:
                    clock_kw: dict[str, Any] = {} if rate_clock is None else {"now": rate_clock}
                    _s2_state["ip"] = RateLimiter(cfg.rate_limit_per_minute, max_keys=cfg.rate_limit_max_keys, **clock_kw)
                    _s2_state["key"] = (
                        tenancy.KeyRateLimiter(now=rate_clock) if rate_clock is not None else tenancy.KeyRateLimiter()
                    )
                    _s2_state["slots"] = ConcurrencyLimiter(_s2_cfg.max_concurrent)
                return _s2_state["ip"], _s2_state["key"], _s2_state["slots"]

        def _s2_admit(
            request: Request, user: User | None, metered: list[str]
        ) -> tuple[PartnerApiConfig, ConcurrencyLimiter, str] | JSONResponse:
            try:
                cfg = _get_state()[0]
            except FileNotFoundError:
                return JSONResponse(status_code=503, content={"error": "service_config_missing"})
            closed = _window_closed(cfg)
            if closed is not None:
                return closed
            principal, throttled = _principal_or_throttle(request, cfg)
            if throttled is not None:
                return throttled
            if user is None and principal is None:
                return JSONResponse(status_code=401, content={"detail": "Stored papers are offered only to signed-in users"})
            tenant = f"org:{principal.org_id}" if principal is not None else f"user:{user.id}"  # type: ignore[union-attr]
            caller_key = f"key:{principal.key_id}" if principal is not None else f"user:{user.id}"  # type: ignore[union-attr]
            ip = _client_ip(
                request, trust_proxy_header=cfg.trust_proxy_header, trusted_proxies=cfg.trusted_proxies, caller_key=caller_key
            )
            ip_limiter, key_limiter, slots = _s2_limits(cfg)
            if not ip_limiter.allow(ip):
                return rate_limited_response(
                    limit=ip_limiter.per_minute, retry_after=ip_limiter.retry_after_seconds(), scope="caller"
                )
            if principal is not None:
                key = tenancy.key_record(engine_root, principal.key_id, org_id=principal.org_id)
                if key is None:
                    _charge_bad_key(request, cfg)
                    raise HTTPException(401, "Invalid or revoked API key")
                if not key_limiter.allow(key.key_id, key.rate_limit_per_minute):
                    return rate_limited_response(
                        limit=key.rate_limit_per_minute, retry_after=key_limiter.retry_after_seconds(), scope="key"
                    )
                metered.append(key.key_id)
            return cfg, slots, tenant

        def tabular_ep(req: Any, request: Request, user: User | None = CurrentUserDep) -> dict[str, Any] | JSONResponse:
            metered: list[str] = []
            admitted = _s2_admit(request, user, metered)  # the limiter slot is taken BEFORE the ownership read
            if isinstance(admitted, JSONResponse):
                return admitted
            _cfg, slots, tenant = admitted
            run = {"run_id": f"tab-{os.urandom(5).hex()}"}  # on EVERY audit row of this call, whatever the status
            if not slots.acquire():
                _intake_audit(metered, "intake", 503, run)
                return coded_503(AGENT_AT_CAPACITY)
            try:
                from pravrudhi.application.matter_store_factory import open_store as _Store

                assert _s2_a2 is not None  # the route exists only when A2 is on
                out = _s2.table_for_matter(_Store(engine_root, _s2_a2), tenant, req, run["run_id"], _s2_cfg)
            except Exception as e:
                status = getattr(e, "status_code", None)
                if isinstance(status, int) and hasattr(e, "code"):
                    _intake_audit(metered, "intake", status, run)
                    return JSONResponse(status_code=status, content={"detail": str(e), "error": e.code})
                _intake_audit(metered, "intake", 500, run)
                raise
            finally:
                slots.release()
            _intake_audit(metered, "intake", 200, run)
            return out

        tabular_ep.__annotations__["req"] = _s2.TabularRequest  # a local import is not visible to a string annotation
        router.add_api_route(
            "/matter-tabular", tabular_ep, methods=["POST"], include_in_schema=False, response_model=None,
            route_class_override=_s2.FixedValidationRoute,
        )
        _s2.open_route_lists()  # the key-only gate list and roles.USER_FACING name this one route only while it exists

    # --- A4 bail drafting: registered ONLY when partner_api.yaml sets wf_a4_bail_drafting to true. It stores nothing and reads no
    # stored paper: the lawyer's declared facts, dates and tick arrive in the request; the deterministic referrals and the statute
    # framing run first, then at most the configured completer is called (none configured = the template only). Off (the default)
    # adds nothing: no module import, no route, no limiter, no thread, no read. Own limiters and slots; audit mode "intake" with a
    # "bail-" run id, nothing of the request in any row.
    try:
        _a4_cfg = None
        if _flags.get("wf_a4_bail_drafting") is True:
            from pravrudhi.application.bail_draft_config import load_bail_draft_config as _load_a4

            _a4_cfg = _load_a4(engine_root)
        if _a4_cfg is not None:
            from pravrudhi.application import bail_draft_route as _a4
    except Exception:  # noqa: BLE001 - an optional route must never stop the engine's own routes from starting
        _logger.warning("bail drafting unavailable; it stays off", exc_info=True)
        _a4_cfg = None
    if _a4_cfg is not None:
        _a4_state: dict[str, Any] = {}
        _a4_lock = threading.Lock()

        def _a4_limits(cfg: PartnerApiConfig) -> tuple[RateLimiter, Any, ConcurrencyLimiter, Any]:
            with _a4_lock:
                if not _a4_state:
                    clock_kw: dict[str, Any] = {} if rate_clock is None else {"now": rate_clock}
                    _a4_state["ip"] = RateLimiter(cfg.rate_limit_per_minute, max_keys=cfg.rate_limit_max_keys, **clock_kw)
                    _a4_state["key"] = (
                        tenancy.KeyRateLimiter(now=rate_clock) if rate_clock is not None else tenancy.KeyRateLimiter()
                    )
                    _a4_state["slots"] = ConcurrencyLimiter(_a4_cfg.max_concurrent)
                    _a4_state["ceiling"] = _a4.shared_ceiling()
                return _a4_state["ip"], _a4_state["key"], _a4_state["slots"], _a4_state["ceiling"]

        def _a4_admit(
            request: Request, user: User | None, metered: list[str]
        ) -> tuple[PartnerApiConfig, ConcurrencyLimiter, Any] | JSONResponse:
            try:
                cfg = _get_state()[0]
            except FileNotFoundError:
                return JSONResponse(status_code=503, content={"error": "service_config_missing"})
            closed = _window_closed(cfg)
            if closed is not None:
                return closed
            principal, throttled = _principal_or_throttle(request, cfg)
            if throttled is not None:
                return throttled
            if user is None and principal is None:
                return JSONResponse(status_code=401, content={"detail": "Drafting is offered only to signed-in users"})
            caller_key = f"key:{principal.key_id}" if principal is not None else f"user:{user.id}"  # type: ignore[union-attr]
            ip = _client_ip(
                request, trust_proxy_header=cfg.trust_proxy_header, trusted_proxies=cfg.trusted_proxies, caller_key=caller_key
            )
            ip_limiter, key_limiter, slots, ceiling = _a4_limits(cfg)
            if not ip_limiter.allow(ip):
                return rate_limited_response(
                    limit=ip_limiter.per_minute, retry_after=ip_limiter.retry_after_seconds(), scope="caller"
                )
            if principal is not None:
                key = tenancy.key_record(engine_root, principal.key_id, org_id=principal.org_id)
                if key is None:
                    _charge_bad_key(request, cfg)
                    raise HTTPException(401, "Invalid or revoked API key")
                if not key_limiter.allow(key.key_id, key.rate_limit_per_minute):
                    return rate_limited_response(
                        limit=key.rate_limit_per_minute, retry_after=key_limiter.retry_after_seconds(), scope="key"
                    )
                metered.append(key.key_id)
            return cfg, slots, ceiling

        def bail_draft_ep(req: Any, request: Request, user: User | None = CurrentUserDep) -> dict[str, Any] | JSONResponse:
            metered: list[str] = []
            admitted = _a4_admit(request, user, metered)
            if isinstance(admitted, JSONResponse):
                return admitted
            _cfg, slots, ceiling = admitted
            run = {"run_id": f"bail-{os.urandom(5).hex()}"}  # on EVERY audit row of this call, whatever the status
            if not slots.acquire():
                _intake_audit(metered, "intake", 503, run)
                return coded_503(AGENT_AT_CAPACITY)
            try:
                out = _a4.draft_for_request(req, _a4_cfg, run["run_id"], ceiling)
            except Exception as e:
                status = getattr(e, "status_code", None)
                if isinstance(status, int) and hasattr(e, "code"):
                    _intake_audit(metered, "intake", status, run)
                    body = {"error": e.code}
                    if e.code == "sensitive_declaration_required":
                        body["detail"] = str(e)  # R1's line, for the app to show
                    return JSONResponse(status_code=status, content=body)
                _intake_audit(metered, "intake", 500, run)
                raise
            finally:
                slots.release()
            _intake_audit(metered, "intake", 200, run)
            return out

        bail_draft_ep.__annotations__["req"] = _a4.BailDraftRequest  # a local import is not visible to a string annotation
        router.add_api_route(
            "/bail-draft", bail_draft_ep, methods=["POST"], include_in_schema=False, response_model=None,
            route_class_override=_a4.FixedValidationRoute,
        )
        _a4.open_route_lists()  # the key-only gate list and roles.USER_FACING name this one route only while it exists

    # --- A6 cheque-case check: registered ONLY when partner_api.yaml sets wf_a6_cheque_case_check to true. It reads A2's stored
    # papers (tenant-checked) and runs A3's chronology, A5's papers agenda and, when the request carries confirmed dates, B3's
    # checklist as functions, so it needs A2 ON (wf_a2_span_index true, not just a store folder); the other three slices' own
    # flags are NOT required (only their modules are imported, lazily). Off (the default) adds nothing: no module import, no
    # route, no limiter, no thread, no read. Own limiters and slots; audit mode "intake" with a "cheque-" run id.
    try:
        _a6_cfg = None
        if _flags.get("wf_a6_cheque_case_check") is True:
            from pravrudhi.application.cheque_case_config import load_cheque_case_config as _load_a6

            _a6_cfg = _load_a6(engine_root)
        if _a6_cfg is not None:
            from pravrudhi.application import cheque_case_route as _a6
            from pravrudhi.application import matter_spans_config as _a6m
    except Exception:  # noqa: BLE001 - an optional route must never stop the engine's own routes from starting
        _logger.warning("cheque-case check unavailable; it stays off", exc_info=True)
        _a6_cfg = None
    if _a6_cfg is not None:
        _a6_state: dict[str, Any] = {}
        _a6_lock = threading.Lock()

        def _a6_limits(cfg: PartnerApiConfig) -> tuple[RateLimiter, Any, ConcurrencyLimiter]:
            with _a6_lock:
                if not _a6_state:
                    clock_kw: dict[str, Any] = {} if rate_clock is None else {"now": rate_clock}
                    ip_limit = getattr(cfg, "slice_ip_limit", cfg.rate_limit_per_minute)  # the slice limit once it exists (#534)
                    _a6_state["ip"] = RateLimiter(ip_limit, max_keys=cfg.rate_limit_max_keys, **clock_kw)
                    _a6_state["key"] = (
                        tenancy.KeyRateLimiter(now=rate_clock) if rate_clock is not None else tenancy.KeyRateLimiter()
                    )
                    _a6_state["slots"] = ConcurrencyLimiter(_a6_cfg.max_concurrent)
                return _a6_state["ip"], _a6_state["key"], _a6_state["slots"]

        def _a6_admit(
            request: Request, user: User | None, metered: list[str]
        ) -> tuple[PartnerApiConfig, ConcurrencyLimiter, str] | JSONResponse:
            try:
                cfg = _get_state()[0]
            except FileNotFoundError:
                return JSONResponse(status_code=503, content={"error": "service_config_missing"})
            closed = _window_closed(cfg)
            if closed is not None:
                return closed
            principal, throttled = _principal_or_throttle(request, cfg)
            if throttled is not None:
                return throttled
            if user is None and principal is None:
                return JSONResponse(status_code=401, content={"detail": "Stored papers are offered only to signed-in users"})
            tenant = f"org:{principal.org_id}" if principal is not None else f"user:{user.id}"  # type: ignore[union-attr]
            caller_key = f"key:{principal.key_id}" if principal is not None else f"user:{user.id}"  # type: ignore[union-attr]
            ip = _client_ip(
                request, trust_proxy_header=cfg.trust_proxy_header, trusted_proxies=cfg.trusted_proxies, caller_key=caller_key
            )
            ip_limiter, key_limiter, slots = _a6_limits(cfg)
            if not ip_limiter.allow(ip):
                return rate_limited_response(
                    limit=ip_limiter.per_minute, retry_after=ip_limiter.retry_after_seconds(), scope="caller"
                )
            if principal is not None:
                key = tenancy.key_record(engine_root, principal.key_id, org_id=principal.org_id)
                if key is None:
                    _charge_bad_key(request, cfg)
                    raise HTTPException(401, "Invalid or revoked API key")
                if not key_limiter.allow(key.key_id, key.rate_limit_per_minute):
                    return rate_limited_response(
                        limit=key.rate_limit_per_minute, retry_after=key_limiter.retry_after_seconds(), scope="key"
                    )
                metered.append(key.key_id)
            return cfg, slots, tenant

        def cheque_case_ep(req: Any, request: Request, user: User | None = CurrentUserDep) -> dict[str, Any] | JSONResponse:
            metered: list[str] = []
            admitted = _a6_admit(request, user, metered)
            if isinstance(admitted, JSONResponse):
                return admitted
            _cfg, slots, tenant = admitted
            run = {"run_id": f"cheque-{os.urandom(5).hex()}"}  # on EVERY audit row of this call, whatever the status
            if not slots.acquire():
                _intake_audit(metered, "intake", 503, run)
                return coded_503(AGENT_AT_CAPACITY)
            try:
                a2_cfg = _a6m.load_matter_spans_config(engine_root)  # "A2 on" = its flag in the config, never the store folder
                if a2_cfg is None:
                    _intake_audit(metered, "intake", 409, run)
                    return JSONResponse(status_code=409, content={"detail": _a6.STORE_OFF, "error": "store_off"})
                from pravrudhi.application.matter_store_factory import open_store as _Store

                out = _a6.check_for_matter(_Store(engine_root, a2_cfg), tenant, req, run["run_id"])
            except Exception as e:
                status = getattr(e, "status_code", None)
                if isinstance(status, int) and hasattr(e, "code"):
                    _intake_audit(metered, "intake", status, run)
                    return JSONResponse(status_code=status, content={"detail": str(e), "error": e.code})
                _intake_audit(metered, "intake", 500, run)
                raise
            finally:
                slots.release()
            _intake_audit(metered, "intake", 200, run)
            return out

        cheque_case_ep.__annotations__["req"] = _a6.ChequeCaseRequest  # a local import is not visible to a string annotation
        router.add_api_route(
            "/cheque-case-check", cheque_case_ep, methods=["POST"], include_in_schema=False, response_model=None,
            route_class_override=_a6.FixedValidationRoute,
        )
        _a6.open_route_lists()  # the key-only gate list and roles.USER_FACING name this one route only while it exists

    # --- A7 matter hearing pack: registered ONLY when partner_api.yaml sets wf_a7_matter_hearing_pack to true. It reads A2's
    # stored papers (tenant-checked) and runs A3's chronology and A5's papers agenda as functions, plus S1's citation sweep ONLY
    # when the S1 flag is also on and the citation index is available; it needs A2 ON (wf_a2_span_index true, not just a store
    # folder). A3's and A5's own flags are NOT required. Off (the default) adds nothing: no module import, no route, no limiter,
    # no thread, no read. Own limiters and slots; audit mode "intake" with a "hearing-" run id.
    try:
        _a7_cfg = None
        if _flags.get("wf_a7_matter_hearing_pack") is True:
            from pravrudhi.application.matter_hearing_pack_config import load_matter_hearing_pack_config as _load_a7

            _a7_cfg = _load_a7(engine_root)
        if _a7_cfg is not None:
            from pravrudhi.application import matter_hearing_pack_route as _a7
            from pravrudhi.application import matter_spans_config as _a7m
    except Exception:  # noqa: BLE001 - an optional route must never stop the engine's own routes from starting
        _logger.warning("matter hearing pack unavailable; it stays off", exc_info=True)
        _a7_cfg = None
    if _a7_cfg is not None:
        _a7_state: dict[str, Any] = {}
        _a7_lock = threading.Lock()

        def _a7_limits(cfg: PartnerApiConfig) -> tuple[RateLimiter, Any, ConcurrencyLimiter]:
            with _a7_lock:
                if not _a7_state:
                    clock_kw: dict[str, Any] = {} if rate_clock is None else {"now": rate_clock}
                    ip_limit = getattr(cfg, "slice_ip_limit", cfg.rate_limit_per_minute)  # the slice limit once it exists (#534)
                    _a7_state["ip"] = RateLimiter(ip_limit, max_keys=cfg.rate_limit_max_keys, **clock_kw)
                    _a7_state["key"] = (
                        tenancy.KeyRateLimiter(now=rate_clock) if rate_clock is not None else tenancy.KeyRateLimiter()
                    )
                    _a7_state["slots"] = ConcurrencyLimiter(_a7_cfg.max_concurrent)
                return _a7_state["ip"], _a7_state["key"], _a7_state["slots"]

        def _a7_admit(
            request: Request, user: User | None, metered: list[str]
        ) -> tuple[PartnerApiConfig, ConcurrencyLimiter, str] | JSONResponse:
            try:
                cfg = _get_state()[0]
            except FileNotFoundError:
                return JSONResponse(status_code=503, content={"error": "service_config_missing"})
            closed = _window_closed(cfg)
            if closed is not None:
                return closed
            principal, throttled = _principal_or_throttle(request, cfg)
            if throttled is not None:
                return throttled
            if user is None and principal is None:
                return JSONResponse(status_code=401, content={"detail": "Stored papers are offered only to signed-in users"})
            tenant = f"org:{principal.org_id}" if principal is not None else f"user:{user.id}"  # type: ignore[union-attr]
            caller_key = f"key:{principal.key_id}" if principal is not None else f"user:{user.id}"  # type: ignore[union-attr]
            ip = _client_ip(
                request, trust_proxy_header=cfg.trust_proxy_header, trusted_proxies=cfg.trusted_proxies, caller_key=caller_key
            )
            ip_limiter, key_limiter, slots = _a7_limits(cfg)
            if not ip_limiter.allow(ip):
                return rate_limited_response(
                    limit=ip_limiter.per_minute, retry_after=ip_limiter.retry_after_seconds(), scope="caller"
                )
            if principal is not None:
                key = tenancy.key_record(engine_root, principal.key_id, org_id=principal.org_id)
                if key is None:
                    _charge_bad_key(request, cfg)
                    raise HTTPException(401, "Invalid or revoked API key")
                if not key_limiter.allow(key.key_id, key.rate_limit_per_minute):
                    return rate_limited_response(
                        limit=key.rate_limit_per_minute, retry_after=key_limiter.retry_after_seconds(), scope="key"
                    )
                metered.append(key.key_id)
            return cfg, slots, tenant

        def hearing_pack_ep(req: Any, request: Request, user: User | None = CurrentUserDep) -> dict[str, Any] | JSONResponse:
            metered: list[str] = []
            admitted = _a7_admit(request, user, metered)
            if isinstance(admitted, JSONResponse):
                return admitted
            cfg, slots, tenant = admitted
            run = {"run_id": f"hearing-{os.urandom(5).hex()}"}  # on EVERY audit row of this call, whatever the status
            if not slots.acquire():
                _intake_audit(metered, "intake", 503, run)
                return coded_503(AGENT_AT_CAPACITY)
            try:
                a2_cfg = _a7m.load_matter_spans_config(engine_root)  # "A2 on" = its flag in the config, never the store folder
                if a2_cfg is None:
                    _intake_audit(metered, "intake", 409, run)
                    return JSONResponse(status_code=409, content={"detail": _a7.STORE_OFF, "error": "store_off"})
                from pravrudhi.application.matter_store_factory import open_store as _Store

                sweep_runner: Callable[..., dict[str, Any]] | None = None
                if _s1_cfg is not None:  # S1's own flag is on (its module and config were imported above)
                    env_path = os.environ.get("PRAVRUDHI_CITATION_INDEX")
                    path = citation_index_path or (Path(env_path) if env_path else None)
                    if path is not None and Path(path).is_file():
                        index_path = Path(path)

                        def _a7_look(citation_text: str) -> tuple[str, bool]:
                            result, _cov, compared = _lookup(
                                index_path, VerifyCitationRequest(citation=citation_text), cfg.verify_timeout_s
                            )
                            return result.result, compared

                        def sweep_runner(store: Any, tnt: str, matter_id: str, run_id: str) -> dict[str, Any]:  # noqa: F811
                            req_s1 = _s1.SweepRequest(matter_id=matter_id)
                            return _s1.sweep_for_matter(store, tnt, req_s1, run_id, _s1_cfg, _a7_look)

                out = _a7.pack_for_matter(_Store(engine_root, a2_cfg), tenant, req, run["run_id"], sweep_runner)
            except Exception as e:
                status = getattr(e, "status_code", None)
                if isinstance(status, int) and hasattr(e, "code"):
                    _intake_audit(metered, "intake", status, run)
                    return JSONResponse(status_code=status, content={"detail": str(e), "error": e.code})
                _intake_audit(metered, "intake", 500, run)
                raise
            finally:
                slots.release()
            _intake_audit(metered, "intake", 200, run)
            return out

        hearing_pack_ep.__annotations__["req"] = _a7.MatterHearingPackRequest  # a local import is not visible to an annotation
        router.add_api_route(
            "/matter-hearing-pack", hearing_pack_ep, methods=["POST"], include_in_schema=False, response_model=None,
            route_class_override=_a7.FixedValidationRoute,
        )
        _a7.open_route_lists()  # the key-only gate list and roles.USER_FACING name this one route only while it exists

    # --- A8 synopsis and list of dates: registered ONLY when partner_api.yaml sets wf_a8_synopsis_list_of_dates to true. It reads
    # A2's stored papers through A3's chronology (a function; A3's own flag is NOT required), so it needs A2 ON (wf_a2_span_index
    # true, not just a store folder). The drafting endpoint, caps and the in-process day ceiling are the ones bail drafting uses.
    # Off (the default) adds nothing: no module import, no route, no limiter, no thread, no read. Own limiters and slots; audit
    # mode "intake" with a "synopsis-" run id, nothing of the request or the papers in any row.
    try:
        _a8_cfg = None
        if _flags.get("wf_a8_synopsis_list_of_dates") is True:
            from pravrudhi.application.synopsis_dates_config import load_synopsis_dates_config as _load_a8

            _a8_cfg = _load_a8(engine_root)
        if _a8_cfg is not None:
            from pravrudhi.application import bail_draft_route as _a8b
            from pravrudhi.application import matter_spans_config as _a8m
            from pravrudhi.application import synopsis_dates_route as _a8
    except Exception:  # noqa: BLE001 - an optional route must never stop the engine's own routes from starting
        _logger.warning("synopsis and list of dates unavailable; it stays off", exc_info=True)
        _a8_cfg = None
    if _a8_cfg is not None:
        _a8_state: dict[str, Any] = {}
        _a8_lock = threading.Lock()

        def _a8_limits(cfg: PartnerApiConfig) -> tuple[RateLimiter, Any, ConcurrencyLimiter]:
            with _a8_lock:
                if not _a8_state:
                    clock_kw: dict[str, Any] = {} if rate_clock is None else {"now": rate_clock}
                    ip_limit = getattr(cfg, "slice_ip_limit", cfg.rate_limit_per_minute)  # the slice limit once it exists (#534)
                    _a8_state["ip"] = RateLimiter(ip_limit, max_keys=cfg.rate_limit_max_keys, **clock_kw)
                    _a8_state["key"] = (
                        tenancy.KeyRateLimiter(now=rate_clock) if rate_clock is not None else tenancy.KeyRateLimiter()
                    )
                    _a8_state["slots"] = ConcurrencyLimiter(_a8_cfg.max_concurrent)
                return _a8_state["ip"], _a8_state["key"], _a8_state["slots"]

        def _a8_admit(
            request: Request, user: User | None, metered: list[str]
        ) -> tuple[PartnerApiConfig, ConcurrencyLimiter, str] | JSONResponse:
            try:
                cfg = _get_state()[0]
            except FileNotFoundError:
                return JSONResponse(status_code=503, content={"error": "service_config_missing"})
            closed = _window_closed(cfg)
            if closed is not None:
                return closed
            principal, throttled = _principal_or_throttle(request, cfg)
            if throttled is not None:
                return throttled
            if user is None and principal is None:
                return JSONResponse(status_code=401, content={"detail": "Drafting is offered only to signed-in users"})
            tenant = f"org:{principal.org_id}" if principal is not None else f"user:{user.id}"  # type: ignore[union-attr]
            caller_key = f"key:{principal.key_id}" if principal is not None else f"user:{user.id}"  # type: ignore[union-attr]
            ip = _client_ip(
                request, trust_proxy_header=cfg.trust_proxy_header, trusted_proxies=cfg.trusted_proxies, caller_key=caller_key
            )
            ip_limiter, key_limiter, slots = _a8_limits(cfg)
            if not ip_limiter.allow(ip):
                return rate_limited_response(
                    limit=ip_limiter.per_minute, retry_after=ip_limiter.retry_after_seconds(), scope="caller"
                )
            if principal is not None:
                key = tenancy.key_record(engine_root, principal.key_id, org_id=principal.org_id)
                if key is None:
                    _charge_bad_key(request, cfg)
                    raise HTTPException(401, "Invalid or revoked API key")
                if not key_limiter.allow(key.key_id, key.rate_limit_per_minute):
                    return rate_limited_response(
                        limit=key.rate_limit_per_minute, retry_after=key_limiter.retry_after_seconds(), scope="key"
                    )
                metered.append(key.key_id)
            return cfg, slots, tenant

        def synopsis_dates_ep(req: Any, request: Request, user: User | None = CurrentUserDep) -> dict[str, Any] | JSONResponse:
            metered: list[str] = []
            admitted = _a8_admit(request, user, metered)
            if isinstance(admitted, JSONResponse):
                return admitted
            _cfg, slots, tenant = admitted
            run = {"run_id": f"synopsis-{os.urandom(5).hex()}"}  # on EVERY audit row of this call, whatever the status
            if not slots.acquire():
                _intake_audit(metered, "intake", 503, run)
                return coded_503(AGENT_AT_CAPACITY)
            try:
                a2_cfg = _a8m.load_matter_spans_config(engine_root)  # "A2 on" = its flag in the config, never the store folder
                if a2_cfg is None:
                    _intake_audit(metered, "intake", 409, run)
                    return JSONResponse(status_code=409, content={"detail": _a8.STORE_OFF, "error": "store_off"})
                from pravrudhi.application.matter_store_factory import open_store as _Store

                out = _a8.synopsis_for_request(
                    _Store(engine_root, a2_cfg), tenant, req, _a8_cfg, run["run_id"], _a8b.shared_ceiling()
                )
            except Exception as e:
                status = getattr(e, "status_code", None)
                if isinstance(status, int) and hasattr(e, "code"):
                    _intake_audit(metered, "intake", status, run)
                    return JSONResponse(status_code=status, content={"detail": str(e), "error": e.code})
                _intake_audit(metered, "intake", 500, run)
                raise
            finally:
                slots.release()
            _intake_audit(metered, "intake", 200, run)
            return out

        synopsis_dates_ep.__annotations__["req"] = _a8.SynopsisDatesRequest  # a local import is not visible to an annotation
        router.add_api_route(
            "/synopsis-dates", synopsis_dates_ep, methods=["POST"], include_in_schema=False, response_model=None,
            route_class_override=_a8.FixedValidationRoute,
        )
        _a8.open_route_lists()  # the key-only gate list and roles.USER_FACING name this one route only while it exists

    # #1038 cold start with progress. Off unless the YAML flag `cold_start_wake` is the boolean true, its numbers are valid AND
    # the HMAC secret env var is set: with anything missing none of these routes exists and no slice module is imported.
    # PR-4: no job object and no engine-run screening. The coordination state is shared by every container (wake_store.py); the
    # CLIENT submits the screening itself once /analyse-facts/wake/status says ready or partial.
    _cold_registered = False
    if _flags.get("cold_start_wake") is True:
        from pravrudhi.api.identity import AuthMode, auth_mode
        from pravrudhi.application import cold_start_service as _cs
        from pravrudhi.application import judge_endpoint_state as jes
        from pravrudhi.application import judge_wake as _jw
        from pravrudhi.application import wake_policy as _wp
        from pravrudhi.application import wake_store as _ws
        from pravrudhi.application import wake_ticket as _wt

        _cold_cfg = _jw.parse_cold_start_config(_flags)
        _cold_secret = os.environ.get(_cold_cfg.waker_secret_env, "") if _cold_cfg is not None else ""
        if _cold_cfg is not None and _cold_secret:
            _cold_ac: dict[str, Any] = {}

            def _cold_reading(role: str) -> str:
                """One judge's load reading for the wake: `ready` for a judge that is not a RunPod serverless endpoint (nothing
                to wake) or when no second judge is configured; otherwise the endpoint's own /health classification."""
                try:
                    if "ac" not in _cold_ac:
                        _cold_ac["ac"] = getattr(factory(engine_root), "config", None)
                    ac = _cold_ac["ac"]
                    spec = (ac.house_judge if role == "first" else ac.second_judge) if ac is not None else None
                    block = dict(spec or {})
                    if role == "second" and not block:
                        return "ready"
                    url = str(block.get("base_url") or "")
                    if not url:
                        return "unknown"
                    if jes.runpod_endpoint_id(url) is None:
                        return "ready"
                    key = os.environ.get("NYAYA_HOUSE_JUDGE_API_KEY" if role == "first" else "NYAYA_SECOND_JUDGE_API_KEY")
                    _offline_check.ttl_s = _get_state()[0].judge_health_ttl_s  # the same cache lifetime as the offline check
                    reading = str(_offline_check.state(url, key or block.get("api_key")).status)
                    if reading == "ready" and not _offline_check.serving_state(url, key or block.get("api_key")):
                        return "warming"  # #1041: a worker is up but the judge does not serve yet: not ready
                    return reading
                except Exception:  # noqa: BLE001 - a failed reading is `unknown`, which is never ready
                    return "unknown"

            _status_judges.append(lambda: {role: _cold_reading(role) for role in ("first", "second")})
            _wake_call = tenancy.wake_call_or_none()
            _wake_store: _ws.WakeStore = (
                _ws.PgWakeStore(_wake_call, _cold_cfg) if _wake_call is not None else _ws.MemoryWakeStore(_cold_cfg)
            )
            _cold_svc = _cs.ColdStartService(_cold_cfg, secret=_cold_secret, store=_wake_store, health=_cold_reading)
            # two limiters, so a flood of anonymous addresses against the waker routes cannot spend the team users' budget (or the
            # reverse), and each holds far more keys than a flood can plausibly fill before stale ones are reclaimed
            _waker_limiter = RateLimiter(120, max_keys=4096)
            _user_limiter = RateLimiter(120, max_keys=4096)

            _any_signed_in = _wp.any_signed_in_enabled()  # read ONCE at startup: exactly "1"; a later env change does nothing

            def _is_waker(user: User | None) -> bool:
                return _wp.may_wake(
                    user, auth_enabled=auth_mode() is not AuthMode.DISABLED, wakers=_wp.waker_ids(), any_signed_in=_any_signed_in
                )

            class _RealTicket:
                def mode(self, request: Request, user: User | None) -> str | None:
                    """The mode of a VALID ticket held by an allowlisted user (no I/O); anything else is no ticket at all."""
                    value = request.headers.get(_wt.HEADER)
                    if not value or user is None or not _is_waker(user):
                        return None
                    return _cold_svc.ticket_mode(value, user.id)

                def extend(self) -> None:
                    _cold_svc.extend()

            _tickets[0] = _RealTicket()

            def _waker_throttled(request: Request) -> JSONResponse | None:
                """Per client address, so an anonymous caller from elsewhere cannot spend the box poller's budget."""
                cfg = _get_state()[0]
                addr = _client_ip(request, trust_proxy_header=cfg.trust_proxy_header, trusted_proxies=cfg.trusted_proxies)
                if _waker_limiter.allow(f"waker:{addr}"):
                    return None
                return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"}, headers={"Retry-After": "5"})

            def _wake_refused(user: User | None) -> JSONResponse | None:
                """The same gate for the start and the status: an allowlisted signed-in user inside the service window, within a
                per-user call budget; every refusal is the one identical marker (no judge-state oracle for anyone else)."""
                if user is None or not _is_waker(user):
                    return JSONResponse(status_code=503, content={"error": "judges_offline", "wake": "not_allowed"})
                try:
                    cfg = _get_state()[0]
                except FileNotFoundError:
                    return JSONResponse(status_code=503, content={"error": "service_config_missing"})
                if _window_closed(cfg) is not None or not _user_limiter.allow(f"user:{user.id}"):
                    return JSONResponse(status_code=503, content={"error": "judges_offline", "wake": "not_allowed"})
                return None

            @router.post("/analyse-facts/wake", include_in_schema=False)
            def wake_start_ep(user: User | None = CurrentUserDep) -> Any:
                refused = _wake_refused(user)
                if refused is not None or user is None:
                    return refused
                outcome, body = _cold_svc.start(user.id)
                if outcome == "not_allowed":
                    return JSONResponse(status_code=503, content={"error": "judges_offline", "wake": "not_allowed"})
                if outcome == "ready":
                    return JSONResponse(status_code=200, content=body)
                return JSONResponse(status_code=202, content=body, headers={"Retry-After": str(_cold_cfg.poll_after_s)})

            @router.get("/analyse-facts/wake/status", include_in_schema=False)
            def wake_status_ep(user: User | None = CurrentUserDep) -> Any:
                refused = _wake_refused(user)
                if refused is not None or user is None:
                    return refused
                try:
                    return JSONResponse(status_code=200, content=_cold_svc.status(user.id))
                except Exception:  # noqa: BLE001 - a store that cannot answer is a refusal, never a state
                    return JSONResponse(status_code=503, content={"error": "judges_offline", "wake": "not_allowed"})

            def _waker_headers(request: Request) -> tuple[str | None, str | None, str | None]:
                h = request.headers
                return h.get("x-waker-timestamp"), h.get("x-waker-nonce"), h.get("x-waker-signature")

            @router.get("/internal/wake-requests", include_in_schema=False)
            def wake_requests_ep(request: Request) -> JSONResponse:
                throttled = _waker_throttled(request)
                if throttled is not None:
                    return throttled
                status, body = _cold_svc.requests(*_waker_headers(request))
                return JSONResponse(status_code=status, content=body)

            _cold_registered = True

            @router.post("/internal/wake-results", include_in_schema=False)
            async def wake_results_ep(request: Request) -> JSONResponse:
                throttled = _waker_throttled(request)
                if throttled is not None:
                    return throttled
                declared = request.headers.get("content-length")  # refuse an oversize body BEFORE reading it
                if declared is None or not declared.isdigit() or int(declared) > _jw.MAX_BODY:
                    return JSONResponse(status_code=413, content={"error": "body_too_large"})
                raw = await request.body()
                status, body = _cold_svc.deliver(*_waker_headers(request), raw)
                return JSONResponse(status_code=status, content=body)

    # #1038: with cold start NOT configured the two waker paths still exist, as plain 404s. The identity gate lets a request that
    # carries exactly one x-waker-signature header reach these two paths (it cannot know whether cold start is on), and without a
    # route they would fall through to the app's catch-all and answer 200 with the page. Nothing is read, written or checked here.
    if not _cold_registered:

        class _NotFound(BaseModel):
            detail: str

        @router.get("/internal/wake-requests", include_in_schema=False, response_model=_NotFound)
        def _wake_requests_absent() -> JSONResponse:
            return JSONResponse(status_code=404, content={"detail": "Not Found"})

        @router.post("/internal/wake-results", include_in_schema=False, response_model=_NotFound)
        def _wake_results_absent() -> JSONResponse:
            return JSONResponse(status_code=404, content={"detail": "Not Found"})

    return router

"""#1038 cold start with progress, PR-1: the pure core (nothing here is reached while `cold_start_wake` is off).

Three small parts, independent of the HTTP route and of any network:

* `parse_cold_start_config`: the switch and the numbers (flag `cold_start_wake`, packaged false), read from
  `partner_api.yaml`. Any bad value keeps the feature OFF (never raises).
* `JudgeWake`: the per-judge state machine behind the coarse `wake` block the app polls: waiting -> starting -> ready | failed,
  one budget for both judges, an injected clock, no I/O. A health reading of `unknown` is NOT ready (it counts as starting
  until the budget ends). Outcomes: both ready = `ready`; the first ready and the second failed = `partial` (the run then
  refers every established ingredient: nothing is shown as supported without both judges); the first failed = `failed` or
  `timeout`.
* `WakeBoard` / `sign` / `verify_http`: the engine's side of the waker contract v2 (design note WAKER_CONTRACT_V2_PULL_1038): the
  engine makes NO outbound call. It lists signed, label-only wake/extend requests that a box-side waker polls, and takes the
  waker's signed results back. The engine holds an HMAC secret for the waker and calls no RunPod write route itself.

Nothing in this module logs or returns a secret, a key id, a lease or a cap."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

FLAG = "cold_start_wake"
ROLES = ("first", "second")
HARD_MAX_BUDGET_S = 420.0
WAKER_PATH = "/wake"

ModelState = Literal["waiting", "starting", "ready", "failed"]
WakeState = Literal["waking", "ready", "partial", "failed", "timeout"]
Reason = Literal["second_judge_unavailable", "first_judge_unavailable", "start_timeout"]
#: What `judge_endpoint_state.classify` reads from a judge endpoint's /health.
Reading = Literal["ready", "warming", "parked", "unknown"]


@dataclass(frozen=True)
class WakeConfig:
    budget_s: float  # per judge, both in parallel; capped by HARD_MAX_BUDGET_S
    poll_after_s: int  # how soon the app should poll (a hint, not shown)
    deadline_margin_s: float  # deadline_s = budget + this margin (the app stops polling then; never shown)
    waker_secret_env: str  # the NAME of the env var holding the HMAC secret (secret by reference)
    request_ttl_s: float = 120.0  # an unanswered wake request is dropped (= failed) after this
    user_wakes_per_day: int = 6  # per verified user, failed wakes count too (R2 item 10)
    failed_cooldown_s: float = 120.0  # after a failed wake the same user waits this long (no retry storm)
    ticket_ttl_s: float = 600.0  # PR-4: how long a wake ticket (issued with a ready/partial status) is honoured
    row_ttl_s: float = 900.0  # PR-4: how long a shared judge-wake request row lives (then it is purged)


_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{2,63}")


def _num(raw: Any, lo: float, hi: float) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not (lo <= raw <= hi):
        return None
    return float(raw)


def parse_cold_start_config(body: Any) -> WakeConfig | None:
    """The config for a parsed `partner_api.yaml` body, or None (off). Pure; never raises. Only the boolean true turns it
    on, and then every value must be valid and `cold_start_wake_second` the value `parallel` (the lazy variant is not built
    yet). One bad value keeps it OFF. Whether the secret env var is actually set is the caller's check at start-up."""
    if not isinstance(body, Mapping) or body.get(FLAG) is not True:
        return None
    budget = _num(body.get("cold_start_budget_s", 300), 30, HARD_MAX_BUDGET_S)
    poll = _num(body.get("cold_start_poll_after_s", 5), 1, 60)
    margin = _num(body.get("cold_start_deadline_margin_s", 30), 0, 300)
    ttl = _num(body.get("cold_start_request_ttl_s", 120), 30, 600)
    env = body.get("cold_start_waker_secret_env", "PRAVRUDHI_WAKER_SECRET")
    if budget is None or poll is None or margin is None or ttl is None:
        return None
    if body.get("cold_start_wake_second", "parallel") != "parallel":
        return None
    if not isinstance(env, str) or not _ENV_NAME.fullmatch(env):
        return None
    per_day = _num(body.get("cold_start_user_wakes_per_day", 6), 1, 50)
    cooldown = _num(body.get("cold_start_failed_cooldown_s", 120), 0, 3600)
    ticket_ttl = _num(body.get("cold_start_ticket_ttl_s", 600), 60, 3600)
    row_ttl = _num(body.get("cold_start_row_ttl_s", 900), 300, 86400)
    if per_day is None or cooldown is None or ticket_ttl is None or row_ttl is None:
        return None
    return WakeConfig(budget, int(poll), margin, env, ttl, int(per_day), cooldown, ticket_ttl, row_ttl)


def state_of(model: Mapping[str, str], *, first_timed_out: bool = False) -> WakeState:
    """The overall wake state from the two judges' states. Pure: the one decision table, shared by `JudgeWake` and the
    stateless status read of PR-4. A failed first judge ends it at once (nothing can be scored without it)."""
    if model["first"] == "failed":
        return "timeout" if first_timed_out else "failed"
    if any(model[r] in ("waiting", "starting") for r in ROLES):
        return "waking"
    return "ready" if model["second"] == "ready" else "partial"


def reason_of(state: str) -> Reason | None:
    reasons: dict[str, Reason] = {
        "partial": "second_judge_unavailable", "timeout": "start_timeout", "failed": "first_judge_unavailable",
    }
    return reasons.get(state)


class JudgeWake:
    """The load state of the two judges for ONE wake. Pure: the caller feeds it what it saw (`start`, `waker_answered`,
    `health`) and asks `state()`/`block()`; `tick()` applies the budget. A judge already ready at the start is never woken."""

    def __init__(self, cfg: WakeConfig, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._cfg = cfg
        self._clock = clock
        self._t0: float | None = None
        self._model: dict[str, ModelState] = dict.fromkeys(ROLES, "waiting")
        self._timed_out: set[str] = set()

    def start(self, initial: Mapping[str, str]) -> list[str]:
        """Begin the wake from the first health readings (role -> reading). Returns the roles to wake (not already ready)."""
        if self._t0 is None:
            self._t0 = self._clock()
        for role in ROLES:
            if initial.get(role) == "ready":
                self._model[role] = "ready"
        return [r for r in ROLES if self._model[r] == "waiting"]

    def waker_answered(self, role: str, answer: str) -> None:
        """The waker's answer for a judge that was waiting: granted/already_awake -> starting; anything else -> failed."""
        if role in self._model and self._model[role] == "waiting":
            self._model[role] = "starting" if answer in ("granted", "already_awake") else "failed"

    def health(self, role: str, reading: str) -> None:
        """A reading for a judge that is starting. Only `ready` moves it; warming, parked and `unknown` leave it starting."""
        if role in self._model and self._model[role] == "starting" and reading == "ready":
            self._model[role] = "ready"

    def tick(self) -> None:
        """Apply the budget: every judge still waiting or starting when it ends has failed (it timed out)."""
        if self._t0 is None or self._clock() - self._t0 < min(self._cfg.budget_s, HARD_MAX_BUDGET_S):
            return
        for role in ROLES:
            if self._model[role] in ("waiting", "starting"):
                self._model[role] = "failed"
                self._timed_out.add(role)

    @property
    def terminal(self) -> bool:
        return self.state() != "waking"

    def state(self) -> WakeState:
        return state_of(self._model, first_timed_out="first" in self._timed_out)

    def reason(self) -> Reason | None:
        return reason_of(self.state())

    def block(self) -> dict[str, Any]:
        """The coarse `wake` block: states and a reason only. No ids, counts, lease, cap, cost, envelope or key (R2 item 11)."""
        return {
            "state": self.state(),
            "models": [{"role": r, "state": self._model[r], "progress": None} for r in ROLES],
            "reason": self.reason(),
            "deadline_s": int(min(self._cfg.budget_s, HARD_MAX_BUDGET_S) + self._cfg.deadline_margin_s),
        }




# ---- the engine's side of the waker contract (v2, pull) ---------------------------------------------------------------


KINDS = ("wake", "extend")
ANSWERS = ("granted", "already_awake", "refused", "failed")
_NONCE = re.compile(r"[0-9a-f]{32}")
WINDOW_S = 60
MAX_BODY = 2048  # a poll delivery body (a wake request body is about 150 bytes)


def canonical(ts: int, nonce: str, label: str, kind: str, lease_ref: str | None = None) -> str:
    """The string a wake/extend request's HMAC covers (the contract): `v1|POST|/wake|ts|nonce|label|kind`, plus `|lease_ref` for
    an extend. Raises ValueError on a kind, label or nonce outside the contract or a `|` in the lease_ref (R2: no ambiguity)."""
    if kind not in KINDS or label not in ROLES or not _NONCE.fullmatch(nonce) or not isinstance(ts, int):
        raise ValueError("not a valid wake request")
    if (kind == "extend") != (lease_ref is not None) or (lease_ref is not None and ("|" in lease_ref or not lease_ref)):
        raise ValueError("lease_ref belongs to an extend and must not contain a pipe")
    base = f"v1|POST|{WAKER_PATH}|{ts}|{nonce}|{label}|{kind}"
    return f"{base}|{lease_ref}" if lease_ref is not None else base


def _hmac(secret: str | bytes, message: str) -> str:
    key = secret.encode() if isinstance(secret, str) else secret
    return hmac.new(key, message.encode(), hashlib.sha256).hexdigest()


def sign(secret: str | bytes, ts: int, nonce: str, label: str, kind: str, lease_ref: str | None = None) -> str:
    return _hmac(secret, canonical(ts, nonce, label, kind, lease_ref))


def canonical_http(method: str, path: str, ts: int, nonce: str, body: bytes | None = None) -> str:
    """The string a poll or a result delivery's HMAC covers: `v1|GET|path|ts|nonce` for a read, and for a delivery
    `v1|POST|path|ts|nonce|sha256(body)`. A different domain from `canonical`, so a wake signature is not a valid poll."""
    base = f"v1|{method}|{path}|{ts}|{nonce}"
    return f"{base}|{hashlib.sha256(body).hexdigest()}" if method == "POST" and body is not None else base


def sign_http(secret: str | bytes, method: str, path: str, ts: int, nonce: str, body: bytes | None = None) -> str:
    return _hmac(secret, canonical_http(method, path, ts, nonce, body))


class NonceStore:
    """Nonces seen in the last `window_s` seconds (thread-safe, in this process). `first_use(nonce)` is True once per nonce."""

    def __init__(self, window_s: float = 2 * WINDOW_S, *, clock: Callable[[], float] = time.time) -> None:
        self._window, self._clock = window_s, clock
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def first_use(self, nonce: str) -> bool:
        with self._lock:
            now = self._clock()
            for n in [n for n, t in self._seen.items() if now - t > self._window]:
                del self._seen[n]
            if nonce in self._seen:
                return False
            self._seen[nonce] = now
            return True


def verify_http(
    secret: str | bytes,
    method: str,
    path: str,
    ts_header: str | None,
    nonce: str | None,
    sig_header: str | None,
    body: bytes | None,
    *,
    nonces: NonceStore,
    clock: Callable[[], float] = time.time,
) -> bool:
    """True only for a correctly signed poll/delivery inside the 60 s window whose nonce has not been used. Constant-time
    comparison; the nonce is recorded only AFTER the signature is valid (a forger cannot burn real nonces). Never raises."""
    try:
        if not ts_header or not nonce or not sig_header or not sig_header.startswith("v1="):
            return False
        if not _NONCE.fullmatch(nonce) or (body is not None and len(body) > MAX_BODY):
            return False
        ts = int(ts_header)
        if abs(clock() - ts) > WINDOW_S:
            return False
        want = sign_http(secret, method, path, ts, nonce, body)
        if not hmac.compare_digest(want, sig_header[3:]):
            return False
        return nonces.first_use(nonce)
    except Exception:  # noqa: BLE001 - any oddity is a refusal
        return False


@dataclass
class _Request:
    nonce: str
    label: str
    kind: str
    lease_ref: str | None
    created: float
    answer: str | None = None
    answer_lease: str | None = None


class WakeBoard:
    """The wake/extend requests the engine wants the box-side waker to carry out, and the waker's answers. In-process (like the
    job store): with one engine process per container a poll finds them; across two containers (a swap) a wake the other
    container never sees simply expires as `failed` and the run fails closed. Thread-safe, injected clock and nonce source."""

    MAX_LISTED = 20

    def __init__(
        self,
        ttl_s: float,
        *,
        clock: Callable[[], float] = time.time,
        nonce: Callable[[], str] = lambda: secrets.token_hex(16),
    ) -> None:
        self._ttl, self._clock, self._nonce = ttl_s, clock, nonce
        self._reqs: dict[str, _Request] = {}
        self._lease: dict[str, str] = {}
        self._lock = threading.Lock()

    def request(self, label: str, kind: str = "wake", lease_ref: str | None = None) -> str:
        """Ask for a wake/extend of a judge; returns the request's nonce. One live request per (label, kind): asking again for
        the same pair while one is unanswered and unexpired returns the existing nonce (idempotent)."""
        canonical(0, "0" * 32, label, kind, lease_ref)  # validates label, kind and lease_ref (raises ValueError)
        with self._lock:
            now = self._clock()
            self._expire_locked(now)
            for r in self._reqs.values():
                if r.label == label and r.kind == kind and r.answer is None:
                    return r.nonce
            n = self._nonce()
            self._reqs[n] = _Request(n, label, kind, lease_ref, now)
            return n

    def signed(self, secret: str | bytes) -> list[dict[str, str]]:
        """The unanswered, unexpired requests, signed FRESH now (the same nonce, a current timestamp): what a poll returns."""
        with self._lock:
            now = self._clock()
            self._expire_locked(now)
            out: list[dict[str, str]] = []
            for r in list(self._reqs.values()):
                if r.answer is not None or len(out) >= self.MAX_LISTED:
                    continue
                ts = int(now)
                payload: dict[str, Any] = {"v": 1, "kind": r.kind, "label": r.label, "ts": ts, "nonce": r.nonce}
                if r.lease_ref is not None:
                    payload["lease_ref"] = r.lease_ref
                body = json.dumps(payload, separators=(",", ":"))
                out.append({"body": body, "sig": "v1=" + sign(secret, ts, r.nonce, r.label, r.kind, r.lease_ref)})
            return out

    def deliver(self, results: Any) -> int:
        """Apply the waker's results (a list of {nonce, state, lease_ref}); unknown nonces, bad states and repeats are ignored.
        Returns how many were applied."""
        applied = 0
        if not isinstance(results, list):
            return 0
        with self._lock:
            for item in results[: self.MAX_LISTED]:
                if not isinstance(item, dict):
                    continue
                key = item.get("nonce")
                r = self._reqs.get(key) if isinstance(key, str) else None
                state = item.get("state")
                if r is None or r.answer is not None or state not in ANSWERS:
                    continue
                r.answer = str(state)
                ref = item.get("lease_ref")
                r.answer_lease = ref if isinstance(ref, str) and ref and "|" not in ref else None
                if r.answer in ("granted", "already_awake") and r.answer_lease and r.kind == "wake":
                    self._lease[r.label] = r.answer_lease
                applied += 1
        return applied

    def answer(self, nonce: str) -> tuple[str, str | None] | None:
        """(state, lease_ref) once the request is answered or expired (an expired one is `failed`), else None."""
        with self._lock:
            self._expire_locked(self._clock())
            r = self._reqs.get(nonce)
            return None if r is None or r.answer is None else (r.answer, r.answer_lease)

    def lease_for(self, label: str) -> str | None:
        """The lease reference the waker last granted for this judge (what an extend carries), or None."""
        with self._lock:
            return self._lease.get(label)

    def open_rows(self) -> list[dict[str, Any]]:
        """The unanswered, unexpired requests as plain rows (PR-4: the caller signs them), at most MAX_LISTED."""
        with self._lock:
            self._expire_locked(self._clock())
            rows = [r for r in self._reqs.values() if r.answer is None]
            return [
                {"nonce": r.nonce, "label": r.label, "kind": r.kind, "lease_ref": r.lease_ref} for r in rows[: self.MAX_LISTED]
            ]

    def latest(self, label: str) -> tuple[str | None, float] | None:
        """(answer or None, age in seconds) of the most recent `wake` request for this judge, or None when there is none.
        An unanswered request older than the ttl reads as answered `failed`."""
        with self._lock:
            now = self._clock()
            self._expire_locked(now)
            rows = [r for r in self._reqs.values() if r.label == label and r.kind == "wake"]
            if not rows:
                return None
            r = max(rows, key=lambda x: x.created)
            return r.answer, now - r.created

    def pending(self) -> int:
        with self._lock:
            self._expire_locked(self._clock())
            return sum(1 for r in self._reqs.values() if r.answer is None)

    def _expire_locked(self, now: float) -> None:
        for r in self._reqs.values():
            if r.answer is None and now - r.created > self._ttl:
                r.answer = "failed"
        for n in [n for n, r in self._reqs.items() if r.answer is not None and now - r.created > 4 * self._ttl]:
            del self._reqs[n]

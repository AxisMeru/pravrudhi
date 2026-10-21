"""#1038 cold start with progress, PR-1: the pure core (nothing here is reached while `cold_start_wake` is off).

Three small parts, independent of the HTTP route and of any network:

* `parse_cold_start_config`: the switch and the numbers (flag `cold_start_wake`, packaged false), read from
  `partner_api.yaml`. Any bad value keeps the feature OFF (never raises).
* `JudgeWake`: the per-judge state machine behind the coarse `wake` block the app polls: waiting -> starting -> ready | failed,
  one budget for both judges, an injected clock, no I/O. A health reading of `unknown` is NOT ready (it counts as starting
  until the budget ends). Outcomes: both ready = `ready`; the first ready and the second failed = `partial` (the run then
  refers every established ingredient: nothing is shown as supported without both judges); the first failed = `failed` or
  `timeout`.
* `HttpWaker` / `sign`: the engine's side of the waker contract (design note WAKER_CONTRACT_ENGINE_TO_WORKER_1038): a signed
  label-only request to a separate waker service. The engine holds an HMAC secret for the waker and calls no RunPod write route
  itself; it never raises: any failure, timeout or odd answer is a `refused` answer.

Nothing in this module logs or returns a secret, a key id, a lease or a cap."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

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
    waker_url: str  # https://.../wake
    waker_secret_env: str  # the NAME of the env var holding the HMAC secret (secret by reference)
    waker_timeout_s: float
    user_wakes_per_day: int = 6  # per verified user, failed wakes count too (R2 item 10)
    failed_cooldown_s: float = 120.0  # after a failed wake the same user waits this long (no retry storm)


_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{2,63}")


def _num(raw: Any, lo: float, hi: float) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not (lo <= raw <= hi):
        return None
    return float(raw)


def parse_cold_start_config(body: Any) -> WakeConfig | None:
    """The config for a parsed `partner_api.yaml` body, or None (off). Pure; never raises. Only the boolean true turns it
    on, and then every value must be valid: the waker URL an https URL whose path is exactly /wake, and
    `cold_start_wake_second` the value `parallel` (the lazy variant is not built yet). One bad value keeps it OFF."""
    if not isinstance(body, Mapping) or body.get(FLAG) is not True:
        return None
    budget = _num(body.get("cold_start_budget_s", 300), 30, HARD_MAX_BUDGET_S)
    poll = _num(body.get("cold_start_poll_after_s", 5), 1, 60)
    margin = _num(body.get("cold_start_deadline_margin_s", 30), 0, 300)
    timeout = _num(body.get("cold_start_waker_timeout_s", 5), 1, 30)
    env = body.get("cold_start_waker_secret_env", "PRAVRUDHI_WAKER_SECRET")
    url = body.get("cold_start_waker_url", "")
    if budget is None or poll is None or margin is None or timeout is None:
        return None
    per_day = _num(body.get("cold_start_user_wakes_per_day", 6), 1, 50)
    cooldown = _num(body.get("cold_start_failed_cooldown_s", 120), 0, 3600)
    if per_day is None or cooldown is None or body.get("cold_start_wake_second", "parallel") != "parallel":
        return None
    if not isinstance(env, str) or not _ENV_NAME.fullmatch(env) or not isinstance(url, str):
        return None
    try:
        parts = urllib.parse.urlparse(url)
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname or parts.path != WAKER_PATH:
        return None
    if parts.query or parts.fragment or parts.username:
        return None
    return WakeConfig(budget, int(poll), margin, url, env, timeout, int(per_day), cooldown)


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
        m = self._model
        if any(m[r] in ("waiting", "starting") for r in ROLES):
            return "waking"
        if m["first"] == "ready":
            return "ready" if m["second"] == "ready" else "partial"
        return "timeout" if "first" in self._timed_out else "failed"

    def reason(self) -> Reason | None:
        s = self.state()
        if s == "partial":
            return "second_judge_unavailable"
        if s == "timeout":
            return "start_timeout"
        if s == "failed":
            return "first_judge_unavailable"
        return None

    def block(self) -> dict[str, Any]:
        """The coarse `wake` block: states and a reason only. No ids, counts, lease, cap, cost, envelope or key (R2 item 11)."""
        return {
            "state": self.state(),
            "models": [{"role": r, "state": self._model[r], "progress": None} for r in ROLES],
            "reason": self.reason(),
            "deadline_s": int(min(self._cfg.budget_s, HARD_MAX_BUDGET_S) + self._cfg.deadline_margin_s),
        }


# ---- the engine's side of the waker contract ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WakerAnswer:
    state: Literal["granted", "already_awake", "refused"]
    lease_ref: str | None = None


class Waker(Protocol):
    def wake(self, label: str) -> WakerAnswer: ...

    def extend(self, label: str, lease_ref: str) -> WakerAnswer: ...


def canonical(ts: int, nonce: str, label: str, kind: str, lease_ref: str | None = None) -> str:
    """The exact string the HMAC covers (the contract): `v1|POST|/wake|ts|nonce|label|kind`, plus `|lease_ref` for an extend."""
    base = f"v1|POST|{WAKER_PATH}|{ts}|{nonce}|{label}|{kind}"
    return f"{base}|{lease_ref}" if lease_ref is not None else base


def sign(secret: str | bytes, ts: int, nonce: str, label: str, kind: str, lease_ref: str | None = None) -> str:
    key = secret.encode() if isinstance(secret, str) else secret
    return hmac.new(key, canonical(ts, nonce, label, kind, lease_ref).encode(), hashlib.sha256).hexdigest()


Send = Callable[[str, bytes, Mapping[str, str], float], tuple[int, bytes]]
_MAX_ANSWER = 4096


def _post(url: str, body: bytes, headers: Mapping[str, str], timeout_s: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")  # noqa: S310 - https, validated
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:  # noqa: S310
            return int(r.status), r.read(_MAX_ANSWER)
    except urllib.error.HTTPError as e:  # a non-2xx status is an answer, not an exception
        return int(e.code), b""


class HttpWaker:
    """Calls the waker service. One attempt per call, a bounded timeout, never raises: every failure is `refused`."""

    def __init__(
        self,
        url: str,
        secret: str | bytes,
        timeout_s: float = 5.0,
        *,
        clock: Callable[[], float] = time.time,
        nonce: Callable[[], str] = lambda: secrets.token_hex(16),
        send: Send = _post,
    ) -> None:
        if urllib.parse.urlparse(url).path != WAKER_PATH:
            raise ValueError("the waker URL path must be exactly /wake")
        self._url, self._secret, self._timeout = url, secret, timeout_s
        self._clock, self._nonce, self._send = clock, nonce, send

    def wake(self, label: str) -> WakerAnswer:
        return self._call("wake", label, None)

    def extend(self, label: str, lease_ref: str) -> WakerAnswer:
        return self._call("extend", label, lease_ref)

    def _call(self, kind: str, label: str, lease_ref: str | None) -> WakerAnswer:
        refused = WakerAnswer("refused")
        try:
            if label not in ROLES:
                return refused
            ts, nonce = int(self._clock()), self._nonce()
            payload: dict[str, Any] = {"v": 1, "kind": kind, "label": label, "ts": ts, "nonce": nonce}
            if lease_ref is not None:
                payload["lease_ref"] = lease_ref
            sig = sign(self._secret, ts, nonce, label, kind, lease_ref)
            headers = {"Content-Type": "application/json", "X-Waker-Signature": "v1=" + sig}
            body = json.dumps(payload, separators=(",", ":")).encode()
            status, raw = self._send(self._url, body, headers, self._timeout)
            if status != 200:
                return refused
            out = json.loads(raw.decode("utf-8"))
            state = out.get("state") if isinstance(out, dict) else None
            ref = out.get("lease_ref") if isinstance(out, dict) else None
            if state in ("granted", "already_awake"):
                return WakerAnswer(state, ref if isinstance(ref, str) else None)
            return refused
        except Exception:  # noqa: BLE001 - the waker must never turn a refusal into an engine error
            return refused

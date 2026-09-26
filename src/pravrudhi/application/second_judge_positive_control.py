"""Issue #44 (standing second-judge positive control) + Lead-2's 2026-09-26 dry-run-driven spec
clarification: a PRESENT-and-WRONG second judge is not caught by availability monitoring alone -- the
endpoint answers, so it reads as healthy even if silently miscalibrated or serving the wrong weights.

Three checks against two fixed, sealed control sets (research/gates/P2b/configC/second_judge_positive_
control/ in prabhasa-nyaya: `established_200.json`, `ne_discrimination_71.json`, each element carrying a
`sealed_reference_p` captured from the sealed production run):

1. PARITY (serving integrity, gates): the LIVE second judge's own tau-decisions on all 271 elements must
   agree with the SEALED reference decisions on >=`parity_floor`, AND median(|p_live-p_sealed|) must be
   <=`parity_median_abs_dp`. Answers "is this the same calibrated model that was sealed", not "is the model
   good at law" -- trivially 100%/0 if scored against itself (a dry-run), only meaningful against a live
   endpoint.
2. NE DISCRIMINATION vs gold (safety, gates): p_live<`ne_discrimination_tau` on >=`ne_discrimination_min` of
   the 71 NE elements. UNCHANGED from the original spec -- one more fooled element fails this closed, the
   safe direction.
3. Established accuracy vs gold (recorded, NEVER gates): count with p_live>=0.5 and count with
   p_live>=ne_discrimination_tau, out of 200. A miss here fails safe (not_confirmed -> REFER, a coverage
   loss, never a false proof) -- alerted (not failed) if it drops more than `est_accuracy_alert_drop`
   percentage points below the sealed baseline on the same 200.

Below the parity floor OR the NE discrimination floor (including when the live endpoint could not be
reached/timed out at all -- `EndpointUnavailable`): the second judge is treated as unavailable, the SAME
fail-closed-to-REFER path `AndGateJudge` already uses for an absent/errored endpoint -- no new decision
logic in `AndGateJudge` itself.

TRIGGER WIRING (Lead-2, 2026-09-26): the engine refuses to let a real second-judge call happen at all unless
a RECORD of a passing live check exists, is younger than `second_judge_positive_control.max_age_hours`, and
names the CURRENT `second_judge.endpoint_id` / `second_judge.adapter_sha` -- no record, a stale record, or a
mismatched record all fail closed the same way, closing the loop without depending on anyone remembering to
run the preflight CLI. `write_record`/`read_record`/`check_record` are the pure functions; `RecordGatedJudge`
is the `Judge` wrapper `nyaya_agent._build_judge` puts around the real second judge -- it raises
`RecordCheckFailed` instead of ever calling the real endpoint when the record doesn't clear, and
`AndGateJudge`'s existing generic except-Exception handling turns that into `second_judge_unavailable` for
every element, exactly as if the endpoint itself had errored.
"""
from __future__ import annotations

import json
import os
import statistics
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


class EndpointUnavailable(Exception):
    """The live second-judge endpoint could not be scored at all (connection error, timeout, malformed
    reply) -- distinct from a reachable-but-miscalibrated endpoint (which fails PARITY/NE instead). Both
    outcomes lead to the SAME fail-closed decision; this exception exists so the caller can tell them apart
    for logging/alerting without changing the availability verdict."""


class PrivateControlDataUnavailable(RuntimeError):
    """pravrudhi is PUBLIC. The sealed control sets (item ids, sealed reference p values) and the v1
    eval_items.jsonl (scenario/statute text) are private -- they live only in prabhasa-nyaya, never
    committed here. Raised by `resolve_private_root` when the private path isn't configured or doesn't
    contain what's expected; the CLI refuses to run rather than silently skipping the check or falling back
    to some public substitute (there is no safe public substitute)."""


def resolve_private_root(env: Mapping[str, str] | None = None) -> Path:
    """Reads `PRABHASA_NYAYA_ROOT` (or the given `env` mapping, for tests) and verifies the private control
    data actually exists there before returning it. Never returns a path that doesn't have the expected
    files -- the caller can trust the result without re-checking."""
    env = env if env is not None else os.environ
    raw = env.get("PRABHASA_NYAYA_ROOT")
    if not raw:
        raise PrivateControlDataUnavailable(
            "PRABHASA_NYAYA_ROOT is not set. The sealed control sets and eval data are private "
            "(prabhasa-nyaya) and are never committed to this public repo -- refusing to run."
        )
    root = Path(raw)
    configc = root / "research" / "gates" / "P2b" / "configC" / "second_judge_positive_control"
    required = [
        configc / "established_200.json",
        configc / "ne_discrimination_71.json",
        root / "research" / "gates" / "P2b" / "element_judgment_v1" / "eval_items.jsonl",
    ]
    missing = [str(p) for p in required if not p.is_file()]
    if missing:
        raise PrivateControlDataUnavailable(
            f"PRABHASA_NYAYA_ROOT={raw!r} is set but is missing required file(s): {missing} -- "
            "refusing to run."
        )
    return root


@dataclass(frozen=True)
class ControlElement:
    item_id: str
    element_id: str
    sealed_reference_p: float
    gold_status: str  # "established" or "not_established"


class ScoreFn(Protocol):
    """Scores one control element live, production prompt/template. Raises EndpointUnavailable (or lets a
    lower-level exception propagate -- callers should catch broadly) on any failure to get a real score."""

    def __call__(self, element: ControlElement) -> float: ...


@dataclass(frozen=True)
class ParityResult:
    n_total: int
    n_tau_agree: int
    median_abs_dp: float | None  # None iff n_total == 0 -- a median of no differences is undefined, not 0

    @property
    def agree_rate(self) -> float | None:
        """None iff n_total == 0 -- an agreement rate over zero elements is undefined, not a measured 0%."""
        return self.n_tau_agree / self.n_total if self.n_total else None


@dataclass(frozen=True)
class NEResult:
    n_total: int
    n_correct: int


@dataclass(frozen=True)
class EstablishedResult:
    n_total: int
    n_p_ge_half: int
    n_p_ge_tau: int

    @property
    def accuracy(self) -> float | None:
        """None iff n_total == 0 -- an accuracy over zero elements is undefined, not a measured 0%."""
        return self.n_p_ge_half / self.n_total if self.n_total else None


@dataclass(frozen=True)
class ControlCheckResult:
    parity: ParityResult | None  # None iff the endpoint was unavailable (no live scores at all)
    ne: NEResult | None
    established: EstablishedResult | None
    endpoint_unavailable: bool = False
    endpoint_error: str | None = None


@dataclass(frozen=True)
class Verdict:
    available: bool
    reasons: list[str] = field(default_factory=list)


def compute_parity(elements: list[ControlElement], live_scores: dict[str, float], tau: float) -> ParityResult:
    n_agree = 0
    abs_dps = []
    for el in elements:
        key = f"{el.item_id}__{el.element_id}"
        p_live = live_scores[key]
        p_sealed = el.sealed_reference_p
        if (p_live >= tau) == (p_sealed >= tau):
            n_agree += 1
        abs_dps.append(abs(p_live - p_sealed))
    return ParityResult(n_total=len(elements), n_tau_agree=n_agree,
                         median_abs_dp=statistics.median(abs_dps) if abs_dps else None)


def compute_ne_discrimination(ne_elements: list[ControlElement], live_scores: dict[str, float], tau: float) -> NEResult:
    n_correct = 0
    for el in ne_elements:
        key = f"{el.item_id}__{el.element_id}"
        if live_scores[key] < tau:
            n_correct += 1
    return NEResult(n_total=len(ne_elements), n_correct=n_correct)


def compute_established_accuracy(
    established_elements: list[ControlElement], live_scores: dict[str, float], tau: float
) -> EstablishedResult:
    n_ge_half = n_ge_tau = 0
    for el in established_elements:
        key = f"{el.item_id}__{el.element_id}"
        p = live_scores[key]
        if p >= 0.5:
            n_ge_half += 1
        if p >= tau:
            n_ge_tau += 1
    return EstablishedResult(n_total=len(established_elements), n_p_ge_half=n_ge_half, n_p_ge_tau=n_ge_tau)


def decide_availability(
    result: ControlCheckResult, *, parity_floor: float, parity_median_abs_dp: float, ne_discrimination_min: int,
) -> Verdict:
    if result.endpoint_unavailable or result.parity is None or result.ne is None:
        return Verdict(available=False, reasons=[f"endpoint_unavailable: {result.endpoint_error}"])
    reasons = []
    # An empty control set (n_total == 0) makes agree_rate/median_abs_dp None -- undefined, not a passing
    # measurement. Fail closed explicitly rather than comparing None to a float (which would raise).
    if result.parity.n_total == 0:
        reasons.append("parity: no elements scored (empty control set) -- cannot verify")
    else:
        if result.parity.agree_rate < parity_floor:
            reasons.append(f"parity {result.parity.n_tau_agree}/{result.parity.n_total} "
                            f"({result.parity.agree_rate:.4%}) < floor {parity_floor:.2%}")
        if result.parity.median_abs_dp > parity_median_abs_dp:
            reasons.append(f"parity median|dp| {result.parity.median_abs_dp:.4f} > {parity_median_abs_dp}")
    if result.ne.n_total == 0:
        reasons.append("ne_discrimination: no elements scored (empty control set) -- cannot verify")
    elif result.ne.n_correct < ne_discrimination_min:
        reasons.append(f"ne_discrimination {result.ne.n_correct}/{result.ne.n_total} "
                        f"< floor {ne_discrimination_min}/{result.ne.n_total}")
    return Verdict(available=not reasons, reasons=reasons)


def run_live_check(
    established: list[ControlElement], ne: list[ControlElement], *, score_fn: Callable[[ControlElement], float],
    parity_tau: float,
) -> ControlCheckResult:
    """Scores every element in both control sets via `score_fn` (the real implementation calls the live
    second-judge endpoint with the production prompt/template; tests inject a stub). ANY exception from
    `score_fn` -- connection error, timeout, malformed reply -- is caught here and turned into
    `endpoint_unavailable=True`, never a partial/silent result: a positive control that can't reach the
    endpoint at all must fail closed exactly like one that reaches it and finds it miscalibrated."""
    live_scores: dict[str, float] = {}
    try:
        for el in established + ne:
            live_scores[f"{el.item_id}__{el.element_id}"] = score_fn(el)
    except Exception as e:  # noqa: BLE001
        return ControlCheckResult(parity=None, ne=None, established=None, endpoint_unavailable=True,
                                   endpoint_error=f"{type(e).__name__}: {e}")

    parity = compute_parity(established + ne, live_scores, parity_tau)
    ne_result = compute_ne_discrimination(ne, live_scores, parity_tau)
    est_result = compute_established_accuracy(established, live_scores, parity_tau)
    return ControlCheckResult(parity=parity, ne=ne_result, established=est_result)


# -- trigger wiring: the record the engine gates on (Lead-2, 2026-09-26) ------------------------------------

class RecordCheckFailed(Exception):
    """No passing live-check record exists, or the one that does is too old or names a different endpoint/
    adapter than the one about to be used. Raised by `RecordGatedJudge` INSTEAD OF calling the real second
    judge -- `AndGateJudge`'s existing generic except-Exception handling converts this into
    `second_judge_unavailable` for every element, the same fail-closed path an absent/errored endpoint
    already uses."""


def write_record(
    path: Path, *, available: bool, endpoint_id: str | None, adapter_sha: str | None, reasons: list[str],
    timestamp: float | None = None,
) -> None:
    """Called by the live preflight CLI after every run -- the durable record `check_record` reads later.
    Written atomically (write to a temp file, then rename) so a reader never sees a half-written record."""
    record = {
        "timestamp": timestamp if timestamp is not None else time.time(),
        "available": available, "endpoint_id": endpoint_id, "adapter_sha": adapter_sha, "reasons": reasons,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(record, indent=2) + "\n")
    tmp.replace(path)


def read_record(path: Path) -> dict[str, Any] | None:
    """None for "no record" (file absent, or unreadable/malformed) -- a missing or corrupt record is exactly
    as fail-closed-worthy as a stale one, never an error that crashes the caller."""
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def check_record(
    record: dict[str, Any] | None, *, max_age_hours: float, expected_endpoint_id: str | None,
    expected_adapter_sha: str | None, now: float | None = None,
) -> tuple[bool, str]:
    """(ok, reason). ok=False for: no record, a record whose own `available` was False, a record older than
    `max_age_hours`, or a record naming a different endpoint_id/adapter_sha -- all four are indistinguishable
    to the caller (RecordGatedJudge raises the same way for any of them), but the reason string says which."""
    if record is None:
        return False, "no record"
    # Explicit `is not True` rather than `.get("available", False)`: a missing/malformed "available" key
    # must never be silently treated the same as a real measured False -- both fail closed here, but the
    # reason string (and any future caller) can tell "no verdict recorded" apart from "verdict was failure".
    if record.get("available") is not True:
        return False, f"last check failed or malformed record: {record.get('reasons')}"
    if expected_endpoint_id is None or expected_adapter_sha is None:
        return False, (
            "second_judge.endpoint_id/adapter_sha not configured -- cannot verify record identity "
            "(never treat an unset expected identity as matching an unset recorded one)"
        )
    now = now if now is not None else time.time()
    age_hours = (now - record["timestamp"]) / 3600.0
    if age_hours > max_age_hours:
        return False, f"record is {age_hours:.1f}h old, max_age_hours={max_age_hours}"
    if record.get("endpoint_id") != expected_endpoint_id:
        return False, f"record endpoint_id={record.get('endpoint_id')!r} != current {expected_endpoint_id!r}"
    if record.get("adapter_sha") != expected_adapter_sha:
        return False, f"record adapter_sha={record.get('adapter_sha')!r} != current {expected_adapter_sha!r}"
    return True, "ok"


class RecordGatedJudge:
    """Wraps a real second-judge `Judge`. Before EVERY call, checks the positive-control record -- if it
    isn't fresh and matching, raises `RecordCheckFailed` instead of ever reaching the real endpoint.
    `AndGateJudge` sees this exactly like a live endpoint error (its generic except-Exception path), so every
    element fails closed to REFER, never falls back to a primary-alone decision. No new decision logic in
    `AndGateJudge` itself -- the gate lives entirely in this wrapper."""

    def __init__(
        self, inner, *, record_path: Path, max_age_hours: float, expected_endpoint_id: str | None,
        expected_adapter_sha: str | None,
    ) -> None:
        self.inner = inner
        self.record_path = record_path
        self.max_age_hours = max_age_hours
        self.expected_endpoint_id = expected_endpoint_id
        self.expected_adapter_sha = expected_adapter_sha
        self.name = getattr(inner, "name", "second")

    def judge(self, request):
        record = read_record(self.record_path)
        ok, reason = check_record(
            record, max_age_hours=self.max_age_hours, expected_endpoint_id=self.expected_endpoint_id,
            expected_adapter_sha=self.expected_adapter_sha,
        )
        if not ok:
            raise RecordCheckFailed(reason)
        return self.inner.judge(request)

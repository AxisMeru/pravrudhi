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
logic in the judge itself. This module only decides `available: bool`; wiring that decision to the deployed
second_judge config is an operational action, not code here.
"""
from __future__ import annotations

import os
import statistics
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


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
    median_abs_dp: float

    @property
    def agree_rate(self) -> float:
        return self.n_tau_agree / self.n_total if self.n_total else 0.0


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
    def accuracy(self) -> float:
        return self.n_p_ge_half / self.n_total if self.n_total else 0.0


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
                         median_abs_dp=statistics.median(abs_dps) if abs_dps else 0.0)


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
    if result.parity.agree_rate < parity_floor:
        reasons.append(f"parity {result.parity.n_tau_agree}/{result.parity.n_total} "
                        f"({result.parity.agree_rate:.4%}) < floor {parity_floor:.2%}")
    if result.parity.median_abs_dp > parity_median_abs_dp:
        reasons.append(f"parity median|dp| {result.parity.median_abs_dp:.4f} > {parity_median_abs_dp}")
    if result.ne.n_correct < ne_discrimination_min:
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
